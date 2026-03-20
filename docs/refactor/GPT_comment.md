可以。我直接给你一份**按任务书对照的、非常详细的整改优先级清单**，不兜圈子。

先说总判断：

**你这次更新，方向已经明显比旧版对了。**
你已经不再停留在 v1 那种 selective verification 原型，而是开始做 v2 的 deferred certification、全切片最终出 proof、独立 verifier、链路验证和阶段化实验。这说明你已经意识到“light 节点补丁”不是终点，而在往 end-to-end 方向走。([GitHub][1])

但如果**严格按任务书**来衡量，你现在仍然有 4 个硬伤没有跨过去。任务书要的是：

> “原生支持分布式推理的零知识证明框架”，
> “适配分区计算、异步执行特性”，
> “在保障推理过程隐私安全的基础上，实现推理完整性验证”，
> 并评估“证明生成时间、验证时间、推理延迟、单节点 CPU/内存、吞吐量、恶意节点检测准确率”。

而你现在最关键的问题是：

---

## 一、最核心的问题：你现在更像“单机上的端到端可验证原型”，还不是“原生分布式推理框架”

这是和任务书偏差最大的一点。

### 现在代码实际在做什么

v2 的主流程 `run_certified_pipeline()` 和 `run_deferred_pipeline()`，本质上都是**一个本地 orchestrator** 按切片顺序创建 ONNX Runtime session，在一个 Python 进程内依次跑每片推理；证明阶段则由本地 `prove_slice()` 或 `prove_slices_parallel()` 处理。实验脚本也是直接调用这些函数，并不是把每个 slice 作为真正独立节点去调度。([GitHub][1])

### 为什么这和任务书冲突

任务书要的是“原生支持分布式推理”的框架。
你现在实现的是：

* **分片了**
* **每片最终都出 proof 了**
* **执行和 proving 可以解耦了**

但它仍然主要是**单机场景下的协议模拟/原型验证**，还不是“多节点、多主体、分布式资源调度”的真正框架。

### 一针见血地说

**你现在解决的是“如何把 end-to-end 验证协议写对”，不是“如何把这个协议落到真实分布式系统里”。**

### 这是答辩时最危险的一句

如果你现在直接说“我已经实现了面向分布式推理的零知识证明框架”，老师完全可以反问：

> 你这里真正的“分布式”体现在哪里？
> 是多台节点、异步网络消息、远程 prover/verifier，还是一个进程里按切片 for-loop？

这个问题你现在不占便宜。

---

## 二、第二个致命问题：你现在的“commitment linking”其实不是严格的 commitment chain，而是“公开值近似拼接”

这是目前协议层最大的理论短板。

### 现在代码实际怎么做

`build_circuits.py` 里虽然注释起初说的是 input/output hashed，但真正的设置改成了：

* `input_visibility = "public"`
* `output_visibility = "public"`
* `param_visibility = "fixed"`

而且注释直接写明原因：因为 hashed 模式下相邻切片在独立量化尺度下对不上，所以退回到 public mode，再通过比较 `rescaled_outputs[i] ≈ rescaled_inputs[i+1]` 做 linking。([GitHub][2])

`verify_single.py` 也不是在提取某种 request-bound commitment，而是从 proof 里读 `rescaled_inputs` / `rescaled_outputs`（若没有再退回到 `processed_*`），然后 `verify_chain.py` 用一个动态 epsilon 去做近似比较，再额外做 terminal binding。([GitHub][3])

### 这意味着什么

你现在这套东西，严格说更准确的名字应该是：

**proof-bound public-value linking**
而不是
**cryptographic commitment chain**

因为你现在验证的是：

[
\text{rescaled_outputs}*i \approx \text{rescaled_inputs}*{i+1}
]

不是：

[
C^{out}*i = C^{in}*{i+1}
]

### 为什么这很重要

任务书的目标是“推理完整性验证框架”。
如果你协议层自己都还没把“链路绑定”定义成一个严格的密码学对象，而是退化成“公开中间值的近似相等”，那你就还不能把它包装成一个完全成熟的 end-to-end binding 机制。

