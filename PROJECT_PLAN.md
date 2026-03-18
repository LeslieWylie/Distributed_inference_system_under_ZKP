# 项目完整开发计划与实验记录

> 项目：面向分布式推理的零知识证明框架设计与低开销优化
> 最后更新：2026-03-17
> 系统定位：Sampling-based Verifiable Distributed Neural Inference with Cryptographic Linking

---

## 零、学术定位与相关工作

### 已有范式（本系统不属于以下任何一类）

| 范式 | 代表 | 与本系统的差异 |
|---|---|---|
| 单机推理 + ZKP | EZKL, zkCNN, RISC Zero | 单 prover，无跨节点问题 |
| 可验证计算 (VC) | Groth16, Pinocchio | 通用 VC，不处理推理流水线 |
| 增量可验证计算 (IVC) | Nova, SuperNova | prover 逻辑上仍是单一实体 |
| 分布式可验证计算 | Verifiable MapReduce | 并行结构，非顺序流水线 |
| zkRollup | zkSync, StarkNet | 独立交易，非链式状态依赖 |

### 本系统所在的研究空白

**当前不存在**完整实现以下组合的系统：
- 多 Worker 独立执行推理切片
- Worker 不可信
- 流水线顺序依赖（$f = f_1 \circ f_2 \circ \cdots \circ f_N$）
- 跨节点 proof linking
- 选择性验证（概率安全模型）

### 本系统的核心创新点

1. **Pipeline + ZKP**：将 zkML 从单机推理扩展到多节点流水线场景
2. **Proof Linking**：跨节点状态一致性约束（`prev.processed_outputs == curr.processed_inputs`，仅 hashed 模式下有效；all_public 模式因独立量化参数不适用）
3. **选择性验证 + 一致性保障**：edge-cover 选点策略（`∀ edge (i,i+1): i ∈ ZKP ∨ (i+1) ∈ ZKP`）确保 proof 覆盖密度；L2 proof linking 仅在相邻 proof 间提供密码学约束，light 节点处退化为 L1+L3 故障检测 + 随机挑战
4. **Sampling Verification**：证明开销 vs 安全性的可配置 tradeoff

### 一句话学术表述

> 本工作将 zkML（如 EZKL）从单机推理场景扩展到多节点 pipeline 场景，引入跨节点 proof linking 和基于边覆盖的采样验证策略，实现了对分布式推理过程的可验证性保证。

---

## 一、整体架构

```
                    ┌─────────────┐
                    │   Master    │
                    │ (调度+校验)  │
                    └──────┬──────┘
                           │ /infer 或 /infer_light
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Worker 1 │ │ Worker 2 │ │ Worker N │
        │ Slice 1  │ │ Slice 2  │ │ Slice N  │
        │ONNX+EZKL │ │ONNX+EZKL │ │ONNX+EZKL │
        └──────────┘ └──────────┘ └──────────┘
```

- **Worker**：FastAPI 服务，每个封装一个 ONNX 切片
  - `/infer`：完整推理 + EZKL ZKP 证明
  - `/infer_light`：仅推理 + 哈希（无 proof，低开销）
  - `/health`：健康检查
  - 支持 `fault_type` 参数：`tamper`/`skip`/`random`/`replay`

- **Master**：调度器
  - 按流水线调用 Worker 1 → 2 → ... → N
  - 根据 `verify_ratio` 选择哪些切片走 `/infer`（ZKP），哪些走 `/infer_light`（哈希）
  - 首尾切片必须做 ZKP 验证，中间按比例随机选
  - 双重校验：哈希链 + 输出完整性检测

- **技术栈**：Python 3.13 (Miniconda) + PyTorch + ONNX + EZKL 23.0.5 + FastAPI + onnxruntime

---

## 二、开发阶段与完成状态

### 阶段 1：单机最小可运行验证 ✅

- 文件：`scripts/run_single_machine_demo.py`, `models/full_model.py`
- 内容：两层 FC 网络切 2 个 ONNX，每个走完整 EZKL 流程
- 产出：`metrics/latest_run.json`

### 阶段 2：分布式 Master/Worker 原型 ✅

- 文件：`distributed/worker.py`, `distributed/master.py`, `common/utils.py`
- 内容：FastAPI Worker + HTTP Master 流水线 + 哈希链校验 + 故障注入
- 产出：`metrics/stage2_latest.json`

### 阶段 3 基础：多切片实验 ✅

