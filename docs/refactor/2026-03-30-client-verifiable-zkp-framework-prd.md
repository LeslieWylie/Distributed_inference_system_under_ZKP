# 面向分布式推理的零知识证明框架重构 PRD

> 版本：v1
> 日期：2026-03-30
> 目标读者：Claude / Copilot 代码代理、项目作者
> 重构目标：先落地强工程版全链路可信，再为后续更强论文版优化预留接口

---

## 1. Executive Summary

### Problem Statement

当前系统虽然已经从旧版集中证明模式演进到 Prover-Worker 架构，但 v2 仍然存在以下关键问题：

1. 服务端验证语义仍然过强，Coordinator 返回的 `certificate` 容易被误用为最终可信结论。
2. 客户端尚不能在不信任 Coordinator、Workers、网络的前提下独立完成完整验证。
3. v2 存在多条并行叙事：本地 pipeline、deferred pipeline、旧 distributed baseline、Prover-Worker refactor，主链不唯一。
4. 配置、实验入口、指标输出与协议文档之间尚未完全收束，导致系统定位仍不够稳定。

这使得当前实现尚不能被严格定义为一个“客户端可独立验证的面向分布式推理的零知识证明框架”。

### Proposed Solution

将 v2 重构为“强工程版全链路可信框架”：

- Worker 负责切片推理与本地生成 proof。
- Coordinator 只负责请求编排、proof 收集与 Proof Bundle 打包，默认不被信任。
- Client 使用本地验证程序和可信 registry 工件独立完成逐片验证、链路验证与终端绑定。
- 服务端任何 `certificate`、`verified` 或 advisory 结果都不是信任来源。

### Success Criteria

本轮重构完成后，系统必须满足以下条件：

1. 客户端在不信任 Coordinator、Workers、网络的前提下，能够仅依赖本地 verifier 与 registry 工件完成完整验证。
2. 任一单片输出篡改、proof 替换、切片乱序、最终输出调包，客户端验证结果必须为 `invalid` 或失败，不能错误给出 `certified`。
3. v2 仅保留一条清晰主链：构建工件、启动 Worker、运行 Coordinator、生成 Proof Bundle、客户端独立验证。
4. 实验入口收敛为统一体系，所有主实验均围绕同一主链运行。
5. README、协议文档、威胁模型、实验脚本与代码实现之间不存在相互矛盾的信任口径。

---

## 2. User Experience & Functionality

### User Personas

#### Persona A：客户端验证者

需要在不信任服务端的情况下，独立验证分布式推理结果是否可信。

#### Persona B：系统维护者

需要一条稳定、唯一、可复现的 v2 主链，避免文档与实验入口持续分叉。

#### Persona C：论文作者

需要一个在答辩与论文表述中自洽的系统定义：允许恶意节点存在，但错误结果不能在客户端独立验证通过的前提下被接受。

#### Persona D：代码代理

需要一份可执行、边界清晰、尽量少含模糊表述的重构需求文档。

### User Stories

1. 作为客户端，我希望拿到一个完整的 Proof Bundle，并在本地独立验证，这样我无需信任 Coordinator 或 Worker。
2. 作为系统维护者，我希望 v2 只有一条主链和一套推荐入口，这样文档、实验与代码不再分叉。
3. 作为实验操作者，我希望不同实验共享统一配置、统一输出格式和统一工件组织方式，这样数据可比较、可复现。
4. 作为论文作者，我希望系统的“全链路可信”表述有明确前提和可执行实现，这样不会在答辩时被指出逻辑不闭合。

### Acceptance Criteria

#### 2.1 Proof Bundle 成为主产物

- 系统新增 `ProofBundle` 作为 Coordinator 返回给客户端的主产物。
- 服务端返回的 `certificate` 不再被定义为最终可信结论。
- 客户端只信任 bundle 中的原始证明证据，不信任服务端“已验证”字段。

#### 2.2 客户端独立验证能力

- 客户端可以从 `ProofBundle + Registry` 直接生成最终 verdict。
- 客户端验证不依赖服务端的 `verified=true`、`certificate` 或 advisory 字段。

