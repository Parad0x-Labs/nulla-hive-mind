# NULLA Handover
Date: 2026-03-10
Repo: `/path/to/nulla-hive-mind`
Status: In progress, not yet ready for internal testing

## 0. Executive Reality

This repo is not “just a chatbot shell”.

It is trying to become:
- a local-first AI runtime
- with OpenClaw as the control UI
- with Hive / Brain Hive as the shared task, claim, post, and research substrate
- with Liquefy as the dense compression / artifact / knowledge layer
- with a durable signal capture layer for adaptation
- with a closed-loop adaptation spine:
  - collect corpus
  - score corpus
  - train adapter
  - evaluate candidate
  - canary
  - promote or rollback
- with Watch / Trace / Control Plane visibility so the operator is not guessing

The hard truth:
- the architecture is getting real
- the intelligence is still bottlenecked by corpus quality and live model quality
- the Commons funnel was only partially landed in this session
- the repo is not green enough yet to claim “ready for internal testing”

## 1. What We Actually Built Before This Final Session

These systems were already implemented before the last Commons work.

### 1.1 Durable useful output layer

Implemented:
- `/path/to/nulla-hive-mind/storage/migrations.py`
- `/path/to/nulla-hive-mind/storage/useful_output_store.py`

This introduced a canonical `useful_outputs` table and sync pipeline.

Purpose:
- stop adaptation from treating raw chat as equal to accepted work
- mirror structured durable rows into one canonical signal layer

Current canonical sources:
- accepted / reviewed `task_results`
- successful `finalized_responses`
- approved durable / evidence-backed `hive_posts`

Each useful output row carries:
- `source_type`
- `source_id`
- `task_id`
- `topic_id`
- `claim_id`
- `result_id`
- `artifact_ids`
- `instruction_text`
- `output_text`
- `summary`
- `acceptance_state`
- `review_state`
- `archive_state`
- `eligibility_state`
- `durability_reasons`
- `eligibility_reasons`
- `quality_score`
- `metadata`

This is one of the biggest real changes in the repo.

### 1.2 Adaptation loop no longer blindly trusts chat

Implemented:
- `/path/to/nulla-hive-mind/core/adaptation_dataset.py`
- `/path/to/nulla-hive-mind/core/adaptation_autopilot.py`
- `/path/to/nulla-hive-mind/storage/adaptation_store.py`
- `/path/to/nulla-hive-mind/core/policy_engine.py`
- `/path/to/nulla-hive-mind/config/default_policy.yaml`

Key changes:
- corpus builder prefers `useful_outputs`
- raw structured readers are fallback if mirror is empty
- chat is fallback only
- loop now checks:
  - `min_structured_examples`
  - `min_high_signal_examples`
  - `max_conversation_ratio`

The loop now skips honestly instead of pretending:
- `insufficient_examples`
- `insufficient_structured_examples`
- `insufficient_high_signal_examples`
- `conversation_ratio_too_high`

This is the right direction.

### 1.3 Trace / watch / control-plane expose the blocker

Implemented:
- `/path/to/nulla-hive-mind/core/control_plane_workspace.py`
- `/path/to/nulla-hive-mind/core/runtime_task_rail.py`
- `/path/to/nulla-hive-mind/core/nulla_user_summary.py`
- `/path/to/nulla-hive-mind/core/brain_hive_dashboard.py`

Added visibility for:
- useful output totals
- training-ready totals
- high-signal totals
- adaptation blocker
- swarm budget
- Hive budget

This removed a lot of black-box bullshit.

### 1.4 Installer doctor

Implemented:
- `/path/to/nulla-hive-mind/installer/doctor.py`
- `/path/to/nulla-hive-mind/installer/install_nulla.sh`
- `/path/to/nulla-hive-mind/installer/install_nulla.bat`
- `/path/to/nulla-hive-mind/installer/write_install_receipt.py`
- `/path/to/nulla-hive-mind/NULLA_STARTER_KIT.md`

This does not make the installer “finished product”.

What it does do:
- produce explicit install health data
- stop silent warn-and-pray behavior from being the only install story

## 2. What This Session Was Supposed To Finish

The intended target for this session was:
- finish the remaining gaps around durable signal capture
- start the Commons funnel properly
- ensure Commons can become a real promotion lane into Hive research
- ensure Commons does not poison training before review

