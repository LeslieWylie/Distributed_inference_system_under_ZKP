# 深度调研综合文档：GQ1–GQ5（2026-03-20）

> 来源：Gemini Deep Research，基于 2025-2026 最新文献
> 用途：论文写作 + 系统重构参考

---

## 核心结论

分布式切片推理 + 端到端可验证的最佳技术路线：
1. **NanoZK 风格**：分片/分层精确证明 + 显式中间状态 binding（commitment chain）
2. **Artemis/Apollo 风格**：低开销 commitment verification（CP-SNARK）
3. **EZKL/ZKTorch 风格**：外层 aggregation / recursive accumulation

**绝对避免**："每层允许一点误差、希望整体也差不多对"（被 Non-Composability Note 否定）

---

## GQ1: NanoZK 深读

### 论文信息
- 标题: NanoZK: Layerwise Zero-Knowledge Proofs for Verifiable Large Language Model Inference
- arXiv: 2603.18046 (2026-03)
- 链接: https://arxiv.org/html/2603.18046v1

### 完整协议（5步）
1. **按层定义关系**: 每层证明 h_i = f_i(h_{i-1}; W_i)
2. **每层 proof 附带 I/O commitment**: SHA-256 commitment on input/output
3. **验证相邻层 commitment 一致**: 防止 mix-and-match attack
4. **ZK-friendly 非线性替换**: 16-bit fixed-point lookup table + Plookup
5. **Layer-parallel proving**: 可选 Fisher-guided selective verification（仅效率优化）

### Commitment Chain 定义
- 每层 proof 包含 (input_commit, output_commit)
- 验证器检查: output_commit[i] == input_commit[i+1]
- 使用 SHA-256（非 Poseidon）

### Compositional Soundness (Theorem 3.1)
- 若每层 soundness error ≤ ε，且 commitment 抗碰撞
- 则复合 proof chain soundness = union bound(各层 ε + collision概率)
- 证明思路: 取"第一层偏离 honest state 的层"

### 关键数字
| 指标 | 值 |
|---|---|
| Per-layer proof size | **6.9KB** (constant across hidden dims 64-768) |
| Per-layer verify time | **21-23ms** |
| Per-block proving time | **~6.2s** |
| GPT-2 12-layer serial | **8.6 min** |
| GPT-2 12-layer 12-parallel | **3.2 min** |
| vs EZKL MLP proving | **52.5× average speedup** |

### 适用性判断
**适合借鉴结构原则，不适合直接照搬 Transformer-specific 电路模板。**

适合借鉴:
- 精确 slice relation（非近似）
- 显式 boundary commitment
- 可组合 soundness
- 天然并行/流水线

不适合直接用:
- Transformer-specific layer template（需重建 slice circuit family）
- "Constant proof size" 依赖 Halo2 IPA 固定 degree 电路族
- Fisher-guided selective verification 不是密码学安全

---

## GQ2: EZKL Aggregation 版本细节

### EZKL 23.0.5 API 可用性
- `aggregate(...)`: **可用**（bindings 文档列出）
- `setup_aggregate(...)`: **可用**
- `verify_aggr(...)`: **可用**
- `split_proofs` 参数: **存在**（区分普通 aggregation vs split-proof stitching）
- **注意**: 本地实测 `hasattr(ezkl, 'aggregate')` 返回 **False**！文档与 wheel 存在不一致

### Split Proofs 机制
- 官方博客 https://blog.ezkl.xyz/post/splitting/ 明确描述
- 利用 unblinded advice columns 的 commitment 做 stitching
- 前一子电路"输出列" commitment == 后一子电路"输入列" commitment
- `split_proofs=True` 强制顺序匹配

### GitHub 讨论要点 (PR #855)
- subgraph 的 input_scale 必须和前一 subgraph 的 output scale 匹配
- 考虑用 KZG commitments 替代 hashes 以节省 rows
- 需补 "compute proofs, glue, and verify" 的 integration test

### 稳定性判断
**可用但非零摩擦**。GitHub issue #773 用户照 example 仍遇错误。
建议: 作为"可借用的机制与接口"，非"可直接复用的稳定 pipeline"。

---

## GQ3: 2025-2026 Verifiable LLM Inference 路线图

### 五大路线对比

