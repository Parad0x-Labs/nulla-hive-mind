# Local Acceptance

This is the locked local acceptance bar for NULLA on `qwen2.5:7b`.

Canonical profile:
- [`config/acceptance/local_qwen25_7b_profile.json`](/Users/sauliuskruopis/Desktop/Decentralized_NULLA/config/acceptance/local_qwen25_7b_profile.json)

Canonical command:

```bash
python3 ops/run_local_acceptance.py full \
  --run-root artifacts/acceptance_runs/$(date -u +%Y-%m-%d)-qwen25-7b \
  --profile config/acceptance/local_qwen25_7b_profile.json
```

What `full` does:
1. starts local NULLA on the current checked-out commit
2. runs the online acceptance suite
3. captures a manual BTC spot-check against the locked source
4. restarts with web lookup disabled and runs the offline honesty gate
5. restores normal online mode
6. renders the final report under `artifacts/acceptance_runs/<stamp>/evidence/`

Locked gate:
- cold start must stay under `120s`
- simple-prompt median must stay under `8s`
- file-task median must stay under `15s`
- live-lookup median must stay under `45s`
- chained-task median must stay under `60s`
- consistency must stay at `>= 2/3`
- all P0 checks must pass
- offline honesty must pass
- manual BTC verification must pass

This is a real gate, not a vanity report. If a future run wants to call itself green, it should pass this profile or a stricter one.