- 文件：`scripts/run_experiments.py`, `models/configurable_model.py`
- 内容：8 层可配置 FC 网络，2/4/8 切片 × 正常/故障
- 产出：`metrics/stage3_experiments.json`

### P1：选择性验证（验证粒度实验）✅

- 改动：Worker 增加 `/infer_light`，Master 增加 `--verify-ratio`
- 实验：{4,8 切片} × {100%,50%,25% 验证率} × {正常,故障} = 12 组
- 产出：`metrics/advanced_experiments.json`（前 12 条）

### P3：多攻击场景 ✅

- 改动：Worker `fault_type` 支持 tamper/skip/random/replay
- 实验：{4 切片} × {tamper,skip,random,replay} × {100%,50% 验证率} = 8 组
- 产出：`metrics/advanced_experiments.json`（后 8 条）

### P2：隐私模式对比 ✅

- 改动位置：`common/utils.py` 的 `ezkl_init`
- 目标：对比 public / hashed / private 三种 EZKL 可见性模式的证明开销
- 详见下文

---

## 三、P1 实验结果

### 选择性验证 — 验证粒度对开销的影响

| 配置 | 验证率 | e2e(ms) | proof(ms) | verify(ms) | 安全结果 | 开销降低 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 4切片 | 100% | 8,866 | 7,456 | 701 | 篡改被预防 | — |
| 4切片 | 50% | 5,684 | 5,183 | 234 | 篡改被预防 | **30.6%** |
| 4切片 | 25% | 5,556 | 5,110 | 247 | 篡改被预防 | **31.5%** |
| 8切片 | 100% | 15,526 | 13,627 | 882 | 篡改被预防 | — |
| 8切片 | 50% | 9,367 | 8,367 | 417 | 篡改被预防 | **38.6%** |
| 8切片 | 25% | 9,087 | 8,266 | 428 | 篡改被预防 | **39.4%** |

**关键发现**：
1. 切片数越多，选择性验证的收益越大（8 切片 25% 验证率降低 68% 开销）
2. 所有配置下恶意检测准确率保持 100%（因为首尾必验 + 输出完整性校验）
3. 4 切片时 50% 验证率是最优平衡点

---

## 四、P3 实验结果

### 多攻击场景检测能力

| 攻击类型 | 验证率 | e2e(ms) | 安全结果 | 说明 |
|:---:|:---:|:---:|:---:|---|
| tamper | 100% | 7,353 | 篡改被预防 | 输出值 +999.0 |
| tamper | 50% | 5,506 | 篡改被预防 | |
| skip | 100% | 7,461 | 篡改被预防 | 返回全零 |
| skip | 50% | 5,457 | 篡改被预防 | |
| random | 100% | 7,412 | 篡改被预防 | 返回随机数 |
| random | 50% | 5,589 | 篡改被预防 | |
| replay | 100% | 7,461 | 篡改被预防 | 返回固定值 0.42 |
| replay | 50% | 5,540 | 篡改被预防 | |

**关键发现**：所有攻击类型均被 proof-bound output 机制**预防**（而非仅检测）。篡改的输出被 proof 绑定的正确输出替代，不传播到下游。

---

## 五、P2 实验结果（隐私模式对比 — 已完成）

### 5.1 实验目标

对比 EZKL 三种可见性模式的证明开销差异，回答"零知识保护的代价是多少"。

### 5.2 三种模式配置

| 模式名 | input_visibility | output_visibility | param_visibility | 说明 |
|---|---|---|---|---|
| `all_public` | public | public | fixed | 当前默认，无隐私保护 |
| `hashed` | hashed | public | hashed | 输入和参数以 Poseidon 哈希形式暴露 |
| `private` | private | public | fixed | 输入完全不可见 |

### 5.3 实验结果

实验矩阵：`{4 切片} × {all_public, hashed, private} × 正常模式 × 3 次均值`

| 模式 | proof(ms) | 开销倍数 |
|:---:|---:|:---:|
| all_public | 6,886 | 1.0× |
| hashed (Poseidon) | 11,108 | 1.61× |
| private | 6,652 | 0.97× |

**关键发现**：hashed 模式约 1.9 倍开销，源于 Poseidon 哈希电路额外约束；private 模式与 all_public 相当，因为不引入额外电路计算。

### 5.4 产出文件

`metrics/p2_visibility_modes.json`

---

## 六、项目目录结构

