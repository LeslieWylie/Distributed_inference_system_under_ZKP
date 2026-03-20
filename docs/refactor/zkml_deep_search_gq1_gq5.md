# 分布式推理 + zkML 深度搜索文档（GQ1–GQ5）

**面向问题**：  
- GQ1. NanoZK 深读  
- GQ2. EZKL aggregation 版本细节  
- GQ3. 2025–2026 verifiable LLM inference 路线  
- GQ4. Layerwise approximate verification 的理论边界  
- GQ5. Commit-and-Prove SNARK / Artemis / Apollo

**生成日期**：2026-03-20  
**用途**：可直接发给本地 Claude / Gemini 继续扩展，也可作为毕业设计“相关工作 + 架构重构”底稿。

---

## 0. 先给结论

### 0.1 对你当前课题最关键的五个结论

1. **NanoZK 是一条非常贴近你重构方向的路线**：它不是单体大 proof，而是**逐层 proof + 相邻层 commitment chain**。它的核心 statement 不是“整模型一次性证明”，而是“每层独立证明，再靠 commitment 链接起来”。这和你想做的“多切片、全链路绑定、并行 proving”非常接近。[S1][S2]

2. **NanoZK 的 compositional soundness 是“逐层 soundness + commitment collision resistance”的组合结论**。形式上可以理解为：如果每层 proof 都有可忽略 soundness error，且 commitment 哈希抗碰撞，那么整个层链的 composite proof 也只有可忽略失败概率；其证明思路是“定位第一层出错层”并对所有失效模式做 union bound。[S1][S2]

3. **EZKL 的 aggregation 目前存在“latest 文档已公开、stable 23.0.5 文档未公开列出”的版本证据错位**。也就是说：  
   - `pythonbindings.ezkl.xyz/en/latest` 明确列出了 `aggregate`, `setup_aggregate`, `verify_aggr`, `mock_aggregate`, `split_proofs`；  
   - 但 `en/stable`（标题显示为 23.0.5 文档）没有公开列出这些 API。  
   因此，**对“EZKL 23.0.5 是否稳定支持 aggregation”最稳妥的结论是：公开 stable 文档不足以坐实；latest 主线文档已经有；你需要本地 wheel 级别再核验。** [S3][S4][S5]

4. **2026 的 non-composability note 对你非常重要**：它直接否定了“每层近似正确 ⇒ 最终输出近似正确”的一般性推理。对你的系统含义是：  
   - 如果你证明的是**精确的量化/有限域电路 statement**，那没问题；  
   - 但如果你想说“每层误差在 ε 内，所以最终接近原始浮点模型”，这个逻辑在一般情形下是不成立的。  
   所以你后续系统设计里，**必须把‘电路正确性’与‘对原始浮点模型的 fidelity’分开表述**。[S12]

5. **Artemis / Apollo 值得你重点关注，但它们解决的是“外部 commitment 验证过贵”的问题，不是直接替代 recursion / aggregation。**  
   它们的价值在于：如果你未来把跨切片 linking 从“哈希链”升级成“多项式承诺 / KZG / IPA commitment linking”，那么 commit-and-prove SNARK 会很有帮助；但它本身不是“把多 proof 自动压成一个 proof”的递归器。[S15]

---

## 1. GQ1：NanoZK 深读

### 1.1 NanoZK 的问题设定

NanoZK 针对的是 **LLM inference verifiability**：用户调用远程模型 API，但无法确认服务商是否真的运行了宣称的模型。NanoZK 的核心思想不是把整个 LLM 一口气电路化，而是利用 transformer / decoder-only 模型天然的层级串行结构，把证明任务拆成逐层的小 proof，并通过 commitment chain 把它们接起来。[S1]

论文明确把它描述为：**每一层的输出只依赖前一层输出，而不依赖非相邻层的全局共享状态**，因此可以把单个巨大证明任务拆成多个独立层证明；相比 monolithic approach，这样能显著降低单次 proving 的峰值内存与 wall-clock 时间，并支持并行 proving。[S1]

### 1.2 NanoZK 的完整协议（按系统视角重述）

下面是按协议流程重建后的 NanoZK：

#### 阶段 A：模型分解与近似准备
1. 将 LLM 推理分成 layer/block 级子计算；
2. 对非算术操作（如 softmax、GELU、SiLU、RMSNorm 中的某些部分）使用 ZK-friendly approximation；
3. 在论文实现中，这些近似主要通过 lookup tables 完成，而不是大次数多项式近似。[S1]

#### 阶段 B：前向执行
1. 给定输入 prompt，对模型做正常前向；
2. 记录每一层的输入激活 `x_{i-1}` 和输出激活 `x_i`；
3. 这些中间激活成为后续逐层 proving 的 witness 边界。

#### 阶段 C：逐层证明
对每一层 `i`，生成一个 proof，证明：

- `x_i = f_i(x_{i-1}; W_i)`

其中 `f_i` 是该层运算，`W_i` 是该层参数。论文写法中明确说每层 proof 证明该层的输入激活、输出激活、层权重与层计算关系成立。[S1]

#### 阶段 D：承诺链
对每层输入与输出做 commitment：

- `c_{i-1} = H(x_{i-1})`
- `c_i = H(x_i)`

NanoZK 文中这里使用的是 **SHA-256**，不是 Poseidon。[S1]

每个 layer proof 都带着它对应的输入/输出 commitment。验证者检查相邻层是否满足：

- 前一层的输出 commitment = 后一层的输入 commitment

这样就阻止了 mix-and-match attack：攻击者不能把不同运行、不同中间状态、不同 query 的合法 proof 任意拼接成一条假链。[S1]

#### 阶段 E：组合验证
验证者最终做两类检查：

1. 每层 proof individually verifies；
2. 相邻层 commitment 一致。

