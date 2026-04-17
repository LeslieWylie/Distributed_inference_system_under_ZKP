# V3 Phase Status

> 每个 Phase 的 agent 完成后, 更新自己那一行.
> 新增 agent 开工前, 先看这份确认依赖 ready.

| Phase | Doc | Owner Agent | Status | Last Update | Commit | Notes |
|-------|-----|-------------|--------|-------------|--------|-------|
| 0 | 01-phase0-freeze-v2.md | phase0-freeze-agent | ☑ done | 2026-04-17 | (see gitee/master) | v2-final tag pushed; v3 skeleton created |
| 1 | 02-phase1-rust-sonobe.md | (unassigned) | ☐ not started | - | - | - |
| 2 | 03-phase2-mnist-r1cs.md | (unassigned) | ☐ not started | - | - | - |
| 3 | 04-phase3-nova-ivc.md | (unassigned) | ☐ not started | - | - | - |
| 4 | 05-phase4-privacy-pedersen.md | (unassigned) | ☐ not started | - | - | - |
| 5 | 06-phase5-collaborative-folding.md | (unassigned) | ☐ not started (optional, 等用户确认) | - | - | - |
| 6 | 07-phase6-experiments.md | (unassigned) | ☐ not started | - | - | - |
| 7 | 08-phase7-thesis.md | (unassigned) | ☐ not started (concurrent from Phase 3) | - | - | - |

## Status 图例

- ☐ not started
- ◐ in progress (写 owner / start date)
- ☑ done (写 owner / done date / commit)
- ✖ blocked (写 blocker 原因)
- ↺ under review (等 supervising agent 批)
- ! changes requested (review 打回)

## 依赖图

```
Phase 0 ─┬─> Phase 1 ──> Phase 2 ──> Phase 3 ──┬──> Phase 4 ──┬──> Phase 5 (optional) ──┐
         │                                     │              │                         │
         │                                     │              │                         v
         │                                     │              └──────────> Phase 6 <───┘
         │                                     │                                         │
         │                                     └─────────────────> Phase 7 (concurrent)  │
                                                                                         v
                                                                                       交付
```

Phase 6 可以在 Phase 3 done 时就开始做部分实验 (G1/G3/G4 single-party 部分).
Phase 7 在 Phase 3 done 时就开始写第 1-3 章 (基于当时已有的设计).
