# 面向分布式推理的零知识证明框架 — 完整开发文档

> Distributed Inference System under Zero-Knowledge Proofs
> 最后更新：2026-03-18
> 仓库：https://gitee.com/yaolewu/distributed_inference_system_under_-zkp.git

---

## 目录

1. [项目概述](#一项目概述)
2. [学术定位](#二学术定位)
3. [系统架构](#三系统架构)
4. [技术栈](#四技术栈)
5. [代码结构](#五代码结构)
6. [核心模块详解](#六核心模块详解)
7. [安全模型](#七安全模型)
8. [实验设计与结果](#八实验设计与结果)
9. [环境配置指南](#九环境配置指南)
10. [运行指南](#十运行指南)
11. [已解决的工程问题](#十一已解决的工程问题)
12. [系统限制与未来方向](#十二系统限制与未来方向)

---

## 一、项目概述

### 1.1 系统定位

**Sampling-based Verifiable Distributed Neural Inference with Cryptographic Linking**

本系统将深度学习模型按层切分为多个 ONNX 子模型，分配给多个不可信 Worker 节点独立推理。每个 Worker 用 EZKL（基于 Halo2/PLONK/KZG 的 ZKP 引擎）为本地切片生成零知识证明。Master 通过三层校验体系验证推理正确性并检测恶意节点。

**目标是 Verifiable Inference**（可验证推理），不是 Private Inference。ZKP 证明计算正确性，Worker 仍然看到明文输入。

### 1.2 核心创新点

1. **Pipeline + ZKP**：将 zkML 扩展到多节点流水线 $f = f_1 \circ f_2 \circ \cdots \circ f_N$
2. **Proof Linking**：相邻 proof 节点间的状态一致性（`prev.processed_outputs == curr.processed_inputs`，仅当两端均为 proof 节点时成立）
3. **选择性验证 + 外部一致性**：首尾必验 + edge-cover 选点 + 外部哈希链 + 随机挑战组合
4. **Sampling Verification**：证明开销 vs 安全性可配置 tradeoff

### 1.3 学术表述

> 本工作将 zkML（如 EZKL）从单机推理扩展到多节点 pipeline，引入跨节点 proof linking 和基于边覆盖的采样验证，实现分布式推理过程的可验证性保证。

---

## 二、学术定位

### 2.1 研究空白

当前**不存在**完整实现以下组合的系统：
- 多 Worker 独立执行推理 + Worker 不可信
- 流水线顺序依赖（链式状态）
- 跨节点 proof linking（相邻 proof 间密码学一致性，light 节点处退化为外部哈希链）
- 选择性验证（概率安全模型）

### 2.2 与已有范式的差异

| 范式 | 代表 | 与本系统差异 |
|---|---|---|
| 单机推理 + ZKP | EZKL | 单 prover，无跨节点 |
| 可验证计算 | Groth16 | 通用函数，非推理 pipeline |
| 增量可验证计算 | Nova | prover 逻辑仍单一 |
| 分布式可验证计算 | Verifiable MapReduce | 并行，非顺序流水 |
| zkRollup | zkSync | 独立交易，非链式依赖 |

---

## 三、系统架构

### 3.1 高层架构

```
Master (master.py)
  │ HTTP POST
  ├─ /infer (ZKP proof)
  ├─ /infer_light (仅推理)
  ├─ /re_prove (随机挑战)
  │
Worker 1 Worker 2 ... Worker N
  input → output_1 → output_2 → ... → final_output
```

### 3.2 三层校验

| 层 | 机制 | 安全等级 |
|:---:|---|---|
| L1 | SHA256(output_data) == hash_out | fault detection（非对抗） |
| L2 | prev.processed_outputs == curr.processed_inputs (ZKP instance) | **密码学级**（仅当两端均为 proof 节点时生效） |
| L3 | prev.hash_out == curr.hash_in | consistency check（非对抗） |

**L2 是系统密码学安全来源，但仅在相邻切片均被选为 proof 模式时提供跨节点密码学约束。** 当一端为 light 节点时，该边退化为 L1+L3 的故障检测级保护，辅以随机挑战机制。

**L2 linking 与隐私模式的关系**：L2 跨切片约束仅在 `hashed` 模式下有意义。`all_public` 模式下，EZKL 对每个切片独立编译电路时使用不同量化参数（input_scale / param_scale），同一份浮点数据在两个切片的电路中量化为不同 field element，导致 `processed_inputs ≠ processed_outputs`；Master 检测到不匹配后回退跳过。只有 `hashed` 模式下 Poseidon 哈希才产生跨切片一致性约束。

### 3.3 边覆盖策略

```
保证: ∀ edge (i, i+1): i ∈ ZKP ∨ (i+1) ∈ ZKP
max_light_gap = 1: 最多 1 个连续 light 节点
```

三种策略：
- `edge_cover`（默认，推荐）
- `contiguous`（连续段）
- `random`（旧，安全性弱）

---

## 四、技术栈

| 组件 | 版本 | 用途 |
|---|---|---|
| Python | 3.13.12 (Miniconda) | 运行时 |
| EZKL | 23.0.5 | ZKP (Halo2/PLONK/KZG) |
| PyTorch | 2.10.0 | 模型定义 |
| ONNX | 1.20.1 (opset 18) | 模型格式 |
| onnxruntime | 1.24.3 | 推理 |
| FastAPI | 0.135.1 | Worker API |
| uvicorn | 0.42.0 | 应用服务器 |
| requests | 2.32.5 | 网络通信 |
| psutil | 7.2.2 | 内存监测 |

---

## 五、代码结构

```
common/utils.py              # EZKL 初始化、prove、verify
  ├── ezkl_init()           # 一次性初始化
  ├── ezkl_prove()          # 生成证明 + 提取 proof_instances
  └── sha256_of_list()      # 外部哈希

distributed/worker.py        # Worker FastAPI
  ├── InferRequest/Response  # 请求/响应模型
  ├── /infer               # 完整 ZKP
  ├── /infer_light         # 仅推理
  ├── /re_prove            # 随机挑战
  └── main()               # 启动

distributed/master.py        # Master 调度
  ├── _select_verified_slices()  # 边覆盖策略
  ├── run_pipeline()           # 流水线 + 三层校验
  └── main()                   # CLI

models/configurable_model.py  # N 层 FC + 切分
  ├── ConfigurableModel()
  └── split_and_export()

scripts/
  ├── run_single_machine_demo.py   # 阶段1
  ├── run_stage2.py                # 阶段2
  ├── run_experiments.py           # 阶段3 (6组)
  ├── run_advanced_experiments.py  # P1+P3 (20组)
  ├── run_p2_experiment.py         # P2 (9组)
  └── run_p4_p6_experiment.py      # P4+P6 (8+组)
```

---

## 六、核心模块详解

### 6.1 EZKL 初始化（ezkl_init）

**调用时机**：Worker 启动时，`uvicorn.run()` **之前**

```
gen_settings → calibrate → compile → get_srs (+await) → setup
```

**三种隐私模式**：
- `all_public`: input=public, param=fixed (最快)
- `hashed`: input=Poseidon, param=hashed (1.9× 开销)
- `private`: input=private, param=fixed (最安全)

### 6.2 EZKL 证明生成（ezkl_prove）

**步骤**：
1. `gen_witness()` → witness.json (包含 processed_inputs/outputs)
2. `prove()` → proof.json (~1-5秒)
3. `verify()` → True/False (~30-80ms)

**输出**：proof_instances (ZKP 公开实例，proof linking 关键数据)

### 6.3 Worker 推理（_do_inference）

```python
hash_in = SHA256(input)
correct_output = onnxruntime.run(onnx, input)
hash_out = SHA256(correct_output)    # ← 永远基于正确结果
output_data = list(correct_output)
if fault_injected:
    output_data[0] += 999.0          # 只改返回值
```

**4 种攻击**：tamper / skip / random / replay

**Proof-bound output（核心安全机制）**：

- **Worker 侧**：`/infer` 端点的 `output_data` 从 proof 的 `pretty_public_inputs.rescaled_outputs` 提取，替代 onnxruntime 独立推理的浮点结果。
- **Master 侧**：Master 独立从 proof 中提取 `rescaled_outputs`，与 Worker 声称的 `output_data` 交叉比对，并使用 proof 绑定的输出作为下游 Worker 的输入。
- **效果**：proof 验证通过 ⟹ `output_data` 必然正确。恶意 Worker 无法生成通过验证的 proof 同时返回篡改的输出。证明节点上的篡改被**预防**（prevention），光节点上的篡改被**检测**（detection）。

### 6.4 Master 流水线（run_pipeline）

```
for worker in workers:
  [POST /infer 或 /infer_light]
  L1 校验: output 完整性
  L2 校验: proof linking
  L3 校验: 哈希链
  current_input = output

流水线结束:
  随机挑战: /re_prove
  首节点验证
  汇总 metrics
```

---

## 七、安全模型

### 7.1 对手模型

- 控制 $k$ 个 Worker ($k < N$)
- 知道验证策略（adaptive）
- Master 可信（中心化 assumption）

| 对手类型 | 检测率 |
|---|---|
| 独立恶意 | ✅ 100%（proof 节点：proof-bound 预防；light 节点：L1 检测 + 随机挑战） |
| 相邻合谋 | ⚠️ 概率性 |
| 全局合谋 | ⚠️ 概率性 |

### 7.2 安全定理

**定理 1**：在 edge_cover ($g=1$) 下，攻击段 $\ell$ 的逃逸概率 ≤ $0.5^{\lfloor \ell/2 \rfloor}$

**定理 2**：独立恶意节点 $P_{detect} = 1.0$

**定理 3**：整体 $P_{detect} \geq 1 - P_{escape} \cdot (1 - P_{challenge})$

---

## 八、实验设计与结果

### 8.1 实验总概览（43+组实验）

| 阶段 | 内容 | 组数 |
|---|---|:---:|
| 阶段3 | {2,4,8}s × {正常,故障} | 6 |
| P1 | {4,8}s × {100%,50%,25%} × {正常,故障} | 12 |
| P3 | 4攻击 × {100%,50%} | 8 |
| P2 | 3隐私模式 × 3试验 | 9 |
| P4+P6 | 保真度+ZK链 | 8+ |

### 8.2 关键数据

**P1 选择性验证** (8 切片)：
| 验证率 | 开销 | 安全结果 |
|:---:|:---:|:---:|
| 100% | 13,627ms | 篡改被预防 |
| 50% | 8,367ms (-39%) | 篡改被预防 |
| 25% | 8,266ms (-39%) | 篡改被预防 |

> proof 节点上的篡改被 proof-bound output 机制**预防**（prevention）而非仅检测。实验脚本覆盖 L1+L3 校验。

**P2 隐私模式**：
| 模式 | 开销 | 倍数 |
|:---:|:---:|:---:|
| all_public | 5,575ms | 1.0× |
| hashed | 10,579ms | 1.9× |
| private | 5,294ms | 0.95× |

**P4 保真度**：L1=0, L2=0（bit-exact 无损）

---

## 九、环境配置指南

### 9.1 安装 Miniconda

```powershell
winget install --id Anaconda.Miniconda3 -e --silent
```

### 9.2 接受 Conda ToS

```powershell
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
& $PY -m conda tos accept --override-channels
```

### 9.3 安装依赖

```powershell
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
& $PY -m pip install ezkl torch onnx onnxscript psutil fastapi uvicorn requests onnxruntime
```

### 9.4 验证

```powershell
& $PY -c "import ezkl; print('ezkl', ezkl.__version__)"
```

---

## 十、运行指南

### 10.1 环境变量

```powershell
$env:PYTHONIOENCODING = "utf-8"
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
```

### 10.2 各阶段命令

```powershell
# 阶段1
& $PY -u scripts/run_single_machine_demo.py

# 阶段2
& $PY -u scripts/run_stage2.py

# 阶段3
& $PY -u scripts/run_experiments.py

# P1+P3
& $PY -u scripts/run_advanced_experiments.py

# P2
& $PY -u scripts/run_p2_experiment.py

# P4+P6
& $PY -u scripts/run_p4_p6_experiment.py
```

### 10.3 后台执行

```powershell
$p = Start-Process -FilePath $PY -ArgumentList "-u","scripts\run_experiments.py" `
    -WorkingDirectory "C:\ZKP" -PassThru -WindowStyle Hidden
Write-Output "PID: $($p.Id)"
```

### 10.4 手动启动 Worker+Master

```powershell
# Worker 1
Start-Process $PY -ArgumentList "-u","distributed\worker.py","--slice-id","1","--port","8001","--onnx","models\slice_1.onnx","--cal","models\slice_1_cal.json"

# Worker 2
Start-Process $PY -ArgumentList "-u","distributed\worker.py","--slice-id","2","--port","8002","--onnx","models\slice_2.onnx","--cal","models\slice_2_cal.json"

# 等待初始化
Start-Sleep -Seconds 30

# Master
& $PY -u distributed/master.py --verify-ratio 0.5 --verify-strategy edge_cover
```

---

## 十一、已解决的工程问题

| 问题 | 解决 |
|---|---|
| Windows 无 HOME 变量 | `os.environ.setdefault("HOME", Path.home())` |
| GBK 编码崩溃 | `PYTHONIOENCODING=utf-8` |
| 嵌套事件循环 | `ezkl_init()` 在 `uvicorn.run()` 前 |
| 子进程阻塞 | `stdout=DEVNULL` |
| health check 超时 | 增加 `requests.Timeout` |
| Conda ToS 未接受 | `conda tos accept` |

---

## 十二、系统限制与未来方向

### 12.1 现有限制

| 限制 | 缓解 |
|---|---|
| Master 中心化（Master 执行所有验证，被攻破则全部失效） | 去中心化 verifier / 链上合约 / 独立审计节点 |
| light 节点 L1 可被同时伪造 output+hash 绕过 | 随机挑战 re_prove（概率性威慑） |
| 跨节点中间数据缺少原像承诺 | polycommit/swap_proof_commitments |
| light 节点可被合谋 | 边覆盖 + 随机挑战 |
| proof 数 O(N) | Nova/SuperNova IVC |
| 无数据隐私 | MPC/HE（超出范畴） |
| 模型规模小 | 升级 CNN |
| 实验脚本走简化管线（L1+L3），未走 Master 完整逻辑（无独立 proof verify、无 L2 linking、无随机挑战） | 实验指标（开销、检测率）反映 L1+L3 覆盖范围，完整校验效果需参考 Master 设计 |
| L2 proof linking 仅在 hashed 模式下提供密码学级跨切片约束；all_public 模式下由于各切片独立量化参数（input_scale/param_scale）致 processed_inputs ≠ processed_outputs 而不适用 | 使用 hashed 模式运行 L2 linking |

### 12.2 未来方向

1. 形式化安全证明
2. CNN/MNIST 实验
3. IVC 对接 (Nova)
4. 链上验证
5. 去中心化 Master
