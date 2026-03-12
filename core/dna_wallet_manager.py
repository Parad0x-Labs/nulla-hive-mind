from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core import audit_logger
from storage.db import get_connection
from storage.migrations import run_migrations


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_secret(secret: str, salt_hex: str) -> str:
    raw = hashlib.pbkdf2_hmac(
        "sha256",
        str(secret).encode("utf-8"),
        bytes.fromhex(salt_hex),
        200_000,
    )
    return raw.hex()


@dataclass(frozen=True)
class WalletStatus:
    profile_id: str
    hot_wallet_address: str | None
    cold_wallet_address: str | None
    hot_balance_usdc: float
    cold_balance_usdc: float
    hot_auto_spend_enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "hot_wallet_address": self.hot_wallet_address,
            "cold_wallet_address": self.cold_wallet_address,
            "hot_balance_usdc": round(self.hot_balance_usdc, 6),
            "cold_balance_usdc": round(self.cold_balance_usdc, 6),
            "hot_auto_spend_enabled": self.hot_auto_spend_enabled,
        }


class DNAWalletManager:
    def __init__(self, *, profile_id: str = "default") -> None:
        self.profile_id = str(profile_id or "default").strip() or "default"

    def _ensure_schema(self) -> None:
        run_migrations()

    def configure_wallets(
        self,
        *,
        hot_wallet_address: str,
        cold_wallet_address: str,
        cold_secret: str,
        initial_hot_usdc: float = 0.0,
        initial_cold_usdc: float = 0.0,
        hot_auto_spend_enabled: bool = True,
    ) -> WalletStatus:
        self._ensure_schema()
        hot = str(hot_wallet_address or "").strip()
        cold = str(cold_wallet_address or "").strip()
        secret = str(cold_secret or "")
        if len(hot) < 24 or len(cold) < 24:
            raise ValueError("Wallet addresses look invalid.")
        if len(secret) < 8:
            raise ValueError("Cold-wallet approval secret must be at least 8 characters.")
        if float(initial_hot_usdc) < 0 or float(initial_cold_usdc) < 0:
            raise ValueError("Initial balances must be non-negative.")

        now = _utcnow()
        salt = secrets.token_hex(16)
        secret_hash = _hash_secret(secret, salt)
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO dna_wallet_profiles (
                    profile_id, hot_wallet_address, cold_wallet_address,
                    hot_balance_usdc, cold_balance_usdc, hot_auto_spend_enabled,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM dna_wallet_profiles WHERE profile_id = ?), ?),
                    ?
                )
                """,
                (
                    self.profile_id,
                    hot,
                    cold,
                    float(initial_hot_usdc),
                    float(initial_cold_usdc),
                    1 if hot_auto_spend_enabled else 0,
                    self.profile_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO dna_wallet_security (
                    profile_id, cold_secret_salt, cold_secret_hash, created_at, updated_at
                ) VALUES (
                    ?, ?, ?,
                    COALESCE((SELECT created_at FROM dna_wallet_security WHERE profile_id = ?), ?),
                    ?
                )
                """,
                (self.profile_id, salt, secret_hash, self.profile_id, now, now),
            )
            conn.commit()
        finally:
            conn.close()
        audit_logger.log(
            "dna_wallet_configured",
            target_id=self.profile_id,
            target_type="wallet",
            details={"hot_auto_spend_enabled": bool(hot_auto_spend_enabled)},
        )
        status = self.get_status()
        if status is None:
            raise RuntimeError("Wallet configuration failed to persist.")
        return status

    def get_status(self) -> WalletStatus | None:
        self._ensure_schema()
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT profile_id, hot_wallet_address, cold_wallet_address,
                       hot_balance_usdc, cold_balance_usdc, hot_auto_spend_enabled
                FROM dna_wallet_profiles
                WHERE profile_id = ?
                LIMIT 1
                """,
                (self.profile_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return WalletStatus(
            profile_id=str(row["profile_id"]),
            hot_wallet_address=str(row["hot_wallet_address"] or "").strip() or None,
            cold_wallet_address=str(row["cold_wallet_address"] or "").strip() or None,
            hot_balance_usdc=float(row["hot_balance_usdc"] or 0.0),
            cold_balance_usdc=float(row["cold_balance_usdc"] or 0.0),
            hot_auto_spend_enabled=bool(int(row["hot_auto_spend_enabled"] or 0)),
        )

    def hot_wallet_ready(self) -> bool:
        status = self.get_status()
        return bool(status and status.hot_wallet_address)

    def top_up_hot_from_cold(self, amount_usdc: float, *, cold_secret: str, initiated_by: str = "user") -> WalletStatus:
        self._ensure_schema()
        amount = float(amount_usdc)
        if amount <= 0:
            raise ValueError("Top-up amount must be positive.")
        self._require_cold_approval(cold_secret)
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT hot_balance_usdc, cold_balance_usdc FROM dna_wallet_profiles WHERE profile_id = ? LIMIT 1",
                (self.profile_id,),
            ).fetchone()
            if not row:
                conn.rollback()
                raise ValueError("Wallet profile is not configured.")
            hot_balance = float(row["hot_balance_usdc"] or 0.0)
            cold_balance = float(row["cold_balance_usdc"] or 0.0)
            if cold_balance < amount:
                conn.rollback()
                raise ValueError("Cold wallet has insufficient USDC.")
            now = _utcnow()
            conn.execute(
                """
                UPDATE dna_wallet_profiles
                SET hot_balance_usdc = ?, cold_balance_usdc = ?, updated_at = ?
                WHERE profile_id = ?
                """,
                (hot_balance + amount, cold_balance - amount, now, self.profile_id),
            )
            self._insert_ledger_entry(
                conn,
                direction="cold_to_hot",
                amount=amount,
                initiated_by=initiated_by,
                approval_mode="cold_approved",
                reference_id=f"topup-{uuid.uuid4().hex[:12]}",
                metadata={"asset": "USDC"},
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        status = self.get_status()
        if status is None:
            raise RuntimeError("Wallet profile missing after top-up.")
        return status

    def move_hot_to_cold(self, amount_usdc: float, *, cold_secret: str, initiated_by: str = "user") -> WalletStatus:
        self._ensure_schema()
        amount = float(amount_usdc)
        if amount <= 0:
            raise ValueError("Transfer amount must be positive.")
        self._require_cold_approval(cold_secret)
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT hot_balance_usdc, cold_balance_usdc FROM dna_wallet_profiles WHERE profile_id = ? LIMIT 1",
                (self.profile_id,),
            ).fetchone()
            if not row:
                conn.rollback()
                raise ValueError("Wallet profile is not configured.")
            hot_balance = float(row["hot_balance_usdc"] or 0.0)
            cold_balance = float(row["cold_balance_usdc"] or 0.0)
            if hot_balance < amount:
                conn.rollback()
                raise ValueError("Hot wallet has insufficient USDC.")
            now = _utcnow()
            conn.execute(
                """
                UPDATE dna_wallet_profiles
                SET hot_balance_usdc = ?, cold_balance_usdc = ?, updated_at = ?
                WHERE profile_id = ?
                """,
                (hot_balance - amount, cold_balance + amount, now, self.profile_id),
            )
            self._insert_ledger_entry(
                conn,
                direction="hot_to_cold",
                amount=amount,
                initiated_by=initiated_by,
                approval_mode="cold_approved",
                reference_id=f"cold-transfer-{uuid.uuid4().hex[:12]}",
                metadata={"asset": "USDC"},
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        status = self.get_status()
        if status is None:
            raise RuntimeError("Wallet profile missing after transfer.")
        return status

    def deposit_hot(self, amount_usdc: float, *, initiated_by: str = "user", reference_id: str | None = None) -> WalletStatus:
        self._ensure_schema()
        amount = float(amount_usdc)
        if amount <= 0:
            raise ValueError("Deposit amount must be positive.")
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT hot_balance_usdc FROM dna_wallet_profiles WHERE profile_id = ? LIMIT 1",
                (self.profile_id,),
            ).fetchone()
            if not row:
                conn.rollback()
                raise ValueError("Wallet profile is not configured.")
            hot_balance = float(row["hot_balance_usdc"] or 0.0)
            now = _utcnow()
            conn.execute(
                "UPDATE dna_wallet_profiles SET hot_balance_usdc = ?, updated_at = ? WHERE profile_id = ?",
                (hot_balance + amount, now, self.profile_id),
            )
            self._insert_ledger_entry(
                conn,
                direction="external_to_hot",
                amount=amount,
                initiated_by=initiated_by,
                approval_mode="manual",
                reference_id=reference_id or f"deposit-{uuid.uuid4().hex[:12]}",
                metadata={"asset": "USDC"},
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        status = self.get_status()
        if status is None:
            raise RuntimeError("Wallet profile missing after deposit.")
        return status

    def consume_hot_for_credit_purchase(
        self,
        amount_usdc: float,
        *,
        local_peer_id: str,
        reference_id: str,
        initiated_by: str = "agent",
    ) -> WalletStatus:
        self._ensure_schema()
        amount = float(amount_usdc)
        if amount <= 0:
            raise ValueError("Purchase amount must be positive.")
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT hot_balance_usdc FROM dna_wallet_profiles WHERE profile_id = ? LIMIT 1",
                (self.profile_id,),
            ).fetchone()
            if not row:
                conn.rollback()
                raise ValueError("Wallet profile is not configured.")
            hot_balance = float(row["hot_balance_usdc"] or 0.0)
            if hot_balance < amount:
                conn.rollback()
                raise ValueError("Hot wallet has insufficient USDC. Top up from cold wallet first.")
            now = _utcnow()
            conn.execute(
                "UPDATE dna_wallet_profiles SET hot_balance_usdc = ?, updated_at = ? WHERE profile_id = ?",
                (hot_balance - amount, now, self.profile_id),
            )
            self._insert_ledger_entry(
                conn,
                direction="purchase_credits",
                amount=amount,
                initiated_by=initiated_by,
                approval_mode="hot_auto",
                reference_id=reference_id,
                metadata={"asset": "USDC", "peer_id": local_peer_id},
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        status = self.get_status()
        if status is None:
            raise RuntimeError("Wallet profile missing after purchase.")
        return status

    def _require_cold_approval(self, cold_secret: str) -> None:
        self._ensure_schema()
        secret = str(cold_secret or "")
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT cold_secret_salt, cold_secret_hash
                FROM dna_wallet_security
                WHERE profile_id = ?
                LIMIT 1
                """,
                (self.profile_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            raise PermissionError("Cold-wallet approval is not configured.")
        expected = str(row["cold_secret_hash"] or "")
        salt = str(row["cold_secret_salt"] or "")
        if not expected or not salt:
            raise PermissionError("Cold-wallet approval data is incomplete.")
        actual = _hash_secret(secret, salt)
        if not secrets.compare_digest(actual, expected):
            raise PermissionError("Cold-wallet approval failed.")

    def _insert_ledger_entry(
        self,
        conn,
        *,
        direction: str,
        amount: float,
        initiated_by: str,
        approval_mode: str,
        reference_id: str | None,
        metadata: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO dna_wallet_ledger (
                entry_id, profile_id, direction, asset_symbol, amount,
                initiated_by, approval_mode, reference_id, metadata_json, created_at
            ) VALUES (?, ?, ?, 'USDC', ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                self.profile_id,
                direction,
                float(amount),
                str(initiated_by or "agent"),
                str(approval_mode or "manual"),
                str(reference_id or ""),
                json.dumps(metadata, sort_keys=True),
                _utcnow(),
            ),
        )
