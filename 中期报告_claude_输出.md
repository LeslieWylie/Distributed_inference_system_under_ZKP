# 中期答辩 PPT 完整文案

> 基于 GPT 设计说明书生成，所有数据来自 `metrics/` 目录最新实验结果。
> 图表已生成于 `figures/` 目录。

---

## 第 1 页：封面

### 标题
**基于零知识证明的分布式推理可验证性研究**

### 副标题
中期答辩

### 信息
- 学生：武垚乐
- 指导教师：（填写）
- 学院：（填写）
- 日期：2026 年 3 月

---

## 第 2 页：课题背景与研究目标

### 页面标题
课题背景与研究目标

### 左侧：背景

- 大模型推理开销持续上升，单节点推理面临算力和时延瓶颈
- 分布式推理成为提升吞吐与可扩展性的自然选择
- 多节点协同推理场景中，节点可能不完全可信，推理正确性缺乏保障
- 零知识证明为"在不泄露细节的前提下验证计算正确性"提供了可能

### 右侧：任务书三项目标

1. **分布式推理原型**：实现模型切片 + 多 Worker 流水线推理
2. **可验证计算机制**：引入零知识证明（EZKL / Halo2 / PLONK）验证推理正确性
3. **低开销优化**：在保证检测能力的同时降低证明开销

### 本页结论
> 本课题聚焦"分布式推理 + 可验证计算 + 低开销优化"，研究方向与任务书高度一致。

---

## 第 3 页：当前项目进展概况

### 页面标题
当前项目进展概况

### 三栏结构

#### 已完成 ✅
- 分布式推理原型搭建完成（Master + Worker FastAPI 架构）
- 模型按层切片，支持 2/4/8 切片配置
- EZKL (Halo2/PLONK/KZG) 局部零知识证明接入完成
- 三层分层验证逻辑实现（L1 哈希 + L2 proof linking + L3 哈希链）
- Proof-bound output 机制实现（proof 与推理输出数学绑定）
- 选择性验证 + edge-cover 策略实现
- 三种可见性模式支持（all_public / hashed / private）
- 多组阶段性实验已跑通并有数据

#### 正在完善 🔧
- 主系统与简化实验脚本的统一
- 实验口径与 `actual_proof_fraction` 对齐
- 答辩材料与图表整理

#### 下一步 📋
- 统一实验管线
- 收紧论文表述
- 完善性能与安全评估
- 论文写作

### 图表建议
放一张阶段进度图（手绘或 draw.io）：
- 阶段 1：单机最小验证 ✅
- 阶段 2：Master/Worker 分布式原型 ✅
- 阶段 3：多切片基础实验 ✅
- P1：选择性验证 ✅
- P2：可见性模式对比 ✅
- P3：攻击检测 ✅
- P4/P6：补充评估 ✅

### 本页结论
> 项目已进入"研究原型基本完成、正在做实验和论文收口"的阶段。

---

## 第 4 页：系统总体架构

### 页面标题
系统总体架构设计

### 架构图内容（需手绘或 draw.io）

```
Input → Master (调度+校验)
          │
          ├── Worker 1 (/infer 或 /infer_light) → Slice 1 (ONNX+EZKL)
          │     ↓ output_1
          ├── Worker 2 (/infer 或 /infer_light) → Slice 2 (ONNX+EZKL)
          │     ↓ output_2
          ├── ...
          └── Worker N (/infer 或 /infer_light) → Slice N (ONNX+EZKL)
                ↓ final_output

校验路径:
  L1: SHA-256(output) vs hash_out     — 每个节点
  L2: proof linking (Poseidon 哈希)    — 相邻 proof 节点
  L3: prev.hash_out == curr.hash_in   — 全链路
  Challenge: /re_prove                 — light 节点随机挑战
```

### 文字说明
- Master 负责推理调度、验证策略选择和结果汇总
- Worker 负责局部子模型推理与局部证明生成
- proof 节点走 `/infer`（完整 ZKP 证明）
- light 节点走 `/infer_light`（仅推理+哈希，低开销）
- 对部分 light 节点触发 `/re_prove` 进行可追溯随机挑战

