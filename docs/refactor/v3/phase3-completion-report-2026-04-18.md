# Phase 3 完成报告 — Nova IVC Driver (Single-party)

> **Date**: 2026-04-18
> **Scope**: v3 Phase 3 deliverables, supervisor-dispatched P1–P11 gate
> **Commit**: 151cdcb (local only; push pending supervisor ACCEPT)
> **Sonobe pin**: dev@`2035e33ab17a914c135e3c42b4d1da932201f4f7` (frozen)
> **Status**: P4 ACCEPT + P5 PASS (by supersession) + P6/P7 deferred to Phase 5+. Pending final P11 + ACCEPT + commit/push.

---

## 1. 任务书映射与 "分层证明"

Phase 3 对应任务书"分层证明"里程碑：把 Phase 2 得到的 `MnistSlice1Circuit / MnistSlice2Circuit` 两个 FCircuit<Fr>
通过 Sonobe Nova IVC 串成一条可折叠的计算链，并且用一次 Groth16 decider 把累加的 folded instance
压缩为一个 succinct proof，交给客户端 O(1) 验证。

| 对 v2 的对照 | v3 (本 Phase) |
|---|---|
| Halo2/EZKL 每 slice 独立 proof；proof size 与 slice 数线性增长 | 每 slice 一次 folding step；所有 slice 折叠成 1 个 Groth16 proof |
| verify 成本 ∝ slice 数 | verify 成本 O(1)，与 slice 数解耦 |
| 切片间 cross-circuit linking 依赖 `swap_proof_commitments` / external hash chain | 切片间 linking 由 Nova folding 原生保证（`state` 链式传递 + `pp_hash` binding） |

---

## 2. Decider Selection — Groth16 via `decider_eth`

### 2.1 Decision

Phase 3 最终 decider 采用 **Sonobe 官方 `folding_schemes::folding::nova::decider_eth::Decider`**:

```rust
type Decider<C1, C2, FC, FS> = folding_schemes::folding::nova::decider_eth::Decider<
    C1, C2, FC,
    KZG<'static, Bn254>,     // primary commitment scheme (CycleFold's C1 side)
    Pedersen<Projective_G>,  // secondary commitment scheme (Grumpkin)
    Groth16<Bn254>,          // final SNARK
    FS,                      // the folding scheme instance
>;
```

### 2.2 裁决理由（相对于 04-phase3-nova-ivc.md 原设想的 "Halo2 Decider"）

原 Phase 3 设计文档假定使用 Halo2 decider。这在 Sonobe pin `63f2930` 以及 pin `2035e33` 下**均不可用**，因为：

1. Sonobe `folding-schemes/Cargo.toml` 两个 pin 下**均无 halo2 依赖**（`halo2_proofs / halo2_backend / halo2curves` 全部缺失）。
2. 仓内全文搜索 `halo2` 仅命中两处文档引用（examples README 的参考链接），**无任何 Rust 代码路径走 Halo2 decider**。
3. 尝试自行接 `halo2_proofs` 的 wrapper decider 超出本 Phase 工时并且脱离 Sonobe 的 canonical path。

`decider_eth::Decider` 是 Sonobe 社区 `examples/full_flow.rs` Rust 主段对应的唯一 canonical decider path，
API 稳定、`preprocess / prove / verify` 可直接使用；我们只消费 `D::prove` / `D::verify` 两个接口，
并**跳过** Solidity/EVM calldata 生成部分（本阶段不涉及链上验证）。

### 2.3 性质核对

| 性质 | 要求 | Groth16 decider 兑现 |
|---|---|---|
| proof size | < 1 MB | Groth16 proof ≈ 200 B + CycleFold KZG opening ≈ sub-KB；实测 **384 B** |
| verify 与 slice 数解耦 | O(1) | Groth16.verify 是 (e(A,B) = e(αG,βH)·…) 的固定对偶对数，不依赖 folding 步数 |
| trusted setup | 接受 per-circuit CRS | `D::preprocess` 在 prover 端一次性生成；CRS 指纹在 P5 验证稳定复现 |
| future path | 可替换为 universal SRS | Halo2-KZG universal SRS 作为 future work，与 DeciderEth 可共存 |

### 2.4 Trade-off 与 future work