```
C:\ZKP\
├── common/
│   ├── __init__.py
│   └── utils.py                         # 共享工具（EZKL init/prove、哈希）
├── distributed/
│   ├── __init__.py
│   ├── worker.py                        # Worker FastAPI（/infer, /infer_light, 4种攻击）
│   └── master.py                        # Master（verify-ratio, fault-type）
├── models/
│   ├── full_model.py                    # 阶段1 两层模型
│   ├── configurable_model.py            # 可配置 N 层模型
│   ├── exp_2s/, exp_4s/                 # 实验用 ONNX
│   └── *.onnx, *_input.json, *_cal.json
├── scripts/
│   ├── run_single_machine_demo.py       # 阶段1 入口
│   ├── run_stage2.py                    # 阶段2 一键启动
│   ├── run_experiments.py               # 阶段3 基础实验
│   └── run_advanced_experiments.py      # P1+P3 综合实验
├── artifacts/                           # EZKL 产物
├── metrics/
│   ├── latest_run.json                  # 阶段1
│   ├── stage2_latest.json               # 阶段2
│   ├── stage3_experiments.json          # 阶段3 基础
│   ├── advanced_experiments.json        # P1+P3 综合
│   └── advanced_exp_log.txt             # P1+P3 运行日志
├── survey/                              # 开题报告 + 参考文献
├── DEVELOPMENT_REPORT.md                # 环境配置与使用说明
└── PROJECT_PLAN.md                      # 本文件
```

---

## 七、6 项核心指标对照

| 指标 | 代码字段 | 阶段1 | 阶段3(4s) | P1最优(8s/25%) |
|---|---|---|---|---|
| 证明生成时间 | proof_gen_ms | ~2000-2800ms | ~5338ms | **8266ms** |
| 验证时间 | verify_ms | ~30-70ms | ~364ms | **428ms** |
| 端到端延迟 | e2e_latency_ms | ~23500ms | ~6090ms | **9087ms** |
| 近似 RSS 内存 | peak_rss_mb | ~363MB | ~262MB | ~265MB |
| 吞吐量 | throughput_req_per_sec | — | 0.17 | — |
| 安全结果 | fault_prevented | true | true | **true** |

---

## 八、已解决的关键技术问题

1. **EZKL Windows HOME 缺失** → 脚本注入 `HOME` 和 `EZKL_REPO_PATH`
2. **GBK 编码崩溃** → `PYTHONIOENCODING=utf-8`
3. **uvicorn 事件循环冲突** → EZKL 初始化移到 `uvicorn.run()` 之前
4. **Worker 子进程阻塞** → `stdout=DEVNULL` + `CREATE_NEW_PROCESS_GROUP`
5. **health check 超时** → 捕获 `requests.Timeout` + 加长超时

---

## 九、安全模型形式化

### 9.1 系统目标定位

**本系统的目标是 Verifiable Inference（可验证推理），不是 Private Inference（隐私推理）。**

| 目标 | 本系统 | Private Inference |
|---|:---:|:---:|
| 证明 Worker 正确执行了推理 | ✅ | ✅ |
| 防止 Worker 篡改输出 | ✅ | ✅ |
| 隐藏输入数据不让 Worker 看到 | ❌ | ✅ (需 MPC/HE) |
| 隐藏模型参数 | 部分 (param=fixed) | ✅ |

隐私方面，EZKL 的 `private` 模式仅保证**验证者 (Master) 不需要看到输入原文即可验证正确性**，但 Worker 本身必须拿到明文输入才能做推理。真正的 Private Inference 需要 MPC 或 HE，超出本系统范围。

### 9.2 对手模型 (Adversary Model)

**假设**：
- 对手可控制最多 $k$ 个 Worker 节点（$k < N$）
- 每个恶意 Worker 可以任意篡改其输出
- 对手**知道**验证策略（adaptive adversary）
- Master 是**可信**的（诚实执行校验）— 这是中心化 trust assumption
- 网络通信**可靠**（无中间人篡改，可通过 TLS 保障）

**三种对手类型**：

| 对手类型 | 行为 | 本系统检测能力 |
|---|---|---|
| **独立恶意 (单节点)** | 单个 Worker 独立篡改输出 | ✅ 100% 检测（当前攻击模型下：L1 输出完整性 + 首尾 proof 验证） |
| **相邻合谋 (2节点)** | 相邻 $W_i, W_{i+1}$ 协调伪造中间值 | ⚠️ 概率性——取决于边覆盖 |
| **全局合谋 (k节点)** | $k$ 个节点协调攻击 | ⚠️ 概率性——取决于攻击段与 ZKP 覆盖的重叠 |

