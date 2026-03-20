# 面向本地 Claude 的《分布式推理 + zkML 端到端重构总指南》

**项目主题**：基于零知识证明的分布式推理可验证性研究  
**文档用途**：直接交给本地 Claude，作为后续协议重构、代码改造、实验重做、论文收口的统一工作底稿。  
**版本**：2026-03-20  
**定位**：这不是“修补旧系统”的说明书，而是“把当前原型重构为真正 end-to-end verifiable distributed inference system”的总设计文件。

---

## 0. 如何使用这份文档

请本地 Claude 严格按下面的优先级理解和执行：

1. **先重建 statement，再改代码。** 不要从接口 patch 开始。
2. **先做一个慢但正确的版本，再做低延迟版本。**
3. **先跑通全链路可验证，再考虑 aggregation / recursive / bundle。**
4. **所有“看起来安全”的旧机制，只能作为 baseline，不再作为主系统安全依据。**
5. **必须把“电路正确性”和“浮点 fidelity”分开。**

这份文档整合了两类内容：
- 第一类：对你当前仓库和当前论证边界的系统级重构判断；
- 第二类：截至 2026-03-20 与你方向最相关的最新文献与工程线索，包括 NanoZK、EZKL aggregation、zkGPT/zkLLM/VeriLLM、layerwise approximate verification 的理论边界，以及 Artemis / Apollo 这类 commit-and-prove 方向。

---

## 1. 先给最终结论：当前系统到底是什么

### 1.1 它已经是什么

当前仓库已经实现了一个**可运行的研究原型**，不是空壳。它至少具备以下能力：

- 将模型切成多个 ONNX 子模型；
- 由 Master 顺序调度多个 Worker 执行流水线推理；
- 在部分切片上使用 EZKL 生成 proof；
- 支持 `verify_ratio` 或 `verified_set` 一类的选择性验证策略；
- 围绕输出哈希、proof linking、外部哈希链做了若干防护尝试；
- 具备一组实验脚本用于跑延迟、吞吐、故障注入、P4/P6 等指标。

所以，它可以被称为：

> **一个带有分段 proving、选择性验证和实验脚本的分布式推理原型。**

### 1.2 它还不是什么

按严格标准，它**还不能**被称为：

> **end-to-end 可验证的分布式推理 + ZKP 系统。**

原因不是“小 bug 太多”，而是更根本：

> **proof boundary 与 dataflow boundary 还没有重合。**

换句话说，当前系统里“被证明的计算语义”和“真实运行时被下游消费、被用户接收的数据”还没有被完全焊接到一起。

### 1.3 为什么这个判断成立

当前仓库里同时存在下面几类结构性缺口：

- 某些切片允许最终不出 proof；
- Worker 端会本地 `ezkl.verify()` 并把 `verified=True` 回给 Master；
- Master 端并没有始终作为**独立 verifier**来重验证所有 proof；
- 已有 linking 更像局部检查，而不是统一定义的 end-to-end statement；
- 外部哈希链只能绑定“某节点宣称过某值”，不能证明“这个值确实来自正确计算”；
- 旧实验中某些故障注入脚本与 API 参数存在不一致，导致部分“检测率”结论需要重跑确认；
- `hashed` visibility 现状并不等于“已经实现输入/输出 commitment chain”。

这些点合在一起说明：

> 现在最该做的是**协议级重构**，不是继续围绕 light 节点打补丁。

---

## 2. 这次重构的唯一中心问题

### 2.1 不要再问“哪些节点要不要证明”

真正应该重定义的问题是：

> 给定请求 `req_id`、切片模型序列 `M_1, M_2, ..., M_n`、初始输入 `x_0`，验证者如何确认系统实际执行了
>
> `x_1 = M_1(x_0)`  
> `x_2 = M_2(x_1)`  
> `...`  
> `x_n = M_n(x_{n-1})`
>
> 并且返回给用户的最终结果就是 `x_n`？

这个问题里有五个必须被证明或绑定的对象：

1. **模型绑定**：每一片到底运行的是哪个切片模型；
2. **输入绑定**：每一片实际用的输入是什么；
3. **输出绑定**：每一片实际产生的输出是什么；
4. **链路绑定**：第 `i` 片输出确实就是第 `i+1` 片输入；
5. **终端绑定**：用户拿到的结果就是最后一片 proof 语义里的输出。

只要这五件事里任意一件仍然依赖单个 Worker 自报，那么系统就还不是 end-to-end verifiable。

### 2.2 新系统的中心思想

本项目应当被重构为：

> **一种面向端到端可信的、执行—证明解耦（Deferred Certification）的分布式切片推理架构。**

它的核心不再是：
- “只让部分节点出 proof”；
- “让 light 节点看起来也挺安全”；
- “用哈希链补全没被证明的计算”。

而是：
- **所有切片最终都必须出 proof；**
- **证明生成不再阻塞在线推理；**
- **相邻切片的一致性靠输入/输出承诺链保证；**
- **由 Master / Verifier 独立做最终全链路认证。**

---

## 3. 为什么不要继续修补 light 节点

### 3.1 根本原因

只要系统允许某些切片最终不出 proof，那么这些切片对应的 computation gap 就不可能由 ZKP 填满。

你之前设计的：
- L1 输出哈希；
- L2 proof linking；
- L3 哈希链；
- 随机挑战；

从工程上有价值，但它们都不能把“未被证明的计算”变成“已被证明的计算”。

