# 面向分布式推理的零知识证明框架

> ⚠️ **Repository is in transition (2026-04-17)**: the active development line is **v3** (Collaborative Nova-IVC, see `docs/refactor/v3/`). The `v2/` directory is now a **frozen baseline** for comparison experiments and will not receive new features. See `docs/refactor/v3/00-overview.md` for the rationale.

> Zero-Knowledge Proof Framework for Distributed Inference  
> Prover-Worker Architecture with Distributed Proof Generation

## 系统定位

本项目研究如何在分布式切片推理中实现端到端可验证性。系统基于 **Prover-Worker 架构 + 客户端独立验证**：

1. 每个 Worker **本地执行推理并生成 EZKL ZKP 证明**，证明开销分摊到各节点
2. 不可信 Coordinator 收集各 Worker 的 proof，组装为 **Proof Bundle** 返回客户端
3. 客户端使用本地 verifier + registry 工件 **独立验证** bundle，生成最终可信判断

**核心思想：Coordinator 和 Worker 均不可信，客户端独立验证是唯一信任来源。**

服务端返回的任何 `certificate` 或 advisory 结果都不是信任来源。

### 与传统架构的区别

| | 传统集中式证明 | 本系统 (Prover-Worker) |
|---|---|---|
| 证明生成 | Master 集中 prove | 各 Worker 本地 prove |
| 证明开销 | 单节点承担全部 | 分摊到 N 个节点 |
| 安全性 | Worker 返回明文 → Master prove → 可伪造 | Worker 返回 (output, proof) → proof 绑定真实 I/O |
| 可扩展性 | 受限于 Master 资源 | 随 Worker 数量线性扩展 |

## 模型

- **MNIST MLP** (109,386 参数): 784→128→ReLU→64→ReLU→10
  - 训练准确率: 97.24% (MNIST 测试集, 3 epochs)
  - 支持 2 / 4 / 8 切片配置，EZKL 电路 logrows=16
- **MNIST CNN** (~54,170 参数): Conv2d(1→8, 3×3)→ReLU→Flatten→FC(5408→10)
  - 2 切片配置，验证框架对卷积网络的适配能力

## 快速开始

### 环境准备

```powershell
winget install --id Anaconda.Miniconda3 -e --silent
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
& $PY -m pip install ezkl torch torchvision onnx onnxscript psutil fastapi uvicorn requests onnxruntime
```

### 离线编译（一次性）

```powershell
$env:PYTHONIOENCODING = "utf-8"
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"

# 训练 MNIST MLP + 导出 ONNX 切片
& $PY models/mnist_model.py --slices 2 --output-dir v2/artifacts/models

# 编译 EZKL 电路 + 生成 registry
& $PY -c "from v2.compile.build_circuits import build_registry; build_registry(num_slices=2, model_type='mnist')"
```

## 主工作流 (Mainline)

1. **构建 Registry 工件** (离线，一次性)
2. **启动 Prover-Workers** (各自持有编译后电路 + PK)
3. **运行不可信 Coordinator** — 编排请求，收集 proof，组装 Proof Bundle
4. **客户端独立验证** — 使用本地 verifier + registry 工件验证 bundle
5. **仅当本地验证返回 `certified` 时接受结果**

服务端证书 (advisory) 仅供调试/缓存使用，不是信任来源。

### 运行 E2E 实验

```powershell
# 完整端到端实验（5 种攻击场景，全部通过）
& $PY -u -m v2.experiments.refactored_e2e --slices 2

# 快速冒烟测试（3 种攻击）
& $PY -u v2/experiments/smoke_test.py
```

### 运行 v1 基准实验（对照）

```powershell
& $PY -u scripts/run_single_machine_demo.py
& $PY -u scripts/run_stage2.py
```

## 项目结构

```
├── v2/                              ← Prover-Worker 架构（活跃开发）
│   ├── services/
│   │   ├── prover_worker.py         #   Prover-Worker: 推理 + 证明
│   │   ├── distributed_coordinator.py #  Master 协调器 (不参与证明)
│   │   └── workers.json             #   Worker IP/端口配置 (支持跨主机)
│   ├── compile/
│   │   └── build_circuits.py        #   ONNX 切片 + EZKL 编译 + registry
│   ├── prover/
│   │   ├── ezkl_adapter.py          #   prove_slice + canonical I/O 提取
│   │   ├── parallel.py              #   子进程并行 proving
│   │   └── prove_worker.py          #   子进程入口
│   ├── verifier/
│   │   ├── verify_single.py         #   独立单片验证
│   │   └── verify_chain.py          #   全链路 linking + 终端绑定 + 证书
│   ├── execution/                   #   本地模式 pipeline (单元测试用)
│   ├── experiments/                 #   E2E / 保真度 / 可扩展性实验
│   ├── docs/                        #   协议规范 + 威胁模型
│   ├── metrics/                     #   实验结果 JSON
│   └── common/                      #   共享类型 + commitments + logging
│
├── models/
│   ├── mnist_model.py               #   MNIST MLP (109K 参数) + 切片导出
│   ├── mnist_cnn.py                 #   MNIST CNN (~54K 参数) + 切片导出
│   └── configurable_model.py        #   旧玩具模型 (baseline)
│
├── distributed/                     ← v1 基准架构（只读参考）
├── scripts/                         #   v1 实验脚本
├── docs/                            #   论文 + 答辩材料
└── survey/                          #   开题报告 + 文献资料
```

