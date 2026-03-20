# 二、你需要明确告诉 Copilot 的关键点（系统 Prompt 指南）

现在的 AI（如 Copilot）在处理“分布式 ZKP”这个词时，极容易产生概念混淆。它可能会给你生成像 [DIZK](https://www.usenix.org/conference/usenixsecurity18/presentation/wu) 或 [Pianist](https://ieeexplore.ieee.org/document/10646695) 那样“在底层密码学层面分布式计算 FFT / 多项式”的代码。

这绝对不是你要的。

你的任务书要求的是在应用层进行分层证明。

因此，在开启新对话时，你必须把以下几条核心规则“喂”给 Copilot，框定它的生成边界。

## 1. 明确界定“分布式”的架构定义（Model Slicing vs. Distributed Prover）

告诉 Copilot：

“我们的系统参考了 [DSperse](https://arxiv.org/abs/2508.06972) 架构。分布式不是指将单个 ZKP 算法（如 Sumcheck / FFT）拆分到多台机器，而是指『模型切片（Model Slicing）与定向验证（Targeted Verification）』。即将一个深度学习模型按层切分为多个子模型（[ONNX](https://onnx.ai/)），分配给不同的节点。每个节点负责独立运行自己那部分的推理，并调用 [EZKL](https://pythonbindings.ezkl.xyz/en/stable/) 为该子模型生成局部的零知识证明。”

## 2. 明确数据流与验证逻辑（Dataflow & Consistency）

告诉 Copilot：

“系统是流水线（Pipeline）结构。节点 $i$ 执行前向传播得到输出 $S_{i+1}$，生成局部证明 $\pi_i$，并将 $S_{i+1}$ 传给节点 $i+1$。为了防止节点作恶，Master 调度节点必须强制执行一致性校验：检查节点 $i$ 证明中的公开输出（Public Output）哈希值，是否严格等于节点 $i+1$ 证明中的公开输入（Public Input）哈希值。请在代码中实现这条哈希链（Hash Chain）校验逻辑。”

建议直接给 Copilot 的一致性约束为：

$$
H(\mathrm{public\_output}_i) = H(\mathrm{public\_input}_{i+1})
$$

## 3. 明确限定技术栈（Tech Stack）

告诉 Copilot：

“必须使用以下技术栈，不要自己发明底层密码学代码：

1. 模型定义与切分：[PyTorch](https://pytorch.org/)
2. 模型导出：[ONNX](https://onnx.ai/)
3. 零知识证明引擎：[EZKL Python API](https://pythonbindings.ezkl.xyz/en/stable/)，严格使用最新的 `gen_settings`、`compile_circuit`、`prove` 和 `verify` 等函数
4. 分布式通信与调度：[FastAPI](https://fastapi.tiangolo.com/) + [requests](https://requests.readthedocs.io/en/latest/) 或 [gRPC](https://grpc.io/)
5. 性能监控：[psutil](https://psutil.readthedocs.io/) 测量内存占用，`time` 测量延迟”

## 4. 明确要求实验数据打点（Metric Collection）

告诉 Copilot：

“在编写 Master 和 Worker 节点代码时，必须内置打点日志，以便我收集任务书要求的 6 项指标：

1. 证明生成时间
2. 验证时间
3. 端到端推理延迟
4. 单节点峰值内存占用率（Peak RAM）
5. 系统吞吐量
6. 故障注入下的恶意节点检测准确率”

## 5. 制定分步开发计划（Step-by-Step Execution）

告诉 Copilot：

“我们将分步进行开发。

第一步：先不考虑分布式，请给我写一个最简单的单机脚本，用 [PyTorch](https://pytorch.org/) 定义一个两层的小网络，将其切分为两个独立的 [ONNX](https://onnx.ai/) 文件，并分别用 [EZKL](https://pythonbindings.ezkl.xyz/en/stable/) 生成和验证两个 Proof。

在我确认第一步跑通之前，不要给我生成复杂的 RPC 分布式代码。”

## 可直接复用的 Prompt 模板

```md
你现在参与的是一个“面向分布式推理的零知识证明框架”项目。请严格遵守以下规则：

1. 这里的分布式是 Model Slicing + Targeted Verification，不是 Distributed Prover。不要实现分布式 FFT、MSM、多项式承诺或任何底层密码学并行。
2. 参考 DSperse：把模型按层切成多个 ONNX 子模型，每个节点独立运行自己的切片，并用 EZKL 为该子模型生成局部 proof。
3. 系统是流水线结构。节点 i 的公开输出哈希必须等于节点 i+1 的公开输入哈希。请实现 Master 侧 Hash Chain 校验逻辑。
4. 技术栈固定为 PyTorch、ONNX、EZKL Python API、FastAPI + requests 或 gRPC、psutil、time。不要自定义 zk 算法。
5. 代码必须内置指标打点：证明生成时间、验证时间、端到端延迟、单节点峰值内存、吞吐量、恶意节点检测准确率。
6. 当前只做第一阶段：先写一个单机最小脚本，定义两层小网络，切成两个 ONNX，分别生成和验证两个 proof。在我确认跑通前，不要生成复杂分布式代码。
```

## 参考链接

1. [DSperse 论文](https://arxiv.org/abs/2508.06972)
2. [DIZK 论文 / USENIX 页面](https://www.usenix.org/conference/usenixsecurity18/presentation/wu)
3. [Pianist 论文页面](https://ieeexplore.ieee.org/document/10646695)
4. [EZKL Python API 文档](https://pythonbindings.ezkl.xyz/en/stable/)
5. [EZKL GitHub](https://github.com/zkonduit/ezkl)
6. [PyTorch 官网](https://pytorch.org/)
7. [ONNX 官网](https://onnx.ai/)
8. [FastAPI 文档](https://fastapi.tiangolo.com/)
9. [requests 文档](https://requests.readthedocs.io/en/latest/)
10. [gRPC 官网](https://grpc.io/)
11. [psutil 文档](https://psutil.readthedocs.io/)