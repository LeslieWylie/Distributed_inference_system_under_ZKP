# 开发报告 — 面向分布式推理的零知识证明框架

> 最后更新: 2026-04-02
> 仓库: https://gitee.com/yaolewu/distributed_inference_system_under_-zkp.git

---

## 一、项目概述

### 1.1 课题目标

设计并实现一种支持分布式推理验证的零知识证明框架，通过将证明生成任务分摊至多个节点，实现对模型推理完整性的验证，降低单一节点的资源负载。

### 1.2 系统定位

**Prover-Worker 分布式验证架构**: 每个 Worker 本地执行推理 + 生成 ZKP 证明，Master 不参与证明生成，独立 Verifier 通过 proof 公开实例链式链接完成全链路认证。

---

## 二、学术定位

### 2.1 研究空白

当前**不存在**完整实现以下组合的系统:
- 多 Worker 独立执行推理切片 + Worker 不可信
- 流水线顺序依赖（链式状态传递）
- 每个 Worker 本地生成 ZKP 证明（证明分摊）
- 独立 Verifier 通过 proof 公开实例链式链接

### 2.2 与已有范式的差异

| 范式 | 代表 | 本系统差异 |
|---|---|---|
| 单机推理 + ZKP | EZKL, zkCNN | 单 prover，无跨节点分摊 |
| 增量可验证计算 | Nova | prover 逻辑单一 |
| 分层 LLM 证明 | NanoZK, zkLLM | 单机分层，非多节点分布式 |
| 选择性验证 | DSperse | 不保证全切片证明 |

### 2.3 核心贡献

1. **Prover-Worker 架构**: 将证明生成从 Master 下放到各 Worker，实现证明开销的真正分布式化
2. **Proof 公开实例链式链接**: Verifier 从 proof 内部提取 rescaled I/O 做相邻切片链接，不信任网络传输的明文
3. **Terminal Binding**: 最终输出与 proof 内部输出绑定，恶意 Worker 的篡改行为可被检测

---

## 三、系统架构

### 3.1 Prover-Worker 架构

```
Client → Master/Coordinator → Prover-Worker₁ (infer + prove) → (output₁, π₁)
                             → Prover-Worker₂ (infer + prove) → (output₂, π₂)
                             → ...
         Master 收集所有 (output, proof) 对
         Verifier 独立验证全链路 → Certificate
```

### 3.2 协议流程

**Phase 1 — 分布式执行 + 证明 (Worker 端)**:
- Master 按序向各 Prover-Worker 发送 HTTP 请求
- Worker 执行 ONNX 推理 + EZKL prove，返回 `{output_tensor, proof_json}`
- Master 将上一片输出作为下一片输入

**Phase 2 — 独立验证 (Verifier 端)**:
- 逐片独立调用 `ezkl.verify(π_i, vk_i)`
- 提取 `rescaled_inputs(π_i)` 和 `rescaled_outputs(π_i)`
- 相邻链接: `|rescaled_outputs(π_i) - rescaled_inputs(π_{i+1})| < ε`
- 终端绑定: `|rescaled_outputs(π_n) - provisional_output| < ε`
- 全部通过 → CERTIFIED，任何失败 → INVALID

### 3.3 角色与信任

| 角色 | 信任 | 职责 |
|------|------|------|
| Client | 可信 | 提交输入，接收认证结果 |
| Master/Coordinator | 可信 | 调度，收集 proof，触发验证 |
| Prover-Worker | **不可信** | 推理 + 证明 |
| Verifier | 可信 | 独立验证 + 链式链接 + 证书签发 |
| Artifact Registry | 可信 | 存储 VK/PK/SRS/model_digest |

---

## 四、技术栈

| 组件 | 技术 | 版本 |
|---|---|---|
| 运行时 | Python (Miniconda) | 3.13 |
| ZKP 引擎 | EZKL (Halo2/PLONK/KZG) | 23.0.5 |
| 模型框架 | PyTorch | 2.10.0 |
| 视觉数据 | torchvision | 0.25.0 |
| 模型格式 | ONNX (opset 18) | 1.20.1 |
| 推理引擎 | onnxruntime | 1.24.3 |
| 通信层 | FastAPI + uvicorn | — |