### 你现在最该改的不是代码，而是口径

在论文和 README 里，**现在不能再把它写成 commitment chain 已经实现**。
你现在更稳的表述应该是：

> 当前实现的是基于 proof-bound public instances 的跨切片 linking；
> 真正的 hashed/polycommit commitment chain 由于独立量化尺度不一致，尚未工程化落地。

这个表述是稳的，而且不会被一问就崩。

---

## 三、第三个致命问题：你还没有真正把“请求本身”绑定进 end-to-end statement

这个问题很隐蔽，但非常关键。

### 现在代码里的现象

你在 `verify_chain()` 里定义了 `initial_input_commit` 参数，但当前实现没有真正把它作为首片绑定检查的一部分用起来；核心检查仍然是：

1. 单片 proof verify
2. 相邻 slice 的输入输出近似 linking
3. 最后一片输出与 provisional output 的 terminal binding

也就是说，你现在确实做了“链路内部一致性”，但**还没有把“这条链就是这次请求 req_id 的链”这个 statement 完整做实。** ([GitHub][3])

### 更直白一点

你目前验证的是：

> 这些 proof 彼此之间能连起来

但你还没有充分验证：

> 这些 proof 就是用户这次请求的那条计算链

### 为什么这会影响 replay 问题

你在 `v2/common/commitments.py` 里设计了带域分离的 SHA-256 承诺，这是很好的想法；但 `deferred_pipeline.py` 自己也写得很诚实：这些 `compute_commitment(...)` 目前“仅用于审计日志和请求追踪，不作为安全验证依据”，真正的安全绑定来自 proof 的公开实例。([GitHub][4])

这就导致一个结果：

**req_id / slice_id / model_digest 虽然被你算进了外部 commitment，但并没有真正进入 proof statement。**

所以现在的“replay 防护”还不是协议层强绑定，而更像系统层追踪。

### 你的 replay 实验也不是真 replay

你代码里的 replay 故障注入，本质上是把输出改成固定常量（例如 `[0.42, ...]`），这更像“伪造中间值”，不是“复用另一条历史请求的合法 proof / 合法中间状态 / 合法证书”。
所以你现在实验里测到的 replay invalid，**不能直接写成“系统已防止跨请求重放”**。

### 必改项

你后面要么：

1. 把 `req_id` / `slice_id` / `model_digest` 真正放进 proof 可验证的公开实例或 commitment 中；
   要么
2. 在论文里老实说：当前 replay 防护仍主要依赖系统级 request scoping 与 verifier 端匹配，不是 proof 内生防重放。

---

## 四、第四个致命问题：任务书要“隐私安全”，你现在的 v2 还不能宣称隐私成立

这个必须说清楚，不然会被直接抓。

### 现在代码实际情况

`build_circuits.py` 最终选的是：

* 输入：public
* 输出：public
* 参数：fixed

而不是 hashed/private。代码注释也明确说明，这是为了让相邻切片 linking 能工作。([GitHub][2])

### 这意味着什么

你现在的 v2，是在用**公开中间值可验证性**换取链路可拼接性。
这在工程上完全可以接受，但它**不能再被表述成“在保障推理过程隐私安全的基础上”已经完成。**

### 跟任务书的关系

任务书把“隐私安全”写进了总目标。
所以你现在最稳的处理方式只有两种：

#### 方案 A：收口

把论文写成：

> 本工作当前优先解决 end-to-end integrity / certification；
> 隐私模式下的跨切片 linking 因独立量化尺度问题尚未完全打通，作为后续工作。

#### 方案 B：补一个最小隐私分支

哪怕不能 end-to-end，也至少补一个单片/两片的 hashed/private demo，证明你确实探索过“隐私 + 验证”的技术路径。

不然老师会说：
**你题目里有“零知识证明框架”，结果你主协议是 public/public/fixed，这个零知识体现在哪里？**

---

## 五、第五个问题：你现在的 Phase A 其实并不是真正的“慢但正确”

这是一个代码层的关键语义问题。

### 现在代码实际流程

在 `run_certified_pipeline()` 里，每一片是这样跑的：

