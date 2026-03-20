# 《基于零知识证明的分布式推理可验证性研究》重构设计说明书
**面向本地 Claude 的详细工作文档**  
版本：2026-03-20  
适用对象：本地 Claude / Gemini / 后续代码重构执行者

---

## 0. 文档目的

这份文档不是对当前仓库做“补洞式修修补补”，而是重新定义一个**真正配得上“分布式推理 + ZKP + end-to-end 可验证”**的系统目标，并给出围绕该目标的完整重构路线。

文档有五个直接目标：

1. 明确说明：当前仓库为什么**还不能**被严格称为 end-to-end 可验证系统。
2. 给出新的**正式安全目标（security statement）**与**威胁模型（threat model）**。
3. 给出一个**执行-证明解耦（deferred certification）**的新架构，使系统既保留流水线推理低延迟，又最终获得全链路证明。
4. 把该架构映射到当前 EZKL + ONNX + FastAPI 代码仓库上，形成可执行的代码重构计划。
5. 汇总截至 2026-03-20 能检索到的最新相关文献，并提炼出对本毕设最有用的设计启发。

这份文档的核心立场是：

> **不要再试图把 light 节点修补成“看起来也安全”。**  
> 正确方向应当是：**所有切片最终都生成 proof；证明生成从关键路径剥离；跨切片一致性通过承诺链和独立验证者建立。**

---

## 1. 先下最终判断：当前系统“是什么”，以及“还不是什么”

### 1.1 当前系统是什么

根据仓库 README 与代码结构，当前项目已经实现了以下几个重要部件：

- 模型切片：把一个网络拆成多段 ONNX 子模型。
- 分布式执行：由 Master 依次调度多个 Worker 做流水线推理。
- EZKL proving：部分 Worker 可为本片推理生成 proof。
- 选择性验证：通过 `verify_ratio` 或 `verified_set` 决定哪些切片出 proof。
- 三层检测思路：L1 输出哈希、L2 `processed_outputs -> processed_inputs` linking、L3 外部哈希链。
- 实验脚本：阶段性实验、选择性验证实验、隐私模式实验、P4/P6 图表采集。

因此，它不是“空壳”，也不是“只有论文没有系统”。它是一个**有运行价值的研究原型**。

### 1.2 当前系统还不是什么

但如果用严格的密码学/系统安全标准去问：

> “验证者是否能独立确认：用户最终拿到的输出，确实是整条切片链路按指定模型、指定顺序、指定输入计算得到的？”

当前答案仍然是：**不能**。

更准确地说，当前仓库还不是：

- 一个**end-to-end 可验证分布式推理系统**；
- 一个**全链路每段都受 ZKP 约束的系统**；
- 一个**由独立 verifier 而不是 worker 自己宣布 correctness 的系统**；
- 一个**把运行时数据流和证明语义真正绑定起来的系统**。

### 1.3 为什么这个判断成立

原因不是单点 bug，而是**系统 statement 还没有被完整定义并完整落实**。

如果系统允许：

- 某些切片不最终出 proof；
- proof 由 Worker 本地验证后回传 `verified=True`；
- proof 里的输出与真正发给下游/用户的输出之间没有强绑定；
- 相邻切片之间只做外部哈希链而不是双端承诺绑定；

那么系统仍存在“未被证明的计算缺口（computation gaps）”。  
只要 gap 还在，它就不是 end-to-end verifiable。

---

## 2. 必须重建的问题定义：什么叫 end-to-end verifiable distributed inference

### 2.1 正确的问题不是“某几个节点有没有 proof”

真正的问题应当写成：

> 给定请求 `req_id`、模型切片序列 `M1, M2, ..., Mn`、初始输入 `x0`，  
> 验证者需要确认系统实际执行了  
> `x1 = M1(x0), x2 = M2(x1), ..., xn = Mn(xn-1)`，  
> 并且返回给用户的最终输出正是 `xn`。

这句话里至少包含 5 个必须被证明或绑定的对象：

1. **模型绑定**：每一片实际运行的是哪一个切片模型；
2. **输入绑定**：每一片实际使用的输入是什么；
3. **输出绑定**：每一片实际产生的输出是什么；
4. **链路绑定**：第 `i` 片输出确实就是第 `i+1` 片输入；
5. **终端绑定**：最终发给用户的结果与最后一片 proof 中声称的输出一致。

只要其中任何一项依赖“节点自己说了算”，而不是 proof/commitment/independent verification，你就没有 end-to-end。

### 2.2 当前系统的真正缺口

当前系统的根缺口可以概括成一句话：

> **proof boundary 与 dataflow boundary 还没有重合。**

也就是：

- proof 证明了“某片对某输入能产生某输出”；
- 但系统运行时真正被发送、真正被消费、真正返回用户的数据，不一定与这个 proof-bound output 完全一致；
- 某些片甚至没有 proof。

这意味着系统里有“证明内语义”和“运行时语义”两套轨道，它们还没有完全焊接起来。

### 2.3 你真正要做的研究贡献

你现在最值得做的事，不是继续给 light 节点增加补丁，而是把研究问题改写为：

> **如何在分布式流水线推理中，实现全链路最终可验证（eventually certified）的 end-to-end statement，同时把高开销 proving 从关键路径中剥离？**

这比“选择性验证能不能再聪明一点”更有研究价值，也更能自圆其说。

---

## 3. 威胁模型：这部分必须在答辩中说清楚

### 3.1 参与方

建议把系统拆成以下角色：

- **Client / User**：提交初始输入并接收结果。
- **Scheduler / Master**：负责任务编排、收集证明、触发验证、生成最终证书。
- **Execution Workers**：只负责执行模型切片推理。
- **Prover Workers / Prover Service**：负责生成对应切片的 ZK proof。
- **Verifier**：独立验证 proof，可以与 Master 合并实现，也可以拆成服务。
- **Artifact Registry**：存储切片模型、设置文件、VK/PK/SRS、模型摘要等静态工件。
- **Request Log / Commitment Log**：记录每个请求的 commitment 链。

### 3.2 对抗者能力

应该明确假设以下攻击能力存在：

