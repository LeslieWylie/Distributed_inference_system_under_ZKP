"""Phase 1 bridge smoke test.

Run from the repo root:

    & $PY v3/python/bridge/test_bridge.py

Expected: exits 0 and prints a summary dict with ``verified=True``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running this script directly via `python v3/python/bridge/test_bridge.py`
# by injecting the repo root into sys.path before the local import.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from v3.python.bridge.runner import BridgeError, nova_hello  # noqa: E402


def main() -> int:
    try:
        result = nova_hello(release=True)
    except BridgeError as exc:
        print(f"[test_bridge] BridgeError: {exc}", file=sys.stderr)
        return 2

    print("[test_bridge] summary:")
    print(json.dumps(result, indent=2))

    if not result.get("verify"):
        print("[test_bridge] FAIL: verify != true", file=sys.stderr)
        return 1
    for required in ("num_steps", "prove_total_ms", "verify_ms", "proof_size_bytes"):
        if required not in result:
            print(f"[test_bridge] FAIL: missing field {required!r}", file=sys.stderr)
            return 1

    # Surface a friendly alias expected by the dispatch notes.
    result["verified"] = bool(result["verify"])
    assert result["verified"] is True
    print("[test_bridge] OK: verified=True")
    return 0


if __name__ == "__main__":
    sys.exit(main())
