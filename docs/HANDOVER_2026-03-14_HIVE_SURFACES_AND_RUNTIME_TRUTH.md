# NULLA Handover: Hive Surfaces And Runtime Truth

Date: 2026-03-14 21:12:35 EET
Status: active handover for fresh agents
Supersedes for Hive/runtime checks: `docs/HANDOVER_2026-03-14_ALPHA_RUNTIME_STATE.md`

## Why This Exists

The workspace now has multiple Hive surfaces that are easy to confuse.

That confusion already caused a bad false claim: calling Hive "empty" after checking the wrong lane while the live watcher board clearly showed active tasks.

This handover exists to stop that from happening again.

## 1. Current Local Workspace State

- Repo root: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA`
- Branch: `codex/local-bootstrap`
- HEAD: `d72bcc4bc84b`
- Worktree: dirty, with many modified and untracked files

Local process state at handover time:

- `openclaw-gateway` is up
  - PID `1275`
  - started `Sat Mar 14 10:01:54 2026`
- `ollama serve` is up
  - PID `2824`
  - started `Thu Mar 12 15:52:34 2026`
- local `apps.nulla_api_server` is not currently up
  - `curl -ksS http://127.0.0.1:11435/healthz` failed with connection refused

Do not assume NULLA is live locally until `http://127.0.0.1:11435/healthz` answers.

## 2. The Hive Surface Map

### Surface A: Human watcher board

This is the board the user was looking at:

- `https://161.35.145.74.sslip.io/brain-hive?mode=hive`

What it is:

- the human-facing Brain Hive workstation / watcher board
- watcher-derived
- built from dashboard data, peers, task events, and topic summaries

Code anchors:

- `apps/brain_hive_watch_server.py:211`
- `apps/brain_hive_watch_server.py:332`
- `core/brain_hive_dashboard.py:753`
- `core/hive_activity_tracker.py:419`

What it answers:

- is the Hive board alive
- how many active/researching tasks are visible
- how many peers/agents are visible
- what recent task events happened

When the user says things like:

- `check hive`
- `what's on hive`
- `what online tasks we have`
- `brain hive`
- `hive board`
- `watcher`
- `who is online`

this is the primary surface to check first.

### Surface B: Raw watcher dashboard JSON

This is the machine-readable live watcher feed behind the board:

- upstream meet route: `/v1/hive/dashboard`

In the current local config, the watcher API URL resolves to:

- `https://161.35.145.74:8788/api/dashboard`

The watch edge fetches upstream meet data from routes like:

- `https://104.248.81.71:8766/v1/hive/dashboard`

Code anchors:

- `apps/brain_hive_watch_server.py:213`
- `core/hive_activity_tracker.py:422`
- `core/hive_activity_tracker.py:523`

What it answers:

- the authoritative watcher-derived JSON counts behind the board
- topic list
- stats block
- task event stream

If the user is arguing about what the board shows, this is the truth source.

### Surface C: Public Hive research queue and research packets

This is a different lane:

- `PublicHiveBridge.list_public_research_queue()`
- `PublicHiveBridge.get_public_research_packet(topic_id)`

Code anchors:

- `core/public_hive_bridge.py:141`
- `core/public_hive_bridge.py:170`
- `core/public_hive_bridge.py:967`

Current config resolves it from meet seed URLs. At handover time:

- `topic_target_url = https://104.248.81.71:8766`

What it answers:

- public research queue rows
- research truth packet fields
- artifact refs and resolution status
- research quality fields

What it does **not** answer by itself:

- whether the watcher board is empty
- whether peers are online
- whether the human Brain Hive board is alive

Do not use this lane alone to answer a board/watcher question.

### Surface D: NULLA chat Hive tracker lane

Inside NULLA chat, Hive task listing and followups are meant to come from:

- `core/hive_activity_tracker.py`

Key anchors:

- `core/hive_activity_tracker.py:364`
- `core/hive_activity_tracker.py:458`
- `core/hive_activity_tracker.py:523`

This is the runtime path that should answer normal Hive asks in chat. It is watcher-derived.

## 3. Current Verified Live Hive State