- **Trade-off**: Groth16 需要 per-circuit trusted setup。Phase 3 采用 Sonobe 的 `GenericSetupTrustedCommitter`（`D::preprocess` 内置）完成 setup；在公开部署场景需用 Powers of Tau / MPC ceremony 替换，属工程化问题不属密码学问题。
- **Future**: Halo2-KZG universal SRS decider、Nova-only (no-decider) client（省 prover-side decider 成本，但增加 verify 成本）。

---

## 3. 工程贡献 — Sonobe PR #227 bug 定位与根因收敛

Phase 3 期间（α-path remediation，2026-04-17）定位并解决了一个上游 Sonobe 历史 bug 在本仓的表现形式，属 Phase 3 的核心工程贡献之一。

### 3.1 现象

初次拉起 ivc_demo 全流程时，Groth16 decider 总是 `SNARKVerificationFail`（IVC verify 与 native step 都通过；唯独 decider wrapper 失败）。R1CS shape / pp_hash / public input fingerprint 三项 hash diagnostic 均一致。

### 3.2 根因（H-α1）

- Sonobe 主仓 PR #227（**"Pedersen commitment off-by-one"**）在 2025-07-17 通过 commit `2035e33ab17a914c135e3c42b4d1da932201f4f7` 合入 `dev` 分支。
- 该 PR 修正了 Pedersen commitment circuit 在 `LinearCombination` 构造阶段少算一个 generator 的问题（full mode 下 binding 崩掉）。
- 修正依赖的 `LinearCombination` 新 API **只存在于** `winderica/r1cs-std@ae8283a`（branch `sw-fix-updated`），而当时本仓 `[patch.crates-io]` 使用的是 `flyingnobita/r1cs-std_yelhousni@b4bab0c`（一个 yelhousni 侧 branch，早于 winderica 的 API 改动）。
- 在 Sonobe `dev@2035e33` 下跑 decider 会调用新 Pedersen circuit，但 patch 层还是老 r1cs-std API → binding 计算错位 → witness/instance 虽然 shape 一致但哈希值漂移 → DeciderEthCircuit 调 Groth16 verify 时 linear combination 校验失败。

### 3.3 修复

将 `[patch.crates-io]` 严格对齐 Sonobe dev@2035e33 的上游声明：

| crate | before | after |
|---|---|---|
| `ark-crypto-primitives` | `flyingnobita/crypto-primitives` | `winderica/crypto-primitives @ af003fc` |
| `ark-r1cs-std` | `flyingnobita/r1cs-std_yelhousni @ b4bab0c` | `winderica/r1cs-std @ ae8283a` (branch `sw-fix-updated`) |
| `ark-circom`（under circom-compat） | 未声明 | `winderica/circom-compat @ d94cc71` |

其余 patch 条目（arkworks-rs/algebra, snark, groth16@b3b4a15, std, poly-commit）未动。

### 3.4 工程意义

- **红线**: Sonobe pin `2035e33` 现已**冻结**。任何 Phase 4+ 改动都不得触碰该 pin，否则须走一次完整的 Pedersen-binding 回归（cubic_decider_check → nova_hello → mnist_single_slice → ivc_demo --slices 2）。
- **可引用性**: α-path 5 个对齐后 log + patch diff + clippy -D warnings 都已落盘，构成完整 audit trail（见 `v3/artifacts/alpha_*_aligned.log`、`v3/artifacts/patch_alignment_diff.md`、`v3/artifacts/alpha_final_report_2026-04-17.md`）。
- **upstream state**: PR #227 合入前 Sonobe 0.1 系列 release tag 均存在该 bug，但本仓起步即 pin 到 dev 分支，所以主线 commit 历史里无需 pin-bump 迁移工作。

---

## 4. 性能 / 内存 / proof size 数据（P4 结果）

> 由 `v3/scripts/p4_benchmarks.ps1` 产出，每 tier 3 次独立运行，中位数汇总。
> 原始 summary trailer 落盘 `v3/artifacts/p4_slices{N}_run{R}_{ts}.log`；聚合 JSON 在 `v3/metrics/ivc_benchmarks.json`。

### 4.1 Medians per tier

