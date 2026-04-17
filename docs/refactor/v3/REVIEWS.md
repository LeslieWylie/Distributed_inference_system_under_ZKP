# V3 Review Log

> Supervising agent 每次 review 追加一小节. 时间倒序.
> 格式: YYYY-MM-DD · Phase N · ACCEPT / CHANGES_REQUESTED / REJECT

---

## 2026-04-17 · Phase 1 · SUBMITTED_FOR_REVIEW

**Reviewer**: (supervising agent, pending)
**Submitted by**: phase1-rust-sonobe-agent
**Commit**: (see `git log gitee/master -1` at submission time)

### Artefacts delivered

- `v3/rust/Cargo.toml` — workspace (`resolver = "2"`), three member crates
  (`v3-circuit`, `v3-folding`, `v3-decider`), and a `[patch.crates-io]` block
  that mirrors Sonobe's own workspace at the pinned commit.
- Sonobe pinned rev `63f2930d363150d4490ce2c4be8e0c25c2e1d92c` via
  `rev = "…"` (never `branch = "main"`), upstream URL switched to the new
  org `github.com/privacy-ethereum/sonobe`.
- `v3/rust/crates/v3-folding/examples/nova_hello.rs` — 10-step cubic IVC
  (`z_{i+1} = z_i^3 + z_i + 5`) using `Nova<BN254, Grumpkin, CubicFCircuit,
  KZG, Pedersen, false>`.  Prints a key/value summary trailer.
- `v3/python/bridge/runner.py` + `v3/python/bridge/test_bridge.py` —
  subprocess-based Python ↔ Rust bridge (Option B; PyO3 deferred to Phase 3
  per the Phase doc).
- `v3/rust/README.md` — build/run/test instructions plus a "Phase 1 Findings"
  section documenting every Sonobe / arkworks drift encountered.
- `docs/refactor/v3/STATUS.md` — Phase 1 row flipped to "under review".

### Acceptance Criteria outcome

| Criterion | Evidence |
|-----------|----------|
| `cargo build --workspace --release` zero errors | `build_release.log` tail: `Finished \`release\` profile [optimized] target(s) in 1m 40s` |
| `cargo test --workspace` passes | `test_all.log`: 3 × `test result: ok. 1 passed` (one per crate) + 3 doc-test stubs |
| `cargo run --release --example nova_hello -p v3-folding` prints `verify: true` | `nova_hello.log`: `verify: true`, `prove_total_ms: 2963`, `verify_ms: 49`, `proof_size_bytes: 7129432` |
| Python bridge returns `verified=True` | `v3_bridge.log`: `[test_bridge] OK: verified=True` |
| Sonobe pinned to commit, not branch | `v3/rust/Cargo.toml`: `rev = "63f2930d363150d4490ce2c4be8e0c25c2e1d92c"` |
| README with build commands + Findings | `v3/rust/README.md` |
| STATUS.md Phase 1 updated | owner = `phase1-rust-sonobe-agent`, date = 2026-04-17 |
| v2 regression still certifies | `v2_regression.log`: `normal → client=certified  advisory=certified  prove=13666ms client_verify=389ms` |

### Baseline numbers (release, single run on a laptop-class CPU)

| Metric | Value |
|--------|-------|
| Rust toolchain | `rustc 1.95.0` / `cargo 1.95.0` (x86_64-pc-windows-msvc) |
| `setup_ms` (Nova preprocess) | ~680 ms |
| `prove_total_ms` (10 steps) | ~2950 ms (~295 ms/step average) |
| `per_step_ms` (first → last) | 203, 221, 294, 303, 334, 323, 320, 323, 313, 325 ms |
| `verify_ms` (IVC verifier, pre-decider) | ~48 ms |
| `proof_size_bytes` | 7 129 432 B (≈ 6.8 MiB) |

Phase 3 will compress the 6.8 MiB IVC proof with the Halo2-KZG decider; the
bigness here is expected and is **not** the final verifier-facing size.

### Findings that diverge from the Phase 1 spec pseudocode

1. **Crate name**: upstream crate is `folding-schemes`, not `sonobe`.
2. **Org move**: `privacy-scaling-explorations/sonobe` → `privacy-ethereum/sonobe`.
3. **arkworks**: nominal `^0.5.0` but Sonobe pins many git revisions via
   `[patch.crates-io]` (GR1CS migration, commit `9b7dd34`); our root
   Cargo.toml mirrors that block verbatim.
4. **`parallel` feature is load-bearing**: `folding-schemes` defaults to
   `["parallel"]`; disabling it surfaces as `E0308` + `E0599` inside the
   espresso sum-check prover.
5. **R1CS → GR1CS**: `ark_relations::r1cs::*` is gone; Phase 2 FCircuit
   code must import from `ark_relations::gr1cs::*`.
6. **Unused-patch warnings**: expected — they are curves not touched by
   `nova_hello` but kept to preserve parity with Sonobe's lockfile.

### Declared out-of-scope (Phase 1 red lines honored)

- No MNIST circuit code (Phase 2).
- No multi-slice folding (Phase 3).
- No Pedersen-hiding integration (Phase 4).
- No changes under `v2/`.

### Task-book "four characters" mapping for Phase 1

| 字 | Phase 1 contribution |
|----|----------------------|
| 分布式推理 | none (Phase 3/5 responsibility) |
| ZKP | toolchain that *can* produce a real Nova IVC proof is building + verifying locally |
| 分摊 | none yet |
| 隐私 | none yet |

### Decision
- verdict: pending supervising-agent review (per `99-review-checklist.md`)
- follow-up: Phase 2 can start once ACCEPT is recorded; Phase 2 must read
  the Findings section to avoid repeating the arkworks / GR1CS drift traps.

---

## Template (复制用)

```
## YYYY-MM-DD · Phase N · <verdict>

**Reviewer**: supervising agent
**Submitted by**: <sub-agent name>
**Commit**: <hash>

### Checklist outcome
(paste filled 99-review-checklist 里对应 Phase 的部分)

### Findings
- ...

### Decision
- verdict: ACCEPT / CHANGES_REQUESTED / REJECT
- follow-up actions: ...
```

---