### 3.2 各机制的边界

- **L1 输出哈希**：恶意节点完全可以同时篡改输出和哈希；
- **L3 哈希链**：相邻恶意节点可以协同伪造一条自洽的链；
- **随机挑战**：本质上是概率性威慑，不是确定性 correctness guarantee；
- **L2 linking**：只有当边两端都被强约束时才真正有意义。

### 3.3 与 DSperse 的关系

DSperse 走的是 **targeted verification** 路线。它本来就不是“全链路确定性密码学正确性”的方案，而是性能与安全之间的折中。它把 audit、replication、economic incentives 之类机制作为整体信任最小化的一部分，而不是把 selective verification 说成完整 end-to-end guarantee。

这件事对你的启发非常直接：

> 选择性验证可以保留成 baseline，但不应再被包装成主系统的完备安全结论。

### 3.4 这次应该怎么转向

你要从下面这个旧问题：

> “如何让部分节点不出 proof，但系统仍然安全？”

转向这个新问题：

> “如何让所有节点最终都出 proof，同时把证明生成从关键路径移走，从而保住在线延迟？”

这就是新的研究主轴。

---

## 4. 新系统必须明说的安全语义

### 4.1 不要假装“立即低延迟 + 立即最终可信”可以同时免费获得

如果你允许下游节点在上游 proof 还未产生、还未验证时，就先消费上游输出，那么在在线阶段系统本质上消费的是**尚未认证**的数据。

这不是错误，而是新系统必须诚实承认的语义边界。

### 4.2 正确的语义划分

系统必须区分两类结果：

- **Provisional Output**：在线推理完成后立刻返回，低延迟，但尚未完成全链路证明认证；
- **Certified Output**：待所有切片 proof 生成完成、全部验证通过、链路 commitments 闭合后，才升级为正式认证结果。

如果任一切片 proof 失败，或者任一相邻边的 commitment linking 失败，则该请求被标记为：

- `INVALID`
- 或 `UNCERTIFIED`

### 4.3 建议的请求状态机

每个请求应具有如下状态：

- `SUBMITTED`
- `EXECUTING`
- `EXECUTED_UNCERTIFIED`
- `PROVING`
- `VERIFYING`
- `CERTIFIED`
- `INVALID`
- `EXPIRED`（可选）

这套状态机不仅要写进代码，也应该写进论文和答辩图。

---

## 5. 正式安全目标：本项目应当证明什么

### 5.1 基本版 End-to-End Statement

对请求 `req_id`，存在一组中间状态 `x_1, x_2, ..., x_{n-1}`，使得：

- `x_1 = M_1(x_0)`
- `x_2 = M_2(x_1)`
- `...`
- `x_n = M_n(x_{n-1})`

其中每个 `M_i` 都是预先注册的切片模型 `model_digest_i` 所对应的电路 / 权重，且最终返回给用户的结果 `y` 满足 `y = x_n`。

### 5.2 推荐采用的增强版 Statement

除了上述计算关系，还要显式引入 commitment：

- `Cin_1 = Commit(req_id, slice_id=1, model_digest_1, x_0)`
- `Cout_i = Commit(req_id, slice_id=i, model_digest_i, x_i)`
- `Cin_{i+1} = Commit(req_id, slice_id=i+1, model_digest_{i+1}, x_i)`

然后要求：

1. 每片 proof 证明“我用 `Cin_i` 对应的输入，经 `M_i` 计算得到 `Cout_i` 对应的输出”；
2. 对所有 `i`，都有 `Cout_i = Cin_{i+1}`；
3. 用户收到的最终输出对应 `Cout_n`。

这才是完整的 end-to-end statement。

### 5.3 为什么旧系统不满足这个 statement

因为旧系统里：
- 并非所有切片都最终有 proof；
- 相邻切片的输入/输出没有统一作为 proof 语义对象暴露；
- Worker 仍然能在“发给下游的数据”和“proof 里声称的数据”之间制造分离。

---

## 6. 核心协议设计：不要只做输出承诺，要做双端承诺链

### 6.1 只做输出承诺为什么不够

若第 `i` 个 Worker：

1. 正确计算出 `real_out`；
2. 给 Master 提交 `Commit(real_out)`；
3. 在后台为 `real_out` 生成合法 proof；
4. 但发给下游的是 `fake_out`；

那么：
- 上游 proof 仍可能是对的；
- Master 看到的 commitment 也可能是对的；
- 但流水线实际消费的是假的数据。

所以，**只绑定输出，不足以绑定运行时链路。**

### 6.2 正确做法：相邻切片双端绑定

必须同时证明：

- 第 `i` 片公开它的 `output_commit = Cout_i`；
- 第 `i+1` 片公开它实际使用的 `input_commit = Cin_{i+1}`；

并由验证者检查：

`Cout_i == Cin_{i+1}`

这一步一旦成立，proof boundary 与 dataflow boundary 才真正闭合。

### 6.3 commitment 的定义不要太裸

建议 commitment 不采用裸 `Poseidon(tensor)`，而是引入域分离信息：

`Commit(req_id || slice_id || model_digest || tensor)`

若直接把完整张量进哈希不方便，也至少要把：
- `req_id`
- `slice_id`
- `model_digest`
- `tensor_digest`

混进去。

这样做的目的：
- 防止跨请求 replay；
- 防止不同切片之间拼接；
- 防止旧模型与新模型之间复用旧 commitment；
- 便于日志与证书解释。