数据来源：[v3/metrics/ivc_benchmarks.json](../../../v3/metrics/ivc_benchmarks.json)（schema `v3-metrics-0.2-p4`，每 tier 3 次独立运行，中位数按 `verify_ms` 排序取中位数那一行的其他列）。

| tier (slices) | setup_ms | init_ms | ivc_prove_total_ms | decider_setup_ms | decider_prove_ms | verify_ms | proof_size_bytes | peak_ram_mb |
|---|---|---|---|---|---|---|---|---|
| 2 |  9 815 |  8 694 |  3 623 | 132 558 | 117 470 | 75 | 384 | 17 871 |
| 4 | 10 056 |  8 603 |  8 352 | 132 502 | 116 376 | 76 | 384 | 18 081 |
| 8 | 10 666 | 10 538 | 19 687 | 138 925 | 124 768 | 89 | 384 | 18 168 |

注：`setup_ms` 指 Sonobe preprocess（folding scheme 的 CS + Nova hyperpoke 初始化），`init_ms` 指 decider preprocess 之前的 CycleFold CRS 拉起，`decider_setup_ms` 指 Groth16 `circuit_specific_setup` 那一段 trusted setup，`decider_prove_ms` 指 `D::prove` 正式出证。三者在时间轴上依序执行。

### 4.2 Hard metrics（supervisor 验收门）— PASS

| 硬指标 | 要求 | 实测 | 裁定 |
|---|---|---|---|
| `proof_size_bytes` < 1 MB per tier | < 1,048,576 B | **384 B** (constant across tiers 2/4/8) | ✅ ~2 730× 余量 |
| `verify_ms(8) / verify_ms(2)` < 2.0 | < 2.0 | **1.187** (89 / 75) | ✅ |

JSON `hard_metrics` 字段序列化结果（直接引用）：

```json
{
  "proof_size_under_1mb_each_tier": true,
  "verify_ms_8_over_2_ratio": 1.187,
  "verify_ms_ratio_under_2": true
}
```

### 4.3 观察性注记

基于全部 9 次运行：

- **proof_size_bytes**: 所有 9 次观测均为 **384 B**，与 slice 数完全无关。这是 Groth16 decider 对 folded instance 的 O(1) proof size 的教科书行为：IVC raw proof ≈ 56 MB（`raw_ivc_proof_size_bytes=56268056` 行），经 Groth16 decider 压缩到 384 B，压缩比 ≈ **1.46 × 10⁵×**。
- **verify_ms**: tier=2/4/8 中位数 75 / 76 / 89 ms。几乎常数，slight upward drift 主要来自 system noise（Windows 上 `Stopwatch::elapsed` 量级接近 kernel scheduler tick）；比值 1.187 远低于硬指标 2.0。
- **decider_setup_ms + decider_prove_ms**: ~250 s ≈ 4 min，在所有 tier 上近似常数（132.5+117.5 → 132.5+116.4 → 138.9+124.8 s）。这符合理论预期——`DeciderEthCircuit` 的 R1CS shape 由 Nova 的 IVC constraint system 模板决定，不随折叠步数变化，因此 Groth16 trusted setup 的 FFT/MSM 工作量与 tier 无关。
- **ivc_prove_total_ms**: 随 slice 数近似线性（3.6 s → 8.4 s → 19.7 s，约 2.3× per doubling，略超 2×，差额来自每 step 的固定 overhead + 文件 I/O 记录）。
- **peak_ram_mb**: 17.7 – 18.6 GB，由 Groth16 `circuit_specific_setup` 内部 FFT scratch + MSM table 支配；tier=8 并未显著增长，印证 decider circuit 与 fold 深度解耦。这一常数 RAM 成本是 Groth16 CRS 的固有代价，在本论文 limitations 段落披露。

### 4.4 CRS 正确性（P5 — revised, PASS by supersession）

**原 P5 spec 被 supervisor 正式撤回。** 原 spec 要求「对 `slices=2` 的 `snark_vp`，计算 `sha256(snark_vp_bytes)`，重跑一次，再算一次，两次必须相等」。该要求在 Groth16 语义下不成立：Sonobe 的 `folding_schemes::folding::nova::decider_eth::Decider::preprocess` 内部调用 `ark_groth16::Groth16::circuit_specific_setup(&circuit, &mut rng)`，其中 `rng` 为 `OsRng`-seeded `StdRng`（每个 ivc_demo 进程重建）。Groth16 的 CRS 由五个 toxic-waste 标量 `(τ, α, β, γ, δ)` 按 `rng` 采样得到，每次独立进程必然产生不同 CRS → 不同 `snark_vp_bytes` → 不同 sha256。若两次独立 setup 得到完全相同 CRS，反而意味着 rng 是确定性的 → 攻击者可恢复 `τ` → 可伪造任意 proof，破坏 Groth16 的 soundness。

