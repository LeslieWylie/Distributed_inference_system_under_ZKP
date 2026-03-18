# 面向分布式推理的零知识证明框架

> Distributed Inference System under Zero-Knowledge Proofs

基于 DSperse 架构的分布式推理分层可验证性研究原型。将深度学习模型按层切片分配给多个 Worker 节点推理，每个 Worker 用 EZKL 生成局部 ZKP 证明，Master 通过分层校验体系（外部哈希 + 相邻 proof 间 ZKP Linking + 哈希链 + 随机挑战）验证推理正确性并检测恶意节点。

## 快速开始

### 环境准备

```powershell
# 1. 安装 Miniconda
winget install --id Anaconda.Miniconda3 -e --silent

# 2. 安装依赖
$PYTHON = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
& $PYTHON -m pip install ezkl torch onnx onnxscript psutil fastapi uvicorn requests onnxruntime
```

### 运行

```powershell
$env:PYTHONIOENCODING = "utf-8"
$PYTHON = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"

# 阶段 1：单机验证
& $PYTHON -u scripts/run_single_machine_demo.py

# 阶段 2：分布式推理 (2 Workers + Master)
& $PYTHON -u scripts/run_stage2.py

# 阶段 3：实验 (2/4/8 切片 × 正常/故障)
& $PYTHON -u scripts/run_experiments.py

# P1+P3：选择性验证 + 多攻击实验
& $PYTHON -u scripts/run_advanced_experiments.py

# P2：隐私模式对比
& $PYTHON -u scripts/run_p2_experiment.py
```

## 项目结构

```
├── common/                  # 共享工具层
│   └── utils.py             #   EZKL 初始化/prove/verify、哈希、内存监测
│
├── distributed/             # 分布式推理层
│   ├── worker.py            #   Worker FastAPI 服务 (/infer, /infer_light)
│   └── master.py            #   Master 调度 + 三层校验
│
├── models/                  # 模型层
│   ├── full_model.py        #   阶段1 两层FC模型
│   └── configurable_model.py#   可配置N层模型 (支持2/4/8切片)
│
├── scripts/                 # 运行脚本
│   ├── run_single_machine_demo.py    # 阶段1
│   ├── run_stage2.py                 # 阶段2 一键启动
│   ├── run_experiments.py            # 阶段3 基础实验 (简化管线, L1+L3)
│   ├── run_advanced_experiments.py   # P1+P3 选择性验证+多攻击 (简化管线)
│   ├── run_p2_experiment.py          # P2 隐私模式对比
│   └── run_p4_p6_experiment.py       # P4保真度+P6完整性检查对比
│
├── metrics/                 # 实验结果 (JSON)
├── survey/                  # 开题报告 + 参考文献
├── DEVELOPMENT_REPORT.md    # 环境配置指南
└── PROJECT_PLAN.md          # 完整开发计划 + 安全模型
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

### 选择性验证 (P1)

| 切片数 | 请求验证率 | 端到端(ms) | 开销降低 | 检测结果 |
|:---:|:---:|---:|:---:|:---:|
| 8 | 100% | 12,139 | — | 检测到 |
| 8 | 50% | 6,265 | **49%** | 检测到 |
| 8 | 25% | 3,761 | **68%** | 检测到 |

> 注：“请求验证率”为 verify_ratio 参数，实际 proof 覆盖率受 edge-cover 策略影响可能更高。检测结果为当前攻击模型（响应层篡改）下的结果。

### 隐私模式 (P2)

| 模式 | proof(ms) | 开销倍数 |
|:---:|---:|:---:|
| all_public | 5,575 | 1.0× |
| hashed (Poseidon) | 10,579 | 1.90× |
| private | 5,294 | 0.95× |

### 多攻击场景 (P3) — 当前攻击模型下全部检测到

tamper / skip / random / replay × {100%, 50%} 请求验证率

> 当前攻击模型为响应层篡改（Worker 正确计算但返回篡改输出）。若恶意 Worker 同时伪造 output 和 hash，需依赖随机挑战模式检测。

## 文档

- [DEVELOPMENT_REPORT.md](DEVELOPMENT_REPORT.md) — 环境配置、使用说明、FAQ
- [PROJECT_PLAN.md](PROJECT_PLAN.md) — 开发计划、安全模型、检测概率推导、威胁模型
- [survey/reference/RESEARCH_SYNTHESIS_2026-03-18.md](survey/reference/RESEARCH_SYNTHESIS_2026-03-18.md) — 外部论文、本地资料与当前实现边界的综合整理

## 参考

- [DSperse: Targeted Verification in ZKML](https://arxiv.org/abs/2508.06972)
- [EZKL Documentation](https://docs.ezkl.xyz/)
- [EZKL Python Bindings](https://pythonbindings.ezkl.xyz/en/stable/)