### 6.4 若模型不是纯线性链怎么办

如果未来切片结构不是严格线性，而是带有：
- residual 分支；
- 多分支 DAG；
- 共享状态；
- RNN / 循环依赖；

则线性 commitment chain 需要升级为：

> **commitment graph / DAG commitment binding**

但就你当前“小模型顺序切片”场景，线性链已经足够。

---

## 7. 新系统总体架构：四个平面

### 7.1 Execution Plane（执行平面）

职责：
- 只做在线推理；
- 接收输入张量；
- 输出下一片所需张量；
- 计算输入/输出 commitment；
- 记录执行日志；
- 不对 correctness 作最终声明。

### 7.2 Proving Plane（证明平面）

职责：
- 为每个切片最终生成 proof；
- 将 request metadata、模型 digest、input/output commitments 一并纳入 statement；
- 可异步、后台、并行执行。

### 7.3 Verification Plane（验证平面）

职责：
- 独立验证每片 proof；
- 独立检查相邻 commitments 是否一致；
- 最终做全请求级认证；
- 签发 certificate 或标记 invalid。

### 7.4 Control Plane（控制平面）

职责：
- 管理请求状态机；
- 调度执行与 proving；
- 管理 artifacts 与 job queue；
- 维护审计日志；
- 管理失败重试、超时、清理策略。

---

## 8. 当前仓库必须正视的结构性现状

下面这些点不是为了否定旧系统，而是为了让 Claude 明白：为什么这次必须做 statement-level refactor。

### 8.1 README 叙事仍把 selective verification 当主要安全结论

仓库 README 和文档叙事仍将系统表述为“Master 调度 + 三层校验”，并将部分低验证率实验写成较强的检测结论。这与当前你已经得到的安全认识不再一致。

### 8.2 `common/utils.py` 中 `hashed` 模式并不等于输出承诺链

现状是：
- `input_visibility = "hashed"`
- `output_visibility = "public"`
- `param_visibility = "hashed"`

这说明当前代码并没有真正实现“输出也进入 hashed commitment 语义”。因此，当前 `hashed` 模式不能被表述为“已经有输出承诺链”。

### 8.3 proving 与 verify 仍混在同一 helper 内

当前 helper 会先 `ezkl.prove()`，再立即 `ezkl.verify()`，并把：
- `proof`
- `verified`
- `proof_instances`

一起返回。这导致 Worker 既是 prover，又像自封 verifier。

### 8.4 Master 仍会读取 Worker 自报的 `verified`

这说明系统当前还没有把“独立 verifier”作为强角色落下去。

### 8.5 L2 linking 仍然只覆盖特定有 proof 的边

旧逻辑中的 linking 判断更像“遇到 proof 就顺手比一下”，而不是“每条边都是协议里的强约束对象”。

### 8.6 随机挑战不是重证明原始 statement

旧挑战逻辑里使用的是记录下来的某段 `output_data` 来驱动 `/re_prove`。这并不等价于“重证明该切片当时真实接收的原始输入”。

### 8.7 故障实验中哈希与实际输出可能不绑定同一对象

Worker 里的 `hash_out` 与故障注入后的 `output_data` 并非天然绑定为同一语义对象，这会让部分攻击实验更像“人为设计为可检出”，而不是面对自适应恶意节点的最强对抗模型。

### 8.8 部分实验脚本的 fault 参数名称与 Worker 接口不一致

这说明历史实验里的某些结果不能不加复核地直接沿用到新论文主结论中。

### 8.9 P6 现状更像 proof-enabled sanity check，而不是 ZK 链完整性评估

如果指标仍然只是读取 `verified=True/False`，它最多代表“Worker 说自己验证过了”，而不是“Verifier 端已独立确认整条链正确”。

### 8.10 这些现象共同说明

> 旧系统已经有工程雏形，但安全核心仍是“局部 proof + 外部检测”的组合，而不是一个完整闭合的 end-to-end protocol。

---

## 9. 与最新文献对齐后的路线选择

### 9.1 NanoZK：与你的新方向高度同构

NanoZK 不是 monolithic 大 proof，而是：

- **layerwise proofs**；
- **相邻层 commitment chain**；
- **并行 proving**；
- **compositional soundness**；
- 明确承认 selective verification 只是效率优化，而不是 cryptographic certainty。

它给你的最强启发是：

> 你的系统完全可以沿“逐切片 proof + commitment chain + 并行 / 后台 proving”的方向重构，而不是继续往“少量 proof 也许够安全”那条路上走。

### 9.2 zkGPT / zkLLM：它们代表 monolithic / specialized 路线

zkGPT、zkLLM 更像是：
- 面向 LLM 的专用高效大证明；
- 更强调把整次推理压进一个强 statement；
- 不以“外部层链 / 多 Worker 切片”作为中心抽象。

它们对你最重要的意义，是提供比较基准：
- **monolithic route**：单体大 proof，statement 强，但不天然适配多节点切片协议；
- **layerwise route**：与多节点执行天然贴合，但需要显式处理中间状态绑定；
- **recursive / accumulation route**：未来可把多个 proof 进一步压缩成一个 bundle。

### 9.3 VeriLLM：作为“非 ZK 轻量验证路线”对照

VeriLLM 更接近“轻量公开可验证”而不是“完整零知识正确性证明”。它适合在 related work 里作为对照：
- 它追求极低额外开销；
- 但不提供你现在想要的 end-to-end cryptographic guarantee。