These values were fetched live at handover time through the project code, outside the sandbox, using the configured watcher/bridge settings.

### Watcher dashboard snapshot

Watcher config:

- `enabled = True`
- `watcher_api_url = https://161.35.145.74:8788/api/dashboard`
- `tls_ca_file = config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem`
- `tls_insecure_skip_verify = True`

Watcher dashboard state:

- `topics = 6`
- `task_event_stream = 40`
- `stats.active_agents = 2`
- `stats.presence_agents = 6`
- `stats.visible_agents = 3`
- `stats.raw_visible_agents = 7`
- `stats.duplicate_visible_agents = 4`
- `stats.task_stats.researching_topics = 6`
- `stats.task_stats.solved_topics = 0`
- `stats.total_posts = 621`
- `stats.total_topics = 6`

Visible researching topics:

- `a951bf9d-7b20-4176-b7ed-20a9d646655c`
  - `Agent Commons: Agent commons brainstorm: better human-visible watcher and task-flow UX`
- `967cc865-c10d-4463-a338-916be6fc9d8c`
  - `NULLA Trading Learning Desk`
- `7d33994f-dd40-4a7e-b78a-f8e2d94fb702`
  - `Agent Commons: better human-visible watcher and task-flow UX`
- `ada43859-6c97-45db-9d20-6cb328d9408a`
  - `quick vm proof task from codex doctor check`
- `1c663ffb-8cb6-43a9-a790-c93624f82dc6`
  - `Improving UX-Self learning from chat, building heuristics on human interactions, preserving it in pure compressed formats for best and fastest future re-use`
- `9b56a981-ee3f-43be-b4ac-954a315715f6`
  - `VM live proof for startup and hive pull`

Hard conclusion:

- the Hive board is not empty
- the watcher lane is alive
- the user was right to call out the earlier "empty" claim as wrong

### Public research queue snapshot

Public bridge config:

- `enabled = True`
- `topic_target_url = https://104.248.81.71:8766`
- first three seed URLs:
  - `https://104.248.81.71:8766`
  - `https://157.245.211.185:8766`
  - `https://159.65.136.157:8766`
- `home_region = eu`

Public queue state:

- `research_queue = 6`

Observed queue rows at handover time:

- `NULLA Trading Learning Desk` -> `research_quality_status = artifact_missing`
- `Agent Commons: Agent commons brainstorm: better human-visible watcher and task-flow UX` -> `artifact_missing`
- `Agent Commons: better human-visible watcher and task-flow UX` -> `artifact_missing`
- `VM live proof for startup and hive pull` -> `artifact_missing`
- `quick vm proof task from codex doctor check` -> `artifact_missing`
- `Improving UX-Self learning from chat, building heuristics on human interactions, preserving it in pure compressed formats for best and fastest future re-use` -> `artifact_missing`

Hard conclusion:

- the public queue is also live right now
- but visible research quality is still weak
- current surfaced quality is closer to `busy but under-grounded` than `deep and trustworthy`

## 4. What We Know vs What We Do Not Know

### Known true

- The board URL the user showed is real and alive.
- The correct watcher-derived board currently shows `6` researching topics.
- The local NULLA API is down right now.
- The workspace is dirty.
- The board being alive does **not** mean the research quality is good.
- Recent visible quality signals are still weak:
  - `6` researching topics
  - `0` solved topics
  - many recent events are generic `progress_update` or `result_submitted`
  - current queue rows surface `artifact_missing`

### Known unclear or unstable

- Whether the external watch-edge deployment is fully aligned with the latest local repo code at every moment.
- Whether a restarted local NULLA runtime will exactly match what the user is seeing on the external watcher board.
- Whether current active tasks will turn into strong final outputs without more gating and better background research behavior.

## 5. Fresh-Agent Mandatory Check Order

Any fresh agent touching Hive or NULLA should follow this order.

### Step 1: Check local NULLA runtime health

Run:

```bash
curl -ksS http://127.0.0.1:11435/healthz
```

If it fails, say the local NULLA API is down. Do not bluff.

### Step 2: If the user is talking about the Brain Hive board, check the watcher lane first

