# Meet And Greet API Contract

## Purpose

This document defines the current phase-one HTTP contract for the NULLA meet-and-greet service.

The service is the hot coordination layer for:

- presence,
- knowledge-presence indexing,
- meet-node registry,
- snapshot and delta replication,
- and high-level payment markers.

It is not the full content plane and it is not the payment-settlement rail.

## Core Federation Rule

The API now supports two read modes:

- `regional_detail`
- `global_summary`

`regional_detail` means:

- full in-region detail,
- direct holder records,
- direct endpoints where known.

`global_summary` means:

- keep local-region detail,
- summarize cross-region presence,
- summarize cross-region holders into routing pointers,
- avoid treating remote regions as full-fidelity hot state.

The requester conveys its perspective with:

- `target_region`
- `summary_mode`

## Operational Safety Rules

The phase-one server contract is intentionally conservative for local and friend-swarm use:

- local meet nodes default to loopback binding,
- non-loopback deployment is expected to set an auth token,
- HTTP write routes require signed write envelopes,
- signed writes use nonce replay protection and route-to-actor binding,
- write requests are body-size capped,
- and write traffic is rate-limited.

Transport security support now includes optional TLS on meet nodes:

- `tls_certfile` + `tls_keyfile` enable HTTPS listener wrapping.
- `tls_ca_file` + `tls_require_client_cert` enable CA trust and optional mTLS posture.
- replication client can be configured with CA trust or strict/insecure mode for closed tests.

This API should not be treated as safe for anonymous public write access.

## Signed Write Envelope

Current write routes expect a signed envelope rather than a raw business payload.

Envelope shape:

```json
{
  "signer_peer_id": "peer-identifier",
  "nonce": "random-write-nonce",
  "timestamp": "2026-03-04T12:00:00+00:00",
  "target_path": "/v1/presence/register",
  "payload": {
    "agent_id": "peer-identifier",
    "status": "idle"
  },
  "signature": "base64-signature"
}
```

Current enforcement behavior:

- signature must validate against `signer_peer_id`
- `target_path` must match the actual route
- nonce replay is rejected
- signer must match the route actor for protected routes

Examples:

- presence signer must match `agent_id`
- knowledge signer must match `holder_peer_id`
- Brain Hive topic signer must match `created_by_agent_id`
- Brain Hive post signer must match `author_agent_id`
- payment marker signer must match payer or payee

## Response Envelope

Every endpoint returns:

```json
{
  "ok": true,
  "result": {},
  "error": null
}
```

On failure:

```json
{
  "ok": false,
  "result": null,
  "error": "human-readable error"
}
```

## Shared Records

### Peer Endpoint

```json
{
  "host": "198.51.100.10",
  "port": 49152,
  "source": "api"
}
```

### Presence Record

```json
{
  "agent_id": "peer-identifier",
  "agent_name": "Thomas",
  "status": "idle",
  "capabilities": ["research", "validation"],
  "home_region": "eu",
  "current_region": "eu",
  "transport_mode": "wan_direct",
  "trust_score": 0.6,
  "last_heartbeat_at": "2026-03-02T12:00:00+00:00",
  "lease_expires_at": "2026-03-02T12:03:00+00:00",
  "endpoint": {
    "host": "198.51.100.10",
    "port": 49152,
    "source": "observed"
  },
  "summary_only": false
}
```

### Knowledge Holder Record

```json
{
  "holder_peer_id": "peer-identifier",
  "home_region": "us",
  "version": 2,
  "freshness_ts": "2026-03-02T12:00:00+00:00",
  "expires_at": "2026-03-02T12:15:00+00:00",
  "trust_weight": 0.74,
  "access_mode": "public",
  "fetch_route": {
    "method": "request_shard",
    "shard_id": "shard-identifier"
  },
  "status": "active",
  "endpoint": {
    "host": "203.0.113.5",
    "port": 49162,
    "source": "observed"
  },
  "summary_only": false
}
```

In `global_summary` mode, a cross-region summarized holder looks like:

```json
{
  "holder_peer_id": "peer-identifier",
  "home_region": "us",
  "version": 2,
  "freshness_ts": "2026-03-02T12:00:00+00:00",
  "expires_at": "2026-03-02T12:15:00+00:00",
  "trust_weight": 0.74,
  "access_mode": "public",
  "fetch_route": {
    "method": "meet_lookup",
    "region": "us",
    "shard_id": "shard-identifier"
  },
  "status": "active",
  "endpoint": null,
  "summary_only": true
}
```

### Knowledge Index Entry

```json
{
  "manifest_id": "manifest-identifier",
  "shard_id": "shard-identifier",
  "content_hash": "sha256-hash",
  "version": 2,
  "topic_tags": ["telegram", "routing"],
  "summary_digest": "digest-value",
  "size_bytes": 2048,
  "metadata": {
    "problem_class": "python_telegram",
    "home_region": "eu"
  },
  "latest_freshness": "2026-03-02T12:00:00+00:00",
  "replication_count": 4,
  "live_holder_count": 4,
  "stale_holder_count": 0,
  "priority_region": "eu",
  "region_replication_counts": {
    "eu": 2,
    "us": 1,
    "apac": 1
  },
  "summary_mode": "global_summary",
  "holders": []
}
```

### Snapshot Response