#### 2.3 信任语义收束

- Coordinator 默认视为不可信编排层。
- Worker 默认视为不可信 proving 节点。
- Client-side verifier 成为唯一最终可信判断入口。

#### 2.4 主链收束

- v2 必须存在唯一推荐主工作流。
- 旧 coordinator、旧 distributed baseline、本地 execution-only 入口必须降级为 reference 或 baseline。
- README 与实验脚本不能继续同时推荐多个互相竞争的主入口。

#### 2.5 实验体系收敛

- 主实验改为“服务端生成 bundle，客户端独立验证 bundle”。
- 所有主实验输出统一包含：运行配置、bundle 元数据、客户端验证 verdict、分阶段时延、每片 proving 指标。

### Non-Goals

本轮明确不做：

1. 跨主机部署与运维自动化。
2. 模型升级与更大规模模型切换。
3. registry 发布方完全去信任化。
4. `polycommit` 精确密码学链接升级。
5. public visibility 带来的隐私暴露修复。
6. 更强论文版 trust model 的完整落地。

---

## 3. Product Positioning

### 本轮系统的准确定义

本轮交付的系统不是“所有节点天然可信”的系统，而是：

> 一个允许 Coordinator、Workers、网络都不可信，但客户端仍可独立验证推理完整性的分布式 ZKP 框架。

### 本轮系统保证的性质

如果客户端输出 `certified`，则系统必须保证：

1. 每片 proof 均可使用本地 registry 中的验证材料独立验证通过。
2. 每片 proof 对应的 `model_digest` 与 registry 中注册工件一致。
3. 首片 proof 的输入与客户端提交的请求输入一致。
4. 相邻切片之间满足当前工程语义下的 linking 检查。
5. 最后一片 proof 输出与 bundle 声称的最终输出一致。

### 本轮系统不保证的性质

1. 可用性：恶意 Coordinator 或 Worker 可拒绝服务。
2. 隐私：当前 public visibility 仍会暴露中间激活。
3. registry 发布方完全无信任。
4. 精确密码学 commitment chain。

---

## 4. Technical Specifications

### 4.1 Architecture Overview

目标架构采用四层职责分离：

#### Layer 1：Prover-Workers

每个 Worker 负责：

- 执行切片推理
- 本地生成单片 proof
- 返回切片输出、proof 与必要元数据

#### Layer 2：Untrusted Coordinator

Coordinator 负责：

- 请求编排
- 切片顺序调度
- proof 收集
- Proof Bundle 组装与返回
- 可选生成服务端 advisory 结果

Coordinator 默认不被信任，其任何“已验证”输出不得作为最终可信依据。

#### Layer 3：Artifact Registry

Registry 提供客户端独立验证所需的可信静态工件：

- `vk`
- `settings`
- `srs`
- `model_digest`
- 切片顺序与必要元数据

#### Layer 4：Client-side Verifier

客户端使用本地 verifier：

- 校验 bundle 结构完整性
- 校验每片 proof
- 校验首端输入绑定
- 校验相邻 linking
- 校验终端绑定
- 生成唯一可信 verdict

### 4.2 Core Design Principles

1. **Coordinator 组织证据，但不决定真实性。**
2. **客户端验证 bundle，而不是相信服务端状态。**
3. **错误节点可以存在，但不能让错误结果被客户端当成正确结果接受。**
4. **服务端只能 fail-open 到“无结果/invalid”，不能 fail-open 到“错误 certified”。**
5. **主链必须唯一，实验体系必须围绕主链收束。**

### 4.3 Proof Bundle Specification

新增统一主产物 `ProofBundle`，建议结构如下：

```json
{
  "bundle_version": "1.0",
  "req_id": "req-...",
  "created_at": "ISO8601",
  "model_id": "mnist_mlp",
  "registry_digest": "sha256(...)",
  "slice_count": 2,
  "initial_input": [...],
  "claimed_final_output": [...],
  "slices": [
    {
      "slice_id": 1,
      "model_digest": "...",
      "proof_json": {...},
      "worker_claimed_output": [...],
      "metrics": {...}
    }
  ],
  "server_side_advisory": {
    "status": "certified|invalid|unknown",
    "note": "non-authoritative"
  }
}
```

