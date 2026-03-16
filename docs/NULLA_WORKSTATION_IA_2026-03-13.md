# NULLA Workstation IA

## Intent

Brain Hive and Trace Rail should stop acting like two unrelated internal pages.
They should behave like one dark workstation with:

- one shell
- one navigation model
- one object language
- one inspector pattern
- one split between human view, agent view, and raw view

This pass is UI and information architecture only.
It does not add new backend features.

## Primary Modes

Top-level modes:

- `Overview`
- `Hive`
- `Trace`
- `Fabric`

Rules:

- `Overview` is the default human entry.
- `Hive` is the operator and watcher workspace.
- `Trace` is the causality and execution workspace.
- `Fabric` stays hidden unless explicitly enabled.

## Shared Object Model

Everything in the workstation should reduce to the same inspectable object set:

- `Peer`
- `Task`
- `Session`
- `Observation`
- `Artifact`
- `Claim`
- `Conflict`

The workstation should never force humans or agents to guess whether two screens are talking about different object types with different names.

## Shared Shell

The shell owns:

- brand row
- top-level mode navigation
- human / agent / raw view toggle
- persistent dark theme
- optional experimental mode exposure

The shell should exist on:

- Brain Hive home
- Hive topic drill-down
- Trace Rail

## Dual Rendering

The same underlying state should render three ways:

- `Human`
  - concise labels
  - grouped summaries
  - progressive disclosure
- `Agent`
  - denser metadata
  - stable ids
  - machine-friendly labels
- `Raw`
  - full JSON or structured payload dump
  - no hidden conflict or source details

The toggle changes rendering, not the underlying state.

## Screen Map

### 1. Overview

Purpose:

- answer what is active
- answer what is stale
- answer what is blocked
- answer what changed recently

Layout:

- left rail: object counts, source health, freshness, filters
- center: home board with high-signal cards and recent changes
- right inspector: selected object detail

### 2. Hive

Purpose:

- operator and watcher workstation
- active topics, claims, peer posture, freshness, recent changes

Layout:

- left rail: filters and object model counts
- center: Hive board
- right inspector: selected peer/task/claim/observation

### 3. Trace

Purpose:

- causality and execution inspection
- what happened, why, what changed, what failed, when it stopped

Layout:

- left rail: session list plus vertical execution rail
- center: session detail plus selected-step detail plus event feed
- right summary: state, failures, retries, artifacts, raw output

### 4. Fabric

Purpose:

- experimental shared knowledge fabric

Status:

- hidden unless enabled
- not on the default alpha path

## Component Map

Shared shell components:

- `WorkstationHeader`
- `ModeNav`
- `ViewToggle`

Shared structural components:

- `FilterRail`
- `PrimaryBoard`
- `InspectorPanel`
- `StateCard`
- `Badge`
- `Drawer`
- `RawPanel`

Brain Hive components:

- `OverviewHomeBoard`
- `HiveBoard`
- `PeerTable`
- `ClaimStream`
- `RecentChangeList`
- `InspectorPayloadRenderer`

Trace components:

- `SessionRail`
- `ExecutionRail`
- `SelectedStepPanel`
- `EventFeed`
- `SessionSummaryGrid`
- `FailureRetryPanel`
- `ArtifactPanel`

## Proposed Navigation

Top-level:

- `Overview`
- `Hive`
- `Trace`
- `Fabric` hidden unless enabled

Within Brain Hive:

- `Overview`
- `Hive Board`
- `Peers`
- `Claims`
- `Markets`
- `Learnings`
- `Activity`
- `Knowledge`

Within Trace:

- session selection happens in the left rail
- step selection happens in the event feed
- right side always reflects the currently selected session and step

Drill-in rule:

- every important card, row, fold, or event should populate the inspector or selected-step panel
- important state should never dead-end as plain text with nowhere to inspect further

## Visual Hierarchy Rules

- dark by default, no light fallback for the main workstation
- dense, but not flat
- left rail chooses scope
- center column answers the main question
- right side explains the selected object
- high-signal state uses cards first, bulk state moves into drawers, folds, or tabs
- source, freshness, and conflict badges must appear close to the object they qualify
- raw output should be one click away, not mixed into the normal human view

## Dark Theme Token Set

Core tokens:

- `--wk-bg`
- `--wk-bg-alt`
- `--wk-panel`
- `--wk-panel-strong`
- `--wk-panel-soft`
- `--wk-line`
- `--wk-line-strong`
- `--wk-text`
- `--wk-muted`
- `--wk-accent`
- `--wk-accent-strong`
- `--wk-warn`
- `--wk-bad`
- `--wk-good`
- `--wk-chip`
- `--wk-chip-strong`
- `--wk-shadow`
- `--wk-radius`
- `--wk-radius-lg`
- `--wk-font-ui`
- `--wk-font-mono`

Usage rules:

- use accent for live focus and selected state
- use warn for stale, retry, or bounded caution
- use bad for failed or conflicted state
- use good for completed or verified state
- never use color without label text for critical state

## Brain Hive First-Pass Redesign

What changed:

- Brain Hive is no longer one long flat dump
- left rail now handles primary filters and object model overview
- center now emphasizes a real home board
- right side is a persistent inspector
- lower-priority material moves behind tabs and drawers

Default home view should answer:

- what is active
- what is stale
- what is blocked
- what changed recently

First-pass structure:

- left rail
  - primary views
  - object model
  - health
  - sources
  - freshness
- center
  - hero
  - top stats
  - home board
  - Hive board tab
  - specialty tabs for peers, claims, markets, learnings, activity, knowledge
- right inspector
  - object title
  - badges
  - human summary
  - agent summary
  - raw payload

## Trace Rail First-Pass Redesign

What changed:

- Trace Rail now uses the same workstation shell as Brain Hive
- session selection is separated from event inspection
- selected step is a first-class panel
- the right side is session summary, not a loose side list

Trace should answer:

- what happened
- why it happened
- what changed
- what failed
- what the stop reason was
- whether there were retries
- what artifacts exist

First-pass structure:

- left rail
  - session list
  - execution rail
- center
  - session detail
  - selected-step detail
  - event feed
- right summary
  - session summary cards
  - stop / failure / retry cards
  - focus list
  - artifact list
  - retry and query list
  - raw view

## Implementation Patch Plan

Minimum viable implementation order:

1. shared shell helper
2. shared dark token set
3. Brain Hive layout split into rail / board / inspector
4. Trace Rail layout split into session rail / selected step / summary
5. shared human / agent / raw toggle
6. topic drill-down moved under the same shell
7. HTML contract tests updated to defend the new workstation

What this pass does not claim:

- no new backend state model
- no new data pipelines
- no new swarm-fabric integration
- no new operator actions

## Risks

- shared UI language is ahead of full shared backend object normalization
- some labels are still derived from old payload shapes
- Fabric is still experimental and should not drive alpha navigation
- Brain Hive still contains large legacy sections; this pass reorganizes access, not the whole data model
- Trace still uses existing runtime event payloads, so inspector richness is limited by those payloads

## Minimum Viable Outcome

The minimum successful outcome is:

- both screens read as one workstation
- both screens share shell, theme, and mode toggle
- both screens expose the same object language
- Brain Hive feels like an operator workspace
- Trace Rail feels like an execution workspace
- humans and agents can inspect the same state at different densities

