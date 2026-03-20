# v2 重构说明：Deferred Certification Architecture

> **From selective-verification prototype to end-to-end verifiable distributed inference system**  
> 重构日期：2026-03-20  
> 重构范围：新增 `v2/` 目录，保留旧系统作为 baseline

---

## 1. 重构动机

旧系统（`distributed/` + `scripts/`）存在以下结构性缺口：

1. **部分切片永不出 proof**（light 节点），导致全链路不可验证
2. **Worker 自报 `verified=True`**，Master 不做独立验证
3. **proof 语义与运行时数据流未绑定** — proof boundary ≠ dataflow boundary
4. **hashed 模式下 output_visibility="public"** — 输出不在承诺范围内
5. **实验脚本走简化管线**（L1+L3），不走 Master 完整逻辑

核心判断：**需要协议级重构，而非接口修补。**

---

## 2. 新架构：Deferred Certification

### 核心思想
所有切片最终都生成 proof；证明生成从关键路径剥离；跨切片一致性通过公开实例 linking 和独立验证者建立。

### 请求状态机
```
SUBMITTED → EXECUTING → EXECUTED_UNCERTIFIED → PROVING → VERIFYING → CERTIFIED / INVALID
```

### 四个平面
| 平面 | 职责 | 对应模块 |
|---|---|---|
| Execution | 在线推理，不宣称 correctness | `v2/execution/` |
| Proving | 后台生成 proof | `v2/prover/` |
| Verification | 独立验证 + 签发证书 | `v2/verifier/` |
| Control | 状态管理、调度、日志 | `v2/common/logging.py` |

---

## 3. 新系统目录结构

```
v2/
├── common/
│   ├── types.py           # RequestStatus, SliceArtifact, ProofJob, Certificate
│   ├── commitments.py     # SHA-256 域分离承诺 (req_id‖slice_id‖model_digest‖tensor)
│   └── logging.py         # JSON Lines 结构化审计日志
│
├── compile/
│   └── build_circuits.py  # ONNX 切片导出 + EZKL 编译 + slice_registry.json
│
├── prover/
│   ├── ezkl_adapter.py    # prove_slice (仅 proving，不含 verify)
│   ├── parallel.py        # subprocess.Popen 子进程并行 proving
│   └── prove_worker.py    # 子进程 prover 入口
│
├── verifier/
│   ├── verify_single.py   # 独立单片 proof 验证
│   └── verify_chain.py    # 全链路 linking + 终端绑定 + model_digest 校验 + 证书签发
│
├── execution/
│   ├── pipeline.py        # Phase A: 同步全链路 pipeline
│   └── deferred_pipeline.py # Phase B/C: 执行-证明解耦 pipeline
│
├── experiments/
│   ├── e2e_certified.py       # G2 协议正确性 (Phase A)
│   ├── deferred_certified.py  # G2+G3 协议正确性 + 延迟分解 (Phase B/C)
│   ├── fidelity.py            # F1 切片 + F2 量化 + F3 认证 fidelity
│   └── scalability.py         # G4 多切片可扩展性 (2/4/8)
│
├── docs/
│   ├── protocol.md        # 正式协议文档
│   └── threat_model.md    # 威胁模型
│
└── metrics/               # 实验结果 JSON
    ├── e2e_certified_results.json
    ├── deferred_certified_results.json
    ├── fidelity_results.json
    └── scalability_results.json
```

---

## 4. 关键设计决策

### 4.1 Visibility: `public` 而非 `hashed`
EZKL 独立校准使不同电路对同一张量产生不同 Poseidon 哈希（独立量化 scale），导致 hashed 模式下跨切片 linking 天然失败。

`public` 模式下 proof soundness 仍然密码学绑定 `rescaled_inputs/outputs` 作为公开实例 — 值不可伪造。

### 4.2 Linking 机制
- **相邻链路**: `|rescaled_outputs[i] - rescaled_inputs[i+1]| < ε`（ε = 0.01）
- **终端绑定**: `|rescaled_outputs[last] - provisional_output| < ε`
- $\epsilon$ 覆盖量化精度（~$1/2^{13}$），远小于任何攻击幅度

### 4.3 承诺构造
```python
Commit(req_id, slice_id, model_digest, tensor) = SHA-256(JSON({...}))
```
域分离防止：跨请求 replay / 跨切片拼接 / 模型版本混淆

### 4.4 Prover/Verifier 彻底分离
- `prove_slice()` 仅生成 proof，不调用 `verify()`
- `verify_proof()` 使用 registry 的 vk/settings/srs 独立验证
- `verify_chain()` 额外检查 model_digest 一致性

