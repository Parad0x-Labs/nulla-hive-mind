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
            escrow_id = receipt_id or f"escrow:{reason}"
            conn.execute(
                """
                INSERT INTO compute_credit_ledger (
                    peer_id, amount, reason, receipt_id, settlement_mode, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (peer_id, -charge_amount, reason, escrow_id, LEDGER_MODE, now_iso),
            )
            task_id = str(metadata.get("parent_task_id", "") if metadata else "") or reason.removeprefix("dispatch_task:").strip()
            if task_id:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dispatch_credit_escrow (
                        escrow_id, parent_task_id, poster_peer_id,
                        total_escrowed, total_released, total_refunded,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, 0, 'active', ?, ?)
                    """,
                    (escrow_id, task_id, peer_id, charge_amount, now_iso, now_iso),
                )
            conn.commit()
            return DispatchBudgetReservation(
                allowed=True,
                mode="paid",
                reason="credits_escrowed",
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


def escrow_credits_for_task(
    poster_peer_id: str,
    parent_task_id: str,
    amount: float,
    *,
    receipt_id: str | None = None,
) -> bool:
    """Move credits from poster's balance into escrow for a dispatched task."""
    if amount <= 0:
        return True
    _init_ledger_table()
    escrow_id = receipt_id or f"escrow:{parent_task_id}"
    now_iso = _utcnow_iso()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, escrow_id):
            conn.rollback()
            return True
        balance = _credit_balance_in_tx(conn, poster_peer_id)
        if balance < amount:
            conn.rollback()
            return False
        conn.execute(
            """
            INSERT INTO compute_credit_ledger (
                peer_id, amount, reason, receipt_id, settlement_mode, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (poster_peer_id, -amount, f"escrow_hold:{parent_task_id}", escrow_id, LEDGER_MODE, now_iso),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO dispatch_credit_escrow (
                escrow_id, parent_task_id, poster_peer_id,
                total_escrowed, total_released, total_refunded,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, 0, 'active', ?, ?)
            """,
            (escrow_id, parent_task_id, poster_peer_id, amount, now_iso, now_iso),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def release_escrow_to_helper(
    parent_task_id: str,
    helper_peer_id: str,
    payout: float,
    *,
    receipt_id: str | None = None,
) -> bool:
    """Transfer credits from task escrow to a helper who completed work."""
    if payout <= 0:
        return True
    _init_ledger_table()
    release_receipt = receipt_id or f"escrow_release:{parent_task_id}:{helper_peer_id}"
    now_iso = _utcnow_iso()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, release_receipt):
            conn.rollback()
            return True
        escrow = conn.execute(
            """
            SELECT escrow_id, total_escrowed, total_released, total_refunded
            FROM dispatch_credit_escrow
            WHERE parent_task_id = ? AND status = 'active'
            LIMIT 1
            """,
            (parent_task_id,),
        ).fetchone()
        if not escrow:
            conn.rollback()
            return False
        remaining = float(escrow["total_escrowed"]) - float(escrow["total_released"]) - float(escrow["total_refunded"])
        actual_payout = min(payout, max(0.0, remaining))
        if actual_payout <= 0:
            conn.rollback()
            return True
        conn.execute(
            """
            INSERT INTO compute_credit_ledger (
                peer_id, amount, reason, receipt_id, settlement_mode, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (helper_peer_id, actual_payout, f"task_reward:{parent_task_id}", release_receipt, LEDGER_MODE, now_iso),
        )
        conn.execute(
            """
            UPDATE dispatch_credit_escrow
            SET total_released = total_released + ?, updated_at = ?
            WHERE parent_task_id = ? AND status = 'active'
            """,
            (actual_payout, now_iso, parent_task_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def refund_escrow_remainder(parent_task_id: str) -> float:
    """Return any unused escrow credits back to the poster. Returns amount refunded."""
    _init_ledger_table()
    now_iso = _utcnow_iso()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        escrow = conn.execute(
            """
            SELECT escrow_id, poster_peer_id, total_escrowed, total_released, total_refunded
            FROM dispatch_credit_escrow
            WHERE parent_task_id = ? AND status = 'active'
            LIMIT 1
            """,
            (parent_task_id,),
        ).fetchone()
        if not escrow:
            conn.rollback()
            return 0.0
        remaining = float(escrow["total_escrowed"]) - float(escrow["total_released"]) - float(escrow["total_refunded"])
        if remaining <= 0:
            conn.execute(
                "UPDATE dispatch_credit_escrow SET status = 'settled', updated_at = ? WHERE parent_task_id = ? AND status = 'active'",
                (now_iso, parent_task_id),
            )
            conn.commit()
            return 0.0
        refund_receipt = f"escrow_refund:{parent_task_id}"
        if not _receipt_exists(conn, refund_receipt):
            conn.execute(
                """
                INSERT INTO compute_credit_ledger (
                    peer_id, amount, reason, receipt_id, settlement_mode, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (escrow["poster_peer_id"], remaining, f"escrow_refund:{parent_task_id}", refund_receipt, LEDGER_MODE, now_iso),
            )
        conn.execute(
            """
            UPDATE dispatch_credit_escrow
            SET total_refunded = total_refunded + ?, status = 'settled', updated_at = ?
            WHERE parent_task_id = ? AND status = 'active'
            """,
            (remaining, now_iso, parent_task_id),
        )
        conn.commit()
        return remaining
    except Exception:
        conn.rollback()
        return 0.0
    finally:
        conn.close()


def get_escrow_for_task(parent_task_id: str) -> dict | None:
    """Return the escrow state for a task, or None if no escrow exists."""
    _init_ledger_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT escrow_id, parent_task_id, poster_peer_id,
                   total_escrowed, total_released, total_refunded, status
            FROM dispatch_credit_escrow
            WHERE parent_task_id = ?
            LIMIT 1
            """,
            (parent_task_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "escrow_id": row["escrow_id"],
            "parent_task_id": row["parent_task_id"],
            "poster_peer_id": row["poster_peer_id"],
            "total_escrowed": float(row["total_escrowed"]),
            "total_released": float(row["total_released"]),
            "total_refunded": float(row["total_refunded"]),
            "remaining": float(row["total_escrowed"]) - float(row["total_released"]) - float(row["total_refunded"]),
            "status": row["status"],
        }
    finally:
        conn.close()


def transfer_credits(
    from_peer_id: str,
    to_peer_id: str,
    amount: float,
    reason: str = "peer_transfer",
    *,
    receipt_id: str | None = None,
) -> bool:
    """Transfer credits between peers. Atomic: debit sender + credit receiver in one tx."""
    if amount <= 0 or from_peer_id == to_peer_id:
        return False
    _init_ledger_table()
    now_iso = _utcnow_iso()
    send_receipt = receipt_id or f"transfer:{from_peer_id}:{to_peer_id}:{now_iso}"
    recv_receipt = f"{send_receipt}:recv"
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, send_receipt):
            conn.rollback()
            return False
        balance = _credit_balance_in_tx(conn, from_peer_id)
        if balance < amount:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO compute_credit_ledger (peer_id, amount, reason, receipt_id, settlement_mode, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (from_peer_id, -amount, f"sent:{reason}", send_receipt, LEDGER_MODE, now_iso),
        )
        conn.execute(
            "INSERT INTO compute_credit_ledger (peer_id, amount, reason, receipt_id, settlement_mode, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (to_peer_id, amount, f"received:{reason}", recv_receipt, LEDGER_MODE, now_iso),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def award_presence_credits(peer_id: str, amount: float = 0.10, *, receipt_id: str | None = None) -> bool:
    """Award a small credit for responding to a heartbeat health check."""
    return award_credits(peer_id, amount, "presence_heartbeat", receipt_id=receipt_id)


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