若全部通过，则得到整次推理的 composite correctness statement。

---

### 1.3 NanoZK 的 commitment chain 如何定义

论文定义非常直接：  
**每个 layer proof 都包含对该层输入和输出的 cryptographic commitments；验证者检查相邻 proof 的 commitment 是否一致。** [S1]

这条链的安全目标是：  
- 防止“把 A 运行中的第 1 层 proof”和“B 运行中的第 2 层 proof”拼在一起；
- 防止 prover 在中间状态上做不一致替换。

因此，NanoZK 的 commitment chain 本质上是：

- `c_0 = H(x_0)`
- `c_1 = H(x_1)`
- ...
- `c_L = H(x_L)`

每个 proof `π_i` 公开绑定 `(c_{i-1}, c_i)`，验证时检查：
- `Verify(π_i, c_{i-1}, c_i) = 1`
- 且所有相邻层 commitments 连续。

### 1.4 compositional soundness 的正式 statement

论文的主定理可以保守地重述成下面这句：

> 若每个 layer proof 的 soundness error 至多为 `ε_i`，且承诺哈希函数抗碰撞，则整个 composite proof 的 soundness error 至多为“所有层 soundness error 之和 + 哈希碰撞项”的上界。

论文正文和附录里的证明思路是：

1. 假设 adversary 伪造出一个接受的 composite proof，但最终输出不正确；
2. 取第一个与诚实计算偏离的层；
3. 那一层必然是在前缀都正确的前提下，试图为一个假 statement 通过证明；
4. 若不是某层 proof 不 sound，那就只能是 commitment chain 中发生了碰撞或不一致；
5. 对这些失效模式做 union bound，得到整体 soundness 上界。[S1][S2]

附录还给了 Halo2 IPA + SHA-256 的具体实例化说明：  
对 32 层模型，在 128-bit 安全参数下，整体失败概率仍然是可忽略的。[S2]

### 1.5 proof size、并行 proving、layer granularity

NanoZK 最关键的工程亮点是：**proof size 基本按层常数化，而不是随整体模型宽度线性膨胀。** [S1]

论文给出的关键数字：

- 每层 proof 大约 **6.9 KB**
  - attention proof 约 3.2 KB
  - MLP proof 约 3.7 KB
- 对 12-layer GPT-2，整条 proof 链总计约 **82.8 KB**
- 验证时间约 **21–23 ms / 层**
- proving 时间约 **6.2 s / block**
- 12 层 GPT-2-Small：
  - 串行 proving：**8.6 分钟**
  - 12 个并行 worker：**3.2 分钟** [S1][S2]

NanoZK 这里的 layer granularity 基本是 **transformer block 级**。它并不是把整个模型一锅端，也不是把每个矩阵乘再拆成非常细的微算子；而是把每个 block 当成一个足够自然的独立证明单元。这样兼顾了：
- statement 的自然性；
- 中间状态的可绑定性；
- 并行度；
- proof size 与 setup/proving cost 的平衡。

### 1.6 selective verification 在 NanoZK 里的地位

NanoZK 也讨论 selective verification（用 Fisher 信息挑重要层）来做效率优化。  
但这点你要特别注意：**NanoZK 自己并没有把 selective verification 当成完整安全结论，而是明确把它放在效率优化层面。** 论文表述很清楚：如果应用要求 cryptographic certainty，就必须验证所有层。[S1]

这和你当前毕设的核心反思高度一致。也就是说，NanoZK 其实在论证上站在你现在的新方向这一边，而不是站在“低 verify_ratio 依然全链路安全”这一边。

### 1.7 是否适合映射到非 Transformer 小模型切片场景

#### 结论
**适合，但只在“切片边界是清晰顺序依赖”的场景下适合。**  
它天然适配：
- 纯 MLP / feed-forward stack
- 无跨层旁路的顺序 CNN block 序列
- 普通 transformer block 序列
- 小模型的 layerwise / stagewise slicing

#### 为什么适合
NanoZK 依赖的不是“模型一定是 transformer”，而是更一般的结构条件：

- 每个 slice 的输入、输出边界清楚；
- slice `i+1` 的语义输入就是 slice `i` 的语义输出；
- 相邻 slice 之间能用一个 commitment equality 约束起来。

这和你现在的“小模型按层切成 8 段、分给 8 个 worker”完全是同一个抽象。

#### 什么情况下不再是简单链
如果模型有：
- 残差旁路（skip connection）
- 多分支 DAG
- 共享状态
- RNN / cycle

那么简单的线性 commitment chain 需要升级成**DAG commitment graph** 或更复杂的状态绑定协议。EZKL 官方 split 相关讨论里甚至明确提醒：循环结构（recurrent structures）并不适合当前这种拓扑切分方案。[S6]

#### 对你最重要的映射建议
如果你的毕设后续重构是“小模型多切片、非 Transformer，但基本按顺序传 activation”，那么 NanoZK 的思想可以几乎原样借鉴为：

- 每片最终都要出 proof；
- 每片 proof 公开绑定 `(input_commit_i, output_commit_i)`；
- Master 检查所有 proof；
- 对每条边检查 `output_commit_i == input_commit_{i+1}`；
- 若模型存在残差，则边界状态必须把主分支与 residual branch 一起打包 commitment。

---

## 2. GQ2：EZKL aggregation 的版本细节

### 2.1 结论先行

这里最稳妥的结论不是“有”或“没有”，而是：

> **从公开文档证据看，EZKL latest 主线已经有 aggregation API；但 stable 23.0.5 文档没有公开列出这些 API。因此：latest 能力存在，23.0.5 是否稳定公开支持，不能仅凭 stable 文档坐实。**

同时，latest 文档里的 `aggregate(..., split_proofs=False, ...)` 说明 EZKL 主线已经在支持一种**“多个 proof 聚合，其中 proof 可以是更大电路的 segments”** 的模式。[S3][S4][S5]

