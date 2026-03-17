# DistriZKP 研究综述与工程边界整合笔记

最后更新：2026-03-18

## 1. 目的

本文档用于把三类知识沉淀到当前仓库中：

1. 最新参考论文中的可直接复用结论。
2. 本地外部资料库中的工程与安全约束。
3. 当前 DistriZKP 源码已经实现、但论文中需要谨慎表述的边界。

该文档的作用不是替代正式论文，而是作为论文写作、系统说明、答辩表述和后续开发的统一基线。

## 2. 参考来源

### 在线论文

1. DSperse: A Framework for Targeted Verification in Zero-Knowledge Machine Learning
2. VeriLLM: A Lightweight Framework for Publicly Verifiable Decentralized Inference
3. IMMACULATE: A Practical LLM Auditing Framework via Verifiable Computation
4. A Survey of Zero-Knowledge Proof Based Verifiable Machine Learning
5. zkLLM: Zero Knowledge Proofs for Large Language Models

### 本地外部资料

1. survey/external/ezkl
2. survey/external/zkllm-ccs2024
3. survey/external/zkml-blueprints

### 仓库内已有资料

1. README.md
2. PROJECT_PLAN.md
3. DEVELOPMENT_REPORT.md
4. survey/reference/EZKL_DSPERSE_REFERENCE.md

## 3. 与本项目最相关的理论结论

### 3.1 DSperse 对本项目的直接启发

DSperse 的核心不是“把 prover 底层算子并行化”，而是“将大模型推理划分为若干可独立验证的 slices，只对高价值子计算进行 targeted verification”。

对 DistriZKP 而言，可直接吸收的结论是：

1. 全模型一次性电路化会导致证明时间和内存成本快速上升，不适合作为本科原型的唯一方案。
2. 模型切片后，每个切片可以单独证明，从而降低单节点资源压力。
3. 局部证明本身并不自动推出全局正确性，系统仍需额外的一致性机制、审计机制或调度逻辑。

因此，DistriZKP 的合理学术定位是“DSperse 风格的模型切片 + 定向验证原型”，而不是“完整的跨节点统一证明系统”。

### 3.2 VeriLLM 对状态绑定的启发

VeriLLM 关注去中心化推理中的公开可验证性，其关键思想是把与推理正确性强相关的状态绑定进验证对象，并通过低成本复核机制控制验证开销。

对 DistriZKP 而言，应区分以下两种状态绑定：

1. 强绑定：proof 节点上，EZKL witness 中的 processed_inputs 和 processed_outputs 属于电路导出的公开实例，可用于相邻 proof 节点间的一致性比较。
2. 弱绑定：light 节点上，hash_in 和 hash_out 只是电路外部的应用层哈希，不等价于统一的密码学承诺链。

因此，论文中可以写“借鉴 VeriLLM 的状态绑定思想”，但不应直接写成“本系统已实现对全部隐藏状态的统一承诺协议”。

### 3.3 IMMACULATE 对选择性验证的启发

IMMACULATE 表明，在高吞吐推理系统中，只对少量请求或节点进行 verifiable audit，可以在较低吞吐损失下获得较强的违规检测能力。

对 DistriZKP 而言，这与 verify_ratio、edge-cover 选点和 /re_prove 随机挑战具有直接对应关系：

1. 并非所有切片都需要每次生成 proof。
2. 对 light 节点的低成本执行，可用抽查复核降低攻击收益。
3. 系统目标不是把漏检概率降为零，而是在可接受开销下提高整体检测概率。

### 3.4 ZKML Survey 与 zkLLM 提供的底层事实

这两类资料提供了论文写作时经常需要的工程事实：

1. ZKML 的主要瓶颈通常来自证明生成时间、峰值内存和复杂非线性算子的电路化成本。
2. PLONKish / Halo2 风格系统中，查找表、定点量化和矩阵运算约束组织方式会显著影响性能。
3. 非线性算子如 ReLU、Softmax、Attention 往往需要特殊优化，不能简单视为普通加减乘。
4. 集中式一次性证明虽然安全边界更清晰，但在大模型或深网络上常受制于资源开销；切片化和模块化验证是现实工程中的重要折中。

## 4. 本地外部资料的工程启发

### 4.1 EZKL 工程调用链

从官方 README 和 Python bindings 可确认，当前项目采用的 EZKL 调用路径与官方主流程一致：

1. gen_settings
2. calibrate_settings
3. compile_circuit
4. get_srs
5. setup
6. gen_witness
7. prove
8. verify

这意味着当前原型是建立在 EZKL 官方推荐工作流上的，而不是依赖私有 hack。

### 4.2 EZKL 的可见性与承诺边界

根据 Python bindings，EZKL 的 input_visibility、output_visibility、param_visibility 支持 public、private、fixed、hashed/public、hashed/private、polycommit 等模式。

对当前项目的实际意义如下：

