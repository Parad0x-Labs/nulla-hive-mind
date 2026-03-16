from __future__ import annotations

from unittest import mock

import pytest

from core.credit_ledger import award_credits, get_credit_balance
from core.memory_first_router import ModelExecutionDecision
from network.signer import get_local_peer_id


def test_credit_balance_uses_model_wording_over_real_current_credits(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="credit-balance",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="You currently have 12.00 compute credits available.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    peer_id = get_local_peer_id()
    award_credits(peer_id, 12.0, "test_award", receipt_id="credit-balance-test")

    result = agent.run_once(
        "what is my credit balance?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert get_credit_balance(peer_id) >= 12.0
    assert result["response_class"] == "utility_answer"
    assert result["model_execution"]["used_model"] is True
    assert "12.00 compute credits" in result["response"]


def test_credit_status_explains_current_reward_contract(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="credit-policy",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=(
                "Plain public Hive posts do not mint credits by themselves. "
                "Credits and provider score come from rewarded assist tasks and accepted results."
            ),
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        "how do i earn hive credits?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == "utility_answer"
    assert result["model_execution"]["used_model"] is True
    assert "plain public hive posts do not mint credits" in lowered
    assert "rewarded assist tasks and accepted results" in lowered


@pytest.mark.xfail(strict=False, reason="Chat-level credit transfer and task-priority spending are not wired into the runtime contract yet.")
def test_future_chat_can_spend_credits_to_prioritize_hive_task(make_agent):
    agent = make_agent()
    peer_id = get_local_peer_id()
    award_credits(peer_id, 50.0, "future_priority_seed", receipt_id="future-priority-seed")

    result = agent.run_once(
        "spend 10 credits to prioritize the current Hive task",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "task_status"
    assert "reserved 10.00 credits" in result["response"].lower()


@pytest.mark.xfail(strict=False, reason="The runtime has ledger/economics primitives, but end-user chat transfer policy is not implemented.")
def test_future_chat_can_transfer_credits_to_another_peer(make_agent):
    agent = make_agent()
    result = agent.run_once(
        "send 5 credits to peer-remote-1 for helping on this task",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "task_status"
    assert "sent 5.00 credits" in result["response"].lower()