### 2.2 我确认到的证据

#### A. PyPI 版本
PyPI 显示 `ezkl 23.0.5` 是 2026-02-20 发布的版本。[S5]

#### B. stable 文档
`pythonbindings.ezkl.xyz/en/stable/` 页面标题直接显示为 **ezkl 23.0.5 documentation**。[S3]

但在这套 stable 文档里，我能确认看到的是：
- `prove`
- `verify`
- `kzg_commit`
- `poseidon_hash`
- `swap_proof_commitments`
- `input_visibility`
- `output_visibility`
- `param_visibility`

而**没有公开列出**：
- `aggregate`
- `setup_aggregate`
- `verify_aggr`
- `mock_aggregate`
- `create_evm_verifier_aggr`

这意味着：如果只看 stable 文档，aggregation 并不是一个已经公开稳定文档化的能力。[S3]

#### C. latest 文档
`pythonbindings.ezkl.xyz/en/latest/` 明确列出了：

- `aggregate(aggregation_snarks=..., ..., split_proofs=False, ..., commitment=...)`
- `mock_aggregate(..., split_proofs=False)`
- `setup_aggregate(...)`
- `verify_aggr(...)`
- `create_evm_verifier_aggr(...)`
- `prove(..., proof_type accepts single / for-aggr)` [S4]

这说明主线文档里 aggregation pipeline 已经相当完整。

### 2.3 针对你 4 个子问题的逐项回答

#### 问：EZKL 23.0.5 中 `aggregate`, `setup_aggregate`, `verify_aggr` 是否可用？

**保守回答：公开 stable 文档不足以确认。**

- latest 文档：**是，明确可见**
- stable 23.0.5 文档：**未公开列出**
- 因此：
  - 若你问“主线能力有没有”：**有**
  - 若你问“23.0.5 稳定公开支持是否已坐实”：**没有完全坐实**

> 最可靠的做法不是再猜文档，而是在本地 23.0.5 wheel 里直接执行符号检查。

建议你本地执行：

