from __future__ import annotations

import json

from core.identity_lifecycle import identity_lifecycle_snapshot


def main() -> int:
    print(json.dumps(identity_lifecycle_snapshot(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
