from __future__ import annotations

from dataclasses import dataclass

from .role_contracts import provider_role_for_task_role
from .task_envelope import TaskEnvelopeV1


@dataclass(frozen=True)
class ScheduledTask:
    task_id: str
    provider_role: str
    priority: int


def schedule_task_envelopes(envelopes: list[TaskEnvelopeV1]) -> list[ScheduledTask]:
    scored: list[tuple[int, ScheduledTask]] = []
    for envelope in envelopes:
        latency_bonus = {"low_latency": 30, "balanced": 20, "deep": 10}.get(str(envelope.latency_budget or "balanced"), 20)
        quality_bonus = {"high": 20, "standard": 10, "draft": 5}.get(str(envelope.quality_target or "standard"), 10)
        side_effect_penalty = 5 if envelope.allowed_side_effects else 0
        priority = latency_bonus + quality_bonus - side_effect_penalty
        scheduled = ScheduledTask(
            task_id=envelope.task_id,
            provider_role=provider_role_for_task_role(envelope.role),
            priority=priority,
        )
        scored.append((priority, scheduled))
    scored.sort(key=lambda item: (item[0], item[1].task_id), reverse=True)
    return [item[1] for item in scored]