- 恶意 Worker 可返回任意伪造输出；
- 恶意 Worker 可伪造外部 SHA-256 哈希；
- 恶意 Worker 可试图对“真输入/真输出”出 proof，但向下游发送“假输出”；
- 相邻恶意节点可协同伪造一致外部哈希链；
- 恶意节点可重放旧请求输出；
- 恶意节点可声称“我已经 verify 过了”；
- 恶意节点可试图替换切片模型或量化设置。

### 3.3 信任假设

要把信任边界写得比以前更干净：

- **不信任任何单个 Worker 的自我声明；**
- **信任 ZKP 后端的 soundness；**
- **信任承诺函数（Poseidon / KZG / IPA commitment）抗碰撞/绑定性；**
- **信任 Master/Verifier 不与恶意 Worker 串谋**（若 Master 本身也不可信，则需进一步引入链上验证或多验证者机制）；
- **信任静态工件注册阶段已固定模型摘要、设置、VK/SRS 等。**

### 3.4 输出语义：必须区分 provisional 与 certified

这是新架构最关键的一点：

- **在线执行完成时**，系统只能返回 `provisional output`；
- **所有切片 proof 完成且验证通过后**，系统才可签发 `certified output`；
- 任一 proof 失败或链路承诺不一致，则该请求被标记为 `invalid / uncertified`。

这个 distinction 不是缺点，而是新的系统语义核心。  
你要明确承认：**低延迟与立即最终可信不能同时免费获得。**

---

## 4. 新系统的正式安全目标（建议写入论文正文）

### 4.1 End-to-End Statement

建议在论文里显式定义如下 statement：

> 对于请求 `req_id`，存在一组中间状态 `x1, x2, ..., xn-1`，使得  
> `x1 = M1(x0)`, `x2 = M2(x1)`, ..., `xn = Mn(xn-1)`；  
> 每个 `Mi` 都是预注册模型分片 `model_digest_i` 所对应的电路/权重；  
> 并且返回给用户的结果 `y` 满足 `y = xn`。

### 4.2 强化版 Statement（推荐）

更强版本可加入 commitment：

- `Cin_1 = Commit(req_id, slice_id=1, x0)`
- `Cout_i = Commit(req_id, slice_id=i, xi)`
- `Cin_{i+1} = Commit(req_id, slice_id=i+1, xi)`

并要求：

- 每片 proof 证明：  
  “我用输入 commitment `Cin_i` 对应的明文输入，通过切片模型 `Mi` 计算得到输出 commitment `Cout_i`”
- 对每条边：`Cout_i = Cin_{i+1}`

最终验证者检查：

1. 每片 proof 均通过；
2. 每片绑定的 `model_digest_i` 正确；
3. 所有相邻 commitment 相等；
4. 最终返回输出与最后一片 output commitment 对应。

### 4.3 为什么这才是正确 statement

因为它同时绑定了：

- 模型；
- 输入；
- 输出；
- 相邻链路；
- 最终返回结果。

只有这样，你才能真正说“end-to-end”。

---

## 5. 为什么“只证明部分节点”这条路线不值得继续

### 5.1 结论先行

**任何未最终出 proof 的切片，都会留下无法由 ZKP 填补的 computation gap。**

### 5.2 轻量节点补丁为什么不够

你原有的三层机制：

- L1：输出哈希；
- L2：proof linking；
- L3：哈希链；
- 再加随机挑战；

从工程上确实有价值，但从严格对抗安全角度看：

- L1 不能阻止恶意节点“同时改输出和哈希”；
- L3 不能阻止相邻协同节点伪造一致链；
- 随机挑战只是概率威慑，不是确定性保证；
- L2 只有在边两端都真正受 proof/commitment 约束时才是强绑定。

### 5.3 与 DSperse 的关系

DSperse 明确把自己定位为 **targeted verification**：它避免整模型电路化的高成本，通过只验证部分 subcomputations 来取得“pragmatic trust minimization”，并把全局一致性留给 audit、replication 或 incentive mechanisms。  
这说明 selective verification 本身就是一条**折中路线**，不是完备安全路线。  
所以你完全可以引用它作为对照：**现有公开工作也承认 targeted verification 不是 full end-to-end guarantee。**

### 5.4 你的正确转向

所以你的课题核心应转为：

> **所有切片最终出 proof，选择性不再作用于“是否证明”，而只作用于“是否立即证明 / 如何调度证明 / 是否进一步聚合”。**

这会把你的系统从“采样验证系统”提升为“延迟认证系统”。

---

## 6. 新架构总览：Deferred Certification Architecture

### 6.1 核心思想

新的系统可命名为：

**Deferred Certification Architecture for End-to-End Verifiable Distributed Inference**  
中文可译为：  
**一种面向端到端可信的执行-证明解耦分布式推理架构**

它的核心思想是：

1. 在线阶段只做**流水线推理执行**；
2. 每片执行完成后立即把输出传给下一片，保持低延迟；
3. 同时为该片提交**后台 proving job**；
4. 所有 proving 完成后，由 Master/Verifier 独立完成全链路验证；
5. 只有验证通过，请求才从 `EXECUTED_UNCERTIFIED` 升级为 `CERTIFIED`。

### 6.2 四个平面（建议作为架构图）

#### A. Execution Plane（执行平面）
职责：只负责算，不负责宣称自己是对的。

#### B. Proving Plane（证明平面）
职责：对每个切片最终生成 proof，绑定输入承诺与输出承诺。

#### C. Verification Plane（验证平面）
职责：独立验证 proof，并检查承诺链闭合。

#### D. Control Plane（控制平面）
职责：调度、状态管理、日志、重试、失败回滚、最终证书签发。

### 6.3 请求状态机

建议系统把每个请求置于如下状态机：

- `SUBMITTED`
- `EXECUTING`
- `EXECUTED_UNCERTIFIED`
- `PROVING`
- `VERIFYING`
- `CERTIFIED`
- `INVALID`
- `EXPIRED`（可选）

这个状态机要写进代码与论文，因为它把“先出结果、后认证”的语义说清楚了。

---

## 7. 最关键的技术点：不要只做输出承诺，要做双端承诺链

### 7.1 只做输出承诺为什么不够