Use the local tracker path, not the public queue path:

```bash
python3 - <<'PY'
from core.hive_activity_tracker import HiveActivityTracker, load_hive_activity_tracker_config
tracker = HiveActivityTracker(load_hive_activity_tracker_config())
dashboard = tracker.fetch_dashboard()
print("topics", len(dashboard.get("topics") or []))
print("stats", dashboard.get("stats"))
PY
```

This is the right first check for:

- board alive or empty
- active tasks
- peers online
- recent watcher events

### Step 3: Only then inspect the public research queue if the question is about research packets or artifact truth

Run:

```bash
python3 - <<'PY'
from core.public_hive_bridge import PublicHiveBridge
bridge = PublicHiveBridge()
rows = bridge.list_public_research_queue(limit=12)
print("research_queue", len(rows))
for row in rows[:6]:
    print(row.get("topic_id"), row.get("title"), row.get("research_quality_status"))
PY
```

This is the right lane for:

- research packet truth
- artifact visibility
- research quality status

### Step 4: Never call Hive "empty" unless the watcher dashboard itself is empty

Do **not** call Hive empty unless watcher-derived counts show:

- `topics == 0`
- and `task_stats.researching_topics == 0`
- and `task_stats.open_topics == 0`

### Step 5: Match the user’s noun to the right surface

Use watcher lane first when the user says:

- `hive`
- `hive mind`
- `brain hive`
- `watcher`
- `board`
- `what's on hive`
- `online tasks`
- `who is online`

Use public bridge when the user says:

- `research packet`
- `artifact`
- `public topic`
- `quality status`
- `source domains`

## 6. Open Problems The Next Agent Must Not Ignore

### 6.1 Background Hive work is active, but still not serious enough

The product idea is correct:

- NULLA should work in the background
- the user should be able to keep chatting
- the task trail / watcher board should expose the work

The current problem is not the concept. The problem is the quality:

- too much background churn
- not enough sharp synthesis
- too many active tasks with weak closure
- visible `artifact_missing` quality states

### 6.2 Execution/tooling is partly there, but the UX is still fragmented

Relevant code:

- `core/runtime_execution_tools.py:125`
- `core/runtime_execution_tools.py:168`
- `core/local_operator_actions.py:130`
- `core/local_operator_actions.py:191`
- `core/execution_gate.py:63`
- `core/execution_gate.py:166`

What exists:

- workspace read/write tool surfaces
- `workspace.ensure_directory`
- `sandbox.run_command`
- approval-gated local operator actions
- command safety gate

What still does not feel clean enough:

- one obvious user-facing approval flow for terminal commands
- one obvious receipt/trail for approved command execution
- reliable routing from natural language into those execution paths

Hard truth:

- creating a folder is not a hard technical problem here
- if folder/bootstrap/start-coding behavior still fails, the failure is routing/execution reliability, not the absence of primitives

## 7. Files A Fresh Agent Should Read Before Touching Hive

- `docs/HANDOVER_2026-03-14_HIVE_SURFACES_AND_RUNTIME_TRUTH.md`
- `docs/HANDOVER_2026-03-14_ALPHA_RUNTIME_STATE.md`
- `docs/HANDOVER_PUBLIC_HIVE_2026-03-08.md`
- `docs/ALPHA_SEMANTIC_CONTEXT_SMOKE_PACK_2026-03-14.md`
- `memory/2026-03-14.md`

And the code anchors that define the current truth split:

- `apps/brain_hive_watch_server.py`
- `core/hive_activity_tracker.py`
- `core/public_hive_bridge.py`
- `core/brain_hive_dashboard.py`

## 8. Non-Negotiable Rules For The Next Agent

1. Do not conflate watcher-derived board state with public research-packet state.
2. Do not call Hive empty without checking the watcher dashboard first.
3. Do not claim local NULLA is live without a passing `healthz`.
4. Do not equate "6 active tasks" with "good research quality."
5. Do not paste secrets or tokens into chat. Use config/bootstrap-backed code paths.
6. If a surface is down or ambiguous, say that exactly instead of guessing.
