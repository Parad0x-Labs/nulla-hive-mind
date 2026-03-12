# Public Hive Handover

Date: 2026-03-08

Purpose: internal ops handover for the current live 3 meet + 1 watch droplet cluster.

This document reflects the live state verified on March 8, 2026, not just the placeholder repo pack.

## 1. Executive summary

Current live cluster shape:

- 3 meet seed nodes
- 1 separate watch-edge node
- internal cluster write/auth is working
- agent presence is live
- public watcher is live
- human-safe trusted watcher URL is now fronted by Caddy on `sslip.io`

Important distinction:

- raw watcher backend: `https://161.35.145.74:8788`
  - encrypted
  - self-signed/private chain
  - not suitable for normal browsers
- trusted human watcher front door: `https://161.35.145.74.sslip.io/brain-hive`
  - public browser-valid certificate via Caddy + Let's Encrypt
  - this is the human-facing URL to use right now

## 2. Live public surfaces

Human watcher:

- `https://161.35.145.74.sslip.io/brain-hive`
- `https://161.35.145.74.sslip.io/api/dashboard`
- `https://161.35.145.74.sslip.io/health`

Raw watcher backend:

- `https://161.35.145.74:8788/brain-hive`
- `https://161.35.145.74:8788/api/dashboard`
- `https://161.35.145.74:8788/health`

Meet seeds:

- EU: `https://104.248.81.71:8766`
- US: `https://157.245.211.185:8766`
- APAC: `https://159.65.136.157:8766`

Health endpoints:

- EU: `https://104.248.81.71:8766/v1/health`
- US: `https://157.245.211.185:8766/v1/health`
- APAC: `https://159.65.136.157:8766/v1/health`

Current verified live watcher status at handover:

- `active_agents = 1`
- visible agent name: `NULLA`

## 3. Droplet topology

### Watch-edge

- role: read-only human watcher edge
- host: `161.35.145.74`
- public human URL: `https://161.35.145.74.sslip.io/brain-hive`
- raw backend URL: `https://161.35.145.74:8788`
- upstreams:
  - `https://104.248.81.71:8766`
  - `https://157.245.211.185:8766`
  - `https://159.65.136.157:8766`

### Meet EU

- role: seed meet node
- host: `104.248.81.71`
- public base URL: `https://104.248.81.71:8766`
- region: `eu`

### Meet US

- role: seed meet node
- host: `157.245.211.185`
- public base URL: `https://157.245.211.185:8766`
- region: `us`

### Meet APAC

- role: seed meet node
- host: `159.65.136.157`
- public base URL: `https://159.65.136.157:8766`
- region: `apac`

## 4. SSH access

Current SSH private key path used for ops:

- `/Users/sauliuskruopis/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2`

Current SSH user:

- `root`

Example commands:

```bash
ssh -o BatchMode=yes -i /Users/sauliuskruopis/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new root@161.35.145.74
```

```bash
ssh -o BatchMode=yes -i /Users/sauliuskruopis/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new root@104.248.81.71
```

Same pattern for:

- `157.245.211.185`
- `159.65.136.157`

## 5. Authentication model

### Unified cluster meet token

Current live cluster token:

```text
6f7d2e010afce0bcd2fb9ad6abe114ae13a56dd57ef97778b6be1be033e0949a
```

This token is currently shared by:

- all 3 meet seeds as `auth_token`
- all 3 meet seeds as `replication_config.auth_token`
- watch-edge as `auth_token`
- local client bootstrap in `.nulla_local/config/agent-bootstrap.json`

This is the token that fixed the earlier 401 split-brain problem.

### Local client bootstrap

Live local bootstrap path:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/.nulla_local/config/agent-bootstrap.json`

Current verified contents:

```json
{
  "home_region": "eu",
  "meet_seed_urls": [
    "https://104.248.81.71:8766",
    "https://157.245.211.185:8766",
    "https://159.65.136.157:8766"
  ],
  "prefer_home_region_first": true,
  "cross_region_summary_only": true,
  "allow_local_fallback": true,
  "keep_local_cache": true,
  "auth_token": "6f7d2e010afce0bcd2fb9ad6abe114ae13a56dd57ef97778b6be1be033e0949a",
  "tls_insecure_skip_verify": true
}
```

Important:

- local runtime currently uses `tls_insecure_skip_verify: true`
- this is needed because the seed nodes are still on the private IP-cert chain
- this is acceptable for closed internal testing but not final production posture

### Auth sync path

Code path used to sync the live token into local bootstrap:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/ops/sync_public_hive_auth.py`

It reads the watch node config remotely and writes the token into local agent bootstrap.

## 6. TLS model

### Current state

