from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

from core import policy_engine
from storage.db import get_connection
from storage.migrations import run_migrations

LEDGER_MODE = "simulated"
_LEDGER_TABLE_READY = False
_LEDGER_TABLE_LOCK = Lock()


@dataclass(frozen=True)
class LedgerReconciliation:
    peer_id: str
    balance: float
    entries: int
    mode: str


@dataclass(frozen=True)
class DispatchBudgetReservation:
    allowed: bool
    mode: str
    reason: str
    amount: float
    paid_credits_charged: float
    free_tier_points_used: float
    free_tier_points_limit: float


def credit_purchases_enabled() -> bool:
    return bool(policy_engine.get("economics.credit_purchase_enabled", False))


def starter_credits_enabled() -> bool:
    return bool(policy_engine.get("economics.starter_credits_enabled", True))


def starter_credit_amount() -> float:
    try:
        return max(0.0, float(policy_engine.get("economics.starter_credits_amount", 24.0)))
    except (TypeError, ValueError):
        return 24.0


def _init_ledger_table() -> None:
    global _LEDGER_TABLE_READY
    if _LEDGER_TABLE_READY:
        return
    with _LEDGER_TABLE_LOCK:
        if _LEDGER_TABLE_READY:
            return
        # Ledger schema is owned by storage migrations; do not fork DDL here.
        run_migrations()
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='compute_credit_ledger' LIMIT 1"
            ).fetchone()
            if not row:
                raise RuntimeError("compute_credit_ledger table is missing after migrations.")
            _LEDGER_TABLE_READY = True
        finally:
            conn.close()


def _init_dispatch_budget_table() -> None:
    _init_ledger_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='swarm_dispatch_budget_events' LIMIT 1"
        ).fetchone()
        if not row:
            raise RuntimeError("swarm_dispatch_budget_events table is missing after migrations.")
    finally:
        conn.close()