That part is only partially complete.

## 3. What Was Added In The Final Commons Slice

These are the files changed in the last active coding pass.

### 3.1 Models

Changed:
- `/path/to/nulla-hive-mind/core/brain_hive_models.py`

Added Pydantic models for Commons:
- `HiveCommonsEndorseRequest`
- `HiveCommonsEndorseRecord`
- `HiveCommonsCommentRequest`
- `HiveCommonsCommentRecord`
- `HiveCommonsPromotionCandidateRequest`
- `HiveCommonsPromotionReviewRequest`
- `HiveCommonsPromotionCandidateRecord`
- `HiveCommonsPromotionActionRequest`

Purpose:
- make Commons a real bounded API surface, not an implied UI concept

### 3.2 DB schema

Changed:
- `/path/to/nulla-hive-mind/storage/migrations.py`

Added tables:
- `hive_post_endorsements`
- `hive_post_comments`
- `hive_commons_promotion_candidates`
- `hive_commons_promotion_reviews`

These are additive only.

They do not replace existing Hive topic/post/claim structures.

### 3.3 Store layer

Changed:
- `/path/to/nulla-hive-mind/storage/brain_hive_store.py`

Added:
- `upsert_post_endorsement`
- `list_post_endorsements`
- `create_post_comment`
- `list_post_comments`
- `upsert_commons_promotion_candidate`
- `get_commons_promotion_candidate`
- `get_commons_promotion_candidate_by_post`
- `list_commons_promotion_candidates`
- `upsert_commons_promotion_review`
- `list_commons_promotion_reviews`

Also added row converters:
- `_row_to_comment`
- `_row_to_promotion_candidate`
- `_row_to_promotion_review`

This is the sqlite backbone for Commons.

### 3.4 Service layer

Changed:
- `/path/to/nulla-hive-mind/core/brain_hive_service.py`

Added:
- `endorse_post`
- `list_post_endorsements`
- `comment_on_post`
- `list_post_comments`
- `evaluate_promotion_candidate`
- `list_commons_promotion_candidates`
- `review_promotion_candidate`
- `promote_commons_candidate`

Also added helper logic:
- Commons-only guard on posts
- score computation for promotion candidates
- review summary aggregation
- promoted topic generation
- `commons_meta` injection into recent post feed

Important behavior:
- Commons post actions only work on approved Commons posts
- Commons post comments reuse existing Hive post moderation guard behavior
- promotion candidates are scored, not blindly promoted
- promotion requires reviewer approval
- promoted candidate creates a real Hive topic tagged:
  - `commons_promoted`
  - `research_candidate`
  - `agent_commons`

### 3.5 Meet server routes

Changed:
- `/path/to/nulla-hive-mind/apps/meet_and_greet_server.py`

Added new scoped Hive write paths:
- `/v1/hive/commons/endorsements`
- `/v1/hive/commons/comments`
- `/v1/hive/commons/promotion-candidates`
- `/v1/hive/commons/promotion-reviews`
- `/v1/hive/commons/promotions`

Added GET routes:
- `/v1/hive/commons/promotion-candidates`
- `/v1/hive/commons/posts/{post_id}/endorsements`
- `/v1/hive/commons/posts/{post_id}/comments`

Added POST routes:
- `/v1/hive/commons/endorsements`
- `/v1/hive/commons/comments`
- `/v1/hive/commons/promotion-candidates`
- `/v1/hive/commons/promotion-reviews`
- `/v1/hive/commons/promotions`

Also:
- `dispatch_request()` now catches `ValueError` and returns `400` instead of wrongly returning `500`

### 3.6 Policy / quota layer

Changed:
- `/path/to/nulla-hive-mind/core/policy_engine.py`
- `/path/to/nulla-hive-mind/config/default_policy.yaml`
- `/path/to/nulla-hive-mind/core/public_hive_quotas.py`

Added config:
- `brain_hive.commons_review_threshold`
- `brain_hive.commons_archive_threshold`

Added route cost entries for Commons writes.

This keeps Commons under the same quota/signed write discipline as other Hive writes.

### 3.7 Dashboard / watch

Changed:
- `/path/to/nulla-hive-mind/core/brain_hive_dashboard.py`

Added:
- `commons_overview.promotion_candidates` in snapshot
- `Promotion Queue` panel in Commons tab
- Commons post cards now surface:
  - support weight
  - comment count
  - promotion status
  - promotion score
  - review state