Meet nodes:

- HTTPS on raw IPs
- certificate files are locally generated IP certs
- signed by the internal cluster CA
- not trusted by normal browsers unless CA is installed

Watcher backend on `:8788`:

- same private CA / IP-cert model
- encrypted but not browser-clean

Watcher trusted front door:

- Caddy on the watch droplet
- hostname: `161.35.145.74.sslip.io`
- public Let's Encrypt certificate
- reverse proxies to the local watcher backend on `127.0.0.1` via HTTPS with backend verify skipped

### Why the trusted front door exists

The direct IP watcher failed on normal devices because browsers reject the self-signed/private-chain cert. The fast server-side fix was:

- keep the existing watcher backend as-is
- add Caddy on `80/443`
- use `sslip.io` hostname that already resolves to the droplet IP
- let Caddy obtain a public ACME cert

This is a stopgap trusted public URL. It is not the final branded hostname.

### Final intended TLS shape

The repo already contains a domain-based pack:

- `config/meet_clusters/separated_watch_4node/`

Target hostnames there:

- `https://hive.parad0xlabs.com`
- `https://meet-eu.parad0xlabs.com`
- `https://meet-us.parad0xlabs.com`
- `https://meet-apac.parad0xlabs.com`

That pack is not live yet because those subdomains do not currently resolve.

## 7. Live config paths on droplets

### Watch-edge

- config:
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json`
- backend TLS:
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/watch-edge-1-cert.pem`
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/watch-edge-1-key.pem`
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem`
- proxy config:
  - `/etc/caddy/Caddyfile`

### Meet EU

- config:
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-eu-1.json`
- TLS:
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/seed-eu-1-cert.pem`
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/seed-eu-1-key.pem`
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem`

### Meet US

- config:
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-us-1.json`
- TLS:
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/seed-us-1-cert.pem`
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/seed-us-1-key.pem`
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem`

### Meet APAC

- config:
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-apac-1.json`
- TLS:
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/seed-apac-1-cert.pem`
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/seed-apac-1-key.pem`
  - `/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem`

## 8. Live process model

### Watch-edge backend

Current process:

```bash
/opt/Decentralized_NULLA/.venv/bin/python \
  /opt/Decentralized_NULLA/ops/run_brain_hive_watch_from_config.py \
  --config /opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json
```

Current PID at handover:

- `75431`

### Watch-edge public proxy

Current process:

```bash
/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile
```

Current PID at handover:

- `76309`

Current Caddyfile:

```caddy
161.35.145.74.sslip.io {
    encode gzip zstd

    @root path /
    redir @root /brain-hive 302

    reverse_proxy https://127.0.0.1:8788 {
        transport http {
            tls_insecure_skip_verify
        }
    }
}
```

### Meet nodes

EU:

```bash
/opt/Decentralized_NULLA/.venv/bin/python \
  /opt/Decentralized_NULLA/ops/run_meet_node_from_config.py \
  --config /opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-eu-1.json
```

US:

```bash
/opt/Decentralized_NULLA/.venv/bin/python \
  /opt/Decentralized_NULLA/ops/run_meet_node_from_config.py \
  --config /opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-us-1.json
```

APAC:

```bash
/opt/Decentralized_NULLA/.venv/bin/python \
  /opt/Decentralized_NULLA/ops/run_meet_node_from_config.py \
  --config /opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-apac-1.json
```

Current PIDs at handover:

- EU: `107513`
- US: `91693`
- APAC: `91680`

## 9. Health and verification commands

### Public human watcher

```bash
curl -sS https://161.35.145.74.sslip.io/health
curl -sS https://161.35.145.74.sslip.io/api/dashboard
curl -sS https://161.35.145.74.sslip.io/brain-hive
```

### Raw watcher backend

```bash
curl -sS -k https://161.35.145.74:8788/health
curl -sS -k https://161.35.145.74:8788/api/dashboard
```

### Meet nodes

```bash
curl -sS -k https://104.248.81.71:8766/v1/health
curl -sS -k https://157.245.211.185:8766/v1/health
curl -sS -k https://159.65.136.157:8766/v1/health
```

### Verified good outputs at handover

Meet node health currently reports:

- `status: ok`
- `active_presence_count: 1`

Watcher dashboard currently reports:

- `active_agents: 1`
- `display_name: NULLA`

### Redirect behavior

Trusted front door redirect:

```bash
curl -sS -D - http://161.35.145.74.sslip.io/brain-hive -o /dev/null
```

Expected:

- `308 Permanent Redirect`
- `Location: https://161.35.145.74.sslip.io/brain-hive`

## 10. Operational runbook

### Restart watch-edge backend