要求：

- `slices` 必须按 `slice_id` 升序排列。
- `proof_json` 必须是客户端验证所需的主证据。
- `worker_claimed_output` 仅作审计，不作信任输入。
- `server_side_advisory` 必须显式标注为非信任来源。

### 4.4 Client Verification Flow

客户端收到 bundle 后，必须执行以下步骤：

1. 校验 `bundle_version`、`slice_count`、`registry_digest` 是否与本地 registry 一致。
2. 按 `slice_id` 顺序校验每片 `model_digest` 是否与 registry 一致。
3. 对每片 `proof_json` 执行本地 proof verification。
4. 从每片 proof 的公开实例中提取 `rescaled_inputs / rescaled_outputs`。
5. 校验首片输入是否绑定 `initial_input`。
6. 校验相邻切片 linking。
7. 校验末片输出是否绑定 `claimed_final_output`。
8. 生成最终 `certified / invalid`。

客户端的最终 verdict 是唯一可信结果。

### 4.5 Server-side Advisory Semantics

现有 `Certificate` 结构保留，但语义必须降级：

- 仅供缓存、调试、日志、实验对照使用。
- 不再代表最终可信输出。
- 如果保留，必须改名或增加显式字段，例如：
  - `server_side_advisory`
  - `server_side_check`
  - `non_authoritative_certificate`

禁止继续使用会误导客户端的语义名称，例如：“final certificate”。

---

## 5. File-Level Refactor Requirements

### 5.1 Must Modify

#### [v2/common/types.py](v2/common/types.py)

必须新增：

- `ProofBundle`
- `ProofBundleSlice`
- `ClientVerificationResult`

必须调整：

- `Certificate` 的语义注释
- 让其明确不再代表最终唯一可信结论

#### [v2/services/distributed_coordinator.py](v2/services/distributed_coordinator.py)

必须调整其主职责：

- 从“执行后验证并输出最终可信结果”改为“编排、收集、打包、返回 bundle”
- bundle 应包含原始 proof 证据与必要元数据
- 允许附带 advisory，但不得将其作为主产物语义

#### [v2/verifier/verify_chain.py](v2/verifier/verify_chain.py)

必须重构为可复用 verifier library：

- 可由客户端直接调用
- 支持直接消费 bundle 或由 bundle 解构得到的 proof jobs
- 输出结构面向客户端独立验证而非服务端内部状态机

#### [v2/experiments/refactored_e2e.py](v2/experiments/refactored_e2e.py)

必须重构为：

- 服务端运行主链生成 bundle
- 客户端本地调用 verifier 完成最终验证
- 实验结果中同时记录 advisory 与 client verdict，但以 client verdict 为准

#### [README.md](README.md)

必须更新为唯一主链说明：

- Worker 生成 proof
- Coordinator 打包 bundle
- Client 独立验证
- 服务端证书不是信任来源

#### [v2/docs/protocol.md](v2/docs/protocol.md)

必须重写协议主叙事：

- 强工程版信任假设
- Proof Bundle 主产物
- Client-side verification 主流程

#### [v2/docs/threat_model.md](v2/docs/threat_model.md)

必须更新威胁模型：

- Coordinator 不可信
- Workers 不可信
- 网络不可信
- Client verifier + registry + crypto assumptions 为最小信任根

### 5.2 Should Modify

#### [v2/experiments/resource_metrics.py](v2/experiments/resource_metrics.py)

应改为围绕新主链产出：

- bundle 生成开销
- client verification 开销
- advisory 与 client verdict 对照

#### [v2/execution/pipeline.py](v2/execution/pipeline.py)

应降级为 reference / baseline，不再作为主系统对外推荐入口。

#### [v2/execution/deferred_pipeline.py](v2/execution/deferred_pipeline.py)

应明确定位为辅助实验链或 baseline，对外不与主链竞争。