### 9.4 ZKTorch / recursive composed inference：未来 bundle / aggregation 方向

ZKTorch 代表“basic blocks + parallel accumulation”路线；递归式 composed proof 则代表“多段 proof 最终压成常数大小 final proof”的方向。它们很值得作为未来扩展方向，但不应成为你第一阶段系统正确性的前提。

### 9.5 Non-Composability Note：对你的陈述方式至关重要

这篇文章直接否定：

> “每层都近似正确，所以最终输出近似正确”

这一推理在一般情况下是成立的。

因此，你必须把：
- **circuit correctness**
- **float-model fidelity**

分开写。

如果你证明的是：
- 精确的量化 / 有限域电路 statement；

那没有问题。

但如果你想说：
- 每层都有一点容差，所以整体也接近原始浮点模型；

这在一般情况下并不成立。

### 9.6 Artemis / Apollo：不是替代 aggregation，而是提升 commitment verification 能力

Artemis 这类 commit-and-prove SNARK 的价值，在于：
- 如果以后你不满足于简单哈希链；
- 想把 linking 升级为多项式承诺 / KZG / IPA commitment 语义；
- 又不希望外部 commitment verification 太贵；

那么它会非常有帮助。

但它不是自动把所有 proof 聚成一个 proof 的递归器。它更像是“让 commitment 也能被便宜地证明与验证”。

---

## 10. EZKL 在新系统中的角色定位

### 10.1 先说原则

新系统第一阶段不要把 correctness 建在“EZKL aggregation 已经完全成熟”这一点上。

第一阶段应当只依赖这几件已足够明确的能力：

- 为单片电路生成 proof；
- 独立 verify 单片 proof；
- 在 `PyRunArgs` 层面对 input / output visibility 做设置；
- 暴露可用于 linking 的公开实例或 commitment；
- 对分片边界的 scale 做显式管理。

### 10.2 关于 `rescaled_outputs`

必须明确：
- proof 直接绑定的是**公开实例 / public outputs**；
- `rescaled_outputs` 更适合作为调试/可读视图；
- 不应把系统核心安全逻辑写成“只相信 `rescaled_outputs`”。

### 10.3 关于 input/output visibility

对新系统第一版，最自然的做法是：

- `input_visibility = "hashed/public"`
- `output_visibility = "hashed/public"`

目的很简单：
- Verifier 能够看到输入承诺；
- Verifier 能够看到输出承诺；
- 相邻切片才能用 commitment equality 做 stitching。

### 10.4 关于量化 scale 对齐

跨切片 linking 若想稳定成立，必须确保边界量化参数可对齐。你已经观察到 `processed_outputs` 与 `processed_inputs` 会因量化参数不同而不一致，这与 EZKL 团队关于 split 子图时 `input_scale` 要对齐前一片 `output scale` 的讨论是一致的。

因此，新的编译/注册阶段必须显式包含：

- 边界 scale 规范；
- 每片 input/output scale 的导出与锁定；
- 不允许每片自由独立漂移；
- 必要时统一由全局校准策略决定边界 scale。

### 10.5 关于 proving 是否支持后台化

即便 EZKL 本地 API 是同步 `prove()`，系统层也完全可以通过：
- `multiprocessing`
- `ProcessPoolExecutor`
- 独立 prover service
- 作业队列

把 proving 从关键路径中剥离。

### 10.6 关于 aggregation 的保守定位

现有公开信息表明：
- `latest` 文档已明确出现 `aggregate`, `setup_aggregate`, `verify_aggr`, `split_proofs` 等接口；
- 但 `stable 23.0.5` 文档未完整公开列出这些 API；
- 因此，应把 aggregation 视为**可选增强项**，而不是主系统第一阶段的必要前提。

更重要的是：

> aggregation 只压缩验证成本，不自动解决语义 linking。

也就是说：
- 先要有单片 proof；
- 先要有 input/output commitments；
- 先要有边界 equality；
- 然后 aggregation 才有意义。

---

## 11. 建议采用的系统协议（按请求时序写给 Claude）

### 11.1 离线编译阶段

Claude 需要先把这部分做成稳定的 pipeline：

1. 读取完整模型；
2. 按预定切分点导出多个 ONNX 子模型；
3. 为每片生成 EZKL settings；
4. 显式锁定各片 input/output scale；
5. 生成 PK/VK/SRS / 编译工件；
6. 计算每片 `model_digest_i`；
7. 将静态工件注册到 artifact registry；
8. 输出一份全局 `slice_registry.json`，记录：
   - `slice_id`
   - `model_path`
   - `compiled_path`
   - `settings_path`
   - `pk_path`
   - `vk_path`
   - `srs_path`
   - `model_digest`
   - `input_scale`
   - `output_scale`

### 11.2 在线执行阶段

对请求 `req_id`：

1. Client 提交原始输入 `x_0`；
2. Master 构造 `Cin_1`；
3. Master 将 `x_0` 路由给 Worker 1；
4. Worker 1 做切片推理，得到 `x_1`；
5. Worker 1 计算 `Cout_1`；
6. Worker 1 把 `(x_1, Cout_1)` 发给 Worker 2；
7. Worker 1 同时把 proving job 提交给 proving plane；
8. 如此重复到 Worker n；
9. Master 接收到最终 `x_n` 后，将其作为 `provisional output` 返回给用户；
10. 请求状态切到 `EXECUTED_UNCERTIFIED`。

### 11.3 后台 proving 阶段