### 4.5 并行 Proving
- Phase B: ThreadPoolExecutor（受 GIL 限制）
- Phase C: subprocess.Popen 子进程（真正 CPU 并行）
- 4 worker 时 wall-clock 加速 ~1.42×

---

## 5. 实验结果汇总

### G2 协议正确性 — 6/6 PASS
| 攻击 | 状态 | Provisional | Certification |
|---|:---:|---:|---:|
| normal | **certified** | 37ms | 4680ms |
| tamper_last | **invalid** | 66ms | 5155ms |
| tamper_mid | **invalid** | 103ms | 5019ms |
| skip | **invalid** | 8ms | 5527ms |
| random | **invalid** | 10ms | 5070ms |
| replay | **invalid** | 9ms | 5207ms |

### G3 延迟分解 — 子进程并行
| 并行度 | Proving | Total | 加速比 |
|:---:|---:|---:|:---:|
| 1w | 6344ms | 6441ms | 1.0× |
| 2w | 5078ms | 5174ms | 1.25× |
| 4w | 4469ms | 4562ms | **1.42×** |

### Fidelity（严格分层）
| 层级 | Max Abs Error | 说明 |
|---|---|---|
| F1 Partition | **0.0** | 切片保持函数组合 |
| F2 Quantization | **~1.5×10⁻⁸** | EZKL 量化误差极小 |
| F3 Certified | **~1.5×10⁻⁸** | 认证输出 ≈ 浮点基线 |

### G4 可扩展性 (2/4/8 slices)
| Slices | Proof | Verify | Tamper |
|:---:|---:|---:|:---:|
| 2 | 2.8s | 40ms | detected |
| 4 | 6.8s | 83ms | detected |
| 8 | 12.7s | 168ms | detected |

---

## 6. 与旧系统的关系

| 维度 | 旧系统 (`distributed/`) | 新系统 (`v2/`) |
|---|---|---|
| 验证策略 | 选择性（部分切片不出 proof） | 全切片最终出 proof |
| Worker 角色 | 推理 + 证明 + 自验证 | 仅推理（或仅证明） |
| 验证者 | Worker 自报 `verified=True` | 独立 Verifier |
| 链路绑定 | 外部哈希链（可被绕过） | proof 公开实例 linking（密码学绑定） |
| 输出语义 | 单阶段结果 | provisional / certified 双阶段 |
| 安全声明 | "selective verification + hash chain" | "eventual full-chain certification" |

旧系统保留作为 **G1 baseline 对照**，不再代表主系统安全结论。

---

## 7. 与文献的对齐

- **NanoZK** [arXiv 2603.18046]: 逐层 proof + commitment chain + compositional soundness — 与本系统高度同构
- **DSperse**: targeted verification 是工程折中，非完备安全 — 本系统进一步走向 eventual full-chain
- **Non-Composability Note** [arXiv 2602.15756]: 层级近似验证不可组合 — 本系统证明精确量化电路 statement，fidelity 单独测量
- **EZKL 23.0.5**: aggregation API 不可用 — 正确标注为 future work

---

## 8. 文件清单（本次 git 提交）

### 新增
```
v2/__init__.py
v2/common/__init__.py
v2/common/types.py
v2/common/commitments.py
v2/common/logging.py
v2/compile/__init__.py
v2/compile/build_circuits.py
v2/prover/__init__.py
v2/prover/ezkl_adapter.py
v2/prover/parallel.py
v2/prover/prove_worker.py
v2/verifier/__init__.py
v2/verifier/verify_single.py
v2/verifier/verify_chain.py
v2/execution/__init__.py
v2/execution/pipeline.py
v2/execution/deferred_pipeline.py
v2/experiments/__init__.py
v2/experiments/e2e_certified.py
v2/experiments/deferred_certified.py
v2/experiments/fidelity.py
v2/experiments/scalability.py
v2/experiments/phase_b_smoke.py
v2/experiments/quick_test.py
v2/experiments/quick_test_v2.py
v2/docs/protocol.md
v2/docs/threat_model.md
v2/metrics/e2e_certified_results.json
v2/metrics/deferred_certified_results.json
v2/metrics/fidelity_results.json
v2/metrics/scalability_results.json
docs/refactor/REFACTORING_CHANGELOG.md (本文件)
```

### 修改
```
.gitignore (添加 v2/artifacts/ 和 v2/logs/ 排除)
```

### 未修改（保留为 baseline）
```
distributed/master.py
distributed/worker.py
common/utils.py
scripts/run_experiments.py
scripts/run_advanced_experiments.py
```
