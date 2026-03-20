# Copilot 系统 Prompt 指南

## 目标

本项目的目标不是实现 DIZK、Pianist 一类的底层分布式 prover，也不是把 FFT、Sumcheck、多项式承诺等密码学底层算子拆到多台机器上并行计算。

本项目的目标是：

1. 在应用层做分布式推理。
2. 将模型按层切分为多个子模型。
3. 每个节点独立执行自己负责的推理切片。
4. 每个节点用 EZKL 为本地切片生成局部零知识证明。
5. Master 通过哈希链校验跨节点输入输出一致性。
6. 记录任务书要求的 6 类指标。

## 必须告知 Copilot 的核心约束

### 1. 明确定义“分布式”

必须告诉 Copilot：

我们的系统参考 DSperse 架构。这里的“分布式”是 Model Slicing 与 Targeted Verification，而不是 Distributed Prover。

正确理解：

1. 将一个深度学习模型切成多个连续子模型。
2. 每个子模型导出为独立 ONNX 文件。
3. 每个节点独立运行自己的 ONNX 子模型。
4. 每个节点独立调用 EZKL 生成本地 proof。

禁止生成的方向：

1. 不要实现分布式 FFT。
2. 不要实现分布式 MSM。
3. 不要实现分布式多项式承诺。
4. 不要实现自定义底层 zk prover。
5. 不要走 DIZK / Pianist / collaborative prover 的底层密码学路线。

### 2. 明确数据流与一致性逻辑

必须告诉 Copilot：

系统是流水线结构。节点 i 输出中间状态 $S_{i+1}$，并生成局部证明 $\pi_i$。节点 i+1 接收 $S_{i+1}$ 作为输入继续执行。

Master 必须执行一致性校验：

1. 读取节点 i proof 对应的公开输出或其公开输出哈希。
2. 读取节点 i+1 proof 对应的公开输入或其公开输入哈希。
3. 强制校验这两个值完全相等。

请要求 Copilot 在代码中实现一条显式的 Hash Chain：

$$
H(\text{public\_output}_i) = H(\text{public\_input}_{i+1})
$$

若不相等，Master 必须标记该链路异常，并将该节点组合记为潜在恶意或错误执行。

### 3. 明确限定技术栈

必须告诉 Copilot：

只允许使用下列技术栈，不要自己发明密码学实现。

1. 模型定义与切分：PyTorch。
2. 模型导出：ONNX。
3. 零知识证明引擎：EZKL Python API。
4. 分布式通信与调度：Python FastAPI + requests，或 gRPC。
5. 性能监控：psutil 和 time。

EZKL 侧必须优先使用以下流程：

1. `gen_settings`
2. `calibrate_settings`
3. `compile_circuit`
4. `get_srs` 或 `gen_srs`
5. `gen_witness`
6. `setup`
7. `prove`
8. `verify`

### 4. 明确要求实验打点

必须告诉 Copilot：

Master 和 Worker 的代码中必须内置日志与指标采集，覆盖以下 6 项：

1. 证明生成时间。
2. 验证时间。
3. 端到端推理延迟。
4. 单节点峰值内存占用。
5. 系统吞吐量。
6. 故障注入场景下的恶意节点检测准确率。

建议日志字段：

1. `request_id`
2. `slice_id`
3. `node_id`
4. `proof_gen_ms`
5. `verify_ms`
6. `forward_ms`
7. `e2e_ms`
8. `peak_rss_mb`
9. `hash_in`
10. `hash_out`
11. `consistency_ok`
12. `fault_injected`
13. `malicious_detected`

### 5. 强制分步开发

必须告诉 Copilot：

我们按阶段开发，当前只做第一阶段。

阶段 1：

1. 不做分布式通信。
2. 不做 RPC。
3. 只写单机脚本。
4. 用 PyTorch 定义一个两层小网络。
5. 将网络切成两个子模型。
6. 分别导出两个 ONNX。
7. 分别用 EZKL 生成并验证两个 proof。
8. 验证输出 1 的哈希能否与输出 2 的输入哈希形成一致链。

在阶段 1 没有跑通之前，不允许生成复杂的 Master / Worker 分布式代码。

## 推荐直接粘贴给 Copilot 的约束模板

你现在参与的是一个“面向分布式推理的零知识证明框架”项目。请严格遵守以下规则：

1. 这里的分布式是 Model Slicing + Targeted Verification，不是 Distributed Prover。不要实现分布式 FFT、MSM、多项式承诺或任何底层密码学并行。
2. 参考 DSperse：把模型按层切成多个 ONNX 子模型，每个节点独立运行自己的切片，并用 EZKL 为该子模型生成局部 proof。
3. 系统是流水线结构。节点 i 的公开输出哈希必须等于节点 i+1 的公开输入哈希。请实现 Master 侧 Hash Chain 校验逻辑。
4. 技术栈固定为 PyTorch、ONNX、EZKL Python API、FastAPI + requests 或 gRPC、psutil、time。不要自定义 zk 算法。
5. 代码必须内置指标打点：证明生成时间、验证时间、端到端延迟、单节点峰值内存、吞吐量、恶意节点检测准确率。
6. 当前只做第一阶段：先写一个单机最小脚本，定义两层小网络，切成两个 ONNX，分别生成和验证两个 proof。在我确认跑通前，不要生成复杂分布式代码。

## 使用说明

每次开启新对话时，建议先贴这份文件中的“推荐直接粘贴给 Copilot 的约束模板”，再继续提出具体编码任务。