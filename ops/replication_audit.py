from __future__ import annotations

import json

from core.knowledge_replication import desired_replication_count, replication_gap
from storage.knowledge_index import swarm_knowledge_index


def audit_replication() -> list[dict]:
    out: list[dict] = []
    for item in swarm_knowledge_index():
        out.append(
            {
                "shard_id": item["shard_id"],
                "replication_count": item["replication_count"],
                "desired_replication_count": desired_replication_count(),
                "replication_gap": replication_gap(item["shard_id"]),
                "holders": item["holders"],
            }
        )
    return out


def main() -> int:
    print(json.dumps(audit_replication(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