| 系统 | 路线 | 中间状态绑定 | Proof Size | Proving Time |
|---|---|---|---|---|
| **zkGPT** | 单体+内部commitment | 内部 advice commitment | 101KB (GPT-2) | 21.8s (32线程) |
| **NanoZK** | 逐层+commitment chain | SHA-256 I/O commitment | 6.9KB/层 | 6.2s/层 |
| **VeriLLM** | 轻量承诺审计 | Merkle root + signatures | N/A | 验证开销 0.78% |
| **ZKTorch** | 递归/累积压缩 | KZG + Mira accumulation | 6.54MB (GPT-j) | 1397s (GPT-j) |
| **Jolt Atlas** | ONNX DAG + lookup/sumcheck | DAG of sumchecks | - | 38s (GPT-2) |

### 对本课题的路线选择
- **主线**: NanoZK 式 exact slice proof + commitment chain
- **工程先跑**: 借 EZKL split-proofs + aggregate
- **未来 bundle**: Artemis + recursive accumulation
- **绝不用**: layerwise tolerance 作为安全论证

---

## GQ4: Layerwise Approximate Verification 理论边界

### 论文信息
- 标题: A Note on Non-Composability of Layerwise Approximate Verification for Neural Inference
- arXiv: 2602.15756 (Zamir, 2026)
- 链接: https://arxiv.org/abs/2602.15756

### 核心定理
对任意网络 N，存在函数等价网络 N'，满足:
1. **Exact equivalence**: ∀x, N'(x) = N(x)
2. **Steering property**: ∀x, ∀bounded target y, 存在 δ-consistent transcript 使输出 = y

### 构造机制
- 隐藏层塞入辅助 trigger 通道
- 第一层注入 ≤δ 的小 trigger
- 后续层通过块对角映射放大
- 最后一层线性读出转成任意目标偏移
- 精确执行时额外通道无影响

### 对量化 zkML 的影响
- **精确证明离散量化语义**: 不受此论文打击（证明的是 exact discrete statement）
- **每层容差接受**: 正中靶心，被此论文否定

### 修复方向
- A: 改成 exact discrete semantics（最稳）
- B: 增加全局误差证明（需 Lipschitz/robustness 上界）
- C: 限制扰动模型（需进一步理论工作）

---

## GQ5: Artemis/Apollo CP-SNARK

### 论文信息
- 标题: Artemis: Efficient Commit-and-Prove SNARKs for zkML
- arXiv: 2409.12055 (2024)
- 链接: https://arxiv.org/abs/2409.12055

### 核心机制
解决: "witness 某部分已有外部 commitment，如何在 SNARK 里高效证明一致性"

| 方案 | 底层 | Setup | 风格 |
|---|---|---|---|
| Apollo | KZG | Trusted | White-box |
| Artemis | 任意 homomorphic PC | 可无 | **Black-box** |

### Artemis 关键特性
- 支持 Halo2 + IPA（无 trusted setup）
- Verifier overhead: KZG 仅 1.0-1.1×，IPA 至多 1.2×
- VGG 示例: commitment check overhead 从 11.5× 降到 1.2×

### 对本课题的价值
- 每个 Worker 对边界张量做 commitment
- 子图 proof 里不必把 commitment 过程重算
- 用 CP-SNARK linking 证明 witness 与外部 commitment 一致
- 比"把大张量再 hash 一遍塞进电路"更省约束

### 理想架构（三层叠加）
1. **slice-local exact proof** — 精确切片计算证明
2. **CP-SNARK commitment linking** — 低成本边界一致性
3. **outer aggregation/accumulation** — 多 proof 压缩

---

## 参考文献完整索引

| ID | 论文 | arXiv/URL | 年份 | 类别 |
|---|---|---|---|---|
| R1 | NanoZK | 2603.18046 | 2026 | layerwise proof |
| R2 | zkGPT | USENIX Sec 2025 | 2025 | monolithic proof |
| R3 | VeriLLM | 2509.24257 | 2025/26 | commit-and-audit |
| R4 | ZKTorch | 2507.07031 | 2025 | recursive accumulation |
| R5 | Jolt Atlas | 2602.17452 | 2026 | ONNX DAG + lookup |
| R6 | Non-Composability | 2602.15756 | 2026 | theory negative |
| R7 | Artemis/Apollo | 2409.12055 | 2024 | CP-SNARK |
| R8 | EZKL Split Blog | blog.ezkl.xyz/post/splitting/ | 2024 | engineering |
| R9 | EZKL PR #855 | github.com/zkonduit/ezkl/pull/855 | 2024 | engineering |
| R10 | zkLLM | 2404.16109 | 2024 | specialized proof |
| R11 | DSperse | - | 2025 | targeted verification |
| R12 | TeleSparse | - | 2025 | privacy-preserving |
| R13 | TensorCommitments | 2602.12630 | 2026 | lightweight binding |