## 技术栈

| 组件 | 技术 | 版本 |
|---|---|---|
| 运行时 | Python (Miniconda) | 3.13 |
| ZKP 引擎 | EZKL (Halo2/PLONK/KZG) | 23.0.5 |
| 模型框架 | PyTorch | 2.10.0 |
| 视觉工具 | torchvision | 0.25.0 |
| 模型格式 | ONNX | 1.20.1 (opset 18) |
| 推理引擎 | onnxruntime | 1.24.3 |
| 通信层 | FastAPI + uvicorn | — |

## 实验结果

### 可扩展性 (2 / 4 / 8 切片)

| 切片数 | 证明总耗时 | 客户端验证 | 端到端总时延 | 正常路径 |
|--------|-----------|-----------|-------------|----------|
| 2 | 7,356 ms | 174 ms | 7,845 ms | certified |
| 4 | 19,176 ms | 438 ms | 20,289 ms | certified |
| 8 | 32,076 ms | 518 ms | 33,559 ms | certified |

### 攻击检测 — 恶意节点识别率 100%

Precision = 1.0, Recall = 1.0, F1 = 1.0。覆盖篡改、跳过、随机替换、回放和中间切片篡改等场景。

### 保真度 (Fidelity)

| 指标 | 含义 | 结果 |
|------|------|------|
| F1 | 切分一致性 | 严格 0 (bit-exact) |
| F2 | 逐片量化误差 | ~0.003 / ~0.002 |
| F3 | 端到端认证误差 | ~0.002, 认证通过率 100% |

### CNN 跨架构验证 (2 切片)

| 场景 | 预期 | 实际 | 端到端总时延 |
|------|------|------|-------------|
| normal | certified | certified | 24,445 ms |
| tamper_last | invalid | invalid | 22,948 ms |
| skip_last | invalid | invalid | 19,648 ms |

### 资源与吞吐量 (2 切片)

| 角色 | CPU 均值 | RSS 峰值 |
|------|----------|----------|
| 协调节点 | 5.0% | 40 MB |
| 工作节点 1 | 289% | 627 MB |
| 工作节点 2 | 245% | 696 MB |

吞吐量: 0.129 req/s (2 切片串行模式)

## 安全模型

**最小信任根**:
- 客户端本地验证程序
- Registry 工件 (`vk/settings/model_digest/srs`)
- 密码学假设 (EZKL/Halo2 soundness, SHA-256 collision resistance)

**不可信组件**: Coordinator, Prover-Workers, 网络传输

**全链路绑定** (客户端独立验证):
- 从 proof 公开实例提取 I/O → 不信任 Worker 或 Coordinator 传输的明文值
- 相邻切片: `rescaled_outputs(π_i) ≈ rescaled_inputs(π_{i+1})`
- 终端绑定: `rescaled_outputs(π_n) ≈ claimed_final_output`

### 跨切片链接与隐私

| 方案 | 结论 | 原因 |
|------|------|------|
| public + scale 对齐 | 可用 (当前采用) | 阈值 2 ULP，正常/攻击路径量级差距约 6 个数量级 |
| hashed (Poseidon) | 不可行 | 独立电路 witness 不 bit-exact |
| polycommit (KZG) | 不可行 | 同上 |

**Proof-bound canonical handoff** (最新修复): 相邻切片传递的不再是执行浮点值，而是 proof/witness 绑定后的接口值。修复后，polycommit 和 hashed 正常路径已可通过 certified。

### Legacy Paths (Baseline/Reference Only)

以下路径仅作基准对照:
- `v2/execution/` — 本地 pipeline
- `v2/services/master_coordinator.py` — 旧集中式 Master
- `distributed/` + `scripts/` — v1 系统

## 学术定位

- **声称**: 面向不可信 Worker 的应用层可验证推理；证明开销分摊到各节点；三阶段编译管线实现跨切片 scale 对齐
- **不声称**: 分布式 prover 内部协议、隐私推理、恶意 prover 模型
- **参考**: DSperse, NanoZK, VeriLLM, zkLLM, IMMACULATE, ZKML survey