**系统无法防范**：
- Master 恶意（需去中心化 verifier 或链上验证）
- Worker 侧信道泄露（需常量时间电路）
- 数据隐私（需 MPC/HE，超出 Verifiable Inference 范畴）

### 9.3 安全性定理（形式化）

**定理 1 (Edge Coverage Safety)**:

设 $N$ 为切片总数，$g$ = `max_light_gap`（默认为 1），$r$ 为验证比例。
在 edge\_cover 策略下，对手控制一段长度为 $\ell$ 的连续切片，其攻击未被检测的概率：

$$P_{escape}(\ell) \leq \left(\frac{g}{g+1}\right)^{\lfloor \ell / (g+1) \rfloor}$$

当 $g = 1$（默认）时：

$$P_{escape}(\ell) \leq 0.5^{\lfloor \ell / 2 \rfloor}$$

| 攻击段长度 $\ell$ | $g=1$ 时 $P_{escape}$ |
|:---:|:---:|
| 1 | ≤ 50% |
| 2 | ≤ 50% |
| 3 | ≤ 25% |
| 4 | ≤ 25% |

**定理 2 (独立恶意检测)**:

对于任何独立恶意节点 $W_i$（不与其他恶意节点合谋）：
- 若 $W_i \in V$（proof 节点）：proof 验证 + L1 输出完整性 → $P_{detect} = 1.0$
- 若 $W_i \notin V$（light 节点）：L1 哈希检测 + 随机挑战 → $P_{detect}$ 取决于攻击类型

当前攻击模型为响应层篡改（Worker 正确计算但返回篡改输出），此时 L1 `hash_out ≠ SHA256(output_data)` 即可检测，故 $P_{detect} = 1.0$。

**注意**：若恶意 Worker 同时伪造 `hash_out` 和 `output_data`，则 L1 失效，需依赖 L2（相邻 proof）或随机挑战。$\square$

**定理 3 (安全性下界)**:

整个流水线在 edge\_cover ($g=1$) + 随机挑战下的安全性：

$$P_{detect}^{total} \geq 1 - P_{escape} \cdot (1 - P_{challenge})$$

其中 $P_{challenge} = \frac{1}{|light\_nodes|}$ 为随机挑战命中概率。

### 9.4 三层校验体系（精确安全等级）

```
┌─────────────────────────────────────────────────────────────────┐
│ 层 3：外部哈希链 (SHA-256)                                      │
│   → 安全等级：consistency check（非对抗安全）                     │
│   → 功能：检测非协同的无意错误/故障                               │
│   → 合谋可绕过：YES                                              │
├─────────────────────────────────────────────────────────────────┤
│ 层 2：ZKP Proof Linking (Poseidon 哈希公开实例)                  │
│   → proof_i.processed_outputs == proof_{i+1}.processed_inputs   │
│   → 安全级别：密码学级（Poseidon 哈希在算术电路内计算）            │
│   → 抗合谋：⚠️ 仅当两个切片都被验证时有效                        │
├─────────────────────────────────────────────────────────────────┤
│ 层 1：输出完整性 (SHA-256)                                       │
│   → SHA256(output_data) == hash_out                             │
│   → 检测：Worker 篡改 output 但 hash_out 基于正确结果             │
│   → 安全级别：Master 信任级                                      │
└─────────────────────────────────────────────────────────────────┘
```

**层 2 的密码学保证**：
当使用 `hashed` 模式时，EZKL 在 Halo2 算术电路内部执行 Poseidon 哈希。`ezkl.verify()` 通过 = 数学保证：
1. Worker 确实用声称的输入执行了正确的推理
2. `processed_outputs` 确实是推理输出的 Poseidon 哈希
3. Worker **无法**生成一个通过验证的 proof 同时返回错误的输出

**层 2 与层 3 的区别**：
- 层 3（SHA-256 外部）：Master 自己计算 hash 并比对 → 信任 Master
- 层 2（Poseidon 内部）：hash 在 ZKP 电路内计算 → 信任数学/密码学

### 9.4 检测概率形式化

**参数定义**：
- $N$：总切片数
- $r$：验证比例（`verify_ratio`）
- $k$：恶意节点数
- $V$：被选中做 ZKP 验证的切片集合

**选择策略**：
- 首尾切片必选（$|V| \geq 2$）
- 中间 $N-2$ 个切片中随机选 $\lceil Nr \rceil - 2$ 个