1. 先用 ONNX 得到 `output_tensor`
2. 若注入攻击，就直接篡改 `output_tensor`
3. 再调用 `prove_slice()` 生成 proof
4. 然后把 `current_input = output_tensor` 传给下一片

也就是说，**即便已经同步生成了 proof，你也没有把“proof 中绑定的输出”作为下一跳输入的唯一来源**，而是仍然把执行路径里那个可能被篡改的 `output_tensor` 往后传。([GitHub][1])

### 这说明什么

你当前的 Phase A 更准确的语义其实是：

> 同步执行 + 同步 proving + 末端统一验证

而不是：

> 每片都先证明，再把已认证输出传给下一片

### 为什么这重要

如果你把 Phase A 叫“慢但正确”，老师会自然理解成：

> 既然慢，你是不是每片都已经先认证再传？

但你现在不是。你现在还是“先跑完整条链，再判 invalid”。

### 整改建议

这里有两个选择：

#### 选择 1：改口径

把 Phase A 改名成：

**synchronous end-of-pipeline certification**

而不是 slow-but-correct。

#### 选择 2：真改协议

每片 prove 完成后，直接从 proof 的公开输出中取 proof-bound output，作为下一片输入。
这样它才真是“同步认证链”。

如果你时间不够，我建议先选 **改口径**。因为这不影响你整个 v2 的主方向，但能避免被问穿。

---

## 六、第六个问题：你现在的“低开销优化”更多是在线延迟优化，不是任务书意义上的资源负载优化

### 你现在做对了什么

你已经做到了一个很重要的点：

* 在线执行只负责拿到 provisional output
* proving 放到后台并行
* 最后 verifier 再签发 certified output

这正是 deferred certification 的核心。([GitHub][4])

### 但还差什么

任务书写得很明确：要“降低单一节点资源压力”“优化证明生成效率与系统可扩展性”。

你现在的并行 proving 主要还是：

* 本地子进程
* 同机资源并行
* 不是跨节点 offload
* 也没有 aggregation / folding / split-proof bundle

所以现在更准确的说法是：

> 你已经优化了**在线响应延迟**，
> 但还没有真正证明你优化了**分布式资源负载结构**。

### 这一点怎么补

如果你时间有限，至少补一组：

* 单进程 proving
* 2 子进程 proving
* 4 子进程 proving

再加上：

* 每个 proving worker 的 CPU%
* 每个 proving worker 的 RSS 峰值
* 总 wall-clock
* 总 CPU-time

这样你就能把“低开销优化”从“只有 latency”扩展到“资源画像”。

---

## 七、第七个问题：你的评测体系还没有闭环到任务书要求

这点很实在。

### 任务书要求的指标

任务书点名要：

* 证明生成时间
* 验证时间
* 推理延迟
* 单节点 CPU / 内存占用率
* 系统吞吐量
* 恶意节点检测准确率

### 你现在 v2 主要测了什么

当前 v2 的实验主要覆盖了：

* e2e correctness / certificate status
* execution / proving / verification / total latency
* 可扩展性（2/4/8 slices）
* fidelity

这些都很重要。([GitHub][5])

### 但还缺什么

#### 1. CPU%

我没看到 v2 实验里有正式的 per-node / per-process CPU 占用采样。
这和任务书要求直接不对齐。

#### 2. 吞吐量

v2 现在也没有真正的 throughput benchmark。
旧版 `run_experiments.py` 里倒有一个串行请求吞吐率，但 v2 主实验没有把这个补齐。

#### 3. 恶意节点检测准确率

v2 更像在做：

* 攻击场景下是否 invalid
* correctness PASS/FAIL

这很好，但它还不是严格的 detection accuracy 统计。
你至少应该做一张：

* 攻击样本数
* 检出数
* 误报数
* 漏报数
* accuracy / precision / recall

#### 4. 内存指标现在也不够硬

`v2/prover/ezkl_adapter.py` 里的内存统计只是 `max(mem_start, mem_end)`，这不是采样意义上的真实峰值。([GitHub][6])

### 必改项

这部分是**必须补**的，因为它直接关系到“任务 3 是否完成”。