def _utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _utc_day_bucket(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _dispatch_limits() -> tuple[float, float]:
    try:
        daily_limit = max(0.0, float(policy_engine.get("economics.free_tier_daily_swarm_points", 24.0)))
    except (TypeError, ValueError):
        daily_limit = 24.0
    try:
        per_dispatch_limit = max(0.0, float(policy_engine.get("economics.free_tier_max_dispatch_points", 12.0)))
    except (TypeError, ValueError):
        per_dispatch_limit = 12.0
    return daily_limit, per_dispatch_limit


def _dispatch_receipt_record(conn, receipt_id: str | None) -> tuple[str, float] | None:
    if not receipt_id:
        return None
    row = conn.execute(
        """
        SELECT 'paid' AS dispatch_mode, ABS(amount) AS amount
        FROM compute_credit_ledger
        WHERE receipt_id = ?
        LIMIT 1
        """,
        (receipt_id,),
    ).fetchone()
    if row:
        return str(row["dispatch_mode"]), float(row["amount"] or 0.0)
    row = conn.execute(
        """
        SELECT dispatch_mode, amount
        FROM swarm_dispatch_budget_events
        WHERE receipt_id = ?
        LIMIT 1
        """,
        (receipt_id,),
    ).fetchone()
    if row:
        return str(row["dispatch_mode"]), float(row["amount"] or 0.0)
    return None


def _credit_balance_in_tx(conn, peer_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM compute_credit_ledger WHERE peer_id = ?",
        (peer_id,),
    ).fetchone()
    return float(row["total"]) if row else 0.0


def _free_tier_usage_in_tx(conn, peer_id: str, *, day_bucket: str) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM swarm_dispatch_budget_events
        WHERE peer_id = ?
          AND day_bucket = ?
          AND dispatch_mode = 'free_tier'
        """,
        (peer_id, day_bucket),
    ).fetchone()
    return float(row["total"]) if row else 0.0


def get_credit_balance(peer_id: str) -> float:
    _init_ledger_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM compute_credit_ledger WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
        return float(row["total"]) if row else 0.0
    finally:
        conn.close()


def get_free_tier_dispatch_usage(peer_id: str, *, day_bucket: str | None = None) -> float:
    _init_dispatch_budget_table()
    conn = get_connection()
    try:
        return _free_tier_usage_in_tx(conn, peer_id, day_bucket=day_bucket or _utc_day_bucket())
    finally:
        conn.close()


def _receipt_exists(conn, receipt_id: str | None) -> bool:
    if not receipt_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM compute_credit_ledger WHERE receipt_id = ? LIMIT 1",
        (receipt_id,),
    ).fetchone()
    return bool(row)


def award_credits(peer_id: str, amount: float, reason: str = "provider_reward", *, receipt_id: str | None = None) -> bool:
    if amount <= 0:
        return False
    _init_ledger_table()
    now_iso = _utcnow_iso()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, receipt_id):
            conn.rollback()
            return False
        conn.execute(
            """
            INSERT INTO compute_credit_ledger (
                peer_id, amount, reason, receipt_id, settlement_mode, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (peer_id, amount, reason, receipt_id, LEDGER_MODE, now_iso),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def ensure_starter_credits(peer_id: str, *, receipt_id: str | None = None) -> bool:
    clean_peer_id = str(peer_id or "").strip()
    if not clean_peer_id or not starter_credits_enabled():
        return False
    amount = starter_credit_amount()
    if amount <= 0:
        return False
    resolved_receipt = str(receipt_id or f"starter-bootstrap:{clean_peer_id}").strip()
    for attempt in range(3):
        if award_credits(
            clean_peer_id,
            amount,
            reason="starter_bootstrap",
            receipt_id=resolved_receipt,
        ):
            return True
        ledger = reconcile_ledger(clean_peer_id)
        if int(ledger.entries or 0) > 0:
            return False
        if attempt < 2:
            time.sleep(0.15)
    return False


def burn_credits(peer_id: str, amount: float, reason: str = "task_dispatch", *, receipt_id: str | None = None) -> bool:
    if amount <= 0:
        return True
    _init_ledger_table()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, receipt_id):
            conn.rollback()
            return False

        now_iso = _utcnow_iso()
        cur = conn.execute(
            """
            INSERT INTO compute_credit_ledger (
                peer_id, amount, reason, receipt_id, settlement_mode, timestamp
            )
            SELECT ?, ?, ?, ?, ?, ?
            WHERE (
                SELECT COALESCE(SUM(amount), 0)
                FROM compute_credit_ledger
                WHERE peer_id = ?
            ) >= ?
            """,
            (peer_id, -amount, reason, receipt_id, LEDGER_MODE, now_iso, peer_id, amount),
        )
        if int(cur.rowcount or 0) != 1:
            conn.rollback()
            return False

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def reserve_swarm_dispatch_budget(
    peer_id: str,
    amount: float,
    reason: str = "task_dispatch",
    *,
    receipt_id: str | None = None,
    metadata: dict | None = None,
) -> DispatchBudgetReservation:
    charge_amount = max(0.0, float(amount or 0.0))
    daily_limit, per_dispatch_limit = _dispatch_limits()
    if charge_amount <= 0:
        return DispatchBudgetReservation(
            allowed=True,
            mode="zero_cost",
            reason="zero_cost_dispatch",
            amount=0.0,
            paid_credits_charged=0.0,
            free_tier_points_used=get_free_tier_dispatch_usage(peer_id),
            free_tier_points_limit=daily_limit,
        )

    _init_dispatch_budget_table()
    now_iso = _utcnow_iso()
    day_bucket = _utc_day_bucket()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")

        existing = _dispatch_receipt_record(conn, receipt_id)
        if existing:
            mode, reserved_amount = existing
            used = _free_tier_usage_in_tx(conn, peer_id, day_bucket=day_bucket)
            conn.rollback()
            return DispatchBudgetReservation(
                allowed=True,
                mode=mode,
                reason="receipt_reused",
                amount=float(reserved_amount),
                paid_credits_charged=float(reserved_amount) if mode == "paid" else 0.0,
                free_tier_points_used=used,
                free_tier_points_limit=daily_limit,
            )

        balance = _credit_balance_in_tx(conn, peer_id)
        if balance >= charge_amount:
            conn.execute(
                """
                INSERT INTO compute_credit_ledger (
                    peer_id, amount, reason, receipt_id, settlement_mode, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (peer_id, -charge_amount, reason, receipt_id, LEDGER_MODE, now_iso),
            )
            conn.commit()
            return DispatchBudgetReservation(
                allowed=True,
                mode="paid",
                reason="credits_reserved",
                amount=charge_amount,
                paid_credits_charged=charge_amount,
                free_tier_points_used=_free_tier_usage_in_tx(conn, peer_id, day_bucket=day_bucket),
                free_tier_points_limit=daily_limit,
            )

        used = _free_tier_usage_in_tx(conn, peer_id, day_bucket=day_bucket)
        if charge_amount > per_dispatch_limit:
            conn.rollback()
            return DispatchBudgetReservation(
                allowed=False,
                mode="blocked",
                reason="task_cost_exceeds_free_tier_cap",
                amount=charge_amount,
                paid_credits_charged=0.0,
                free_tier_points_used=used,
                free_tier_points_limit=daily_limit,
            )
        if used + charge_amount > daily_limit:
            conn.rollback()
            return DispatchBudgetReservation(
                allowed=False,
                mode="blocked",
                reason="daily_free_tier_budget_exhausted",
                amount=charge_amount,
                paid_credits_charged=0.0,
                free_tier_points_used=used,
                free_tier_points_limit=daily_limit,
            )

        conn.execute(
            """
            INSERT INTO swarm_dispatch_budget_events (
                peer_id, day_bucket, amount, dispatch_mode, reason, receipt_id, metadata_json, created_at
            ) VALUES (?, ?, ?, 'free_tier', ?, ?, ?, ?)
            """,
            (
                peer_id,
                day_bucket,
                charge_amount,
                reason,
                receipt_id,
                json.dumps(metadata or {}, sort_keys=True),
                now_iso,
            ),
        )
        conn.commit()
        return DispatchBudgetReservation(
            allowed=True,
            mode="free_tier",
            reason="free_tier_reserved",
            amount=charge_amount,
            paid_credits_charged=0.0,
            free_tier_points_used=used + charge_amount,
            free_tier_points_limit=daily_limit,
        )
    except Exception:
        conn.rollback()
        used = get_free_tier_dispatch_usage(peer_id, day_bucket=day_bucket)
        return DispatchBudgetReservation(
            allowed=False,
            mode="blocked",
            reason="dispatch_budget_error",
            amount=charge_amount,
            paid_credits_charged=0.0,
            free_tier_points_used=used,
            free_tier_points_limit=daily_limit,
        )
    finally:
        conn.close()


def reconcile_ledger(peer_id: str) -> LedgerReconciliation:
    _init_ledger_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS entries
            FROM compute_credit_ledger
            WHERE peer_id = ?
            """,
            (peer_id,),
        ).fetchone()
        return LedgerReconciliation(
            peer_id=peer_id,
            balance=float(row["total"]) if row else 0.0,
            entries=int(row["entries"]) if row else 0,
            mode=LEDGER_MODE,
        )
    finally:
        conn.close()
