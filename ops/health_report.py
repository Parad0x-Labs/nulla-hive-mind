from __future__ import annotations

import os
import psutil
from datetime import datetime, timedelta, timezone
from storage.db import get_connection
from core.liquefy_bridge import LIQUEFY_AVAILABLE
from core.scoreboard_engine import get_peer_scoreboard
from core.credit_ledger import LEDGER_MODE
from network.signer import get_local_peer_id

def print_node_health():
    print("======================================")
    print("NULLA NODE HEALTH REPORT")
    print("======================================\n")
    
    # 1. Memory / CPU
    process = psutil.Process(os.getpid())
    mem_mb = process.memory_info().rss / 1024 / 1024
    cpu_pct = process.cpu_percent(interval=0.1)
    print(f"[SYSTEM VITALS]")
    print(f"Memory Footprint : {mem_mb:.2f} MB")
    print(f"CPU Utilization  : {cpu_pct:.1f} %")
    print("")

    conn = get_connection()
    try:
        # 2. Peer count
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        row = conn.execute("SELECT COUNT(DISTINCT peer_id) as cnt FROM peers WHERE last_seen_at >= ?", (cutoff,)).fetchone()
        active_peers = int(row["cnt"]) if row else 0
        
        print(f"[SWARM PULSE]")
        print(f"Active Peers (15m): {active_peers}")
        print("")
        
        # 3. Tasks
        row = conn.execute("SELECT COUNT(*) as cnt FROM local_tasks WHERE outcome = 'pending'").fetchone()
        pending_tasks = int(row["cnt"]) if row else 0
        print(f"[WORKLOAD]")
        print(f"Pending Parent Tasks: {pending_tasks}")
        print("")

        print(f"[SCOREBOARD]")
        local_id = get_local_peer_id()
        sb = get_peer_scoreboard(local_id)
        print(f"Provider Score : {sb.provider:.1f}")
        print(f"Validator Score: {sb.validator:.1f}")
        print(f"Trust Score    : {sb.trust:.2f}")
        print(f"Tier           : {sb.tier}")
        print("")

        # 5. Anti-Abuse
        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        row = conn.execute("SELECT COUNT(*) as cnt FROM anti_abuse_signals WHERE created_at >= ?", (cutoff_24h,)).fetchone()
        abuse_signals = int(row["cnt"]) if row else 0
        print(f"[DEFENSE SYSTEMS]")
        print(f"Anti-Abuse Signals (24h): {abuse_signals}")
        print(f"Liquefy Bridge Active   : {'YES' if LIQUEFY_AVAILABLE else 'NO'}")
        print(f"Credit Ledger Mode      : {LEDGER_MODE.upper()}")
        print("")

    finally:
        conn.close()

if __name__ == "__main__":
    print_node_health()
