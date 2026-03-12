#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"

export NULLA_HOME="${NULLA_HOME:-/tmp/nulla_deep_contract_tests}"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "${NULLA_HOME}"

cd "${PROJECT_ROOT}"

python3 -m py_compile \
  apps/nulla_agent.py \
  apps/nulla_api_server.py \
  core/hive_activity_tracker.py \
  core/tool_intent_executor.py \
  core/autonomous_topic_research.py \
  retrieval/web_adapter.py \
  core/persistent_memory.py \
  core/user_preferences.py \
  core/runtime_continuity.py \
  core/public_hive_bridge.py \
  core/knowledge_registry.py \
  core/shard_synthesizer.py \
  core/audit_logger.py \
  tests/conftest.py \
  tests/test_nulla_runtime_contracts.py \
  tests/test_nulla_router_and_state_machine.py \
  tests/test_nulla_hive_task_flow.py \
  tests/test_nulla_web_freshness_and_lookup.py \
  tests/test_nulla_local_first_memory_and_personalization.py \
  tests/test_nulla_credits_and_hive_economy_spec.py \
  tests/test_nulla_shards_and_reuse_spec.py \
  tests/test_nulla_future_vision_spec.py

python3 -m pytest \
  tests/test_nulla_runtime_contracts.py \
  tests/test_nulla_router_and_state_machine.py \
  tests/test_nulla_hive_task_flow.py \
  tests/test_nulla_web_freshness_and_lookup.py \
  -q

python3 -m pytest \
  tests/test_nulla_runtime_contracts.py \
  tests/test_nulla_router_and_state_machine.py \
  tests/test_nulla_hive_task_flow.py \
  tests/test_nulla_web_freshness_and_lookup.py \
  tests/test_nulla_local_first_memory_and_personalization.py \
  tests/test_nulla_credits_and_hive_economy_spec.py \
  tests/test_nulla_shards_and_reuse_spec.py \
  tests/test_nulla_future_vision_spec.py \
  tests/test_openclaw_tooling_context.py \
  tests/test_hive_activity_tracker.py \
  tests/test_nulla_api_server.py \
  -q