### 5.3 May Downgrade or Mark as Legacy

以下文件可保留，但必须在文档与注释中明确降级：

- [v2/services/master_coordinator.py](v2/services/master_coordinator.py)
- [v2/experiments/distributed_e2e.py](v2/experiments/distributed_e2e.py)
- 旧 distributed baseline 路径

---

## 6. Evaluation Requirements

### 6.1 Mandatory Attack Cases

重构后系统至少必须覆盖：

1. 最后一片输出篡改
2. 中间片输出篡改
3. proof 替换
4. slice 乱序
5. claimed final output 调包
6. model_digest 对应工件替换

对以上所有场景，客户端最终 verdict 必须不是错误的 `certified`。

### 6.2 Metrics Requirements

主实验输出必须统一包含：

- `bundle_generation_ms`
- `client_verification_ms`
- `total_exec_ms`
- `total_prove_ms`
- `verification_ms`（若服务端保留 advisory，可单独记录）
- `client_verdict`
- `server_side_advisory_status`
- `per_slice_metrics`

### 6.3 Reporting Semantics

实验结论必须以客户端独立验证结果为准。

禁止出现这样的结论口径：

- “Coordinator certified，所以结果可信”
- “服务端验证通过，因此全链路可信”

必须改成：

- “Client-side verification certified the bundle”
- “The result is accepted only after local verification against registry artifacts”

---

## 7. Risks

### Technical Risks

1. **Bundle 体积膨胀**：proof 全量传输会增加 I/O 压力。
2. **客户端依赖变重**：本地验证要求客户端具备相应运行环境。
3. **旧入口清理不彻底**：可能继续保留多个互相竞争的主链叙事。
4. **文档不同步**：如果代码改了但文档没改，论文与实现仍会脱节。
5. **近似 linking 的理论边界**：强工程版可以成立，但不能过度宣传为最强精确密码学承诺链。

### Scope Risks

1. 如果试图在本轮同时解决 registry 去信任化、privacy、polycommit 精确 linking，会导致范围失控。
2. 如果继续保留太多旧路径而不降级，系统定位仍会模糊。

---

## 8. Roadmap

### Phase 0：语义重置

- 重新定义信任边界
- Coordinator 语义降权
- Client 成为最终验证方

### Phase 1：强工程版落地

- 实现 `ProofBundle`
- 实现客户端验证入口
- 改造 Coordinator 返回 bundle
- 改造主实验为 client-side verification

### Phase 2：主链收束

- 收束 v2 主推荐路径
- 统一配置、指标、文档
- 降级旧入口为 baseline/reference

### Phase 3：长期增强

- registry 签名/审计机制
- 更强 bundle 完整性约束
- 更强 linking 方案
- 后续模型升级

---

## 9. Explicit Instructions For Claude / Copilot

执行本 PRD 时，必须遵守以下规则：

1. 不要把服务端 `certificate` 继续当作最终可信输出。
2. 不要删除旧路径后导致 baseline 无法参考；应先降级再逐步清理。
3. 所有面向外部的主实验和主文档都必须围绕 `ProofBundle + Client Verification` 重写。
4. 任何新增字段或数据结构，必须优先服务客户端独立验证，而不是服务端内部方便。
5. 重构后如果 README、protocol、threat model 与代码语义不一致，视为未完成。
6. 默认目标是“强工程版 end-to-end verifiability”，不要在代码或文档中暗示已完成更强论文版或理想版。

---

## 10. Definition Of Done

以下条件全部满足，才视为本轮重构完成：

1. Coordinator 的主返回产物是 `ProofBundle`。
2. 客户端可以独立从 `ProofBundle + Registry` 生成最终 verdict。
3. 服务端任何 advisory 结果都明确标记为非信任来源。
4. 主实验脚本以客户端独立验证结果作为最终结论。
5. README、protocol、threat model 全部同步更新。
6. v2 只保留一套主叙事：Worker proving、Coordinator bundling、Client verifying。
7. 对篡改类场景，系统不允许错误给出 `certified`。