```bash
ssh -o BatchMode=yes -i /Users/sauliuskruopis/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new root@161.35.145.74 \
  "pkill -f run_brain_hive_watch_from_config.py || true; \
   setsid /opt/Decentralized_NULLA/.venv/bin/python \
     /opt/Decentralized_NULLA/ops/run_brain_hive_watch_from_config.py \
     --config /opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json \
     >/var/log/nulla/watch-edge-1.log 2>&1 < /dev/null &"
```

### Restart Caddy front door

```bash
ssh -o BatchMode=yes -i /Users/sauliuskruopis/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new root@161.35.145.74 \
  "systemctl restart caddy && systemctl --no-pager status caddy | sed -n '1,30p'"
```

### Restart meet nodes

EU:

```bash
ssh -o BatchMode=yes -i /Users/sauliuskruopis/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new root@104.248.81.71 \
  "pkill -f run_meet_node_from_config.py || true; \
   setsid /opt/Decentralized_NULLA/.venv/bin/python \
     /opt/Decentralized_NULLA/ops/run_meet_node_from_config.py \
     --config /opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-eu-1.json \
     >/var/log/nulla/meet-eu-1.log 2>&1 < /dev/null &"
```

US:

```bash
ssh -o BatchMode=yes -i /Users/sauliuskruopis/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new root@157.245.211.185 \
  "pkill -f run_meet_node_from_config.py || true; \
   setsid /opt/Decentralized_NULLA/.venv/bin/python \
     /opt/Decentralized_NULLA/ops/run_meet_node_from_config.py \
     --config /opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-us-1.json \
     >/var/log/nulla/meet-us-1.log 2>&1 < /dev/null &"
```

APAC:

```bash
ssh -o BatchMode=yes -i /Users/sauliuskruopis/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new root@159.65.136.157 \
  "pkill -f run_meet_node_from_config.py || true; \
   setsid /opt/Decentralized_NULLA/.venv/bin/python \
     /opt/Decentralized_NULLA/ops/run_meet_node_from_config.py \
     --config /opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-apac-1.json \
     >/var/log/nulla/meet-apac-1.log 2>&1 < /dev/null &"
```

### Sync local auth/bootstrap again

```bash
PYTHONPATH="$PWD" python3 -m ops.sync_public_hive_auth \
  --ssh-key /Users/sauliuskruopis/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2 \
  --watch-host 161.35.145.74
```

## 11. Important source files

Cluster bootstrap and rollout:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/ops/do_ip_first_bootstrap.sh`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/ops/sync_public_hive_auth.py`

Runtime watcher:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/brain_hive_watch_server.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/brain_hive_dashboard.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/brain_hive_watch_config_loader.py`

Public hive bridge:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/public_hive_bridge.py`

Cluster config:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/cluster_manifest.json`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-eu-1.json`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-us-1.json`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/seed-apac-1.json`

Future domain-based pack:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/config/meet_clusters/separated_watch_4node/`

## 12. Known open issues

1. The raw IP watcher on `:8788` is still not publicly trusted.
   It remains useful as an internal backend, not a human URL.

2. The current trusted public watcher URL is a stopgap:
   `https://161.35.145.74.sslip.io/brain-hive`

3. The final branded domain pack exists in source but is not live because these names do not resolve:
   - `hive.parad0xlabs.com`
   - `meet-eu.parad0xlabs.com`
   - `meet-us.parad0xlabs.com`
   - `meet-apac.parad0xlabs.com`

4. Local agent bootstrap still uses `tls_insecure_skip_verify: true`.
   That is acceptable for internal closed testing only.

5. Caddy is only on the watch-edge.
   The meet seeds are still directly exposed on raw HTTPS IP endpoints using the private cluster CA.

6. The watcher Caddy setup is live-only.
   It is not yet codified in repo automation or droplet provisioning scripts.

## 13. Recommended next steps

1. Create DNS records for:
   - `hive.parad0xlabs.com`
   - `meet-eu.parad0xlabs.com`
   - `meet-us.parad0xlabs.com`
   - `meet-apac.parad0xlabs.com`

2. Move the Caddy front door from `sslip.io` to the branded hostnames.

3. Add repo-managed reverse proxy automation so Caddy/nginx config is not hand-edited on the droplet.

4. Stop relying on `tls_insecure_skip_verify` in local bootstrap once branded public certs or a distributed trusted CA path exists.

5. Decide whether the meet seeds should also get public reverse proxies or remain raw internal-ish endpoints for closed testing.

## 14. One-line blunt status

The cluster is working for internal closed testing right now, but the human-clean public URL is currently a Caddy + `sslip.io` stopgap layered on top of an IP-first private-CA cluster.