---

## 五、代码结构

```
C:\ZKP\
├── v2/                              ← 当前活跃架构
│   ├── services/
│   │   ├── prover_worker.py         # Prover-Worker (POST /infer_and_prove)
│   │   ├── distributed_coordinator.py # Master (不 prove)
│   │   └── workers.json             # Worker 地址配置
│   ├── compile/build_circuits.py    # ONNX 切片 + EZKL 编译 + Registry
│   ├── prover/                      # EZKL 适配器 + 并行 proving
│   ├── verifier/                    # 独立验证 + 链式链接 + 证书
│   ├── execution/                   # 本地模式 pipeline
│   ├── experiments/                 # E2E / 保真度 / 可扩展性实验
│   ├── docs/                        # protocol.md + threat_model.md
│   └── metrics/                     # 实验结果 JSON
│
├── models/
│   ├── mnist_model.py               # MNIST MLP (109K 参数)
│   └── configurable_model.py        # 旧玩具模型 (baseline)
│
├── distributed/ + scripts/          # v1 基准系统 (只读参考)
├── docs/                            # 论文 + 答辩材料
└── survey/                          # 文献 + 开题材料
```

---

## 六、模型

### 6.1 MNIST MLP

- 架构: `784 → Linear(128) → ReLU → Linear(64) → ReLU → Linear(10)`
- 参数量: 109,386
- 训练: MNIST 数据集, 3 epochs, Adam optimizer
- 测试准确率: **97.24%** (9724/10000)
- 切分: 2 / 4 / 8 片
- ONNX 导出: opset 18, `dynamo=False` (EZKL tract 兼容)

### 6.2 MNIST CNN

- 架构: `Conv2d(1→8, 3×3) → ReLU → Flatten → FC(5408→10)`
- 参数量: ~54,170
- 切分: 2 片 (卷积层 + 全连接层)
- 用途: 验证框架对卷积网络的适配能力

### 6.3 旧模型 (baseline)

- ConfigurableModel: 8 层 FC, hidden_dim=8, ~500 参数
- 仅作 v1 基准对照，不用于论文主要实验

---

## 七、实验结果

### 7.1 G2: 协议正确性 — 5/5 PASS

| 攻击类型 | 预期 | 结果 | 检测机制 |
|----------|------|------|----------|
| normal | certified | ✓ | — |
| tamper (output+999) | invalid | ✓ | Terminal binding |
| skip (全零输出) | invalid | ✓ | Terminal binding |
| random (随机噪声) | invalid | ✓ | Terminal binding |
| replay (固定值) | invalid | ✓ | Terminal binding |

### 7.2 G4: 可扩展性 (2 / 4 / 8 切片)

| 切片数 | 证明总耗时 | 客户端验证 | 端到端总时延 |
|--------|-----------|-----------|-------------|
| 2 | 7,356 ms | 174 ms | 7,845 ms |
| 4 | 19,176 ms | 438 ms | 20,289 ms |
| 8 | 32,076 ms | 518 ms | 33,559 ms |

### 7.3 保真度 (F1 / F2 / F3)

| 指标 | 结果 |
|------|------|
| F1 (切分一致性) | 0 (bit-exact) |
| F2 (量化误差) | ~0.003 / ~0.002 |
| F3 (端到端认证) | ~0.002，认证 100% |

### 7.4 CNN 跨架构验证 (2 切片)

| 场景 | 预期 | 实际 | 总时延 |
|------|------|------|--------|
| normal | certified | certified | 24,445 ms |
| tamper_last | invalid | invalid | 22,948 ms |
| skip_last | invalid | invalid | 19,648 ms |

### 7.5 资源与吞吐量 (2 切片)