每片 proving job 应至少绑定以下对象：

- `req_id`
- `slice_id`
- `model_digest`
- `input_commit`
- `output_commit`
- 本片 witness
- 本片 proof

### 11.4 最终验证阶段

Verifier 必须做四类检查：

1. 每片 proof individually verifies；
2. 每片绑定的 `model_digest_i` 与注册表一致；
3. 所有相邻边满足 `Cout_i == Cin_{i+1}`；
4. 用户看到的最终输出与 `Cout_n` 对应。

若全部通过：
- 请求状态从 `VERIFYING` 升级为 `CERTIFIED`；
- 生成 certificate。

若任一失败：
- 请求标记为 `INVALID`；
- 记录失败原因：哪一片 proof 失败、哪一条边 linking 失败、是否存在工件不一致等。

---

## 12. 建议的数据结构

### 12.1 SliceArtifact

```python
SliceArtifact = {
    "slice_id": int,
    "model_path": str,
    "compiled_path": str,
    "settings_path": str,
    "pk_path": str,
    "vk_path": str,
    "srs_path": str,
    "model_digest": str,
    "quant_meta": {
        "input_scale": int,
        "output_scale": int,
        "param_scale": int
    }
}
```

### 12.2 ExecutionRecord

```python
ExecutionRecord = {
    "req_id": str,
    "slice_id": int,
    "input_commit": str,
    "output_commit": str,
    "output_tensor": list[float],
    "executor_id": str,
    "started_at": str,
    "finished_at": str,
    "status": "executed_uncertified"
}
```

### 12.3 ProofJob

```python
ProofJob = {
    "job_id": str,
    "req_id": str,
    "slice_id": int,
    "input_commit": str,
    "output_commit": str,
    "artifact_ref": str,
    "witness_path": str,
    "proof_path": str | None,
    "status": "queued|running|done|failed",
    "error": str | None,
}
```

### 12.4 CertifiedRequest

```python
CertifiedRequest = {
    "req_id": str,
    "client_output_commit": str,
    "proof_bundle": list[str],
    "all_single_proofs_verified": bool,
    "all_links_verified": bool,
    "aggregate_proof_path": str | None,
    "status": "certified|invalid"
}
```

---

## 13. 建议的新代码目录结构

```text
project/
├── artifacts/
│   ├── models/
│   ├── circuits/
│   ├── keys/
│   └── registry/
│
├── common/
│   ├── commitments.py
│   ├── quantization.py
│   ├── types.py
│   ├── ids.py
│   ├── hashing.py
│   └── config.py
│
├── compile/
│   ├── export_slices.py
│   ├── build_circuits.py
│   ├── align_scales.py
│   └── register_artifacts.py
│
├── execution/
│   ├── scheduler.py
│   ├── pipeline.py
│   ├── request_store.py
│   └── states.py
│
├── prover/
│   ├── jobs.py
│   ├── worker_local.py
│   ├── worker_pool.py
│   └── ezkl_adapter.py
│
├── verifier/
│   ├── verify_single.py
│   ├── verify_chain.py
│   ├── aggregate.py
│   └── certificate.py
│
├── services/
│   ├── execution_worker.py
│   ├── prover_service.py
│   ├── verifier_service.py
│   └── master_api.py
│
├── experiments/
│   ├── baseline_selective.py
│   ├── e2e_certified_latency.py
│   ├── commitment_linking.py
│   ├── quantization_fidelity.py
│   └── aggregation_eval.py
│
└── docs/
    ├── protocol.md
    ├── threat_model.md
    ├── api_contract.md
    └── experiment_plan.md
```

### 13.1 为什么这样拆

- `execution/` 只关心在线数据流；
- `prover/` 只关心 proving job 生命周期；
- `verifier/` 只关心 correctness 检查；
- `compile/` 统一管理静态工件与量化边界；
- `common/` 统一类型、ID、commitment 和配置。

这会把项目从“脚本堆叠型 demo”重构成“协议实现型系统”。

---

## 14. 当前主要文件的重构映射

### 14.1 `distributed/master.py`

现状问题：
- 兼做调度器、检测器、打印器、安全结论出口；
- 仍然信任 Worker 端回传的 `verified`；
- 将随机挑战作为安全逻辑的一部分。

重构建议：
- 将其拆成：
  - `execution/scheduler.py`
  - `services/master_api.py`
  - `verifier/verify_chain.py`
- Master 只做：
  - 请求编排；
  - 状态机推进；
  - 接收 proof；
  - 调用独立 verifier；
  - 生成 certificate。

### 14.2 `distributed/worker.py`

现状问题：
- 同时是执行 worker、prover、self-verifier、故障注入点；
- 安全职责混杂。

重构建议：
- 拆成：
  - `services/execution_worker.py`
  - `prover/worker_local.py`
  - `tests/fault_injection.py`
- execution worker 不再返回 `verified`；
- proving job 独立管理；
- 故障注入仅保留在测试环境。

### 14.3 `common/utils.py`

现状问题：
- 混合 proving、verify、可见性配置、IO 和杂项 helper；
- 不利于 statement 清晰。

重构建议：
- 拆成：
  - `prover/ezkl_adapter.py`
  - `verifier/verify_single.py`
  - `common/quantization.py`
  - `common/commitments.py`
  - `compile/build_circuits.py`

### 14.4 `run_experiments.py`

定位调整：
- 不再代表主系统；
- 重命名为 `experiments/baseline_selective.py`；
- 所有来自该脚本的历史数字必须重跑确认。