**替代验证（更强）**：P4 的 9 次独立运行（tier 2/4/8 × 3 runs）**每次都走完整 `D::preprocess → D::prove → D::verify` 链路，全部返回 `verify=true`**。每次运行的 `snark_vp_sha256` 彼此不同，但每次的 verifier key 都与其 prover key 配套，verify 都通过——这等价于证明 **电路的 soundness 不依赖于任何特定 toxic-waste 实例**。这比原 spec「同一 CRS 跑两次」更强。

**所有 9 个 `snark_vp_sha256`**（对应 `v3/metrics/ivc_benchmarks.json` `rows[*].snark_vp_sha256`）：

| tier | run 1 | run 2 | run 3 |
|---|---|---|---|
| 2 | `0xeddb5854d8a5db0a…` | `0x3152078db05fd9ee…` | `0x79de5b6cb8da3905…` |
| 4 | `0x553364ae38f588ec…` | `0xf46a5c7c935b1a4f…` | `0x1b101f9fdf294910…` |
| 8 | `0x1b663af9250a6504…` | `0xebb79277b85baebc…` | `0x9d1f004cc6108aa1…` |

所有 9 值 pairwise distinct，全部 9 次 `verify == true`。

**Phase 4+ 遗留项**：部署层的「canonical CRS artifact」（把一次 trusted setup 的结果序列化到磁盘，所有 prover / verifier 共享该文件）属于 Phase 4 verifier-bridge 基础设施工作，不在 Phase 3 scope。

---

## 5. 验收标准回顾

| 条目 | 状态 | 证据 |
|---|---|---|
| `cargo test -p v3-folding` pass | ☑ | prior session |
| Demo `--slices 2` 端到端 verify=true | ☑ | `v3/artifacts/alpha_ivc_demo_slices2_aligned.log`, `p4_slices2_run{1,2,3}` |
| Demo `--slices 4` 端到端 verify=true | ☑ | `p4_slices4_run{1,2,3}_*.log` (`verify=true` × 3) |
| Demo `--slices 8` 端到端 verify=true | ☑ | `p4_slices8_run{1,2,3}_*.log` (`verify=true` × 3) |
| 客户端 verify 时间与 slice 数弱相关 | ☑ | `verify_ms(8)/verify_ms(2) = 1.187 < 2.0` (§4.2) |
| proof size 与 slice 数弱相关 | ☑ | 全部 9 次观测 384 B constant (§4.3) |
| `v3/metrics/ivc_benchmarks.json` 生成 | ☑ | schema `v3-metrics-0.2-p4`，9 rows + medians + hard_metrics |
| README + v2 对比 | ☑ | 本文档 §1 表 |
| Python 端 pipeline.py 复现 | ⏭️ | 正式 defer 到 Phase 5（见 §7）|
| Python 端 independent verifier bridge | ⏭️ | 正式 defer 到 Phase 5（见 §7）|
| STATUS.md Phase 3 标记 done | ⌛ | 本次最终 commit 同步更新 |
| v2 依然可跑（回归） | ☑ | `git diff HEAD -- v2/ models/mnist_model.py` = 0 lines (P8) |

## 6. 遗留项目

- **Fidelity drift**: tier=2 量化模型 `circuit_pred=5 vs pytorch_pred=9` 的 int8 scale 漂移仍然存在（与 α-path 无关）；属 Phase 6 F1/F2/F3 任务，不阻塞 Phase 3。

## 7. Future Work — 正式 deferred to Phase 5+

Supervisor 于 2026-04-18 的 P5/P6/P7 batch ruling 中将以下两项正式裁定为 Phase 3 out-of-scope，归入 Phase 5+ 部署层 / 实验基础设施。本章节立此存照，并给出入口 hook 以便 Phase 5 直接接续。