This is read visibility only.

There is still no final write UI for Commons interaction in the dashboard.

### 3.8 Control-plane mirror

Changed:
- `/path/to/nulla-hive-mind/core/control_plane_workspace.py`

Added:
- Commons promotion queue loading
- Commons counts in aggregate status:
  - `commons_candidate_count`
  - `commons_review_ready_count`
- writes `workspace/control/queue/commons_promotion_queue.json`

This makes Commons visible to operator tooling.

### 3.9 Useful-output gating for Commons

Changed:
- `/path/to/nulla-hive-mind/storage/useful_output_store.py`

This is a very important safety change.

Behavior now:
- approved/evidence-backed Commons posts are **not** automatically training-eligible
- Commons posts only become training-eligible when their promotion review/archive state is approved enough

In short:
- Commons can feed learning later
- Commons cannot feed learning by default just because it exists

This is correct and intentional.

## 4. What Is Fully Validated Right Now

These validations are current and real.

### 4.1 Syntax / import compile

Command:
```bash
PYTHONPYCACHEPREFIX=/tmp/nulla_pycache python3 -m py_compile \
core/brain_hive_models.py \
storage/migrations.py \
storage/brain_hive_store.py \
core/brain_hive_service.py \
apps/meet_and_greet_server.py \
core/brain_hive_dashboard.py \
core/control_plane_workspace.py \
storage/useful_output_store.py
```

Result:
- passed

Note:
- direct `py_compile` without `PYTHONPYCACHEPREFIX` fails in this sandbox because macOS blocks writes to Python cache under `~/Library/Caches/com.apple.python/...`
- source code itself is not failing compile anymore

### 4.2 Green test batch

Command:
```bash
python3 -m pytest tests/test_brain_hive_watch_server.py tests/test_control_plane_workspace.py tests/test_runtime_task_events.py tests/test_nulla_api_server.py -q
```

Result:
- `31 passed, 1 warning`

Warning:
- LibreSSL / urllib3 warning only

What this proves:
- watch snapshot code is working
- control plane status layer is working
- runtime task events layer is working
- API server layer tested here is working

### 4.3 Focused Commons + useful output test batch

Command:
```bash
python3 -m pytest tests/test_brain_hive_service.py tests/test_useful_output_store.py -q
```

Current result:
- `1 failed, 26 passed`

This is the only currently known red slice after the last fixes.

## 5. The One Known Remaining Failing Test

Current failing test:
- `/path/to/nulla-hive-mind/tests/test_brain_hive_service.py`
- `BrainHiveServiceTests.test_commons_candidate_requires_review_before_promotion`

Exact failure:
- candidate status is `draft`
- test expects `review_required`

So the flow is not crashing anymore.
It is just scoring the fixture below the review threshold.

This is the exact current mismatch.

### 5.1 Why it fails

Current scoring in `/path/to/nulla-hive-mind/core/brain_hive_service.py` likely yields roughly:
- support endorsement: `~1.0`
- cite endorsement: `~0.75`
- one comment: `~0.35`
- two evidence refs: `~1.1`
- downstream use: `0`
- training signal count: `0`
- challenge penalty: `0`

Approx total:
- `~3.2`

Configured threshold:
- `brain_hive.commons_review_threshold = 3.5`

So:
- candidate does not clear threshold
- status stays `draft`
- test expects `review_required`

### 5.2 What this means

This is not a structural failure anymore.

It is a product tuning failure:
- either the threshold is too strict for the test fixture
- or the test fixture is too weak for the threshold

### 5.3 Recommended fix

Do **not** immediately lower the threshold just to make the test green.

Safer next step:
- strengthen the test fixture to represent a real promotion-worthy Commons post

Options:
- add a second support endorsement
- add a second comment
- add downstream-use signal
- add existing useful-output/archive signal

Only lower the threshold if the stronger fixture still feels too hard.

## 6. Current Live Local Control-Plane Truth

Command run:
```bash
python3 - <<'PY'
from core.control_plane_workspace import collect_control_plane_status
import json
status = collect_control_plane_status()
print(json.dumps({
  'useful_outputs': status.get('useful_outputs'),
  'adaptation_loop': (status.get('adaptation') or {}).get('loop_state'),
  'review_pending_count': status.get('review_pending_count'),
  'archive_candidate_count': status.get('archive_candidate_count'),
  'commons_candidate_count': status.get('commons_candidate_count'),
  'commons_review_ready_count': status.get('commons_review_ready_count'),
}, indent=2, sort_keys=True))
PY
```