假设第 `i` 个 Worker：

1. 正确计算得到 `real_out`；
2. 给 Master 提交 `Commit(real_out)`；
3. 后台为 `real_out` 生成 proof；
4. 但发给下游的是 `fake_out`。

则：

- 上游 proof 仍可能通过；
- Master 看到的上游 commitment 也可能没问题；
- 但下游实际消费的数据是假的。

所以，**只绑定上游输出，不足以绑定运行时链路。**

### 7.2 正确做法：相邻切片双端绑定

必须同时要求：

- 第 `i` 片 proof 公开 `Cout_i`
- 第 `i+1` 片 proof 公开 `Cin_{i+1}`

并检查：

`Cout_i == Cin_{i+1}`

这样，第 `i+1` 片被证明“真正使用”的输入，才会与第 `i` 片被证明“真正产出”的输出一致。

### 7.3 为什么这一步是整个系统的焊点

这一步一旦成立，proof boundary 与 dataflow boundary 才真正闭合。  
没有它，你只是“每片各自有 proof”；  
有了它，你才是“整条链被 stitching 成 end-to-end statement”。

### 7.4 commitment 内容不能太裸

推荐 commitment 不是裸 `Poseidon(tensor)`，而是加入域分离信息，例如：

`C_i = Poseidon(req_id || slice_id || model_digest_i || tensor_digest_or_tensor)`

这样做的目的：

- 防止不同请求之间 replay；
- 防止不同 slice 之间误拼接；
- 防止不同模型版本之间复用旧 commitment；
- 增强调试与日志可解释性。

### 7.5 是否用 Poseidon 还是 polycommit

对你当前 EZKL 场景，建议优先顺序：

1. **hashed/public（Poseidon 风格哈希承诺）**：实现难度低，适合先跑通；
2. **polycommit / KZG/IPA commitment**：若你后续要追求更高扩展性或和聚合方案更自然衔接，可作为第二阶段优化。

第一版不要被 commitment 形式拖慢。先把**链路语义**跑通，比一开始就做最优 commitment 重要。

---

## 8. EZKL 视角下的新系统应该如何落地

### 8.1 输出也必须进入 commitment 语义

EZKL 当前公开文档里，`PyRunArgs.input_visibility`、`output_visibility`、`param_visibility` 都支持：

- `public`
- `private`
- `fixed`
- `hashed/public`
- `hashed/private`
- `polycommit`

因此，新的系统不应再停留在：

- 输入 hashed，输出 public

而应升级为：

- 至少把**输入与输出**都纳入可验证承诺语义中；
- 对你的架构，最自然的第一版是：
  - `input_visibility = "hashed/public"`
  - `output_visibility = "hashed/public"`

这样验证者能拿到公开 commitment 进行 linking。

### 8.2 关于 `rescaled_outputs` 的正确使用方式

必须强调：

- 被 proof 密码学绑定的是 proof 对应的**公开实例 / public outputs**；
- `rescaled_outputs` 更适合作为**可读视图**、调试输出或论文中的数值展示；
- 不要把系统核心安全逻辑写成“验证 `rescaled_outputs`”。

系统安全上应依赖：

- public instances
- output commitment
- verifier 本地 `ezkl.verify()`

### 8.3 关于跨切片 linking 与量化尺度

EZKL 团队在自动切片讨论中明确提醒：  
**后一片 subgraph 的 `input_scale` 需要与前一片的 `output scale` 对齐**，并建议在例子中检查 stitched proof commitments 是否一致。

这说明：

- 你观察到的 `processed_outputs != processed_inputs`，不是偶然；
- 它是独立校准/独立量化下的自然后果；
- 不能简单依赖“all_public 下数值看起来一样”。

因此重构时必须加入一条规则：

> **切片边界的量化尺度必须显式对齐，不允许每片独立自由漂移。**

### 8.4 EZKL proving 的同步性与后台化

EZKL Python API 看起来仍是同步 `prove()` / `verify()` 风格。  
这不妨碍你系统层做后台 proving。  
推荐方式：

- 每个 Worker 执行完 inference 后，将 proving job 投递给本地进程池或独立 prover service；
- 可以使用 Python `multiprocessing` / `ProcessPoolExecutor`，避免 GIL 与长时间本地 proving 阻塞；
- 对每个请求维护 proving job 状态；
- Master 轮询或通过回调收集 proof 完成事件。

### 8.5 关于 aggregation：现在可以怎么定位

新 EZKL 文档中已经出现：

- `aggregate`
- `setup_aggregate`
- `verify_aggr`
- `create_evm_verifier_aggr`

这说明“proof aggregation”在当前公开文档中已经是存在的能力。  
但你的系统设计不应把 aggregation 当第一阶段前提，因为：

1. aggregation 不是解决链路 binding 的方法；
2. aggregation 只是把“多个已经成立的证明”压缩成一次验证；
3. 你的第一要务仍然是先把输入/输出承诺链和独立 verifier 跑通。

所以建议定位为：

- **Phase 1**：每片独立 proof + Master 验证全部 proof；
- **Phase 2**：可选 proof bundle / aggregate；
- **Phase 3**：若需要链上部署，再研究 aggregate verifier 合约。

---

## 9. 从当前仓库到新系统：重构总原则

### 9.1 重构不是修补

这次改造不应按“在旧接口上再补一层 if/else”来做。  
应该按下面 4 条原则重构：

1. **执行与验证彻底解耦**
2. **worker 不再宣称 correctness**
3. **每片最终都必须有 proof**
4. **系统输出从单阶段结果变成两阶段结果（provisional / certified）**

### 9.2 应该删除或降级的旧概念

以下概念建议从“主系统语义”中删除，仅保留为 baseline 或历史阶段：

- `light node is safe`
- `edge cover provides full-chain security`
- `worker verified=True`
- `hash chain as adversarial guarantee`
- `random challenge as main security mechanism`

### 9.3 应该保留的旧资产

以下资产保留并重用：

- ONNX 切片导出逻辑；
- FastAPI Worker 服务框架；
- EZKL setup / witness / proof 工具封装；
- 实验脚本总体框架；
- 配置化模型切片接口；
- 数据采集与画图脚本框架。