### 本页结论
> 系统采用 Master-Worker 分层架构，将分布式推理与多层验证机制结合。

---

## 第 5 页：核心验证机制

### 页面标题
分层可验证机制设计

### 层 1：外部完整性检查（L1）
- 通过 SHA-256 哈希对单节点输入/输出进行快速完整性检测
- 成本低，对非协同故障有效
- 对恶意节点（可同时伪造 output+hash）不具备对抗能力

### 层 2：Proof 节点验证与 Linking（L2）
- proof 节点使用 EZKL 在 Halo2 算术电路内生成零知识证明
- Master 本地独立验证 proof（`ezkl.verify()`）
- **Proof-bound output**：推理输出从 proof 公开实例提取，与 proof 数学绑定
- 相邻 proof 节点通过 `processed_outputs == processed_inputs` 进行状态一致性约束（hashed 模式下为密码学级）

### 层 3：跨节点哈希链（L3）
- 前一节点 `hash_out` 与后一节点 `hash_in` 对齐
- 用于发现跨节点链路异常（consistency check，非对抗安全）

### 补充：随机挑战
- 对 light 节点发起基于 `request_id` 的可追溯随机挑战（`/re_prove`）
- 提供概率性威慑

### 本页结论
> 当前系统不是单一验证机制，而是 proof 节点强验证与 light 节点轻量检查结合的分层验证框架。

---

## 第 6 页：模块实现与当前完成情况

### 页面标题
模块划分与当前实现情况

### 模块表格

| 模块 | 功能 | 当前状态 |
|---|---|---|
| `distributed/master.py` | 调度、三层校验、随机挑战、proof-output 绑定验证 | ✅ 已完成 |
| `distributed/worker.py` | 子模型推理、`/infer` `/infer_light` `/re_prove` | ✅ 已完成 |
| `common/utils.py` | EZKL 初始化/prove/verify、哈希、指标采集 | ✅ 已完成 |
| `models/configurable_model.py` | 可配置 N 层模型切片与 ONNX 导出 | ✅ 已完成 |
| `scripts/run_stage2.py` | 主系统功能验证（完整 Master 逻辑） | ✅ 已完成 |
| `scripts/run_experiments.py` | 基础性能评估（简化管线，L1+L3） | ✅ 已完成 |
| `scripts/run_advanced_experiments.py` | 选择性验证/攻击实验（简化管线） | ✅ 已完成 |
| `scripts/run_p2_experiment.py` | 可见性模式对比 | ✅ 已完成 |
| `scripts/run_p4_p6_experiment.py` | 补充评估（保真度 + 完整性机制对比） | ✅ 已完成 |
| `tests/test_core_semantics.py` | 18 项回归测试 | ✅ 全部通过 |

### 说明
- 主系统（master.py + worker.py）包含完整三层校验 + 随机挑战 + proof-bound output
- 部分实验脚本为"简化评估管线"，主要用于性能趋势分析，已在代码和文档中明确标注

### 本页结论
> 当前项目已形成完整的代码模块体系，具备继续开展系统实验与论文写作的基础。

---

## 第 7 页：主系统运行证据

### 页面标题
主系统运行情况与阶段性证据

### 图表：主图使用 `fig01_latency_breakdown.png`，补充图使用 `fig07_proof_bound.png`

### 讲解建议
- 正文主图优先放 `fig01_latency_breakdown.png`，用于说明 2/4/8 切片均可跑通，且主要开销来自 proof generation
- 如果页面空间允许，可在右下角放 `fig07_proof_bound.png` 小图，说明 proof-bound output 对故障注入的预防效果
- `fig02_per_slice_8s.png` 更适合备份页，用于回答“8 切片下各 slice 开销是否均衡”这类追问

### 运行结果表格

| 切片数 | 是否跑通 | 是否产生最终输出 | e2e 时延 (ms) | proof 总计 (ms) | verify 总计 (ms) | 故障处理 |
|:---:|:---:|:---:|---:|---:|---:|:---:|
| 2 | ✅ | ✅ | 2,414 | 2,236 | 117 | 正常运行 |
| 2 | ✅ | ✅ | 2,259 | 2,045 | 99 | **篡改被预防** |
| 4 | ✅ | ✅ | 6,090 | 5,338 | 364 | 正常运行 |
| 4 | ✅ | ✅ | 5,682 | 5,296 | 219 | **篡改被预防** |
| 8 | ✅ | ✅ | 12,042 | 10,411 | 824 | 正常运行 |
| 8 | ✅ | ✅ | 11,060 | 10,315 | 435 | **篡改被预防** |