### 14.5 `run_advanced_experiments.py`

定位调整：
- 改成 `experiments/baseline_selective_attacks.py`；
- 作为旧方案对照，不再混进主系统评估。

### 14.6 `run_p4_p6_experiment.py`

重构建议：
- 拆成：
  - `experiments/quantization_fidelity.py`
  - `experiments/commitment_linking_eval.py`
- 不再让 `data.get("verified")` 直接代表“circuit integrity”。

---

## 15. API 重构建议

### 15.1 执行 worker API

`POST /execute`

输入：
- `req_id`
- `slice_id`
- `input_tensor`
- `input_commit`

输出：
- `output_tensor`
- `output_commit`
- `exec_metrics`

### 15.2 prover service API

`POST /prove`

输入：
- `req_id`
- `slice_id`
- `input_tensor`
- `output_tensor`
- `input_commit`
- `output_commit`
- `artifact_ref`

输出：
- `job_id`

`GET /prove/{job_id}`

输出：
- `status`
- `proof_path`
- `error`

### 15.3 verifier service API

`POST /verify/single`

输入：
- 单片 proof
- vk/settings/srs/artifact refs

输出：
- `ok`
- `public_instances`
- `error`

`POST /verify/chain`

输入：
- 某请求的全部单片 proofs
- 相邻 commitments
- registry metadata

输出：
- `all_single_proofs_verified`
- `all_links_verified`
- `status`
- `failure_reason`

### 15.4 master API

`POST /requests`
- 创建请求
- 返回 `req_id` 和 provisional result（可同步或异步）

`GET /requests/{req_id}`
- 返回状态机当前状态

`GET /requests/{req_id}/certificate`
- 若已认证，返回 certificate

### 15.5 为什么不该再保留 `/infer_light`

因为在新系统里，不再存在“最终永不出 proof 的 light 切片”。

最多存在的是：
- `execute_now_prove_later`
- `execute_and_sync_prove`

而不是：
- `execute_without_eventual_proof`

---

## 16. 对 Claude 的具体实现顺序要求

### Phase A：先做一个“慢但正确”的版本

**目标**：第一次真正满足 end-to-end statement。

要求：
- 所有切片同步出 proof；
- Master 独立 verify；
- input/output commitments linking 跑通；
- 不做后台 proving；
- 不做 aggregation；
- 不做 light 节点。

阶段验收标准：
- 任一 forged output 会被最终认证阶段发现；
- 任一 broken link 会被发现；
- 任一 proof failure 会让请求标记为 invalid；
- 能生成 certified result。

### Phase B：执行—证明解耦

**目标**：在 correctness 已经成立的前提下恢复低延迟。

要求：
- execution 和 proving 解耦；
- 在线阶段只返回 provisional output；
- 后台 proving 使用进程池或 prover service；
- Master 维护状态机和作业表；
- 认证完成后生成 certificate。

阶段验收标准：
- provisional latency 显著低于 certification latency；
- proof 失败时不会影响系统如实标记 uncertified/invalid；
- request lifecycle 可追踪。

### Phase C：工程优化

目标：
- 让系统更稳、更可复现。

可做项：
- process pool / prover queue；
- witness cache；
- retry policy；
- timeout / cleanup；
- artifact registry versioning；
- structured logs。

### Phase D：可选 aggregation / bundle

目标：
- 在 correctness 已稳定后，减少 verifier 端多 proof 验证开销。

前置条件：
- 单片 proof 已完全稳定；
- commitments 链已稳定；
- registry 与 statement 已清晰；
- 本地 EZKL 版本已实测确认 aggregation API 可用。

---

## 17. 对实验体系的重构要求

### 17.1 不要再让 baseline 和主系统混在一起

新论文 / 新图表应分成四大组：

#### G1. Baseline（旧方案对照）
- selective verification
- random / contiguous / edge-cover
- hash-chain + challenge

用途：
- 作为历史原型对照；
- 不再代表主系统安全结论。

#### G2. Protocol Correctness（新主实验）
- all-slice eventual proofs
- independent verifier correctness
- commitment linking correctness
- forged output / replay / mixed-proof attacks

用途：
- 证明新协议真正补上了 end-to-end gap。

#### G3. Latency Decomposition
- execution latency
- provisional latency
- proof generation latency
- certification completion latency
- verification latency

用途：
- 展示 deferred certification 的工程价值。

#### G4. Scalability / Aggregation（可选）
- number of slices vs verification cost
- single proofs vs aggregated proof
- per-slice proving parallelism

### 17.2 新的指标体系

推荐正文固定使用：

- `execution_latency_ms`
- `provisional_latency_ms`
- `certification_latency_ms`
- `per_slice_proof_ms`
- `total_proof_gen_ms`
- `total_verification_ms`
- `certification_success_rate`
- `all_links_verified`
- `fidelity_mae`
- `fidelity_max_abs_err`
- `prover_peak_mem_mb`

### 17.3 哪些指标不宜再作为主指标

不建议再将以下内容包装成主结论：

- `worker returned verified`
- 单独的 `hash_chain_ok`
- 没有区分并发/串行含义的 throughput

---

## 18. fidelity 应如何重写

### 18.1 必须分成至少两层

#### F1. Partition Fidelity
完整浮点模型 vs 切片后浮点语义串联模型

目的：
- 证明切片本身没有改变函数组合。

