from __future__ import annotations

import json

from storage.knowledge_index import active_presence, swarm_knowledge_index


def build_report() -> dict:
    return {
        "active_presence": active_presence(),
        "knowledge_index": swarm_knowledge_index(),
    }


def main() -> int:
    print(json.dumps(build_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