> 数据来源：`metrics/stage3_experiments.json`，简化评估管线。
> 故障注入在最后一个切片，被 proof-bound output 机制预防（篡改的输出被 proof 绑定的正确输出替代）。

### 关键观察
- 端到端时延随切片数近似线性增长（2s: 2.4s → 4s: 6.1s → 8s: 12.0s）
- 证明生成时间占比约 85%，验证开销仅 ~7%
- 所有配置均成功完成推理链路并产生最终输出

### 本页结论
> 系统在 2/4/8 切片下均成功完成分布式推理与分层验证流程，具备完整运行能力。

---

## 第 8 页：低开销优化 — 选择性验证结果

### 页面标题
选择性验证的低开销效果

### 图表：使用 `fig03_selective_verification.png` + `fig10_cost_reduction.png`

### 讲解建议
- 正文主图优先放 `fig03_selective_verification.png`，三张子图已经足够完整
- `fig10_cost_reduction.png` 适合作为同页右下角补充图，突出“约 36% 到 42% 的端到端开销降低”

### 核心概念说明
- **请求验证比例** (`verify_ratio`)：用户配置的参数
- **实际 proof 覆盖率** (`actual_proof_fraction`)：edge-cover 策略后的实际值
- 两者不相等！edge-cover 保证每条边至少一端有 proof 节点

### 结果表格 — 8 切片

| 请求验证比例 | 实际 proof 覆盖率 | e2e (ms) | proof 总计 (ms) | verify 总计 (ms) | 开销降低 |
|:---:|:---:|---:|---:|---:|:---:|
| 100% | 100.0% | 15,526 | 13,627 | 882 | — |
| 50% | 62.5% | 9,367 | 8,367 | 417 | **39.7%** |
| 25% | 62.5% | 9,087 | 8,266 | 428 | **41.5%** |

### 结果表格 — 4 切片

| 请求验证比例 | 实际 proof 覆盖率 | e2e (ms) | proof 总计 (ms) | 开销降低 |
|:---:|:---:|---:|---:|:---:|
| 100% | 100.0% | 8,866 | 7,456 | — |
| 50% | 75.0% | 5,684 | 5,183 | **35.9%** |
| 25% | 75.0% | 5,556 | 5,110 | **37.3%** |

> 数据来源：`metrics/advanced_experiments.json`，正常模式（无故障注入）。
> edge-cover 策略使 8 切片在 50% 和 25% 请求比例下实际覆盖率均为 62.5%（首尾必选 + gap=1）。

### 本页结论
> 选择性验证机制已实现，验证预算降低时端到端开销显著下降（~40%），但结果应以实际 proof 覆盖率为准。

---

## 第 9 页：安全性验证 — 攻击检测结果

### 页面标题
攻击检测实验结果

### 图表：使用 `fig04_attack_handling.png`

### 讲解建议
- 该图表达的是“在当前攻击模型下，4 类攻击在 2 种验证预算下均被成功处理”，并用 `P` 标注“proof-bound prevention”
- 这里强调的是**处理成功**而不是泛化到“所有恶意行为均可检测”

### 攻击模型说明
- **当前攻击模型：响应层篡改** — Worker 正确计算但返回篡改输出
- 4 种攻击类型：`tamper`（+999.0）、`skip`（全零）、`random`（随机数）、`replay`（固定值 0.42）
- 每次仅注入 1 个恶意节点（独立恶意），位于切片 4（proof 节点位置）

### 结果表格 — 4 切片，故障 @切片 4