#### F2. Quantization / Circuit Fidelity
完整浮点模型 vs EZKL witness / proof-bound outputs

目的：
- 衡量量化、电路化带来的误差。

#### F3. End-to-End Certified Fidelity（可选）
完整浮点模型 vs certified result

目的：
- 衡量最终全链路认证输出与浮点模型的偏差。

### 18.2 为什么必须这样分

因为 2026 的 non-composability note 已经清楚告诉我们：

> 不能从“每层都差不多对”直接推出“最终输出也差不多对”。

所以：
- 电路正确性是一个 statement；
- fidelity 是另一个统计测量问题；
- 二者不能混写。

---

## 19. 对 Claude 的编码任务清单

下面是建议 Claude 按顺序完成的具体任务。

### 任务 1：清理 statement 与命名

要求：
- 把 README、设计文档、代码注释中所有“light 也安全”的表述删掉或降级；
- 把系统重新命名为：
  - `deferred-certification pipeline`
  - `eventual proof for all slices`
  - `commitment-linked end-to-end verification`
- 把 `verified=True` 的语义改成“verifier side result”，而不是 worker 自报。

### 任务 2：引入 registry

要求：
- 实现 `slice_registry.json`；
- 固定每片：
  - `model_digest`
  - `pk/vk/srs`
  - `input_scale/output_scale`
  - artifact paths
- 所有 proving / verify / execution 都从 registry 取配置。

### 任务 3：分离 execution worker 与 prover

要求：
- execution worker 不再调用 verifier；
- execution worker 只负责：
  - 接受输入；
  - 运行 ONNX / slice inference；
  - 生成 output tensor；
  - 生成 output commitment；
  - 上报 proving job。

### 任务 4：实现 commitment 模块

要求：
- 统一 commitment API；
- 先实现 `hash-commit` 版本；
- 明确输入：
  - `req_id`
  - `slice_id`
  - `model_digest`
  - `tensor`
- 输出 hex string；
- 所有日志和 certificate 都引用该 commitment。

### 任务 5：改造 EZKL adapter

要求：
- 明确区分：
  - `gen_witness`
  - `prove_single`
  - `verify_single`
- 不允许 `prove_single()` 内部隐式替调用方完成全局安全结论；
- 对 input/output visibility 给出统一配置接口；
- 能读取并返回 public instances / commitments。

### 任务 6：实现 verifier service

要求：
- Master 不再相信 worker 的 `verified`；
- Verifier 根据 registry 本地独立验证；
- 验证结果记录到 request store；
- 若 proof 失败，写明失败切片与失败原因。

### 任务 7：实现 chain verifier

要求：
- 收集全请求所有单片 proof 的 commitments；
- 做相邻 equality 检查；
- 检查第 1 片 input 是否与原始请求绑定；
- 检查最后一片 output 是否与用户接收结果绑定；
- 生成 certificate。

### 任务 8：实现 request state machine

要求：
- 请求状态严格走：
  - `SUBMITTED -> EXECUTING -> EXECUTED_UNCERTIFIED -> PROVING -> VERIFYING -> CERTIFIED/INVALID`
- 状态转换必须可追踪、可审计；
- 提供状态查询 API。

### 任务 9：重写实验脚本

要求：
- 将旧实验全部移到 `baseline_*`；
- 重写新主实验；
- 确保所有 fault injection 参数与实际接口完全一致；
- 增加 replay、wrong-link、forged-output、wrong-artifact 四类攻击实验。

### 任务 10：写证书对象

要求：
- 对每个 certified request 生成一个 certificate；
- 内容至少包括：
  - `req_id`
  - `timestamp`
  - `model_version`
  - `slice_count`
  - `final_output_commit`
  - `all_single_proofs_verified`
  - `all_links_verified`
  - optional `aggregate_proof_path`

---

## 20. 对 Claude 的开放问题与研究追踪要求

### 20.1 EZKL aggregation 是否在 23.0.5 wheel 中真实可用

Claude 应当在本地直接做 wheel 级核验，而不是继续猜文档。建议用：

```python
import ezkl, inspect
print(getattr(ezkl, "__version__", "unknown"))
for name in [
    "aggregate",
    "setup_aggregate",
    "verify_aggr",
    "mock_aggregate",
    "create_evm_verifier_aggr",
]:
    print(name, hasattr(ezkl, name))
    if hasattr(ezkl, name):
        try:
            print(inspect.signature(getattr(ezkl, name)))
        except Exception:
            pass
```

### 20.2 是否存在官方稳定示例：多子图 proof linking + aggregation

Claude 需要继续跟踪：
- EZKL examples；
- notebooks；
- discussions / PR；
- 对 `split_proofs=True` 的最小示例。

### 20.3 commitment 形式是否要从 hash 升级到 polycommit

第一版不必强上，但 Claude 应在设计里留接口，便于未来切换到：
- `hashed/public`
- `polycommit`
- 或更高级 commit-and-prove 路线。

### 20.4 residual / 多分支模型是否要纳入下一阶段研究

当前优先级不高，但 Claude 应把“线性链协议如何扩展到 DAG”作为 future work 预留。

---

## 21. 给 Gemini 的追加深搜需求（可直接复制）

### 21.1 核验 EZKL 23.0.5 aggregation

> 请严格核验 EZKL 23.0.5 的 wheel / Git tag / release note / Python symbol export，确认 `aggregate`, `setup_aggregate`, `verify_aggr`, `mock_aggregate`, `create_evm_verifier_aggr` 是否真实存在并可调用。若存在，请给出最小运行示例；若不存在，请解释为何 latest docs 已出现但 stable docs 没有。

