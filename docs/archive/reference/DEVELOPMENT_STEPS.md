# 分步开发计划

## 当前阶段

当前只做阶段 1，不做真正分布式部署。

## 阶段 1：单机最小可运行验证

目标：

1. 用 PyTorch 定义一个两层小网络。
2. 将该网络切成两个独立子模型。
3. 导出两个 ONNX。
4. 为每个切片分别调用 EZKL 完成 settings、compile、setup、witness、prove、verify。
5. 记录两个切片间的哈希链。
6. 输出时间与内存 metrics。

本阶段交付物：

1. 一个单机脚本。
2. 两个 ONNX 模型。
3. 两份 proof。
4. 一份 metrics 日志。
5. 一份 consistency check 结果。

## 阶段 2：本地 Master / Worker 原型

目标：

1. 引入 FastAPI 或 gRPC。
2. 每个切片封装成独立 Worker。
3. Master 负责调度、收集 proof 与执行哈希链校验。
4. 增加故障注入接口。

## 阶段 3：实验与指标采集

目标：

1. 测试不同切片粒度。
2. 测试不同节点数。
3. 测试不同故障注入比例。
4. 输出任务书要求的 6 项核心指标。

## 每次让 Copilot 写代码时的最短提示模板

请只完成阶段 1。不要写分布式 FFT、MSM 或底层 prover 并行代码。请用 PyTorch 定义一个两层小网络，将其切分为两个 ONNX 子模型，并用 EZKL Python API 分别完成 `gen_settings`、`calibrate_settings`、`compile_circuit`、`get_srs`、`setup`、`gen_witness`、`prove`、`verify`。最后实现哈希链校验和 metrics 打点，记录证明时间、验证时间、端到端延迟和峰值内存。