| 攻击类型 | verify_ratio=100% | verify_ratio=50% | 处理方式 |
|:---:|:---:|:---:|:---:|
| tamper | ✅ 成功 | ✅ 成功 | 预防 (proof-bound) |
| skip | ✅ 成功 | ✅ 成功 | 预防 (proof-bound) |
| random | ✅ 成功 | ✅ 成功 | 预防 (proof-bound) |
| replay | ✅ 成功 | ✅ 成功 | 预防 (proof-bound) |

> 数据来源：`metrics/advanced_experiments.json`。
> 由于故障节点（切片 4）在所有配置下均为 proof 节点（首尾必选），篡改被 proof-bound output 机制**预防**而非事后检测。

### 重要限定
- 以上结论仅在**当前攻击模型（响应层篡改）与实验设置**下成立
- 故障节点恰好位于 proof 位置，因此全部被预防；若位于 light 位置，将依赖 L1 检测 + 随机挑战
- 不应表述为"系统对所有恶意行为都能 100% 处理"

### 本页结论
> 在当前攻击模型与实验设置下，对所测四类独立恶意攻击均成功处理。proof 节点上的篡改被 proof-bound 机制在源头预防。

---

## 第 10 页：可见性模式对比

### 页面标题
不同可见性模式的开销对比

### 图表：使用 `fig05_visibility_time.png` + `fig06_visibility_size.png`

### 讲解建议
- `fig05_visibility_time.png` 是正文主图，展示时间开销和标准差
- `fig06_visibility_size.png` 建议作为同页下半部分或右侧补充图，突出产物大小变化

### 模式定义

| 模式 | input_visibility | param_visibility | 说明 |
|---|---|---|---|
| all_public | public | fixed | 无隐私保护（基准） |
| hashed | hashed (Poseidon) | hashed | 输入和参数以 Poseidon 哈希暴露 |
| private | private | fixed | 输入完全不可见 |

### 性能对比（4 切片 × 3 次均值）

| 模式 | proof 总计 (ms) | verify 总计 (ms) | 开销倍数 | proof 大小 (KB) | witness 大小 (KB) |
|:---:|---:|---:|:---:|---:|---:|
| all_public | 6,886 | 309 | 1.00× | 75.8 | 9.9 |
| hashed | 11,108 | 414 | **1.61×** | 89.1 (+18%) | 11.3 (+14%) |
| private | 6,652 | 323 | 0.97× | 71.2 | 9.9 |

> 数据来源：`metrics/p2_visibility_modes.json`，每种模式 3 次重复取均值。

### 关键发现
- hashed 模式约 1.6× 开销，源于 Poseidon 哈希电路额外约束
- private 模式与 all_public 相当，因不引入额外电路计算
- proof 大小：hashed 模式增加 ~18%

### 本页结论
> 系统已具备比较不同可见性模式开销差异的能力，为后续隐私-性能权衡研究打下基础。

---

## 第 11 页：当前存在的问题与系统边界

### 页面标题
当前存在的问题与系统边界

### 左侧：当前问题

1. **实验管线未完全统一**：部分实验使用简化评估管线（L1+L3），未覆盖 Master 完整逻辑（无独立 proof verify、无 L2 linking、无随机挑战）
2. **`verify_ratio` 与 `actual_proof_fraction` 需统一表述**：请求验证比例 ≠ 实际 proof 覆盖率
3. **攻击场景不够完整**：当前实验中故障节点恰好均位于 proof 位置，缺少 light 节点被攻击的实验数据
4. **模型规模较小**：当前使用 480 参数 FC 模型，外部效度有限

### 右侧：系统边界

1. **Master 可信假设**：Master 执行所有验证，被攻破则防线失效
2. **light 节点非强密码学约束**：依赖 L1 外部检查 + 随机挑战，提供故障检测而非数学保证
3. **跨节点无原像承诺**：中间数据离开 proof 保护后为明文传输
4. **本地多进程模拟**：当前原型在单机上运行多进程，未跨网络部署
5. **攻击模型为响应层篡改**：不涉及恶意 prover 在电路内伪造计算

### 本页结论
> 当前系统已形成可运行研究原型，但仍存在实验统一性与安全边界需要进一步澄清的问题。

---

## 第 12 页：下一步工作计划

### 页面标题
下一步工作计划