| 角色 | CPU 均值 | RSS 峰值 |
|------|----------|----------|
| 协调节点 | 5.0% | 40 MB |
| 工作节点 1 | 289% | 627 MB |
| 工作节点 2 | 245% | 696 MB |

吞吐量: 0.129 req/s (2 切片串行)

### 7.6 链接精度结论

| 方案 | 结论 | 原因 |
|------|------|------|
| public + scale 对齐 | 可用 (当前采用) | 阈值 2 ULP |
| hashed (Poseidon) | 不可行* | witness 不 bit-exact |
| polycommit (KZG) | 不可行* | witness 不 bit-exact |

*\*注: 通过 proof-bound canonical handoff 修复后，polycommit 和 hashed 正常路径已可通过 certified。残余工作是将旧攻击场景语义更新为新规则。*

### 7.7 关键观察

- 证明生成占总耗时 88%，这是 ZKP 系统的固有特性
- 推理耗时仅 1ms — 分布式架构不增加推理延迟
- 验证极快 (~130ms) — 可部署为独立审计服务
- 两个 Worker 目前串行通信，可通过流水线化进一步优化

---

## 八、安全分析

### 8.1 全链路信任机制

1. **Per-slice proof soundness**: Worker 生成的 proof 经 EZKL/Halo2 验证
2. **Adjacent linking**: 相邻 proof 的 rescaled 输出/输入近似一致 (ε < 0.01)
3. **Terminal binding**: 最终 proof 输出与 provisional output 一致
4. **Model digest**: ONNX 文件 SHA-256 摘要在验证时重新计算

### 8.2 攻击防御

| 攻击 | 防御 |
|------|------|
| 输出篡改 | Terminal binding 检测 |
| 模型替换 | model_digest 校验 |
| 相邻节点串通 | 两端均需有效 proof |
| 重放攻击 | req_id 域分离 |

### 8.3 已知限制

1. 链式链接默认使用浮点近似 (ε=2 ULP)，非密码学精确匹配
2. Master/Verifier 假设可信
3. Sub-ε 扰动在量化噪声内不可区分
4. 通过 proof-bound canonical handoff，polycommit 和 hashed 正常路径已可 certified；尚需统一更新旧攻击语义并扩展到 deferred/parallel 路径

---

## 九、开发历程

### v1 阶段 (2026-03-17 ~ 03-20)
- Master-Worker 分布式推理原型
- 三层校验 (L1 SHA-256 + L2 proof linking + L3 哈希链)
- 选择性验证 edge-cover 策略
- 玩具模型 (8 层 FC, 500 参数)
- 发现: light 节点对恶意攻击不安全

### v2 阶段 (2026-03-20 ~ 03-28)
- Deferred Certification 架构
- 执行-证明解耦, 子进程并行 proving
- 独立 Verifier + 全链路 linking + 证书签发
- 仍使用旧玩具模型，Master 集中 prove

### v2 重构 (2026-03-30)
- **Prover-Worker 架构**: Worker 本地 prove，证明分摊
- **MNIST MLP**: 真实模型 (109K params, 97.24% 准确率)
- **全链路信任**: proof 绑定真实 I/O，terminal binding 检测篡改
- **跨主机支持**: Worker 绑定 0.0.0.0, workers.json 可配置 IP
- 5/5 攻击测试全部通过

### v2 多模型 + 可扩展性 + 链接精度 (2026-04-02)
- **MNIST CNN** 模型支持 (Conv2d + FC, 2 切片 E2E)
- **2/4/8 切片可扩展性实验**: 12 用例全通过
- **F1/F2/F3 保真度**: F1=0 (bit-exact)，F2/F3 误差 ~10⁻³
- **资源占用 + 吞吐量**: Worker CPU ~250–290%, RSS ~600–700MB
- **三阶段编译管线**: scale 对齐使链接阈值收紧至 2 ULP
- **public / hashed / polycommit 系统性验证**: 定位并修复根因
- **Proof-bound canonical handoff**: 相邻切片传递 proof/witness 绑定接口值，polycommit 和 hashed 正常路径已 certified
- 32/32 自动化测试通过