---

## 八、第八个问题：你的 F2 fidelity 现在名义上有，实际上没真正做出来

这是个很容易被忽略、但老师一看代码就会发现的问题。

### 代码里现在怎么写

`fidelity.py` 里：

* F1：完整 float 模型 vs PyTorch sliced float 串联
* F3：完整 float 模型 vs certified pipeline 最终输出

这两层是对的。
但 F2 名义上说要做“full float model vs EZKL circuit outputs”，实际最后写进去的 `f2_results` 本质上还是用了 F3 的端到端差值，并没有真的逐层对 proof-bound outputs 做独立分析。([GitHub][7])

### 这意味着什么

你现在不能把 F2 写成“量化 fidelity 已完成精确测量”。

### 正确改法

F2 应该这样做：

1. 对每个样本，拿完整 PyTorch 模型逐层中间输出
2. 跑每片 EZKL proof
3. 从每片 proof 的 `rescaled_outputs` 提取该片电路输出
4. 将它与对应层的 float 中间输出比

也就是说，**F2 应该是 per-slice / per-layer 的 circuit fidelity，不是重用 F3 的最终输出差值。**

---

# 现在给你正式的整改优先级清单

---

## P0：答辩前必须完成，不然口径会崩

### P0-1. 先把系统定位改准

你现在最稳的题目口径应该是：

> “一种面向分布式推理验证的 deferred-certification 原型框架”

而不是直接说：

> “我已经完整实现了原生分布式推理零知识证明框架”。

#### 要改的文件

* `README.md`
* `v2/docs/protocol.md`
* `v2/docs/threat_model.md`
* 论文摘要 / 系统设计章节

#### 要改的说法

把“commitment linking”改成：

* “proof-bound public-value linking” 或
* “public-instance linking”

把“privacy achieved”改成：

* “当前优先实现完整性验证，隐私链路尚未完全打通”

把“distributed framework”改成：

* “distributed-inference-oriented protocol prototype”
* 或“面向分布式部署的可验证协议原型”

---

### P0-2. 修正 Phase A 的语义

#### 二选一

要么：

* 把 Phase A 名称从“慢但正确”改掉

要么：

* 真正让下一片只接受 proof-bound output

#### 你现在更推荐的做法

为了赶毕业设计，我建议你**先改名，不先改协议**。
因为改协议会牵动 proving/verify path；改名能立刻避免语义不一致。

---

### P0-3. 把“请求绑定”补上

这是当前协议最需要补的技术点。

#### 最低要求

在 `verify_chain()` 里补首端绑定检查：

* 第 1 片 proof 的 `input_from_proof`
* 必须与当前请求的初始输入一致

#### 更完整的要求

把以下字段绑定到 statement：

* `req_id`
* `slice_id`
* `model_digest`
* `tensor_digest`

#### 若 EZKL 暂时做不到

那就在协议文档里明确分层：

* **proof 内绑定**：输入输出值
* **系统级绑定**：req_id / registry / certificate / audit log

只要你分清楚，老师一般能接受。

---

### P0-4. 把 replay 实验改成“真的 replay”

当前常量替换不够。

#### 真 replay 应该怎么做

1. 先跑请求 A，保存其中一片的 proof / output / certificate fragment
2. 在请求 B 中，故意把 A 的历史片段塞进来
3. 检查 verifier 是否因为 req_id / input mismatch / terminal binding / registry mismatch 而拒绝

#### 这样改完后你才有资格说

“系统对跨请求重放具有检测能力”。

---

### P0-5. 补齐最基本的任务书指标

你答辩前至少要补：

* CPU 占用率
* 更真实的内存峰值
* 吞吐量
* 检测准确率表

#### 建议最小版本

做一组 4-slice + deferred pipeline：

* parallelism = 1 / 2 / 4
* 记录 execution / proving / verification / total
* 记录每个 proving 子进程 CPU%
* 记录每个 proving 子进程 peak RSS
* 记录并发 1 / 2 / 4 请求下吞吐量
* 对 5 类攻击各跑 N 次，统计 accuracy/precision/recall

