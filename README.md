# 面向分布式推理的零知识证明框架

> Distributed Inference System under Zero-Knowledge Proofs  
> Deferred Certification Architecture for End-to-End Verifiable Distributed Inference

## 系统定位

本项目研究如何在分布式切片推理中实现端到端可验证性。系统将深度学习模型按层切片分配给多个 Worker，每个切片最终都生成 EZKL ZKP 证明，由独立 Verifier 通过相邻切片输入/输出公开实例的一致性检查（commitment linking）和终端绑定完成全链路认证。

**核心思想：所有切片最终都被证明，证明不阻塞执行。**

系统区分两类输出：
- **Provisional Output**：在线推理完成后立即返回（~10ms），未经认证
- **Certified Output**：全部 proof 验证通过 + commitment chain 闭合后，升级为认证结果

## 快速开始

### 环境准备

```powershell
winget install --id Anaconda.Miniconda3 -e --silent
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
& $PY -m pip install ezkl torch onnx onnxscript psutil fastapi uvicorn requests onnxruntime
```

### 运行 (v2 新架构)

```powershell
$env:PYTHONIOENCODING = "utf-8"
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"

# Phase A：同步全链路认证 (6 种攻击场景, 全部检出)
& $PY -u -m v2.experiments.e2e_certified --slices 4 --rebuild

# Phase B/C：执行-证明解耦 + 子进程并行 proving
& $PY -u -m v2.experiments.deferred_certified

# Fidelity 分层实验 (F1 切片 + F2 量化 + F3 认证)
& $PY -u -m v2.experiments.fidelity

# 多切片可扩展性 (2/4/8 slices)
& $PY -u -m v2.experiments.scalability
```

### 运行 (v1 旧架构 baseline)

```powershell
# 阶段 1：单机验证
& $PY -u scripts/run_single_machine_demo.py

# 阶段 2：分布式推理 (2 Workers + Master)
& $PY -u scripts/run_stage2.py

# 阶段 3：选择性验证实验
& $PY -u scripts/run_experiments.py
```

## 项目结构

```
├── v2/                              ← 新架构 (Deferred Certification)
│   ├── common/
│   │   ├── types.py                 #   RequestStatus, SliceArtifact, Certificate
│   │   ├── commitments.py           #   SHA-256 域分离承诺
│   │   └── logging.py               #   JSON Lines 审计日志
│   ├── compile/
│   │   └── build_circuits.py        #   ONNX 切片 + EZKL 编译 + registry
│   ├── prover/
│   │   ├── ezkl_adapter.py          #   prove_slice (仅 proving, 不含 verify)
│   │   ├── parallel.py              #   子进程并行 proving
│   │   └── prove_worker.py          #   子进程入口
│   ├── verifier/
│   │   ├── verify_single.py         #   独立单片验证
│   │   └── verify_chain.py          #   全链路 linking + 终端绑定 + 证书
│   ├── execution/
│   │   ├── pipeline.py              #   Phase A: 同步全链路
│   │   └── deferred_pipeline.py     #   Phase B/C: 执行-证明解耦
│   ├── experiments/                 #   G2/G3/G4/F1-F3 实验
│   ├── docs/
│   │   ├── protocol.md              #   正式协议文档
│   │   └── threat_model.md          #   威胁模型
│   └── metrics/                     #   实验结果 JSON
│
├── distributed/                     ← v1 旧架构 (baseline 对照)
│   ├── worker.py                    #   Worker FastAPI (选择性验证)
│   └── master.py                    #   Master 调度 + 三层校验
│
├── models/                          #   模型定义 + ONNX 切片
├── scripts/                         #   v1 旧实验脚本
├── docs/                            #   设计文档 + 重构说明
└── survey/                          #   开题报告 + 文献资料
```

## 技术栈