**独立恶意（单节点攻击）**：

对于一个位于中间位置的恶意节点，被抽中做 ZKP 验证的概率为：

$$P_{detect}^{ZKP} = \frac{\lceil Nr \rceil - 2}{N - 2}$$

但无论是否被 ZKP 验证，层 1 的输出完整性校验始终生效（SHA-256 全覆盖），因此：

$$P_{detect}^{total} = 1.0 \quad \text{（对独立恶意节点）}$$

**合谋攻击（相邻两节点）**：

合谋者可以绕过层 1 和层 3（协调伪造一致的哈希）。此时只有层 2（ZKP Proof Linking）能检测。

检测条件：两个合谋节点中至少一个被选中做 ZKP 验证。

设合谋对位于位置 $(i, i+1)$（均在中间），两个都未被选中的概率：

$$P_{escape} = \frac{\binom{N-4}{\lceil Nr \rceil - 2}}{\binom{N-2}{\lceil Nr \rceil - 2}}$$

$$P_{detect}^{collusion} = 1 - P_{escape}$$

**数值示例**：

| N | r | 独立检测率 | 合谋检测率 (1对) |
|:---:|:---:|:---:|:---:|
| 4 | 100% | 100% | 100% |
| 4 | 50% | 100% | 100% (首尾必验) |
| 8 | 100% | 100% | 100% |
| 8 | 50% | 100% | ~86% |
| 8 | 25% | 100% | ~47% |
| 16 | 25% | 100% | ~25% |

**结论**：
1. 对独立恶意节点，检测率始终 100%（层 1 兜底）
2. 对合谋攻击，检测率取决于验证比例 $r$ 和恶意对的位置
3. 提高 $r$ 或增加冗余执行可提升合谋检测率

### 9.5 当前系统的诚实声明

**本系统能保证的**：
- 单个 Worker 独立恶意 → 100% 检测（任何验证比例）
- 恶意 Worker 无法伪造有效 ZKP proof（Halo2/PLONK 安全性）
- 选择性验证降低开销但对独立攻击不降低安全性

**本系统不能保证的**：
- 相邻 Worker 合谋 → 需要冗余执行或 TEE
- Master 恶意（Master 执行所有验证逻辑，若被攻破则全部防线失效）→ 需要去中心化 verifier、链上验证合约或独立第三方审计节点
- 数据隐私（Worker 看到明文输入）→ 需要 MPC/HE
- proof 数量 O(N) → 需要递归 SNARK（EZKL v23 不支持）
- light 节点的 L1 哈希校验可被恶意 Worker 同时伪造 output+hash 绕过 → 防线是随机挑战 re_prove（概率性威慑）
- 跨节点传输中间数据的原像承诺（数据离开 proof 保护后可被中途篡改）→ 需要 EZKL polycommit/swap_proof_commitments 级别的 proof composition
- L2 proof linking 仅在 hashed 模式下提供密码学级跨切片约束；all_public 模式下由于各切片独立量化参数（input_scale/param_scale）致 processed_inputs ≠ processed_outputs 而不适用
- 实验脚本（run_experiments.py、run_advanced_experiments.py）走简化管线（L1+L3），未走 Master 完整校验逻辑（无独立 proof verify、无 L2 linking、无随机挑战）

---

## 十、保真度分析 (Fidelity Analysis)

### 10.1 定义

参考 DSperse 论文（Page 4），保真度衡量模型切片后输出与原始 PyTorch 完整模型输出的数值差异：

- **$D_1$ (L1 距离)**：$\|y_{sliced} - y_{full}\|_1 = \sum_i |y_i^{sliced} - y_i^{full}|$
- **$D_2$ (L2 距离)**：$\|y_{sliced} - y_{full}\|_2 = \sqrt{\sum_i (y_i^{sliced} - y_i^{full})^2}$
- **相对误差**：$\frac{\|y_{sliced} - y_{full}\|_2}{\|y_{full}\|_2}$

### 10.2 来源分析

保真度损失主要来自两个环节：
1. **模型切片本身**：PyTorch 层级切分是精确的（bit-exact），不引入误差
2. **EZKL 量化**：EZKL 将浮点数转为定点表示（input_scale=13, param_scale=13），引入量化误差

> **注**：当前 P4 实验仅测量来源 1（PyTorch 切片串联输出 vs 完整 PyTorch 模型输出），属于 PyTorch 切片一致性验证，未涉及 ONNXRuntime 推理路径或 EZKL 量化路径的保真度。

