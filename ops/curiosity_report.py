from __future__ import annotations

import json

from storage.curiosity_state import recent_curiosity_runs, recent_curiosity_topics
from storage.migrations import run_migrations


def build_report(limit: int = 10) -> dict[str, object]:
    run_migrations()
    topics = recent_curiosity_topics(limit=limit)
    runs = recent_curiosity_runs(limit=limit)
    return {
        "topic_count": len(topics),
        "run_count": len(runs),
        "topics": topics,
        "runs": runs,
    }


def main() -> int:
    print(json.dumps(build_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