Current output summary:
- `useful_outputs.total_count = 1`
- `useful_outputs.structured_total = 1`
- `useful_outputs.training_eligible_count = 0`
- `useful_outputs.high_signal_count = 0`
- ineligible reason:
  - `unaccepted_result = 1`
- `review_pending_count = 1`
- `archive_candidate_count = 0`
- `commons_candidate_count = 0`
- `commons_review_ready_count = 0`

This is the hard reality:
- the signal layer is now honest
- the local dataset is still weak

## 7. Current Adaptation Status

Command run:
```bash
python3 -m apps.nulla_cli adaptation-status --json
```

Current meaningful fields:
- `dependency_status.ok = true`
- `device = mps`
- `torch = true`
- `transformers = true`
- `peft = true`
- `loop_state.status = idle`
- `loop_state.last_decision = skipped`
- `loop_state.last_reason = insufficient_structured_examples`
- `loop_state.last_example_count = 12`
- `loop_state.last_quality_score = 0.81`
- policy base model:
  - `base_model_ref = /path/to/nulla-hive-mind/.nulla_local/data/trainable_models/Qwen2.5-0.5B-Instruct`
  - `base_provider_name = nulla-trainable-base`
  - `base_model_name = Qwen2.5-0.5B-Instruct`

Signal summary from same command:
- `structured_total = 1`
- `training_eligible_count = 0`
- `high_signal_count = 0`

Interpretation:
- adaptation rails are installed
- training stack is installed
- the loop is closed structurally
- the loop is skipping because the local durable signal pool is still too thin

## 8. What Is Not Tested Yet

These are gaps.

Not tested in this final state:
- Commons API endpoint behavior end-to-end in meet server
- Commons route quota behavior
- Commons write auth behavior
- Commons promotion queue rendering live in watcher
- VM deployment of the latest Commons changes
- live Linux VM / OpenClaw behavior using latest Commons code
- real iMac Qwen retraining rerun after these signal-capture changes
- full installer rebuild after the latest Commons patch set
- Windows/macOS/Linux end-to-end install after the latest Commons patch set

Important:
- do not claim those are done
- they are not done

## 9. What Is Finished Enough To Count As Real

These are real enough to call actual implemented systems:
- useful-output canonical signal layer
- structured-first adaptation gating
- adaptation blocker visibility
- installer doctor
- Commons schema
- Commons store APIs
- Commons service APIs
- Commons meet-server routes
- Commons read visibility in dashboard
- Commons gating out of training until review/archive approval

## 10. What Is Still Partial

Partial:
- Commons promotion tuning
- Commons route test coverage
- Commons live deployment
- Commons control-plane depth
- Commons direct write UI
- actual proof that Commons now increases training signal quality over time

## 11. What Is Missing Entirely

Still missing:
- final Agent Commons product behavior:
  - endorsements/comments/promotion used in real operator or agent UI
- Commons promotion affecting research priority explicitly in visible way
- final reviewer/archivist integration specific to Commons
- final Commons -> adaptation -> promotion -> retrain feedback loop proof
- release-manifest installer product shell
- one-command fully polished install flow
- trustless DNA settlement
- Dark Null as a finished install module
- a real adapted candidate that honestly beats baseline and gets promoted

## 12. What The Next Agent Should Do First

Do these in this exact order.

### Step 1
Rerun the focused service suite:
```bash
python3 -m pytest tests/test_brain_hive_service.py tests/test_useful_output_store.py -q
```

Expected current result:
- one remaining failure unless already fixed by the next patch

### Step 2
Fix the Commons promotion threshold mismatch.

Likely file:
- `/path/to/nulla-hive-mind/core/brain_hive_service.py`

Likely decision:
- strengthen test fixture instead of weakening threshold

### Step 3
Add endpoint tests for Commons API routes.

Best targets:
- `/path/to/nulla-hive-mind/tests/test_meet_and_greet_service.py`
- or a new dedicated Hive Commons server test file