### 近期工作（中期后 1-2 周）
- 统一实验管线：用 Master 完整逻辑重跑关键实验
- 补充 light 节点被攻击的实验场景
- 完善 `actual_proof_fraction` 相关结果展示

### 中期后工作（2-4 周）
- 收紧论文表述与边界说明
- 继续完善性能与安全评估
- （可选）补充 MNIST MLP 或更大模型实验

### 最终目标
- 形成完整实验矩阵
- 完成论文写作
- 完成终期答辩材料

### 本页结论
> 后续工作重点是统一实验口径、完善关键结果并收口论文表达。

---

## 第 13 页：总结页

### 页面标题
阶段性总结

### 已完成
- 分布式推理原型系统（Master + Worker FastAPI 架构）
- 基于 EZKL (Halo2/PLONK/KZG) 的局部零知识证明
- 三层分层验证机制（L1 + L2 + L3 + 随机挑战）
- Proof-bound output 绑定（proof 与推理输出数学绑定）
- 选择性验证与 edge-cover 策略（开销降低 ~40%）
- 三种可见性模式支持
- 四类攻击场景下的安全性验证
- 43+ 组阶段性实验数据

### 当前定位
> 已具备可运行、可验证、可测量的研究原型。不是最终闭合系统，但已具备明确技术路线和阶段性成果。

### 后续重点
- 统一实验脚本与主系统口径
- 重跑关键实验
- 收紧系统边界表述
- 推进论文与终期答辩准备

### 本页结论
> 课题主线已落实为可运行的研究原型，后续主要任务是完善实验与答辩材料收口。

---

# 备份页

---

## 备份页 A：切片逻辑一致性验证 (P4)

### 图表：使用 `fig08_p4_fidelity.png`

### 页面标题建议
切片逻辑一致性验证

| 切片数 | L1 距离 | L2 距离 | 最大绝对误差 | 相对误差 |
|:---:|:---:|:---:|:---:|:---:|
| 2 | 0.0 | 0.0 | 0.0 | 0.0 |
| 4 | 0.0 | 0.0 | 0.0 | 0.0 |
| 8 | 0.0 | 0.0 | 0.0 | 0.0 |

> 验证内容：PyTorch 切片串联输出 vs 完整 PyTorch 模型输出。
> 结论：PyTorch 层级切分为 bit-exact，不引入数值误差。
> 注：此为 PyTorch 切片一致性验证，非 ONNXRuntime/EZKL 量化路径保真度。

### 使用建议
- 该图适合放在备份页，不建议占用正文主图位置
- 因为所有值均为 0，表格形式比柱状图更合适，信息表达是正确的
- 答辩时若老师追问“切片本身是否引入误差”，再翻到这一页即可

---

## 备份页 B：三类完整性检查机制对比 (P6)

### 图表：使用 `fig09_p6_integrity.png`

| 机制 | 正常 proof 时间 (ms) | 相对 all_public 倍数 | 说明 |
|---|---:|:---:|---|
| 外部哈希链 (SHA-256, all_public) | 6,526 | 1.00× | 基准模式 |
| 电路内 Poseidon (hashed) | 11,138 | 1.71× | 额外 Poseidon 约束导致开销上升 |
| 完全隐私 (private) | 6,723 | 1.03× | 与基准接近 |

> 当前图 `fig09_p6_integrity.png` 仅展示**正常模式下三种机制的 proof 时间对比**。
> 不应表述为"完整 ZK linking 实证"，而应定位为"三种完整性检查机制的开销对比"。
> 若老师追问故障下各检查是否通过，可口头补充：由于 proof-bound output 已在源头预防篡改，故障注入场景下 external integrity / circuit verified / chain consistency 均为通过。

---

## 备份页 C：主系统与实验脚本关系说明

| 路径 | 用途 | 是否完整主系统 |
|---|---|:---:|
| `distributed/master.py + worker.py` | 主系统逻辑（三层校验+随机挑战+proof-bound）| ✅ 是 |
| `scripts/run_stage2.py` | 主系统功能验证 | ✅ 是 |
| `scripts/run_experiments.py` | 基础性能评估 | ❌ 简化管线 |
| `scripts/run_advanced_experiments.py` | 选择性验证/攻击评估 | ❌ 简化管线 |
| `scripts/run_p2_experiment.py` | 可见性模式对比 | 部分专项 |
| `scripts/run_p4_p6_experiment.py` | 补充评估 | 部分专项 |