只要这套补上，你和任务书的对齐度会大幅提升。

---

## P1：系统层必须重构，但可以作为“后续实现 / 进阶版本”

### P1-1. 把 v2 从“本地 orchestrator”升级成“真分布式运行时”

#### 你真正该有的模块

* **Execution Worker**：只负责某片 ONNX 推理
* **Prover Worker**：只负责某片或某批 proof job
* **Verifier Service**：只负责 registry + single verify + chain verify + certificate
* **Coordinator**：负责任务编排和状态机

#### 最低落地方式

不一定非要多台机器。
哪怕是同机多个 FastAPI 服务 + 队列，也已经比“函数内 for-loop”更像分布式框架。

#### 为什么这一步重要

因为这一步一做，你就能理直气壮地说：

> 我不是只在研究协议，我还实现了面向分布式部署的运行框架。

---

### P1-2. 把“低开销优化”从 latency 优化扩展成 resource 优化

#### 当前缺的东西

* 没有 proving queue
* 没有 admission control
* 没有 per-node scheduling
* 没有资源配额
* 没有跨节点负载分散实验

#### 你最值得补的设计

* proof job queue
* worker pool
* 每片 proof 的异步提交 / 完成通知
* 每个 prover worker 的资源打点

这样“降低单一节点资源压力”才算真正开始成立。

---

### P1-3. 正式把当前 linking 改名并分级

你现在最好在协议里明确区分两层：

#### 当前已实现

**Level-1: public-instance linking**

* 比较 `rescaled_outputs[i]` 与 `rescaled_inputs[i+1]`
* 使用容差 epsilon

#### 尚未实现

**Level-2: cryptographic commitment chain**

* `C_out_i == C_in_{i+1}`
* request-bound / model-bound / exact chain closure

这一步是论文写作层面的关键修正。

---

### P1-4. 真正完成 F2 fidelity

#### 你要改的文件

* `v2/experiments/fidelity.py`

#### 正确输出应该是

* 每片 proof-bound output vs 对应 float layer output
* 每片 max abs error / mean abs error
* 全链 accumulated error
* 不同切片粒度下的误差曲线

做完这个，你的 fidelity 章节会立刻扎实很多。

---

## P2：加分项，不做也能毕业，但做了会很漂亮

### P2-1. 尝试 aggregation / proof bundle

现在任务书不强制你做聚合，但如果你后续还能推进，这会让“低开销优化”更有说服力。

### P2-2. 探索 hashed/polycommit linking

如果以后 EZKL 或外部 commit-and-prove 工具能更稳定支持多切片 linking，这会把你从“public-instance linking”推进到真正的 commitment chain。

### P2-3. 真实多机实验

哪怕只是 2 台机器：

* 1 台执行
* 1 台 proving / verifying

也会让“分布式框架”这四个字更硬。

[1]: https://raw.githubusercontent.com/LeslieWylie/Distributed_inference_system_under_ZKP/main/v2/execution/pipeline.py "raw.githubusercontent.com"
[2]: https://raw.githubusercontent.com/LeslieWylie/Distributed_inference_system_under_ZKP/main/v2/compile/build_circuits.py "raw.githubusercontent.com"
[3]: https://raw.githubusercontent.com/LeslieWylie/Distributed_inference_system_under_ZKP/main/v2/verifier/verify_chain.py "raw.githubusercontent.com"
[4]: https://raw.githubusercontent.com/LeslieWylie/Distributed_inference_system_under_ZKP/main/v2/execution/deferred_pipeline.py "raw.githubusercontent.com"
[5]: https://raw.githubusercontent.com/LeslieWylie/Distributed_inference_system_under_ZKP/main/v2/experiments/e2e_certified.py "raw.githubusercontent.com"
[6]: https://raw.githubusercontent.com/LeslieWylie/Distributed_inference_system_under_ZKP/main/v2/prover/ezkl_adapter.py "raw.githubusercontent.com"
[7]: https://raw.githubusercontent.com/LeslieWylie/Distributed_inference_system_under_ZKP/main/v2/experiments/fidelity.py "raw.githubusercontent.com"
