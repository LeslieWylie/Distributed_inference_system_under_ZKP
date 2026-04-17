# v3/rust — Collaborative Nova-IVC Workspace

Phase 1 bring-up of the Rust toolchain, the Sonobe folding-scheme dependency,
and a Python ↔ Rust subprocess bridge for V3. No MNIST / circuit / folding
business logic lives here yet — that is Phase 2 / Phase 3.

See `docs/refactor/v3/02-phase1-rust-sonobe.md` for the task spec.

## Layout

```
v3/rust/
├── Cargo.toml                      # workspace + [patch.crates-io] mirror of Sonobe
├── crates/
│   ├── v3-circuit/                 # Phase 2 lands here
│   ├── v3-folding/
│   │   ├── src/lib.rs              # placeholder
│   │   └── examples/nova_hello.rs  # Phase 1 smoke test
│   └── v3-decider/                 # Phase 3/4 land here
└── README.md                       # this file
```

## Pinned upstream

| Component | Version / Rev |
|-----------|---------------|
| Rust toolchain | `stable` (see "Findings" below for exact version) |
| Sonobe (`folding-schemes`) | `privacy-ethereum/sonobe @ 63f2930d363150d4490ce2c4be8e0c25c2e1d92c` (main HEAD on 2026-02-19) |
| arkworks | nominal `^0.5.0`, real resolution via `[patch.crates-io]` git URLs (same as Sonobe's own `Cargo.toml` at that rev) |

The Sonobe dependency is **pinned to a commit**, never `branch = "main"`, per
the Phase 1 acceptance criteria.

## Build / run / test

All commands assume `~/.cargo/bin` is on `$PATH` (`rustup` installer does not
put it there automatically on Windows).

```powershell
# One-time PATH for the current shell:
$env:PATH = "$env:USERPROFILE\.cargo\bin;" + $env:PATH

# From repo root:
cd C:\ZKP\v3\rust

# Debug compile (~10 min first time while downloading deps):
cargo build --workspace

# Workspace unit tests (each crate has a placeholder `dummy_is_a_noop`):
cargo test --workspace

# Release compile for the smoke test:
cargo build --workspace --release

# Phase 1 smoke test — prove+verify a 10-step cubic IVC:
cargo run --release --example nova_hello -p v3-folding
```

## Python bridge smoke test

```powershell
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
$env:PYTHONIOENCODING = "utf-8"
cd C:\ZKP
& $PY v3/python/bridge/test_bridge.py
```

Exits 0 on success and prints the parsed summary (the Rust binary prints
`verify: true` plus timing and proof-size counters; the Python runner parses
that into a dict).  Phase 3 will graduate this bridge to PyO3 when per-call
overhead matters.

## Phase 1 Findings

- **Rust toolchain**: `rustc 1.95.0 (59807616e 2026-04-14)` /
  `cargo 1.95.0 (f2d3ce0bd 2026-03-21)` on `x86_64-pc-windows-msvc`
  (Visual Studio 18 Community MSVC toolset).
- **Sonobe pinned rev**: `63f2930d363150d4490ce2c4be8e0c25c2e1d92c` (2026-02-19,
  the current `main` HEAD).
- **Upstream move**: the repo is now served from
  `github.com/privacy-ethereum/sonobe` (the older
  `privacy-scaling-explorations/sonobe` URL in the Phase 1 doc still redirects).
  Pinned URL in `Cargo.toml` uses the new org.
- **Crate name drift**: the published crate is `folding-schemes`, **not**
  `sonobe`.  Our `workspace.dependencies` block and every `use` statement use
  `folding_schemes::...` accordingly.
- **R1CS module drift**: Sonobe has migrated from `ark_relations::r1cs::...`
  to `ark_relations::gr1cs::...` (`feat: Update arkworks to latest git
  versions with GR1CS migration`, commit `9b7dd34`, Aug 2025).  The
  `nova_hello` example imports `ark_relations::gr1cs::{ConstraintSystemRef,
  SynthesisError}`.  Phase 2's FCircuit code must do the same.
- **arkworks version drift**: Sonobe nominally targets `^0.5.0` on crates.io
  but its workspace pins git versions (e.g. `arkworks-rs/algebra` HEAD,
  `flyingnobita/crypto-primitives@f559264`,
  `flyingnobita/r1cs-std_yelhousni@b4bab0c`).  Our root `Cargo.toml`
  mirrors Sonobe's `[patch.crates-io]` block verbatim so the two workspaces
  see the same trait definitions — otherwise compile fails with
  `trait bound not satisfied` errors from the `Arith` / `FCircuit` traits.
- **`parallel` feature is mandatory**: `folding-schemes`'s `default =
  ["parallel"]` is not cosmetic.  The espresso sum-check prover
  (`folding-schemes/src/utils/espresso/sum_check/prover.rs`) uses
  `cfg_into_iter!(...).fold(init, fn).map(...)` which only type-checks when
  `cfg_into_iter!` expands to `rayon::into_par_iter` (returning a
  `ParallelIterator`).  Disabling defaults (or forgetting `features =
  ["parallel"]` on ark-std/ark-ff/etc.) surfaces as two `E0308`/`E0599`
  errors inside Sonobe.  We therefore keep `folding-schemes` on defaults and
  also activate `parallel` on `ark-std`, `ark-ff` (+ `asm`), `ark-ec`, and
  `ark-r1cs-std`.
- **Unused-patch warnings**: the `[patch.crates-io]` table mirrors Sonobe
  exactly, so several curve patches (`ark-mnt4-298`, `ark-mnt6-298`,
  `ark-pallas`, `ark-vesta`, `ark-circom`) are flagged "was not used in the
  crate graph" because `nova_hello` only touches BN254/Grumpkin.  These are
  safe to leave — removing them would silently drift from Sonobe's pin and
  break Phase 3 / 5 once those curves are pulled in.
- **Nova baseline (`nova_hello`, `--release`)**: see the review report in
  `docs/refactor/v3/REVIEWS.md` for the canonical numbers; the example also
  prints a machine-parseable trailer that the Python bridge captures.
- **Bridge shape**: subprocess only (Option B from the Phase doc).  The Rust
  binary prints a trailer `---- nova_hello summary ----` followed by
  `key: value` lines; `v3/python/bridge/runner.py` parses those.  Phase 3
  should upgrade to PyO3 if per-call latency matters.

## Known follow-ups (deliberately out of scope for Phase 1)

- No `FCircuit` for MNIST — Phase 2.
- No multi-slice fold — Phase 3.
- No Pedersen-hiding integration — Phase 4.
- Decider (`v3-decider`) is an empty crate skeleton — Phase 3.
- PyO3 bridge — deferred; subprocess is enough for Phase 1 acceptance.