```python
import ezkl, inspect
print("version =", getattr(ezkl, "__version__", "unknown"))
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

如果你本地 wheel 真有这些符号，那么可以再做一个最小 smoke test；如果没有，那就说明 latest 文档和 stable wheel 之间确实存在不一致。

#### 问：是否支持 split proofs / segmented circuits 的聚合？

**latest 文档里是明确支持的。**

`aggregate(... split_proofs=False ...)` 的参数说明写得很清楚：  
`split_proofs` 表示这些 accumulated proofs 是否是**一个更大电路的 segments**。[S4]

`mock_aggregate(... split_proofs=False)` 的文档也重复了这一点。[S4]

这意味着：从主线接口设计看，EZKL aggregation 至少已经显式考虑了**分段 proof / segmented circuit** 的聚合场景。

#### 问：是否有官方 notebook 或 issue 讨论“多子图 proof 聚合 + linking”？

**有明确讨论，但我没找到成熟、稳定、官方发布级的完整 notebook 示例。**

最关键的公开证据来自 EZKL 的 split 模型 PR 讨论：[S6]

- 讨论中明确提到：
  - 将小模型如 nanoGPT 拆分；
  - 为每个 chunk 生成 witness；
  - 为每个 chunk proving；
  - 然后验证 proof commitments 是否匹配；
- 还明确提到：
  - 后一个 subgraph 的 `input_scale` 必须等于前一个 subgraph 的 output scale；
  - 可以用 example notebooks 里的方法去验证 “proof commitments match”；
  - stitched commitments 的 visibility 可以考虑 `public` / `hashed` / `polycommit`；
  - 甚至有人建议将哈希切换为 KZG commitments 以减少 rows。[S6]

这对你的课题非常关键，因为它说明 EZKL 团队内部/社区已经明确把问题表述成：

- 多子图；
- proof commitments match；
- input/output scale 对齐；
- stitched commitments 可选 visibility；
- 后续 glue / verify / integration test。

这与“多切片 proof linking”几乎是同一道题。

#### 问：是否存在稳定示例？

**我没有找到一个可以直接引用为“stable official end-to-end example”的公开示例。**

我能找到的是：
- latest API 文档；
- PR / discussion 里的设计讨论；
- 对 example notebooks 的提及；
- 以及若干 aggregation 相关 PR 活跃记录。[S4][S6][S7]

但我没有搜到一份足够稳的、可直接放在论文里说“官方 stable notebook 已给出完整 multi-subgraph aggregation+linking 示例”的材料。

所以论文里最安全的写法是：

> EZKL 主线文档与社区讨论已经显示出 aggregation 和 split-proof linking 的支持方向，但截至公开 stable 23.0.5 文档，尚未找到成熟、稳定、官方发布级的多子图 proof aggregation + linking 示例。

### 2.4 对你系统设计的含义

#### 短期最稳方案
先不要把系统 correctness 绑定在 EZKL aggregation 是否成熟上。  
先做：

- 每片独立 proof
- Master 逐片 verify
- input/output commitments 做 linking

这样就已经满足你需要的 end-to-end statement。

#### 中期优化方案
如果你本地 23.0.5 wheel 实测有 `aggregate`：
- 再把每片 proof 聚合成一个 aggregate proof
- 但**aggregation 只负责压缩验证成本**
- 不能替代 input/output linking 语义

这是非常重要的一点：  
**聚合不会自动替你证明“第 i 片的输出就是第 i+1 片的输入”。**  
这件事仍然要靠公开实例中的 commitments 先被绑定好。

### 2.5 建议直接交给 Gemini 的追加搜索需求

如果你想让 Gemini 专门追这块，我建议用下面这组提示：

#### 提示 A：版本级核验
> 请严格确认 EZKL 23.0.5 wheel / release / tag 中，Python API 是否实际导出 `aggregate`, `setup_aggregate`, `verify_aggr`, `mock_aggregate`, `create_evm_verifier_aggr`。  
> 需要优先搜索：  
> - PyPI wheel contents  
> - GitHub release/tag for 23.0.5  
> - `pythonbindings.ezkl.xyz/en/stable` 与对应 git commit  
> - 任何 changelog / release notes / commit diff  
> 并明确区分：latest docs 与 23.0.5 stable docs。

#### 提示 B：示例级核验
> 请搜索是否存在 EZKL 官方 notebook / example / issue / PR，完整展示：  
> - ONNX auto split / subgraph split  
> - 每个 subgraph proving  
> - `proof commitments match`  
> - optional `aggregate`  
> - `split_proofs=True`  
> 目标是找到一个能直接复现“多子图 proof linking + aggregation”的最稳定公开示例。

---

## 3. GQ3：zkGPT / 2025–2026 verifiable LLM inference 路线

### 3.1 一张总表

| 方案 | 组织方式 | intermediate state binding | 是否显式 commitment chaining | 证明/验证特征 | 对你课题的启发 |
|---|---|---|---|---|---|
| zkLLM (2024 baseline) | 更偏 monolithic / whole-inference specialized proof | 内部状态由单个大 proof 内部约束 | 一般不依赖外部层链 | 13B 级模型 <15 min, proof <200kB | 强 statement，但不天然适配“多 worker 分布式切片” |
| zkGPT (2025) | 更偏 monolithic / specialized GPT inference proof | 内部状态主要由单个 proof 内部绑定 | 不以外部 commitment chain 为核心 | GPT-2 <25s | 代表“做强单体证明”的路线 |
| NanoZK (2026) | **layerwise proofs** | 相邻层边界显式暴露并绑定 | **是**，SHA-256 commitment chain | 6.9KB/层；12 层可并行 | **与你的目标最接近** |
| VeriLLM (2025/26) | 非 ZK，轻量 rerun + on-chain checks | 主要靠经济/协议设计，不是 ZK state binding | 否 | 验证约为推理成本 1% | 适合当“非 ZK 对照路线” |
| TensorCommitments (2026) | commitment-native 轻量 proof-of-inference | 主要靠 tensor commitments / Terkle trees | 是，但不是传统 SNARK 层链 | 仅 0.97% prover / 0.12% verifier overhead | 是非常强的“轻量绑定”对照 |
| ZKTorch (2025) | basic blocks + **parallel accumulation** | 通过 accumulation instances 组合 block proofs | 更偏 accumulation，不是简单哈希链 | 3× smaller proofs, up to 6× faster proving | 适合看“并行证明 + 后续累积”的编译器路线 |
| ZK-DeepSeek / recursive composed inference (2025) | **recursive composition** | 由递归组合与中间证明合并实现 | 可被看作递归式绑定，不是简单 hash chain | 常数大小 final proof | 适合作为“未来 proof bundle / aggregate”方向 |

### 3.2 zkLLM：专用大模型 ZKP 路线

zkLLM 是比较早、但仍然重要的基线。其特点是：

- 面向 LLM 做专用 ZKP；
- 为 attention 设计 `zkAttn`；
- 为非算术张量操作设计 `tlookup`；
- 用 CUDA 做高度并行化实现；
- 目标是**为整个 inference 过程出一个 proof**，而不是显式暴露一条层链。[S9]

它的代表数字很醒目：

- 支持到 13B 参数模型；
- proving 时间约 1–15 分钟；
- proof 小于 200kB；
- verifier 1–3 秒。[S9]

**对你课题的含义**：  
zkLLM 很强，但它代表的是“specialized monolithic proof”路线，不太像“多节点切片后，每片都出 proof，再由 Master 做 linking”。  
也就是说，它证明“强 statement 可以做到”，但不直接给你“多 worker 分布式切片”的协议模板。

### 3.3 zkGPT：更高效的 monolithic/specialized route

zkGPT 是 2025 USENIX Security 的结果，定位是：

- 专门针对 LLM inference；
- 对 linear / non-linear layers 都做了新的证明方法；
- 使用 `constraint fusion` 和 `circuit squeeze` 来提高并行性与压低 overhead；
- 在 GPT-2 上把 proving 压到 **25 秒以内**。[S8]

因此 zkGPT 代表的路线很清楚：

- **不是 layerwise external chaining**
- 而是**尽量把整个 LLM inference 做成一个高效的大 proof**

对你的课题，它最重要的价值不是协议模板，而是“比较基准”：

- 如果你选择 layerwise + commitment chain，你得到的是：
  - 更自然的多节点映射；
  - 更好的 proving 并行度；
  - 更容易做 deferred certification；
- 你付出的代价是：
  - proof 数量更多；
  - 验证端若不做 aggregation，就要多次 verify；
  - 需要明确处理中间状态绑定。

### 3.4 NanoZK：layerwise + commitment chain 路线

NanoZK 与 zkGPT / zkLLM 最大不同在于：  
它**明确把 intermediate state binding 抽象成 commitment chaining**，而不是把所有中间状态都埋在一个 monolithic circuit 里。[S1][S2]

这条路线对你最重要的启发是：

- 它天然适合分布式部署；
- 它天然支持每层/每片 proving 并行；
- 它天然能映射到“前台执行 + 后台 proving”；
- 它天然要求 end-to-end correctness 依赖**所有片最终都出 proof**。

换句话说，NanoZK 在 conceptual level 上，比 zkGPT / zkLLM 更接近你毕设应该重构成的系统。

### 3.5 VeriLLM：公开可验证，但不是完整 ZK 路线

VeriLLM 的定位很不同：

- 不是完整零知识推理证明；
- 它依赖 lightweight empirical rerunning；
- 配合最小链上检查；
- 使用 isomorphic inference-verification architecture；
- 把验证成本压到大约推理成本的 1%。[S10]

这条路线很适合当对照组来讲：

- **优点**：非常轻，工程实用性强；
- **缺点**：它不是“端到端密码学完备 correctness proof”。

对你来说，VeriLLM 最适合作为论文 related work 里的“非 ZK / 低成本公开可验证路线”，帮助你说明：

> 有些工作追求 extremely low overhead，但牺牲了你想要的 cryptographic end-to-end statement。

### 3.6 TensorCommitments：轻量 commitment-native 路线

TensorCommitments 很值得注意，因为它的抽象和你关心的“intermediate binding”非常接近：

- 它把 inference 绑定到 commitment；
- 使用 multivariate Terkle Trees；
- 对 LLaMA2 只增加 0.97% prover 和 0.12% verifier 时间；
- 属于非常轻量的 proof-of-inference 路线。[S13]

它说明了一个重要趋势：

> 未来 verifiable inference 不一定只有“全 SNARK 化”和“完全不验证”两条路，中间还有 commitment-native 的轻量设计空间。

对你来说，它的价值主要是启发：
- commitment linking 不一定非得是简单哈希链；
- 以后可以考虑 tensor-native / polynomial-commitment-native 路线；
- 如果你未来要做“proof bundle + 轻量在线验证”，这类工作值得深挖。

### 3.7 ZKTorch：basic blocks + 并行 accumulation

ZKTorch 的重要性在于：它代表的是一种更“编译器 + accumulation”范式的可验证推理路线：

- 把 inference 编译成许多 basic blocks；
- 每个 block 用专门协议证明；
- 通过 Mira accumulation 的并行扩展来做 succinct proof assembly；
- 结果是 proof size 至少 3× 更小，proving 时间最多快 6×。[S11]

这条路线对你启发很强：

- 你未来不一定只在“proof list”与“single aggregate proof”之间二选一；
- 还可以把“多片 slice proof”进一步看作 accumulation 实例；
- 这样能更自然地导向未来的 bundle / aggregate / recursive verifier。

### 3.8 ZK-DeepSeek / recursive composed inference

2025 年的《Zero-Knowledge Proof Based Verifiable Inference of Models》这类工作很重要，因为它明确走的是：

- recursively composed proofs；
- 无 trusted setup；
- 支持 linear + nonlinear layers；
- 目标得到 constant-size final proof；  
- 并将组件/子矩阵级 proof 递归合并成最终证明。[S16]

这说明 2025–2026 的一个明显趋势是：

- **monolithic proof**：zkLLM / zkGPT 方向  
- **layerwise proof chain**：NanoZK 方向  
- **recursive / accumulation / composed proof**：ZKTorch / ZK-DeepSeek 方向  
- **lightweight public verification**：VeriLLM / TensorCommitments / privacy-preserving verification 方向

### 3.9 对你课题的路线选择建议

如果按“能落地 + 能答辩 + 和你现有工程最接近”排序，我建议：

#### 第一阶段（最现实）
- 全切片最终都出 proof
- 用 hashed/public 或 polycommit 暴露输入/输出承诺
- Master 做逐片 verify + commitment equality
- 不把 correctness 依赖在 aggregation 上

#### 第二阶段（研究升级）
- 引入 proof bundle / aggregate
- 或转向 accumulation / recursive composition

#### 不建议的路线
- “每层带容差的近似验证，然后声称 end-to-end 正确”
- “部分切片不证明，但哈希链补安全”
- “用 Worker 自报 verified 代替 verifier side check”

---

## 4. GQ4：Layerwise approximate verification 的理论边界

### 4.1 这篇 note 在说什么

《A Note on Non-Composability of Layerwise Approximate Verification for Neural Inference》针对的是一种很自然、但其实很危险的想法：

> “如果每层都被证明在容差 δ 内正确，那么最终输出应该也差不多正确。”

这篇 note 说：**这个推理在一般情况下是错的。** [S12]

它甚至给出了一个非常强的反例结论：

- 对任意神经网络，都可以构造一个**功能上完全等价**的新网络；
- 在这个新网络里，即使每层偏差都只有容差量级，攻击者仍然可以把最终输出引向任意目标值（在给定范围内）。[S12]

### 4.2 正式模型：layerwise δ-consistency

论文定义了一种“line-by-line approximate verifier”：

- prover 提供整条中间状态 transcript；
- verifier 对每层只检查：  
  该层输出与“基于 prover 提供的前一层状态算出来的值”相差不超过 δ。[S12]

关键点就在这里：  
**每一步的误差会被喂给下一步。**

当 `δ = 0` 时，这和普通 exact computation 一样，局部检查可以组合；  
但当 `δ > 0` 时，接受的并不是“接近真实前向传播的唯一轨迹”，而只是一个**可被逐步 adversarial perturbation 驱动的动态系统轨迹**。[S12]

### 4.3 Theorem 1 的含义

论文主定理可以直白翻成这样：

> 存在一个与原网络函数完全相同的改造网络，使得：  
> 对任意输入、任意目标输出（在一个有界范围内），攻击者都能构造一条 layerwise-δ-consistent transcript，把最终输出导向这个目标。

也就是说：

- exact inference 完全没变；
- 黑盒测试、功能测试都可能看不出问题；
- 但 layerwise approximate verification 这个协议已经失去端到端意义。[S12]

### 4.4 攻击机制为什么成立

论文的构造本质上是往每层加一个“trigger / auxiliary channel”：

1. 正常精确计算时，这个通道一直保持为 0；
2. 所以网络功能完全不变；
3. 但 approximate verifier 允许第一层在容差范围内注入一个极小偏移；
4. 之后每一层都对已经被污染的状态做“局部近似正确”更新；
5. 这个 trigger 会在层间被放大；
6. 到最后一层，微小的 per-layer deviation 被放大成任意目标输出偏移。[S12]

这正是“局部容差”不推出“全局输出接近”的根本原因。

### 4.5 对量化 zkML 的实际影响

这里要非常小心地区分两类 statement：

#### A. 你证明的是“精确的量化/有限域电路”
例如：

- 给定量化后的模型 `M_q`
- 给定量化后的输入 `x_q`
- 电路精确证明 `y_q = M_q(x_q)`

这种 statement **不受这篇 note 直接攻击**。  
因为你证明的是 exact field/integer computation，不是 per-layer approximate relation。

#### B. 你证明的是“每层都近似正确，所以最终接近原始浮点模型”
这种 statement **会被这篇 note 直接挑战**。  
尤其是当：
- 每层是独立容差约束；
- 下一层继续在前一层 prover-supplied approximate state 上做校验；
- 缺少全局误差传播控制。

所以对你毕设的直接含义是：

> 你完全可以证明“量化模型/切片电路”的 end-to-end correctness；  
> 但你不能再轻易从“每层误差不大”推出“最终接近原始浮点模型”。

### 4.6 对“每层容差证明”体系的限制

这篇 note 基本宣告了：

- **layerwise tolerance proof 不是一般可组合原语**
- 它不能在黑箱条件下替代 exact linking

因此，对“每层容差证明”的限制很明确：

1. 不能只证明局部误差小；
2. 不能让后一层基于前一层 prover-supplied approximate state 继续检查；
3. 不能把 fidelity claim 建立在局部 tolerance 的朴素累积上。

### 4.7 是否存在可组合条件或修正定理？

我当前搜索到的最稳妥结论是：

> **没有找到一条对一般神经网络都成立的“layerwise approximate verification 可组合修正定理”。**

但可以明确指出三类可能的修正方向：

#### 方向 1：不要证明 approximate layerwise relation，改为证明 exact quantized relation
这是目前最稳的工程路线。  
也就是：

- 先固定量化模型；
- 对量化模型做 exact proving；
- fidelity 作为单独实验问题，而不是 correctness statement 的一部分。

这其实就是你现在最该走的路线。

#### 方向 2：加入全局稳定性 / Lipschitz / robustness 假设
理论上，如果你能额外证明：
- 网络全局 Lipschitz 常数较小；
- 每层扰动如何传播有统一上界；
- adversary 不能操控网络结构；

那有机会把局部误差转化为全局误差界。  
但这是**额外强假设**，不是这篇 note 允许你直接白拿的结论。

#### 方向 3：像 approximate sum-check 那样，把“近似性”作为协议级对象
这篇 note 自己就专门说明：  
它的结果**不否定** Bitan 等人的 approximate sum-check 路线，因为后者不是“逐层局部容差拼接”，而是有 round-by-round soundness 控制和协议级误差分析。[S12]

这说明：  
若你将来真要研究“原生近似验证”，正确方向也不是 naive layerwise tolerance，而是：
- 把误差传播纳入协议本身；
- 用全局 soundness 分析去控制 relaxed checks。

### 4.8 对你课题的直接设计建议

这篇 note 对你最重要的操作性结论只有一句：

> **你的 end-to-end correctness statement 必须是 exact statement（针对量化/电路模型），而不是“每层容差成立”。**

所以你的论文/系统设计里，应该明确拆成两层：

#### Correctness 层
- 证明：切片电路的量化推理是精确执行的；
- 绑定：相邻切片输入/输出承诺一致；
- 输出：certified result。

#### Fidelity 层
- 实验比较：量化模型 vs 浮点模型；
- 指标：top-1 / MSE / perplexity / output drift；
- 这是经验指标，不是 proof statement。

这会让你的论证一下子严谨很多。

---

## 5. GQ5：Commit-and-Prove SNARK 与 zkML commitment verification

### 5.1 Artemis / Apollo 在解决什么问题

Artemis / Apollo 论文的出发点非常准确：

> 过去很多 zkML 系统都把精力放在“如何证明模型计算正确”，但经常忽略“如何高效验证模型与数据的外部 commitments 一致”。

而在大模型场景里，**commitment verification 本身就可能占掉 prover 成本的大头**，甚至超过 90%。[S15]

这正是 Artemis / Apollo 的价值所在：  
它们不是再发明一个“证明模型算得对”的 SNARK，而是让 SNARK 能高效证明：

- witness 中某部分，确实与外部 commitment 一致。

这类系统被称为 **Commit-and-Prove SNARK (CP-SNARK)**。[S15]

### 5.2 Apollo 的核心机制

Apollo 是更偏 LegoSNARK / Lunar 风格的 CP-SNARK：

- 面向 Plonk + KZG 风格 commitment；
- 需要对白盒 arithmetization 有较强介入；
- 通过修改 witness arithmetization，把内部 witness commitment 与外部 commitment 的关系更高效地对齐；
- 继承了 trusted setup 依赖。[S15]

Apollo 的优势是：  
在 KZG / pairing-based 环境下，它可以非常高效地把“外部 commitment 验证”纳入证明。

但缺点也很明显：

- 白盒；
- 依赖特定承诺体系；
- 需要 trusted setup。

### 5.3 Artemis 的核心机制

Artemis 是更灵活的版本：

- 对底层 SNARK 是 **black-box use**
- 兼容任意 homomorphic polynomial commitment
- 因此兼容没有 trusted setup 的现代系统，例如 **Halo2 + IPA**。[S15]

Artemis 的关键思想不是在电路里重算所有 commitment，而是：

1. 对 witness polynomial 做 masked linear combination；
2. 对 commitments 也做对应的同态线性组合；
3. 在 challenge point 上检查 evaluation 一致性；
4. 把沉重的 commitment verification 尽量挪到电路外，用更便宜的代数关系在电路内绑定。[S15]

直觉上，它做的是：

- **不再在 SNARK 里“笨重地重复做 commitment 计算”**
- 而是借助 homomorphic PC 和随机点评价，把这个检查压缩成更轻的约束

### 5.4 与 Halo2 / IPA / KZG 的适配关系

#### Apollo
- 偏向 KZG
- 白盒
- trusted setup

#### Artemis
- 支持任意 homomorphic polynomial commitment
- 能适配 Halo2 + IPA
- 不要求 trusted setup
- 对现代 Plonkish / Halo2 风格系统更友好。[S15]

这对你特别重要，因为你当前使用 EZKL，而 EZKL 背后是 Halo2 / PLONK / KZG/IPA 生态的一部分。  
因此，**若你未来想把“跨切片 linking”从哈希承诺升级成更强的多项式承诺 linking”，Artemis 比 Apollo 更贴近你的潜在路线。**

### 5.5 对多切片 commitment linking 的潜在价值

这是你最关心的部分。  
我把答案分成“今天立刻能用”和“未来很有价值”两层。

#### 今天立刻能用吗？
**大概率不能直接插进你当前 EZKL 原型里当现成组件。**

原因：
- Artemis / Apollo 不是 EZKL 现成开关；
- 它们是一类 CP-SNARK 构造思想/实现；
- 你当前系统还没进入“外部 commitment verification 成本成为核心瓶颈”的阶段。

#### 未来为什么很有价值？
因为你现在的 linking 方案若只用哈希链，会有两个长期局限：

1. 哈希链只能证明“边界值一致”，但不天然压缩外部 commitment 验证成本；
2. 如果未来你要把：
   - 模型参数 commitment
   - 输入 commitment
   - 输出 commitment
   - 中间 slice 边界 commitment
   全部并入一个更复杂的 statement，commitment verification 开销可能迅速上升。

这时，CP-SNARK 非常有价值，因为它专门解决：

> 如何在一个 proof 中高效说明“我的 witness 与你在外面看到的 commitments 是一致的”。

#### 对你的 slicing 场景的具体启发
未来你完全可以把每个 slice 的 statement 升级成：

- 我计算了 slice `i`
- 使用了与外部 commitment `C_param_i` 一致的参数
- 使用了与外部 commitment `C_in_i` 一致的输入
- 得到了与外部 commitment `C_out_i` 一致的输出

然后：
- 相邻切片只需要验证 `C_out_i = C_in_{i+1}`
- 而不是公开整个边界张量

这会比纯哈希链更系统化，也更适合未来做 bundle / aggregation。

### 5.6 是否适合你未来做 proof bundle / aggregate？

#### 结论
**适合做“前端 statement 设计的增强”，但不是 recursion/aggregation 本身的替代品。**

也就是说：

- CP-SNARK 解决的是**proof statement 里 commitment verification 太贵**
- aggregation / recursion 解决的是**很多 proof 如何压成少量 proof**

两者是互补关系，不是替代关系。

最理想的未来路线是：

1. 每片 proof 先做成 commitment-aware 的 slice proof；
2. 再用 accumulation / aggregation / recursion 把这些 slice proofs 压成 bundle；
3. 最终 verifier 同时得到：
   - 每片 witness 与外部 commitments 一致
   - 多片之间 commitment chain 一致
   - 总体验证成本低

### 5.7 对你未来研究路线的建议

如果你后续还有继续深挖的空间，我建议研究路径按下面顺序推进：

#### 第一层：哈希链版本先做通
- input/output hashed/public
- Master 逐片 verify
- exact quantized statement
- 全切片最终出 proof

#### 第二层：聚合版本
- 若 EZKL aggregation 可用，加入 aggregate/bundle
- 否则先做 proof list + batch orchestration

#### 第三层：commitment-native 版本
- 研究是否将 slice boundary 从 hash 升级成 KZG / IPA commitment
- 研究是否引入 CP-SNARK 思想
- 研究 parameter commitment 也并入 statement

你现在最不该做的是一上来跳到“复杂聚合 + 复杂 commitment native linking”，因为那会把工程复杂度一下拉满。  
但把 Artemis / Apollo 放进未来路线图，非常合理。

---

## 6. 最终综合判断：对你毕业设计最有价值的启示

### 6.1 你最该采纳的主线

如果把这五组问题合起来，最合理的主线其实已经非常清楚：

1. **从 NanoZK 学 statement 与 protocol**
   - 全 slice 最终都要证明
   - 通过 commitment chain 做 intermediate state binding

2. **从 Non-Composability note 学表述边界**
   - 证明 exact quantized circuit correctness
   - fidelity 单独做实验，不混进 correctness statement

3. **从 EZKL 现实状态学工程策略**
   - 不把系统 correctness 建立在 aggregation 已成熟上
   - 先做独立 proofs + Master linking
   - aggregation 作为优化层

4. **从 Artemis / Apollo 学未来升级方向**
   - 未来可把简单 hash linking 升级为 commitment-aware slice statement
   - 再考虑 bundle / recursive aggregation

### 6.2 最适合你当前阶段的系统定义

你现在最应该把系统定义成：

> **一种面向端到端可信的、执行-证明解耦的分布式切片推理架构。**

它的正式 statement 应该是：

- 对请求 `req_id`
- 对切片模型序列 `M_1,...,M_n`
- 对用户输入 `x_0`
- 系统实际执行了  
  `x_i = M_i(x_{i-1})`  
- 返回给用户的结果就是 `x_n`
- 且所有相邻边界满足  
  `Commit(out_i) = Commit(in_{i+1})`

### 6.3 你当前最稳的架构方案

#### 在线执行阶段
- Worker 完成 slice inference
- 立即把 `output_tensor` 传给下游
- 同时计算 `output_commit`
- 把 `output_commit` 报给 Master

#### 后台 proving 阶段
- 每个 Worker 最终都生成本 slice proof
- proof 公开实例至少包括：
  - `req_id`
  - `slice_id`
  - `input_commit`
  - `output_commit`
  - `model_digest`

#### 最终验证阶段
- Master 独立 verify 每片 proof
- 检查相邻 commitment equality
- 全部通过后签发 certified result

这个方案与 NanoZK 的思想一致，与 non-composability note 不冲突，也不依赖 EZKL aggregation 是否成熟。

### 6.4 对答辩最稳的一句话

你可以直接这样讲：

> 我们最初实现的是“分段 proving + 选择性验证”的分布式推理原型，但它不能为未证明切片提供端到端密码学安全保证。后续重构不再尝试修补 light 节点，而是要求所有切片最终都生成 proof，并通过相邻切片输入/输出承诺的一致性建立全链路绑定。证明生成从关键路径剥离，系统先返回 provisional result，待全部 proof 验证完成后升级为 certified result。

这句话和你现在搜到的所有关键文献方向都一致。

---

## 7. 我建议你下一步立即做的三件事

### 7.1 先把协议图和 statement 图画出来
不要先改代码。先把下面四件事画清楚：

- 每片的 public statement 是什么
- commit 的定义是什么
- Master 最终检查什么
- certified result 与 provisional result 的状态机是什么

### 7.2 本地直接核验 EZKL 23.0.5 wheel
别再围着 docs 猜。直接跑符号检查和一个最小 smoke test。

### 7.3 先做“无 aggregation 的全链路正确版本”
也就是：

- 所有切片最终都出 proof
- Master 逐片 verify
- input/output commitments linking
- 不引入 light 节点

只要这一步通了，你的系统第一次真正配叫 end-to-end verifiable distributed inference。

---

## 8. 给 Gemini / Claude 的追加搜索与追问模板

### 模板 1：EZKL aggregation 最终坐实
> 请严格核验 EZKL 23.0.5 的 wheel / Git tag / release note / Python symbol export，确认 `aggregate`, `setup_aggregate`, `verify_aggr`, `mock_aggregate`, `create_evm_verifier_aggr` 是否真实存在并可调用。  
> 若存在，请给出最小运行示例；若不存在，请解释为何 latest docs 已出现但 stable docs 没有。

### 模板 2：找多子图 linking 示例
> 请专门搜索 EZKL 官方 notebook、discussion、PR、example 中，是否有完整展示“多子图 proof commitments match / input_scale = previous output scale / split_proofs / aggregate”的稳定示例。  
> 优先给 GitHub issue / PR / notebook 链接。

### 模板 3：继续深挖 NanoZK
> 请进一步查 NanoZK 是否有代码仓库、附录实现、benchmark script，特别是：  
> - layer proof 的具体 circuit 组织  
> - LUT 的表大小与范围  
> - setup amortization 的假设  
> - 对 residual / KV-cache / autoregressive decode 的处理

### 模板 4：追 recursive route
> 请继续搜索 2025–2026 期间，是否存在专门针对神经网络/LLM inference 的 recursive SNARK / accumulation 论文，重点关注：  
> - how intermediate states are bound  
> - whether final proof is constant size  
> - whether the system supports segmented/layerwise proving and later recursive merge

---

## 9. 参考资料

### [S1] NanoZK 主文
NANOZK: Layerwise Zero-Knowledge Proofs for Verifiable Large Language Model Inference  
https://arxiv.org/html/2603.18046v1

### [S2] NanoZK 附录 / 扩展 soundness 细节
同一 arXiv HTML 与附录部分  
https://arxiv.org/html/2603.18046v1

### [S3] EZKL stable 文档（标题显示 23.0.5）
https://pythonbindings.ezkl.xyz/en/stable/

### [S4] EZKL latest 文档（公开列出 aggregation API）
https://pythonbindings.ezkl.xyz/en/latest/

### [S5] PyPI: ezkl 23.0.5
https://pypi.org/project/ezkl/

### [S6] EZKL split 模型 / proof commitments match 讨论（PR #855）
https://github.com/zkonduit/ezkl/pull/855

### [S7] EZKL aggregation 相关活跃 PR 线索
https://github.com/zkonduit/ezkl/pulls

### [S8] zkGPT (USENIX Security 2025)
zkGPT: An Efficient Non-interactive Zero-knowledge Proof Framework for LLM Inference  
https://www.usenix.org/system/files/usenixsecurity25-qu-zkgpt.pdf

### [S9] zkLLM
zkLLM: Zero Knowledge Proofs for Large Language Models  
https://arxiv.org/abs/2404.16109

### [S10] VeriLLM
VeriLLM: A Lightweight Framework for Publicly Verifiable Decentralized Inference  
https://arxiv.org/pdf/2509.24257

### [S11] ZKTorch
ZKTorch: Compiling ML Inference to Zero-Knowledge Proofs via Parallel Proof Accumulation  
https://arxiv.org/pdf/2507.07031

### [S12] Non-Composability Note
A Note on Non-Composability of Layerwise Approximate Verification for Neural Inference  
https://arxiv.org/abs/2602.15756

### [S13] TensorCommitments
TensorCommitments: A Lightweight Verifiable Inference for Language Models  
https://arxiv.org/abs/2602.12630

### [S14] Privacy-preserving verified inference route
Privacy-Preserving Mechanisms Enable Cheap Verifiable Inference of LLMs  
https://arxiv.org/abs/2602.17223

### [S15] Artemis / Apollo
Artemis: Efficient Commit-and-Prove SNARKs for zkML  
https://arxiv.org/abs/2409.12055

### [S16] Recursive composed inference / ZK-DeepSeek
Zero-Knowledge Proof Based Verifiable Inference of Models  
https://arxiv.org/abs/2511.19902