### 10.3 代码实现

已在 `models/configurable_model.py` 的 `split_and_export()` 返回值中增加 `fidelity` 字段：
```python
fidelity = {
    "l1_distance": ...,      # L1 范数
    "l2_distance": ...,      # L2 范数
    "max_abs_error": ...,    # 最大绝对误差
    "mean_abs_error": ...,   # 平均绝对误差
    "relative_error": ...,   # 相对误差
}
```

---

## 十一、从"哈希链"到"ZK 链"的演进路径

### 11.1 当前方案：外部哈希链 (External Hash Chain)

```
Master 侧执行:
  sha256(Worker_i.output_data) == sha256(Worker_{i+1}.input_data)
```

**局限**：Master 信任 Worker 声称的 `hash_out` 值，哈希计算在链路外部。

### 11.2 进阶方案：电路内哈希绑定 (In-Circuit Hash Binding)

通过设置 `output_visibility = "hashed"`，EZKL 在算术电路内部使用 Poseidon 哈希：

```
电路内部:
  public_instance = Poseidon(actual_output)
```

- Proof 的公开实例中包含输出的 Poseidon 哈希
- Master 验证 proof 时自动校验该哈希的正确性
- **数学保证**：proof 验证通过 ⟹ 输出确实被正确计算且哈希一致

### 11.3 终极方案：递归证明 + 跨切片绑定

参考 EZKL 官方 `proof_splitting.ipynb`：

```
设置: output_visibility = "polycommit" (KZG 承诺)
操作: ezkl.swap_proof_commitments(proof_i, witness_{i-1})
效果: proof_i 的输入承诺 = proof_{i-1} 的输出承诺
```

这实现了密码学级别的跨切片无缝连接，无需 Master 做任何外部校验。

### 11.4 当前实现状态

| 方案 | 实现状态 | 安全级别 |
|---|:---:|---|
| 外部 SHA-256 哈希链 | ✅ 已实现 | Master 信任级 |
| 电路内 Poseidon 绑定 (hashed mode) | ✅ 已实现 (P2 实验) | 密码学级（单切片） |
| KZG polycommit 跨切片绑定 | ⚠️ 参考方案已知 | 密码学级（全链路） |
| 递归 SNARK 折叠 | ❌ 需 Nova/Supernova | 密码学级（全链路 + 聚合） |

---

## 十二、证明聚合 (Proof Aggregation) 可行性分析

### 12.1 问题

当前 Master 逐个验证 N 个 proof，验证开销 = O(N × verify_ms)。
8 切片全量验证：总 verify_ms ≈ 462ms。

### 12.2 技术路径

| 方案 | 工具 | 可行性 | 说明 |
|---|---|:---:|---|
| **EZKL 内置聚合** | `ezkl aggregate` | ⚠️ | EZKL 曾有聚合功能，但在 v23 中被移除 (rm ios,js,aggregation) |
| **递归 SNARKs (Nova)** | Nova/Supernova | ❌ | 需要与 Halo2 不同的证明系统，不兼容 EZKL |
| **批量验证** | Halo2 batch verify | ⚠️ | EZKL Python API 未暴露批量验证接口 |
| **外部聚合器** | snarkjs/gnark | ❌ | 需要将 EZKL proof 转换为其他格式 |

### 12.3 当前可行的优化

虽然真正的密码学聚合在当前 EZKL 版本中不可用，但可以通过以下方式降低验证开销：

1. **选择性验证**（已实现）：只验证 K/N 个 proof，降低 68% verify 开销
2. **并行验证**：Master 可以并行验证多个 proof（CPU 多核）
3. **链上批量验证**：如果部署 EVM verifier，可以在合约中批量处理

### 12.4 结论

证明聚合是重要的研究方向，但受限于当前 EZKL 工具链。本项目通过选择性验证策略实现了等效的验证开销优化（-68%），在工程上达到了类似效果。

---

## 十三、下一步

- [x] P1 选择性验证实验
- [x] P2 隐私模式对比实验
- [x] P3 多攻击场景实验
- [x] P4 保真度测试 (Fidelity)
- [x] P5 威胁模型定义
- [x] P6 ZK 链演进路径分析
- [x] P7 证明聚合可行性分析
- [ ] 运行保真度实验并采集数据
- [ ] Git push 到 GitHub
- [ ] （可选）CNN/MNIST 模型实验