### 21.2 搜索多子图 linking 稳定示例

> 请专门搜索 EZKL 官方 notebook、discussion、PR、example 中，是否有完整展示“多子图 proof commitments match / input_scale = previous output scale / split_proofs / aggregate”的稳定示例。优先给 GitHub issue / PR / notebook 链接。

### 21.3 深挖 NanoZK 工程细节

> 请继续查 NanoZK 是否有代码仓库、附录 benchmark script，特别关注：layer proof 的 circuit 组织、lookup table 大小、setup amortization 假设、residual / KV-cache / autoregressive decode 的处理。

### 21.4 追 recursive route

> 请继续搜索 2025–2026 期间，是否存在专门针对神经网络 / LLM inference 的 recursive SNARK / accumulation 论文，重点关注 intermediate states binding、是否支持 segmented/layerwise proving、是否能在最终输出 constant-size proof。

---

## 22. 这份重构对论文口径的直接影响

### 22.1 旧口径（应删除或降级）

不建议再保留以下主张作为正文核心结论：

- 低 `verify_ratio` 下系统仍然全链路可信；
- edge-cover 能保证每条边都被充分约束；
- hash chain + challenge 形成三层安全保证；
- 只要 `verified=True` 就能代表电路正确；
- P6 已证明 ZK 链完整性。

### 22.2 新口径（建议采用）

建议直接用下面这段作为答辩与论文重心：

> 我们最初实现的是一个“分段 proving + 选择性验证”的分布式推理原型。该原型能够对部分切片提供密码学可验证性，但无法为未证明切片提供端到端确定性安全保证。因此，后续重构不再尝试修补 light 节点，而是要求所有切片最终都生成 proof，并通过相邻切片输入/输出承诺的一致性建立全链路绑定；证明生成从关键路径剥离，系统在线阶段先返回 provisional result，待全部证明验证完成后升级为 certified result。

这段话与：
- NanoZK 的 layerwise + commitment chain 路线；
- DSperse 对 selective verification 的定位；
- non-composability note 对近似层验证的警告；
- EZKL 当前工程能力边界；

是完全一致的。

---

## 23. 这次重构真正的研究价值

如果你只做 patch，最后的成果很容易被解释为：

> “在一个本来就不闭合的选择性验证系统上，又加了几条检查逻辑。”

但如果你按这份文档重构，成果会变成：

> **一个有正式 security statement、有清晰 threat model、有 provisional/certified 语义、有相邻 commitment linking、有最终独立 verifier 的分布式推理系统原型。**

这会让你的毕设从：
- “带实验的工程 demo”

上升到：
- “围绕 end-to-end statement 明确展开的系统研究原型”。

而这正是答辩最需要的那一步。

---

## 24. Claude 的最终执行原则（最重要）

请本地 Claude 在整个后续执行过程中，始终遵守以下原则：

1. **不要再试图证明“部分节点可能够安全”。**
2. **所有切片最终都必须进入 proof 边界。**
3. **不要相信任何 Worker 的 correctness 自报。**
4. **执行结果与 proof 语义必须通过双端 commitments 焊死。**
5. **不要把 aggregation 当成 correctness 前提。**
6. **不要把 fidelity 与 correctness 混写。**
7. **不要先做性能，再补协议；必须先做协议，再做性能。**
8. **所有新实验必须围绕新的 end-to-end statement 来设计。**

如果只能保留一句话，那就是：

> **不要再证明“部分节点也许够安全”；要证明“所有节点最终都被证明，而且证明不阻塞执行”。**

---

## 25. 参考资料与建议阅读顺序

### 第一组：最先读

1. 仓库当前实现与 README：用于理解现状边界  
   仓库：<https://github.com/LeslieWylie/Distributed_inference_system_under_ZKP>
2. NanoZK：用于理解“逐层 proof + commitment chain + compositional soundness”  
   <https://arxiv.org/html/2603.18046v1>
3. Non-Composability Note：用于明确“不要把层级近似验证当作整体正确性”  
   <https://arxiv.org/abs/2602.15756>

### 第二组：随后读

4. EZKL stable docs（23.0.5 视角）  
   <https://pythonbindings.ezkl.xyz/en/stable/>
5. EZKL latest docs（aggregation / split_proofs 线索）  
   <https://pythonbindings.ezkl.xyz/en/latest/>
6. EZKL split-model PR / discussion（proof commitments match / scale 对齐）  
   <https://github.com/zkonduit/ezkl/pull/855>

### 第三组：作为路线对照

7. zkGPT  
   <https://www.usenix.org/system/files/usenixsecurity25-qu-zkgpt.pdf>
8. zkLLM  
   <https://arxiv.org/abs/2404.16109>
9. VeriLLM  
   <https://arxiv.org/pdf/2509.24257>
10. ZKTorch  
   <https://arxiv.org/pdf/2507.07031>
11. TensorCommitments  
   <https://arxiv.org/abs/2602.12630>
12. Artemis  
   <https://arxiv.org/abs/2409.12055>

---

## 26. 最后一句给 Claude 的工作指令

你的任务不是把旧系统“修好看”，而是把它**重定义**成一个真正成立的端到端可验证协议，并据此重构代码、重做实验、重写论文叙事。

请以“statement-first, protocol-first, verifier-first”的顺序工作。