---

# 图表清单（顶会风格，300 DPI）

| 图 | 文件 | PPT 位置 | 说明 |
|---|---|:---:|---|
| Fig.1 | `fig01_latency_breakdown.png` | **正文 p7** | Stacked bar: proof/verify/IO 开销分解 (2/4/8 切片) |
| Fig.2 | `fig02_per_slice_8s.png` | 备份页 | 逐切片 proof+verify 时间 (8 切片) |
| Fig.3 | `fig03_selective_verification.png` | **正文 p8** | 三合一 line chart: e2e / proof / actual fraction (4+8 切片) |
| Fig.4 | `fig04_attack_handling.png` | **正文 p9** | Grouped bar: 四类攻击 × 两种 verify ratio |
| Fig.5 | `fig05_visibility_time.png` | **正文 p10** | 三种模式 proof+verify 时间 (含 error bar) |
| Fig.6 | `fig06_visibility_size.png` | **正文 p10** | 三种模式 proof+witness 大小 (含 error bar) |
| Fig.7 | `fig07_proof_bound.png` | 正文 p7 右下角 / 备份页 | Normal vs fault-injected 对比 (proof-bound 预防) |
| Fig.8 | `fig08_p4_fidelity.png` | 备份页 A | 切片一致性表格 (全零) |
| Fig.9 | `fig09_p6_integrity.png` | 备份页 B | 三种完整性机制正常模式 proof 时间对比 |
| Fig.10 | `fig10_cost_reduction.png` | 正文 p8 右下角 / 备份页 | 横向柱状: 开销降低百分比汇总 |
| Fig.11 | `fig11_throughput.png` | 备份页 | 吞吐量 by 切片数 |

---

# 中期答辩讲稿提纲（8-10 分钟）

| 页 | 时长 | 讲什么 |
|:---:|:---:|---|
| p1 封面 | 15s | 课题名称，进入正题 |
| p2 背景 | 1min | 为什么做这个题目、任务书要求 |
| p3 进展 | 45s | 当前已做了什么、进度在哪里 |
| p4 架构 | 1min | 系统怎么工作的，数据流和验证流 |
| p5 验证机制 | 1min | 三层验证具体怎么实现 |
| p6 模块表 | 30s | 快速过——代码都写完了 |
| p7 运行证据 | 1min | 系统跑通了，看数据 |
| p8 选择性验证 | 1.5min | **重点**——低开销是核心创新，讲清 verify_ratio vs actual_proof_fraction |
| p9 攻击检测 | 1min | 四类攻击都能处理，解释 proof-bound output |
| p10 模式对比 | 45s | 不同隐私级别的开销差异 |
| p11 问题 | 45s | 主动暴露边界，体现研究诚实 |
| p12 下一步 | 30s | 后续计划 |
| p13 总结 | 15s | 收尾 |

---

# 口径建议

### 可以放心说的
- "系统已实现分布式推理原型并可正常运行"
- "选择性验证机制降低端到端开销约 40%"
- "proof 节点上的篡改被 proof-bound output 机制在源头预防"
- "hashed 模式约 1.6 倍开销，private 与 all_public 相当"
- "切片逻辑一致性验证误差为 0 (bit-exact)"

### 必须加限定条件的
- "在当前攻击模型（响应层篡改）与实验设置下"——所有检测/预防结论前必加
- "请求验证比例（verify_ratio）与实际 proof 覆盖率（actual_proof_fraction）需区分"——提到选择性验证时必加
- "本次实验数据来自简化评估管线，性能趋势结论有效，完整校验效果需参考主系统设计"——涉及 L2/随机挑战时必加
- "当前为本地多进程模拟的研究原型"——提到分布式时

### 不要说的
- ❌ "完整全局密码学验证"
- ❌ "所有节点都被零知识证明严格约束"
- ❌ "100% 检测所有恶意行为"
- ❌ "25% 验证率就只验证了 25% 节点"
- ❌ "系统已完全成熟"