也就是说，你不是把项目推翻，而是把**安全核心重新安到系统中轴**。

---

## 10. 建议的新代码目录结构

下面是一个更适合新架构的目录布局：

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

### 10.1 为什么这样拆

- `execution/` 只关心数据流；
- `prover/` 只关心 proving job；
- `verifier/` 只关心 correctness；
- `compile/` 负责一次性静态工件；
- `common/` 统一类型、ID、commitment 和量化规则。

这会让你的系统从“脚本拼接项目”变成“协议实现项目”。

---

## 11. 关键数据结构设计

### 11.1 Slice Artifact

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

### 11.2 Execution Record

```python
ExecutionRecord = {
    "req_id": str,
    "slice_id": int,
    "input_commit": str,
    "output_commit": str,
    "output_tensor": list[float],      # 仅用于在线继续推理，可不长期持久化
    "executor_id": str,
    "started_at": str,
    "finished_at": str,
    "status": "executed_uncertified"
}
```

### 11.3 Proof Job

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
    "error": str | None
}
```

### 11.4 Certified Request Record

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

## 12. 在线协议：建议的请求时序

### 12.1 预处理阶段（离线）

1. 切分完整模型为 `n` 个 ONNX 子模型；
2. 为每个切片生成 EZKL settings；
3. 强制对齐边界 scale；
4. 编译电路；
5. 生成 PK/VK/SRS；
6. 计算 `model_digest_i`；
7. 将所有静态工件注册到 `Artifact Registry`。

### 12.2 在线执行阶段（关键路径）

对一个请求 `req_id`：

1. Client 提交 `x0`；
2. Master 计算或登记 `Cin_1 = Commit(req_id, 1, model_digest_1, x0)`；
3. 将 `x0` 发给 Worker 1；
4. Worker 1 执行推理，得到 `x1`；
5. Worker 1 计算 `Cout_1`；
6. Worker 1 把 `(x1, Cout_1)` 发给 Worker 2；
7. Worker 1 同时把 proving job 提交给 proving plane；
8. 重复直到 Worker n 得到 `xn`；
9. Master 将 `xn` 返回给用户，但状态标记为 `provisional`。

### 12.3 后台 proving 阶段

每个 slice 完成：

1. 基于本片实际输入/输出生成 witness；
2. 生成 proof；
3. 将 proof 与公开实例登记到 Master/Verifier。

### 12.4 最终 verification 阶段

Verifier 检查：

1. 每片 proof 有效；
2. 每片绑定正确的 `model_digest_i`；
3. `Cout_i == Cin_{i+1}` 对所有 `i` 成立；
4. 用户收到的最终输出与 `Cout_n` 一致。

若都通过：

- 请求状态从 `EXECUTED_UNCERTIFIED` 升级为 `CERTIFIED`；
- 生成证书（certificate）。

否则：

- 标记为 `INVALID`；
- 记录失败原因（哪一片 proof 失败 / 哪一条边 linking 失败）。

---

## 13. API 层要怎么改

### 13.1 当前 Worker API 的问题

当前 `/infer` 同时承担：

- 执行推理；
- 生成 proof；
- 本地 verify；
- 返回 correctness 声明；

这使职责严重混杂。

### 13.2 建议拆分后的 API

#### Execution Worker
- `POST /execute`
  - 输入：`req_id`, `slice_id`, `input_tensor`, `input_commit`
  - 输出：`output_tensor`, `output_commit`, `exec_metrics`

#### Prover Service
- `POST /prove`
  - 输入：`req_id`, `slice_id`, `input_tensor`, `output_tensor`, `input_commit`, `output_commit`
  - 输出：`job_id`

- `GET /prove/{job_id}`
  - 输出：job 状态、proof 路径、错误信息

#### Verifier Service
- `POST /verify/single`
  - 输入：单片 proof 及相关工件
  - 输出：是否通过

- `POST /verify/chain`
  - 输入：某请求的全部 single proofs + commitments
  - 输出：是否整链通过

#### Master API
- `POST /requests`
- `GET /requests/{req_id}`
- `GET /requests/{req_id}/certificate`

### 13.3 为什么不要保留 `/infer_light`

因为在新系统里，light 不再是“最终不证明”的节点。  
最多只存在：

- `execute_now_prove_later`
- `execute_and_sync_prove`

而不应该再有：
- `execute_without_eventual_proof`

---

## 14. 代码重构映射：如何处理你当前仓库里的主要文件

### 14.1 `distributed/master.py`
当前职责太重：调度 + 检测 + 打印 + 安全判断 + 随机挑战。  
重构后应拆成：

- `execution/scheduler.py`
- `verifier/verify_chain.py`
- `services/master_api.py`

`master.py` 不应再包含：
- “worker returned verified means okay”
- “hash chain = security”
- “随机挑战作为核心安全逻辑”

### 14.2 `distributed/worker.py`
当前同时扮演：
- execution worker
- prover
- self-verifier
- 故障注入器

建议拆成：

- `services/execution_worker.py`
- `prover/worker_local.py`
- `tests/fault_injection.py`

尤其要删掉：
- worker 自己返回 `verified` 作为权威结论。

### 14.3 `common/utils.py`
目前混合了：
- EZKL 初始化
- proving
- verify
- visibility 配置
- I/O 辅助

建议拆成：

- `compile/build_circuits.py`
- `prover/ezkl_adapter.py`
- `verifier/verify_single.py`
- `common/quantization.py`
- `common/commitments.py`

### 14.4 `scripts/run_experiments.py`
保留，但改名为：
- `experiments/baseline_selective.py`

不再代表主系统，只代表旧 baseline。

### 14.5 `scripts/run_advanced_experiments.py`
改成：
- `experiments/baseline_selective_attacks.py`

### 14.6 `scripts/run_p4_p6_experiment.py`
拆成两类实验：

- `experiments/quantization_fidelity.py`
- `experiments/commitment_linking_eval.py`

不要再让 P6 用 `data.get("verified")` 直接代表“circuit integrity”。

---

## 15. 一个必须正视的数学问题：层级近似证明的不可组合性

2026 年 2 月有一篇很关键的短文《A Note on Non-Composability of Layerwise Approximate Verification for Neural Inference》。它指出一种很自然但危险的想法：

> “如果每一层都在容差 `δ` 内近似正确，那么整个网络最终输出也应当是合理的。”

这在一般情况下**并不成立**；文中给出了反例，说明逐层近似误差可以在组合后把最终输出任意偏转到一个给定范围内。

这篇文章对你有两个重要启发：

1. 你不能把“每层差不多对”直接当成 end-to-end correctness；
2. 若使用量化/近似，必须把系统 statement 写成：
   - **对量化模型 / 电路语义正确**；
   - 而不是“对原始浮点模型近似正确”的模糊说法。

因此，论文中最好区分两件事：

- **Circuit correctness**：对量化后电路语义的严格正确；
- **Model fidelity**：量化电路输出与原始浮点模型输出的误差大小。

这是两个不同维度，不要混写。

---

## 16. 你论文中的 fidelity 应该怎么重新定义

### 16.1 当前 P4 的问题

当前 P4 更接近“切片 PyTorch 模型和完整 PyTorch 模型是否一致”，而不是“EZKL 量化后输出与浮点基线的偏差”。

### 16.2 正确的 fidelity 分层

建议至少拆成两层：

#### F1. Partition Fidelity
完整浮点模型 vs 切片后按原始浮点语义串联的模型  
目的：检验切片是否改变函数组合。

#### F2. Quantization / Circuit Fidelity
完整浮点模型 vs EZKL witness/proof-bound outputs  
目的：检验 zkML 量化、电路化引入的误差。

#### F3. End-to-End Certified Fidelity（可选）
用户最终拿到的 certified output vs 浮点基线  
目的：检验最终经过全链路证明的系统输出与原始模型偏差。

### 16.3 为什么这样更稳

因为这样你就能避免老师一句追问把图打掉：

> “你这张 fidelity 图到底测的是切片误差，还是 zk 电路量化误差？”

---

## 17. 实验体系应该如何重做

### 17.1 新实验分组

建议新的实验体系分为 4 大组。

#### G1. Baseline Experiments（历史基线）
- selective verification
- edge cover / random / contiguous
- old hash-chain attacks

目的：作为旧方案对照，而不是主结论。

#### G2. Protocol Correctness Experiments（新主实验）
- all slices eventual proof
- independent verifier correctness
- commitment link success rate
- forged output / forged link / replay attacks

目的：证明新协议真正补上了链路缺口。

#### G3. Latency Decomposition Experiments
- pure execution latency
- proving latency
- certification completion latency
- provisional-to-certified delay

目的：展示执行-证明解耦的实际价值。

#### G4. Aggregation / Scalability Experiments（可选）
- number of slices vs verify cost
- aggregate proof vs multi-proof verification
- scaling behavior under 2/4/8 slices

### 17.2 新的指标体系

建议正文固定使用以下指标：

- `execution_latency_ms`
- `provisional_latency_ms`
- `certification_latency_ms`
- `total_proof_gen_ms`
- `total_verification_ms`
- `per_slice_proof_ms`
- `all_links_verified`
- `certification_success_rate`
- `fidelity_mae` / `fidelity_max_abs_err`
- `prover_cpu_mem_peak_mb`

### 17.3 不建议再把什么作为主指标

不建议再把以下指标放在主结论里：

- `worker returned verified`
- 单独的 `hash_chain_ok`
- 未区分并发与串行含义的“throughput”

---

## 18. 代码迁移计划：建议的四阶段落地路线

### Phase A：先做一个“慢但对”的版本
目标：第一次真正满足 end-to-end statement。

要求：
- 所有切片同步出 proof；
- Master 独立 verify；
- 输入/输出承诺链跑通；
- 不做后台 proving；
- 不做 aggregation。

交付物：
- 能生成 certified result；
- 能检测 forged output / replay / broken link。

### Phase B：执行-证明解耦
目标：恢复低延迟。

要求：
- 在线执行立即返回 provisional output；
- proving 放入后台进程池；
- Master 维护请求状态机；
- 支持最终 certificate 查询。

交付物：
- provisional/certified 双阶段输出；
- certification delay 统计。

### Phase C：证明工程优化
目标：提升吞吐与资源利用。

可做：
- multiprocess local prover
- dedicated prover service
- witness cache
- proof queue
- failed job retry

### Phase D：可选聚合
目标：减少验证者端的 proof 数量与验证次数。

要求：
- 在单片 proof 与链路逻辑都正确后再做；
- 比较 aggregate 前后验证时延与工程复杂度。

---

## 19. 你现在最值得立刻修改的系统声明

建议把 README / 答辩口径改成以下版本：

### 旧表述（不建议）
- 选择性验证下系统仍可全链路检测恶意节点；
- edge cover 可保证链路安全；
- hash chain + challenge 形成三层安全；
- verify_ratio<100% 时也可保持可信。

### 新表述（建议）
- 当前仓库最初实现的是“分段 proving + 选择性验证”的研究原型；
- 该原型能够对部分切片提供密码学可验证性，但无法为未证明切片提供确定性安全；
- 因此，后续重构采用“所有切片最终出 proof、执行与证明解耦、相邻切片输入/输出承诺一致性验证”的端到端可信架构；
- 新架构返回结果先为 provisional，待全链路验证完成后升级为 certified。

这会让你的系统表述从“有漏洞还硬撑”变成“发现问题并上升到更强设计”。

---

## 20. 最新相关文献综述（截至 2026-03-20）

下面只挑与你重构最相关的文献，而不是泛泛综述。

### 20.1 Survey：ZKML 总览
**[R1] A Survey of Zero-Knowledge Proof Based Verifiable Machine Learning (2025)**

这篇综述总结了 2017-2024 的 ZKML 研究，并按 verifiable training / inference / testing 分类。  
对你最有价值的作用不是具体方案，而是：

- 帮你把工作定位到 verifiable inference；
- 说明 ZKML 领域的核心痛点仍是效率、通用性、量化、电路化与大模型扩展性；
- 适合作为综述背景引用。

**对你启发：**  
你的工作不需要再把“选择性验证”包装成通用安全答案，而应聚焦“分布式推理中的 end-to-end statement 如何定义与实现”。

---

### 20.2 DSperse：targeted verification 的代表
**[R2] DSperse: A Framework for Targeted Verification in Zero-Knowledge Machine Learning (2025)**

DSperse 明确提出：

- 它做的是 distributed inference with strategic cryptographic verification；
- 允许只验证某些 slices；
- 全局一致性要靠 audit、replication 或 economic incentives。

**对你启发：**

1. DSperse 证明了“selective verification 作为工程折中”是合理研究方向；
2. 但它也等于间接承认：**这不是 full end-to-end cryptographic guarantee**；
3. 因此你现在转向“所有切片最终都证明”的路线，在学术上是合理升级。

**你可以怎么引用它：**  
“现有 targeted verification 框架强调实用的 trust minimization，而非完整全链路证明；本文因此进一步探索 eventual full-chain certification。”

---

### 20.3 zkLLM：整次推理大证明路线
**[R3] zkLLM: Zero Knowledge Proofs for Large Language Models (2024)**

zkLLM 的关键点是：

- 为 LLM inference 生成 correctness proof；
- 强调 specialized proof system；
- 文中声称对 13B 参数模型，整个 inference process 的 correctness proof 可在 15 分钟内完成，proof 小于 200KB。

**对你启发：**

1. zkLLM 代表“把完整推理拉进证明边界”的强 statement 路线；
2. 它不是多 Worker proof stitching 的系统，但它说明：  
   **真正强的 verifiability 方案，最终都倾向于覆盖完整 inference，而不是留下未证明 gap。**
3. 你可以把自己的路线理解为：  
   在分布式条件下，用 slice-level proof + commitment linking 逼近“完整 inference proof”的语义。

---

### 20.4 VeriLLM：低成本公开可验证，但不是 ZKP 完整路线
**[R4] VeriLLM: A Lightweight Framework for Publicly Verifiable Decentralized Inference (2025/2026 v4)**

VeriLLM 的特点：

- 针对 decentralized LLM inference；
- 使用 lightweight empirical rerunning + minimal on-chain checks；
- 验证成本约为底层推理成本的 1%；
- 提出了 isomorphic inference-verification architecture。

**对你启发：**

1. VeriLLM 说明：在 decentralized inference 里，执行网络与验证网络可以同构设计；
2. 它对你有重要系统启发：  
   **不要把 verifier 视为完全脱离执行网络的旁观者；可以把验证资源和执行资源做统一编排。**
3. 但 VeriLLM 不是 full ZKP 路线，它更多是轻量验证 + 激励设计。

**你可以借鉴的不是它的安全结论，而是：**
- execution/verification role multiplexing
- 将验证从“额外孤立流程”变成“系统级资源编排的一部分”

---

### 20.5 NanoZK：最值得你重点跟进的新论文
**[R5] NanoZK: Layerwise Zero-Knowledge Proofs for Verifiable Large Language Model Inference (2026-03 preprint)**

这篇是目前最贴近你新方向的文献之一。它的几个关键点：

- 明确提出 **layerwise proof framework**；
- 每层生成 constant-size proof；
- 使用 **commitment chains** 把各层 proof 连接起来；
- 强调 parallel proving；
- 在“无法证明所有层”的情况下还提出 Fisher-guided prioritization。

**对你启发非常直接：**

1. 你的系统核心 idea —— “分层/分片 proving + 链接中间状态” —— 在 2026 的 LLM 方向上已经被明确提出；
2. 这给你的毕设一个很强的背书：  
   你不是在做奇怪的土法拼接，而是在做一个正在被前沿工作验证有价值的方向；
3. 它也证明了：  
   **layerwise proof 不是天然错误，关键是要有 formal commitment chaining / compositional soundness。**

**建议：**  
NanoZK 是你后续要重点让 Gemini/Claude 深读的一篇。

---

### 20.6 递归证明路线
**[R6] Zero-Knowledge Proof Based Verifiable Inference of Models (2025)**

这篇工作强调：

- recursively composed zero-knowledge proofs；
- 支持线性与非线性神经网络层；
- constant-size proofs；
- 无需 trusted setup 的框架化路线。

**对你启发：**

1. 它代表另一条很“干净”的路线：  
   不只是多片 proof 并列，而是通过 recursion 把整体压成更短的最终证明；
2. 这对你意味着：
   - Phase 1 不必做 recursion；
   - 但论文 future work 完全可以明确写成：  
     “从 slice-level linked proofs 向 recursive composition 演进”。

---

### 20.7 Commit-and-Prove SNARKs
**[R7] Artemis: Efficient Commit-and-Prove SNARKs for zkML (2024)**

Artemis/Apollo 的重点是：

- zkML 里不仅 proving 计算贵，**验证 commitment 的开销**也可能很重；
- 提出 commit-and-prove SNARK 以更高效处理 commitment 验证。

**对你启发：**

1. 你新架构会大量依赖 commitment linking；
2. 这意味着将来瓶颈可能不只是 proving，也可能是 commitment verification；
3. 这篇文献非常适合写进“后续优化方向”：
   - 第一版先把 commitment chain 做对；
   - 后续再研究 CP-SNARK / 更便宜的 commitment verification。

---

### 20.8 TeleSparse：面向大模型验证成本的工程优化
**[R8] TeleSparse: Practical Privacy-Preserving Verification of Deep Neural Networks (2025)**

这篇工作强调的是：

- 对现代神经网络直接做 ZK-SNARK 代价很高；
- 需要通过更 ZK-friendly 的后处理或稀疏化思路来降低成本。

**对你启发：**

你的系统要想以后扩展到大模型，光靠“切片 + 多机 proving”不够；  
最终还要考虑：
- 模型结构的 ZK-friendly 化；
- 非线性算子近似；
- 大矩阵运算的证明成本优化。

这类论文可以帮助你把 future work 写得更可信。

---

### 20.9 Layerwise Approximate Verification 不可组合性
**[R9] A Note on Non-Composability of Layerwise Approximate Verification for Neural Inference (2026)**

这篇虽然短，但特别重要。  
它提醒你：

- 层级/分片验证并不自动推出全局合理输出；
- 近似/容差 statement 必须非常小心。

**对你启发：**

这篇文献直接支撑你把 fidelity 与 correctness 分离。  
也支撑你不要把“误差不大”写成“系统可信”。

---

## 21. 对你最有用的文献结论总结成三句话

1. **DSperse 告诉你：选择性验证是折中路线，不是完备安全。**
2. **NanoZK 告诉你：layerwise proof + commitment chains 是值得做、而且正在变热的方向。**
3. **Non-Composability Note 告诉你：你必须把“电路正确性”和“浮点 fidelity”严格分开写。**

如果你把这三句话写进答辩思路里，整条叙事会立刻变得成熟。

---

## 22. 你现在可以安全声称的研究贡献（建议）

如果按这份文档去重构，最终你可以安全地把贡献写成：

### C1. 问题重定义贡献
提出了一个适用于分布式切片推理场景的 end-to-end verifiable inference statement，明确区分执行结果与认证结果。

### C2. 协议贡献
提出了一个基于相邻切片输入/输出双端承诺一致性验证的 linking 机制，用于把 slice-level proofs 组合成全链路证明语义。

### C3. 系统贡献
设计并实现了一个执行-证明解耦的 deferred certification 架构，使所有切片最终都受 proof 约束，同时降低在线关键路径时延。

### C4. 实验贡献
通过对比 selective verification baseline 与新架构，分析了安全边界、证明成本、认证延迟与 fidelity 的关系。

这 4 条贡献，比“我们提出了三层检测 + edge cover”稳得多。

---

## 23. 对本地 Claude 的明确任务指令

下面这段可以直接发给本地 Claude 作为工作说明。

### 23.1 任务定位
请不要沿用当前仓库的“选择性验证 + light 节点修补”思路。  
请把任务目标提升为：

> 重构一个 end-to-end verifiable distributed inference system，要求每个切片最终都生成 proof，执行与 proving 解耦，最终由独立 verifier 完成全链路认证。

### 23.2 Claude 需要完成的输出
请产出以下内容：

1. 一个新的协议文档：
   - threat model
   - security statement
   - roles
   - message flow
   - failure semantics
   - certificate semantics

2. 一份新的代码目录结构与模块职责分配；

3. 一份最小可运行 MVP 的实现计划：
   - Phase A: synchronous full-proof linked pipeline
   - Phase B: asynchronous proving
   - Phase C: optional aggregation

4. 一份基于当前仓库的迁移清单：
   - 哪些文件删除
   - 哪些文件重命名
   - 哪些函数拆分
   - 哪些接口改成新的 API

5. 一份伪代码：
   - `handle_request()`
   - `execute_slice()`
   - `submit_proof_job()`
   - `verify_request_chain()`
   - `issue_certificate()`

### 23.3 Claude 的限制
请避免以下误区：

- 不要把 `hash chain` 当作 adversarial guarantee；
- 不要把 Worker 返回的 `verified=True` 当作独立验证；
- 不要把 selective verification 继续包装成全链路可信；
- 不要把 fidelity 与 correctness 混成一个指标；
- 不要只做 output commitment 而忽略 next-slice input commitment。

---

## 24. 如果你要让 Gemini 继续深搜，建议直接复制这些检索需求

下面是我认为最值得继续让 Gemini 深挖的几个方向。

### GQ1. NanoZK 深读
请搜索并总结：
- NanoZK 的完整协议；
- 它的 commitment chain 是如何定义的；
- compositional soundness 的正式 statement；
- proof size、并行 proving、layer granularity 的具体设计；
- 是否适合映射到非 Transformer 小模型切片场景。

关键词：
- `"NanoZK Layerwise Zero-Knowledge Proofs commitment chains compositional soundness arXiv 2603.18046"`

### GQ2. EZKL aggregation 的版本细节
请确认：
- EZKL 23.0.5 中 `aggregate`, `setup_aggregate`, `verify_aggr` 是否可用；
- 是否支持 split proofs / segmented circuits 的聚合；
- 是否有官方 notebook 或 issue 讨论“多子图 proof 聚合 + linking”；
- 是否存在稳定示例。

关键词：
- `"EZKL 23.0.5 aggregate setup_aggregate verify_aggr split_proofs example notebook"`
- `"zkonduit ezkl proof commitments match subgraph input_scale output scale"`

### GQ3. zkGPT / 2025-2026 LLM verifiable inference 路线
请整理 2025-2026 期间：
- zkGPT
- NanoZK
- VeriLLM
- 递归 SNARK for inference
- 其它 layerwise/recursive verifiable inference 方案

重点关注：
- monolithic proof vs layerwise proof vs recursive aggregation
- intermediate state binding
- commitment chaining
- latency and proof size

关键词：
- `"2025 2026 verifiable LLM inference layerwise proof recursive proof commitment chain zkGPT NanoZK VeriLLM"`

### GQ4. Layerwise approximate verification 的理论边界
请深挖：
- `A Note on Non-Composability of Layerwise Approximate Verification for Neural Inference`
- 该问题对量化 zkML 的实际影响；
- 对“每层容差证明”体系的限制；
- 是否存在可组合条件或修正定理。

关键词：
- `"Non-Composability of Layerwise Approximate Verification for Neural Inference implications quantized zkML"`

### GQ5. Commit-and-Prove SNARK 与 zkML commitment verification
请总结：
- Artemis/Apollo 的核心机制；
- 与 Halo2 / IPA / KZG 的适配关系；
- 对多切片 commitment linking 的潜在价值；
- 是否适合你未来做 proof bundle / aggregate。

关键词：
- `"Artemis Commit-and-Prove SNARK zkML Halo2 IPA KZG commitment verification"`

---

## 25. 结论：这次重构的真正价值是什么

这次重构最重要的不是“把代码写得更漂亮”，而是把你的毕设从：

> 一个带有分段 proving 和选择性验证实验的分布式推理原型

升级为：

> 一个有清晰 security statement、清晰 threat model、清晰 provisional/certified 语义、并且全链路最终可认证的分布式推理系统原型。

这是一个本质上的升级，而不是局部修 bug。

如果只能用一句话总结新的路线，那就是：

> **不要再证明“部分节点也许够安全”；要证明“所有节点最终都被证明，而且证明不阻塞执行”。**

---

## 参考文献（供 Claude / 论文整理使用）

[R1] Zhizhi Peng et al. *A Survey of Zero-Knowledge Proof Based Verifiable Machine Learning*. arXiv, 2025.  
[R2] Dan Ivanov et al. *DSperse: A Framework for Targeted Verification in Zero-Knowledge Machine Learning*. arXiv, 2025.  
[R3] Haochen Sun, Jason Li, Hongyang Zhang. *zkLLM: Zero Knowledge Proofs for Large Language Models*. arXiv, 2024.  
[R4] Ke Wang et al. *VeriLLM: A Lightweight Framework for Publicly Verifiable Decentralized Inference*. arXiv, 2025/2026.  
[R5] *NanoZK: Layerwise Zero-Knowledge Proofs for Verifiable Large Language Model Inference*. arXiv, 2026-03 preprint.  
[R6] *Zero-Knowledge Proof Based Verifiable Inference of Models*. arXiv, 2025.  
[R7] Hidde Lycklama et al. *Artemis: Efficient Commit-and-Prove SNARKs for zkML*. arXiv, 2024.  
[R8] *TeleSparse: Practical Privacy-Preserving Verification of Deep Neural Networks*. arXiv, 2025.  
[R9] Or Zamir. *A Note on Non-Composability of Layerwise Approximate Verification for Neural Inference*. arXiv, 2026.  
[R10] EZKL Python Bindings Documentation, public docs snapshot accessed 2026-03-20.  
[R11] zkonduit/ezkl discussion and PR notes on auto-splitting ONNX models and matching subgraph commitments, GitHub discussion/PR materials.

## 26. 当前仓库中最能说明“必须重构而非补洞”的代码证据

下面这些是当前仓库里最关键的“现状证据”，它们不是为了指责实现，而是为了帮助本地 Claude 明白：为什么这次应该做协议级重构。

### 26.1 README 与目录说明仍把系统描述为 “Master 调度 + 三层校验”
仓库主页将 `distributed/master.py` 描述为 “Master 调度 + 三层校验”，并在实验摘要中把 8 切片、50%/25% 验证率对应的“检测率”写成 100%。  
这说明当前文档叙事仍然把 selective verification 当成主要安全结论，而不是 baseline。

### 26.2 `common/utils.py` 中当前 `hashed` 模式并没有把输出也放入 hashed visibility
代码中 `visibility_mode == "hashed"` 时，配置是：
- `input_visibility = "hashed"`
- `output_visibility = "public"`
- `param_visibility = "hashed"`

这意味着当前系统并没有实现“输出 commitment 链”，更没有实现“相邻切片输入/输出双端承诺链”。  
因此当前 `hashed` 模式最多只能说“部分实例以哈希方式可见”，不能说“系统已经实现输出 Poseidon 承诺链”。

### 26.3 `common/utils.py` 中 proving 与 verify 仍在同一个 helper 里完成
`ezkl_prove()` 中先调用 `ezkl.prove()`，再调用 `ezkl.verify()`，并把：
- `verified`
- `proof_instances`
- `proof`

一起返回。  
这说明当前系统仍然是“worker 端本地 verify，再把结果交给 Master”，而不是“Master 独立 verify”。

### 26.4 `master.py` 只是读取并打印 `verified`
`master.py` 在接收 worker 返回后，会直接打印 `verified: {data['verified']}`，但不在 Master 侧重新执行 verifier。  
这意味着安全结论仍然依赖 Worker 端自报。

### 26.5 `master.py` 中 L2 proof linking 的判断条件仍然局限在有 proof 的边上
L2 检查逻辑是：
- 当前 slice `use_proof` 时才尝试；
- 还要求 `prev_instances` 和 `curr_instances` 都存在。

这说明它并没有形成对所有相邻边的统一 binding 语义。  
从协议角度看，它仍然是“局部 linking 检查”，不是 end-to-end statement 的组成部分。

### 26.6 `master.py` 的随机挑战 `/re_prove` 传的是上一片 `output_data`
随机挑战部分向 `/re_prove` 发送的 JSON 使用了：
- `json={"input_data": target_data.get("output_data", [])}`

这说明 challenge 使用的是上一阶段记录的 `output_data`，而不是被挑战切片当时真正接收的原始输入。  
即使这个逻辑在形状上能跑通，它也不再是“重证明原始 statement”。

### 26.7 `worker.py` 中 `hash_out` 基于 `correct_output`，而故障注入修改的是 `output_data`
代码显示：
- 先计算 `correct_output`
- 再用它计算 `hash_out`
- 然后若 `fault_type` 非空，再修改真正返回的 `output_data`

这会导致当前攻击实验更像“人为设计为可检测的故障注入”，而不是“面对适应性恶意节点时的最强对抗模型”。  
也正因此，旧实验的检测率不能直接当成新架构的安全证明。

### 26.8 `run_experiments.py` 使用的是 `params={"fault": ...}`，而 Worker 接收的是 `fault_type`
这意味着该脚本中的某些故障实验可能没有真正按预期注入故障。  
所有来自这一脚本的“检测率”数字，都应在重构后重跑并重新确认。

### 26.9 `run_p4_p6_experiment.py` 中 `circuit_integrity = data.get("verified", False)`
这说明 P6 目前实际上测的是“Worker 说自己验证过了没有”，而不是：
- Master 独立 verify 结果；
- 或相邻 commitment linking 是否成立。

因此，P6 不应再被解释为“ZK 链完整性已经得到验证”，最多只能说“当前原型中的 proof-enabled sanity check”。

### 26.10 这些问题共同指向的不是 patch，而是 statement-level refactor
如果把上面所有现象串起来，它们共同说明：

- 当前仓库已经具备良好的工程雏形；
- 但安全核心还停留在“局部 proof + 额外检测”的阶段；
- 真正缺失的是一个由独立 verifier 检查、由 commitment chain 完整闭合的 end-to-end protocol。

因此，这次工作应当被定位为：
**从 selective-verification prototype 重构为 deferred-certification end-to-end verifiable pipeline。**