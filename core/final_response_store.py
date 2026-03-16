from __future__ import annotations

from typing import Any

from storage.db import get_connection


def store_final_response(parent_task_id: str, raw: str, rendered: str, status: str, confidence: float) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO finalized_responses (
                parent_task_id, raw_synthesized_text, rendered_persona_text, status_marker, confidence_score
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(parent_task_id) DO UPDATE SET
                raw_synthesized_text = excluded.raw_synthesized_text,
                rendered_persona_text = excluded.rendered_persona_text,
                status_marker = excluded.status_marker,
                confidence_score = excluded.confidence_score,
                created_at = CURRENT_TIMESTAMP
            """,
            (parent_task_id, raw, rendered, status, confidence)
        )
        conn.commit()
    finally:
        conn.close()

def get_final_response(parent_task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM finalized_responses WHERE parent_task_id = ?",
            (parent_task_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
