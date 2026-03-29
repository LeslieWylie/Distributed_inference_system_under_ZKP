# 面向分布式推理的零知识证明框架

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
- 2 片切分，EZKL 电路 logrows=16

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
│   │   ├── ezkl_adapter.py          #   prove_slice (gen_witness + prove)
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

### Prover-Worker E2E 实验 — 5/5 PASS

| 攻击 | 预期 | 结果 | 总耗时 | 证明耗时 | 验证耗时 |
|------|------|------|--------|----------|----------|
| normal | certified | ✓ | 5085ms | 4600ms | 129ms |
| tamper (+999) | invalid | ✓ | 5027ms | 4498ms | 160ms |
| skip (全零) | invalid | ✓ | 5131ms | 4620ms | 166ms |
| random (噪声) | invalid | ✓ | 4971ms | 4493ms | 130ms |
| replay (固定值) | invalid | ✓ | 5227ms | 4761ms | 119ms |

### 性能分解 (2-slice MNIST MLP)

| 阶段 | 耗时 | 说明 |
|------|------|------|
| ONNX 推理 | ~1ms | 两片总和 |
| EZKL 证明 | ~4.5s | 两个 Worker 串行 (可并行化) |
| 独立验证 | ~130ms | 链式链接 + 终端绑定 |

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

### Legacy Paths (Baseline/Reference Only)

以下路径仅作基准对照，不是推荐的 v2 主链:
- `v2/execution/pipeline.py` — 本地同步 pipeline
- `v2/execution/deferred_pipeline.py` — 本地 deferred pipeline
- `v2/services/master_coordinator.py` — 旧集中式 Master
- `v2/experiments/distributed_e2e.py` — 旧分布式实验
- `distributed/` + `scripts/` — v1 系统

## 学术定位

- **声称**: 面向不可信 Worker 的应用层可验证推理；证明开销分摊到各节点
- **不声称**: 分布式 prover 内部协议、隐私推理、恶意 prover 模型
- **参考**: DSperse, NanoZK, VeriLLM, zkLLM, IMMACULATE, ZKML survey