Routes to test:
- `GET /v1/hive/commons/promotion-candidates`
- `GET /v1/hive/commons/posts/{post_id}/endorsements`
- `GET /v1/hive/commons/posts/{post_id}/comments`
- `POST /v1/hive/commons/endorsements`
- `POST /v1/hive/commons/comments`
- `POST /v1/hive/commons/promotion-candidates`
- `POST /v1/hive/commons/promotion-reviews`
- `POST /v1/hive/commons/promotions`

### Step 4
Add useful-output tests specifically for Commons review gating:
- approved Commons post before promotion review => ineligible
- approved Commons post after approved review => eligible
- promoted Commons candidate => archive approved / eligible
- rejected Commons candidate => ineligible

### Step 5
Only after all that:
- deploy current repo state to Linux VM
- restart API / meet services if needed
- validate watch and trace live

### Step 6
Only after live validation:
- rerun adaptation on the iMac using the improved durable-signal pipeline
- only promote if candidate actually beats baseline

## 13. What The Next Agent Should Not Waste Time On

Do not start with:
- more CSS polish
- more trace cosmetics
- installer copywriting
- chain integrations
- Dark Null
- DNA trustless settlement
- more model plumbing

The immediate blocker is not more plumbing.
The immediate blocker is:
- Commons service stabilization
- then better durable signal capture
- then real adaptation reruns

## 14. File-by-File High-Risk Map

### `/path/to/nulla-hive-mind/core/brain_hive_service.py`
Why risky:
- lots of new logic landed quickly
- scoring + review + promotion all live here
- one known active behavior mismatch remains here

### `/path/to/nulla-hive-mind/storage/brain_hive_store.py`
Why risky:
- many new upsert/list functions
- SQL placeholder ordering mistakes are easy here

### `/path/to/nulla-hive-mind/storage/useful_output_store.py`
Why risky:
- Commons review gating directly changes training eligibility
- wrong logic here could silently pollute adaptation corpus

### `/path/to/nulla-hive-mind/apps/meet_and_greet_server.py`
Why risky:
- new API surface exists
- quota and auth behavior not fully endpoint-tested yet

### `/path/to/nulla-hive-mind/core/brain_hive_dashboard.py`
Why risky:
- dashboard now assumes more Commons data is available
- compatibility with fake/stub services must stay guarded

## 15. Current Bottom Line

We are building:
- a local-first AI runtime
- with OpenClaw shell
- Hive research/task layer
- Liquefy knowledge compression
- durable-signal adaptation
- and Commons as the funnel between raw ideas and real research

What is done:
- most of the durable-signal foundation
- most of the Commons schema/store/service/API structure

What is not done:
- Commons fully green
- Commons live
- adaptation actually improving the model
- one-command install product shell
- internal-testing-ready product state

## 16. Current Readiness Call

Do **not** say:
- “ready for internal testing”

Correct statement today:
- the repo is materially stronger
- signal capture is less fake
- Commons is partially implemented
- one known service behavior mismatch remains
- live deployment and real adaptation rerun are still outstanding

## 17. Exact Current Command History That Matters

Green:
```bash
python3 -m pytest tests/test_brain_hive_watch_server.py tests/test_control_plane_workspace.py tests/test_runtime_task_events.py tests/test_nulla_api_server.py -q
```
Result:
- `31 passed, 1 warning`

Red:
```bash
python3 -m pytest tests/test_brain_hive_service.py tests/test_useful_output_store.py -q
```
Result:
- `1 failed, 26 passed`

Green:
```bash
PYTHONPYCACHEPREFIX=/tmp/nulla_pycache python3 -m py_compile \
core/brain_hive_models.py \
storage/migrations.py \
storage/brain_hive_store.py \
core/brain_hive_service.py \
apps/meet_and_greet_server.py \
core/brain_hive_dashboard.py \
core/control_plane_workspace.py \
storage/useful_output_store.py
```
Result:
- passed

Current adaptation snapshot:
```bash
python3 -m apps.nulla_cli adaptation-status --json
```
Meaningful result:
- loop skips because `insufficient_structured_examples`
- only `1` useful output locally
- `0` training eligible

## 18. Final Hard Pill

The repo is not failing because the idea is wrong.
It is failing because:
- durable signal is still scarce
- Commons is only half promoted into a real workflow
- the adaptation loop is now honest enough to refuse garbage

That is progress.

It is not “ready”.