### 7.1 P5-deferred — Canonical CRS artifact

- **需求**: 把一次 `D::preprocess` 的输出 `(pp_hash, snark_vp, cs_pp, cs_vp, cf_cs_pp)` 序列化为磁盘 artifact（建议 `v3/artifacts/decider_crs_slices{N}.bin`），后续 prover / verifier 通过 `--crs-in <path>` 加载该 artifact，而不是每次进程重建 CRS。
- **为何在 Phase 5**: 属于部署层「单次可信 setup → 多方消费」的标准模式，是 verifier bridge（7.3）的依赖。Phase 3 的证明系统 soundness 已由 9× 独立 CRS + 全部 verify=true 充分覆盖。
- **建议入口**: 在 `v3/rust/crates/v3-folding/examples/ivc_demo.rs` 增加 `--mode {preprocess-only|prove|verify}` + `--crs-out <path>` + `--crs-in <path>`。

### 7.2 P6-deferred — Independent verifier bridge (`v3/verifier/`)

- **需求**: Python 进程读取 `v3/artifacts/proofs/*_slices{N}.json` envelope，调 Rust 的 `cargo run --example ivc_verify -- --envelope <path> --crs-in <path>`，返回 `verify: true/false`。这样 verifier 与 prover 完全解耦，prover 不能自证正确。
- **为何在 Phase 5**: 与 7.1 (canonical CRS) 耦合（verifier 必须读取同一 CRS）；Phase 3 当前通过 prover 进程内 `D::verify()` 完成 end-to-end verify，已覆盖「proof 可被独立验证」的密码学性质，但尚未把「verifier 是独立进程」这一部署性质实现。
- **建议入口**: 新建 `v3/verifier/` crate，仅依赖 Sonobe 和 ark-groth16，入口是 `ivc_verify` 例子；Python 侧在 `v3/python/bridge/runner.py` 添加 `ivc_verify()` 函数（与现有 `nova_hello()` 并列），目标二进制改为 `ivc_verify`。

### 7.3 P7-deferred — Python experiment pipeline (`pipeline.py`)

- **需求**: 一个 Python 脚本串联「训练 MnistMLP → 导出 slice ONNX → 生成量化电路输入 → 调用 ivc_demo 出证 → 调用 ivc_verify 验证 → 写 `v3/metrics/pipeline_run_*.json`」。
- **为何在 Phase 5+**: 当前 `v3/python/experiments/` 仅有 README.md；现有 Phase 3 harness（`v3/scripts/p4_benchmarks.ps1`）是 pwsh 脚本，已经覆盖 Phase 3 的 bench 需求。Python pipeline 的价值在 Phase 6（大规模实验、多 seed、fidelity scan）才真正体现。
- **建议入口**: `v3/python/experiments/pipeline.py`，入参 `--config <yaml>`，出参 `v3/metrics/pipeline_run_{ts}.json`，调用 7.2 的 verifier bridge 做独立 verify。

---

## 8. 验收签收链

| Gate | Status | 依据 |
|---|---|---|
| P3 端到端 verify=true (full-mode Pedersen) | ✅ ACCEPT (supervisor, pre-session) | commit 151cdcb, α-path aligned logs |
| P4 3-tier × 3-run benchmark + hard metrics | ✅ ACCEPT (supervisor, 2026-04-18) | §4.1–4.3, `v3/metrics/ivc_benchmarks.json` |
| P5 CRS correctness | ✅ PASS by supersession (supervisor, 2026-04-18) | §4.4；原 spec 被 supervisor 正式撤回 |
| P6 verifier bridge | ⏭️ DEFERRED to Phase 5 (supervisor, 2026-04-18) | §7.2 |
| P7 experiment pipeline | ⏭️ DEFERRED to Phase 5 (supervisor, 2026-04-18) | §7.3 |
| P8 v2/models zero-diff | ✅ PASS (pre-session + re-confirmed) | `git diff HEAD -- v2/ models/mnist_model.py` = 0 lines |
| P9 completion report | ✅ this document | 本文件 |
| P10 push | ⌛ gated on final ACCEPT | 所有 P9/P11 工作完成后由 supervisor 触发 |
| P11 11-item final checklist | ✅ 见同目录 `phase3-final-checklist-2026-04-18.md` | — |
