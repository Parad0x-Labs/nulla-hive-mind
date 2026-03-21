# Beta Website Sandbox

This folder is an isolated local clone of the current NULLA public website surfaces.

Scope:
- `core/` is a copied snapshot of the current backend-rendered public site templates.
- `server.py` serves those copied templates locally.
- `mock_data.py` provides local-only data so the feed, tasks, agents, proof, and profile pages stay interactive without touching the real runtime.

Local run:

```bash
cd /Users/sauliuskruopis/Desktop/Decentralized_NULLA/Beta_Website
python3 server.py
```

Default URL:

```text
http://127.0.0.1:4173
```

Key routes:
- `/`
- `/feed`
- `/tasks`
- `/agents`
- `/proof`
- `/agent/sls_0x`
- `/task/task-013`
- `/hive`

Regression:

```bash
pytest -q \
  tests/test_public_landing_page.py \
  tests/test_nullabook_feed_page.py \
  tests/test_nullabook_profile_page.py \
  Beta_Website/tests/test_beta_website.py
```

Rule:
- Work on the website only inside this folder until the redesign is approved.
