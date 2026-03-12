from __future__ import annotations

from dataclasses import dataclass

from core.prompt_assembly_report import ContextItem


@dataclass(frozen=True)
class ContextBudget:
    total_tokens: int = 900
    bootstrap_tokens: int = 180
    relevant_tokens: int = 520
    cold_tokens: int = 0
    max_bootstrap_items: int = 5
    max_relevant_items: int = 6
    max_cold_items: int = 2


@dataclass
class BudgetedLayer:
    included: list[ContextItem]
    excluded: list[tuple[ContextItem, str]]
    used_tokens: int
    used_chars: int


def normalize_budget(budget: ContextBudget) -> ContextBudget:
    bootstrap = max(0, int(budget.bootstrap_tokens))
    total = max(bootstrap, int(budget.total_tokens))
    remaining = max(0, total - bootstrap)
    relevant = min(max(0, int(budget.relevant_tokens)), remaining)
    cold = min(max(0, int(budget.cold_tokens)), max(0, remaining - relevant))
    return ContextBudget(
        total_tokens=total,
        bootstrap_tokens=bootstrap,
        relevant_tokens=relevant,
        cold_tokens=cold,
        max_bootstrap_items=max(1, int(budget.max_bootstrap_items)),
        max_relevant_items=max(1, int(budget.max_relevant_items)),
        max_cold_items=max(0, int(budget.max_cold_items)),
    )


def _truncate_to_tokens(item: ContextItem, token_budget: int) -> ContextItem | None:
    if token_budget <= 0:
        return None
    approx_chars = max(32, token_budget * 4)
    content = (item.content or "").strip()
    if len(content) <= approx_chars:
        return item
    trimmed = content[: max(24, approx_chars - 3)].rstrip() + "..."
    return ContextItem(
        item_id=item.item_id,
        layer=item.layer,
        source_type=item.source_type,
        title=item.title,
        content=trimmed,
        priority=item.priority,
        confidence=item.confidence,
        must_keep=item.must_keep,
        include_reason=item.include_reason,
        metadata=dict(item.metadata),
        provenance=dict(item.provenance),
    )


def budget_layer(items: list[ContextItem], *, token_budget: int, max_items: int) -> BudgetedLayer:
    sorted_items = sorted(items, key=lambda item: (item.must_keep, item.priority, item.confidence), reverse=True)
    included: list[ContextItem] = []
    excluded: list[tuple[ContextItem, str]] = []
    used_tokens = 0
    used_chars = 0

    for item in sorted_items:
        if len(included) >= max_items:
            excluded.append((item, "max_items_exceeded"))
            continue

        remaining = max(0, token_budget - used_tokens)
        item_tokens = item.token_count
        if item_tokens <= remaining:
            included.append(item)
            used_tokens += item_tokens
            used_chars += item.char_count
            continue

        if remaining <= 0:
            excluded.append((item, "budget_exhausted"))
            continue

        trimmed = _truncate_to_tokens(item, remaining)
        if trimmed is None or trimmed.token_count > remaining:
            excluded.append((item, "over_budget"))
            continue

        included.append(trimmed)
        used_tokens += trimmed.token_count
        used_chars += trimmed.char_count
        excluded.append((item, "trimmed_to_fit"))

    return BudgetedLayer(
        included=included,
        excluded=excluded,
        used_tokens=used_tokens,
        used_chars=used_chars,
    )