| 组件 | 技术 | 版本 |
|---|---|---|
| 运行时 | Python (Miniconda) | 3.13 |
| ZKP 引擎 | EZKL (Halo2/PLONK/KZG) | 23.0.5 |
| 模型框架 | PyTorch | 2.10.0 |
| 模型格式 | ONNX | 1.20.1 |
| 推理引擎 | onnxruntime | 1.24.3 |
| 通信层 | FastAPI + uvicorn | 0.135.1 |
| 监控 | psutil + time | — |

## 实验结果摘要

### v2 新架构实验

#### G2 协议正确性 — 6/6 PASS

| 攻击 | 状态 | Provisional | Certification |
|---|:---:|---:|---:|
| normal | **certified** | 37ms | 4680ms |
| tamper_last | **invalid** | 66ms | 5155ms |
| tamper_mid | **invalid** | 103ms | 5019ms |
| skip | **invalid** | 8ms | 5527ms |
| random | **invalid** | 10ms | 5070ms |
| replay | **invalid** | 9ms | 5207ms |

#### G3 延迟分解 — 子进程并行

| 并行度 | Proving | Total | 加速比 |
|:---:|---:|---:|:---:|
| 1w | 6344ms | 6441ms | 1.0× |
| 2w | 5078ms | 5174ms | 1.25× |
| 4w | 4469ms | 4562ms | **1.42×** |

#### Fidelity（严格区分 circuit correctness 与 float fidelity）

| 层级 | Max Abs Error | 说明 |
|---|---|---|
| F1 Partition | **0.0** | 切片保持函数组合 |
| F2 Quantization | **~1.5×10⁻⁸** | EZKL 量化误差极小 |
| F3 Certified | **~1.5×10⁻⁸** | 认证输出 ≈ 浮点基线 |

#### G4 可扩展性 (2/4/8 slices)

| Slices | Proof | Verify | Tamper |
|:---:|---:|---:|:---:|
| 2 | 2.8s | 40ms | detected |
| 4 | 6.8s | 83ms | detected |
| 8 | 12.7s | 168ms | detected |

### v1 旧架构 Baseline

> 以下为旧系统数据，仅作对照。旧系统允许部分切片不出 proof，不满足 end-to-end 可验证要求。

| 切片数 | 验证率 | 端到端(ms) | 安全结果 |
|:---:|:---:|---:|:---:|
| 8 | 100% | 15,526 | 篡改被预防 |
| 8 | 50% | 9,367 | 篡改被预防 |
| 8 | 25% | 9,087 | 篡改被预防 |

## 文档

- [v2/docs/protocol.md](v2/docs/protocol.md) — 正式协议文档 (End-to-End Statement, 请求状态机)
- [v2/docs/threat_model.md](v2/docs/threat_model.md) — 威胁模型 (对手能力, 信任假设, 攻击检测矩阵)
- [docs/refactor/REFACTORING_CHANGELOG.md](docs/refactor/REFACTORING_CHANGELOG.md) — 重构变更日志
- [DEVELOPMENT_REPORT.md](DEVELOPMENT_REPORT.md) — 环境配置指南
- [PROJECT_PLAN.md](PROJECT_PLAN.md) — 完整开发计划 + 安全模型

## 参考文献

- [NanoZK: Layerwise ZKP for LLM Inference](https://arxiv.org/abs/2603.18046) — 逐层 proof + commitment chain
- [DSperse: Targeted Verification in ZKML](https://arxiv.org/abs/2508.06972) — 选择性验证 baseline
- [Non-Composability of Layerwise Approximate Verification](https://arxiv.org/abs/2602.15756) — 近似层验证不可组合
- [Artemis: CP-SNARK for zkML](https://arxiv.org/abs/2409.12055) — 低成本 commitment linking
- [zkGPT](https://www.usenix.org/system/files/usenixsecurity25-qu-zkgpt.pdf) — 单体 LLM 证明
- [EZKL Documentation](https://docs.ezkl.xyz/) / [Python Bindings](https://pythonbindings.ezkl.xyz/en/stable/)
- [EZKL Proof Splitting Blog](https://blog.ezkl.xyz/post/splitting/) — split proof + commitment stitching