1. all_public 模式最适合最小可运行原型。
2. hashed 模式会引入额外哈希电路开销，因此证明时间明显增加是符合官方实现逻辑的。
3. private 模式并不意味着 Worker 看不到输入，只意味着验证者无需看到输入明文即可验证证明。
4. polycommit 是 EZKL 提供的另一类承诺机制接口，但当前项目并未基于它实现跨节点统一承诺协议。

### 4.3 EZKL 的安全提醒

本地 advanced_security 文档给出两条对论文非常重要的限制：

1. 低熵数据上的公开承诺可能遭受字典攻击或暴力枚举，因此“哈希了就绝对隐私安全”的写法是不严谨的。
2. 量化可能激活量化后门，因此“PyTorch 正常”并不自动推出“EZKL 量化电路路径同样正常”。

这直接支持当前项目中两个必须写清楚的边界：

1. 外部哈希链提供的是完整性/一致性检测，不应被夸大为通用隐私承诺。
2. 若实验只比较 PyTorch 路径，不能把结论外推到完整 EZKL 量化推理语义一致性。

### 4.4 zkLLM 与 zkml-blueprints 的设计启发

zkLLM 和 zkml-blueprints 更适合作为“为什么 ZKML 开销高、为什么切片合理、为什么 toy model 仍有研究价值”的说明材料。

可直接提炼的要点：

1. 非线性算子和大规模矩阵运算是电路设计热点。
2. 实际高性能 ZKML 往往依赖专门优化，而不是简单把普通深度学习代码直接搬进证明系统。
3. 本项目采用小型全连接网络作为研究原型，主要用于验证分层可验证框架，而不是为了声称已经解决大模型级别的 ZK 推理成本问题。

## 5. 对 DistriZKP 的准确学术定位

结合源码与文献，当前系统最准确的定位应为：

一个面向分布式推理的分层可验证计算研究原型。系统借鉴 DSperse 的模型切片与定向验证思想，在 proof 节点上利用 EZKL 公开实例提供局部密码学正确性保证，在 light 节点上采用外部哈希链与随机挑战提供低开销审计，并通过 edge-cover 选择性验证在检测能力和证明开销之间进行权衡。

## 6. 当前实现中可以强表述的点

以下内容可作为论文中的“已实现机制”直接陈述：

1. Master 独立验证 Worker 返回的 proof，而非信任 Worker 自报结果。
2. 每个 proof 节点都能给出基于 EZKL 的局部证明与本地验证结果。
3. 相邻 proof 节点间可以比较 processed_outputs 与 processed_inputs，以形成局部 linking 检查。
4. light 节点请求可缓存，并通过 request_id 触发严格模式的 /re_prove 抽查。
5. 通过 verify_ratio 和 edge-cover 选点可降低平均证明开销。

## 7. 当前实现中必须弱表述的点

以下内容若写入论文，必须谨慎降级表述：

1. 不能写“所有中间隐藏状态都被统一密码学承诺并跨节点强绑定”。
2. 不能写“系统对任意恶意 prover 都给出完备密码学安全保证”。
3. 不能写“light 节点的哈希链等价于 ZKP 级安全”。
4. 不能写“P4 已证明 ONNXRuntime 与 EZKL 量化路径完全一致”。
5. 不能写“P6 已实现真正的端到端 proof composition”。

## 8. 论文推荐写法

### 8.1 可直接使用的一句话摘要

本工作提出了一个面向分布式推理的分层可验证计算原型，通过模型切片、局部零知识证明、外部一致性检查与随机挑战复核，实现对推理完整性的低开销审计。

### 8.2 方法章节推荐表述

本框架在体系结构上借鉴 DSperse 的 targeted verification 思想，将深度神经网络切分为多个顺序切片，并仅对部分关键切片生成零知识证明。对于 proof 切片，系统利用 EZKL 导出的公开实例执行局部密码学校验；对于未证明的 light 切片，系统采用哈希链一致性检查与基于 request_id 的随机复核机制提升可追溯性。该设计与 IMMACULATE 所强调的 selective auditing 思路一致，即在不对全部计算执行重型验证的前提下，以抽样方式控制系统性作弊风险。

### 8.3 局限性章节推荐表述

当前原型尚未实现跨所有节点中间状态的统一密码学承诺与组合证明。相邻 proof 节点之间的 linking 依赖 EZKL 公开实例的一致性比较，而 light 节点之间仅受外部哈希链与随机挑战保护。因此，本系统更适合被视为分层可验证计算原型，而非完备的恶意多 prover 组合证明系统。

## 9. 后续可扩展方向

1. 用 EZKL 的 polycommit 或等价承诺接口探索更强的跨切片状态绑定。
2. 将实验脚本统一迁移到 Master 完整逻辑，避免“实验路径”和“系统路径”分叉。
3. 增加 ONNXRuntime、EZKL witness 路径与 PyTorch 基线之间的保真度对比。
4. 针对量化后门和低熵公开承诺补充更严格的安全讨论。

## 10. 使用建议

后续撰写论文、答辩 PPT 或 README 时，优先以本文件中的“准确定位”和“强弱表述边界”为准；若与旧文档冲突，以源码已实现能力为准。