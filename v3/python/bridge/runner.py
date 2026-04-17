"""Python ↔ Rust bridge (Phase 1).

This module shells out to the v3 Rust workspace via ``cargo run`` to execute the
``nova_hello`` example and parses its key/value output into a Python dict.

Design notes
------------
Phase 1 intentionally uses a subprocess-based bridge (Option B in
``docs/refactor/v3/02-phase1-rust-sonobe.md``).  A PyO3-based native extension
is deferred until Phase 3, when per-request overhead becomes measurable.

Output contract
---------------
The Rust binary prints a header line ``---- nova_hello summary ----`` followed
by ``key: value`` lines (ASCII).  This module parses those lines into a dict
and coerces known numeric fields.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

# Resolve workspace root relative to this file: <repo>/v3/python/bridge/runner.py
_BRIDGE_DIR = Path(__file__).resolve().parent
_PY_ROOT = _BRIDGE_DIR.parent
_V3_ROOT = _PY_ROOT.parent
_RUST_ROOT = _V3_ROOT / "rust"

_INT_FIELDS = {
    "num_steps",
    "state_len",
    "setup_ms",
    "prove_total_ms",
    "verify_ms",
    "proof_size_bytes",
}
_BOOL_FIELDS = {"verify"}
_LIST_FIELDS = {"per_step_ms"}
_SUMMARY_MARKER = "---- nova_hello summary ----"


class BridgeError(RuntimeError):
    """Raised when the Rust subprocess fails or returns malformed output."""


def _cargo_path() -> str:
    """Locate cargo, falling back to the default Windows rustup install dir."""
    found = shutil.which("cargo")
    if found:
        return found
    user_home = os.environ.get("USERPROFILE") or str(Path.home())
    fallback = Path(user_home) / ".cargo" / "bin" / "cargo.exe"
    if fallback.exists():
        return str(fallback)
    raise BridgeError(
        "cargo not found on PATH and no fallback at %s; install Rust toolchain"
        % fallback
    )


def _parse_summary(stdout: str) -> Dict[str, Any]:
    marker_idx = stdout.find(_SUMMARY_MARKER)
    if marker_idx < 0:
        raise BridgeError(
            "nova_hello output missing summary marker; got:\n" + stdout[-400:]
        )
    tail = stdout[marker_idx + len(_SUMMARY_MARKER) :]
    parsed: Dict[str, Any] = {}
    for line in tail.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if key in _BOOL_FIELDS:
            parsed[key] = raw.lower() == "true"
        elif key in _INT_FIELDS:
            try:
                parsed[key] = int(raw)
            except ValueError as exc:
                raise BridgeError(f"bad int for {key!r}: {raw!r}") from exc
        elif key in _LIST_FIELDS:
            try:
                parsed[key] = list(ast.literal_eval(raw))
            except (ValueError, SyntaxError) as exc:
                raise BridgeError(f"bad list for {key!r}: {raw!r}") from exc
        else:
            parsed[key] = raw
    if "verify" not in parsed:
        raise BridgeError(
            "nova_hello summary missing 'verify' field:\n" + stdout[-400:]
        )
    return parsed


def nova_hello(release: bool = True) -> Dict[str, Any]:
    """Invoke the Rust ``nova_hello`` example and return its parsed summary.

    Parameters
    ----------
    release
        If True (default), run with ``--release`` to match the Phase 1
        acceptance criterion.  Set to False only for faster local iteration.
    """
    cargo = _cargo_path()
    args = [
        cargo,
        "run",
        "--example",
        "nova_hello",
        "-p",
        "v3-folding",
    ]
    if release:
        args.append("--release")
    # Stable environment for deterministic parsing.
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        args,
        cwd=str(_RUST_ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise BridgeError(
            "cargo run failed (rc=%d)\nstdout tail:\n%s\nstderr tail:\n%s"
            % (proc.returncode, proc.stdout[-600:], proc.stderr[-600:])
        )
    return _parse_summary(proc.stdout)


__all__ = ["nova_hello", "BridgeError"]