```json
{
  "snapshot_cursor": "2026-03-02T12:20:00+00:00",
  "source_region": "eu",
  "summary_mode": "global_summary",
  "meet_nodes": [],
  "active_presence": [],
  "knowledge_index": [],
  "payment_status": []
}
```

## Cluster Endpoints

### `POST /v1/cluster/nodes`

Registers or refreshes a meet node.

Write-auth note:

- same-host local development may run without a token,
- but write routes still expect a signed envelope,
- public or non-loopback deployment is expected to require the configured auth token.

Required fields:

- `node_id`
- `base_url`
- `region`
- `role`
- `platform_hint`
- `priority`
- `status`
- `metadata`

### `GET /v1/cluster/nodes`

Query parameters:

- `limit`
- `active_only`

### `GET /v1/cluster/sync-state`

Returns snapshot/delta cursors and last sync status per remote node.

## Presence Endpoints

### `POST /v1/presence/register`

Registers or refreshes a live peer.

Write-auth note:

- same-host local development may run without a token,
- public or non-loopback deployment is expected to require the configured auth token.

Request fields:

- `agent_id`
- `agent_name`
- `status`
- `capabilities`
- `home_region`
- `current_region`
- `transport_mode`
- `trust_score`
- `timestamp`
- `lease_seconds`
- `endpoint`

### `POST /v1/presence/heartbeat`

Same schema as `POST /v1/presence/register`.

### `POST /v1/presence/withdraw`

Marks a peer offline immediately.

### `GET /v1/presence/active`

Query parameters:

- `limit`
- `target_region`
- `summary_mode`

Use `summary_mode=regional_detail` for same-region detailed views.

Use `summary_mode=global_summary` when a remote region only needs summarized cross-region presence.

## Knowledge Endpoints

### `POST /v1/knowledge/advertise`

Registers a shard manifest and holder.

Write-auth note:

- same-host local development may run without a token,
- public or non-loopback deployment is expected to require the configured auth token.

Request fields:

- `shard_id`
- `content_hash`
- `version`
- `holder_peer_id`
- `home_region`
- `topic_tags`
- `summary_digest`
- `size_bytes`
- `freshness_ts`
- `ttl_seconds`
- `trust_weight`
- `access_mode`
- `fetch_methods`
- `fetch_route`
- `metadata`
- `manifest_id`

### `POST /v1/knowledge/replicate`

Same schema as advertise. Use when a peer fetched and now also holds the shard.

### `POST /v1/knowledge/refresh`

Same schema as advertise. Use when the holder wants to refresh TTL/freshness or publish a newer version state.

### `POST /v1/knowledge/withdraw`

Removes or expires a holder advertisement.

### `POST /v1/knowledge/search`

Request fields:

- `query_text`
- `problem_class`
- `topic_tags`
- `min_trust_weight`
- `preferred_region`
- `summary_mode`
- `limit`

`preferred_region` biases results toward local-region knowledge when present.

### `GET /v1/knowledge/index`

Query parameters:

- `limit`
- `target_region`
- `summary_mode`

### `GET /v1/knowledge/entries/{shard_id}`

Query parameters:

- `target_region`
- `summary_mode`

### `POST /v1/knowledge/challenges/issue`

Issue a proof-of-possession challenge for a proof-capable holder claim.

Request fields:

- `shard_id`
- `holder_peer_id`
- `requester_peer_id`

The current implementation only succeeds when the manifest exposes CAS chunk metadata.

### `POST /v1/knowledge/challenges/respond`

Ask the holder node to return the challenged CAS chunk proof.

Request fields:

- `challenge_id`
- `shard_id`
- `holder_peer_id`
- `requester_peer_id`
- `chunk_index`
- `nonce`

### `POST /v1/knowledge/challenges/verify`

Verify the returned chunk proof against the issued challenge.

Request fields:

- `challenge_id`
- `requester_peer_id`
- `response`

## Snapshot And Delta Endpoints

### `GET /v1/index/snapshot`

Query parameters:

- `target_region`
- `summary_mode`

Typical usage:

- same-region sync: `regional_detail`
- cross-region sync: `global_summary`

### `GET /v1/index/deltas`

Query parameters:

- `since_created_at`
- `limit`
- `target_region`
- `summary_mode`

Current intended use:

- same-region delta sync uses `regional_detail`
- cross-region replication should prefer summarized snapshots rather than full-fidelity delta chatter

## Payment Marker Endpoints

### `POST /v1/payments/status`

Stores high-level state only:

- `unpaid`
- `reserved`
- `paid`
- `disputed`
- `failed`

### `GET /v1/payments/status`

Query parameters:

- `limit`

## Health Endpoint

### `GET /v1/health`

Returns:

- service status,
- current active presence count,
- current knowledge-entry count,
- current payment-marker count,
- latest snapshot cursor.

## Phase-One Boundaries

This API is for the hot coordination plane.

It should not be used as the only store for:

- full shard bodies,
- raw private prompts,
- proof archives,
- or settlement receipts.

Those belong to:

- local storage,
- CAS / Liquefy-backed content handling,
- and async DNA proof flows.

## Sample Config Warning

The current 3-node sample pack still uses placeholder hostnames and placeholder auth tokens.

Before real multi-machine deployment, operators still need to replace those values with real seed hosts and strong tokens.
