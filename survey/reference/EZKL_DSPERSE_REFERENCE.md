# EZKL 与 DSperse 本地参考

## 1. 本项目应采用的正确架构边界

根据现有任务书与你收集的材料，本项目应采用的方向是：

1. DSperse 风格的模型切片与定向验证。
2. 将模型拆成多个连续切片，各自独立导出与证明。
3. 利用局部 proof 加跨切片一致性检查，近似实现整体推理完整性保障。

不应采用的方向是：

1. DIZK 式底层电路并行。
2. Pianist 式底层 prover 通信优化实现。
3. 自研多项式承诺、FFT、MSM 分布式计算代码。

## 2. DSperse 论文对本项目最有价值的结论

从 [survey/2508.06972v3.pdf](../2508.06972v3.pdf) 转出的内容来看，DSperse 的关键观点如下：

1. 它强调的是 distributed ML inference with targeted verification。
2. 它反对对整个模型做 full-model circuitization，因为证明开销高、灵活性差。
3. 它主张只对高价值子计算做 proof，也就是 slices。
4. 每个 slice 可以独立证明。
5. 全局正确性不是自动由密码学保证，而是需要额外的一致性机制、审计或编排逻辑来保障。

这与本项目非常一致。你的系统里 Master 的哈希链检查，本质上就是一种跨 slice 一致性机制。

## 3. EZKL Python API 的推荐调用链

结合官方 Python bindings 文档与 `zkonduit/ezkl` 源码测试，推荐采用下面这条稳定流程：

1. `ezkl.gen_settings(model_path, settings_path, py_run_args=run_args)`
2. `ezkl.calibrate_settings(data_path, model_path, settings_path, "resources")`
3. `ezkl.compile_circuit(model_path, compiled_model_path, settings_path)`
4. `ezkl.get_srs(settings_path)` 或 `ezkl.gen_srs(srs_path, logrows)`
5. `ezkl.setup(compiled_model_path, vk_path, pk_path, srs_path=None)`
6. `ezkl.gen_witness(data_path, compiled_model_path, witness_path)`
7. `ezkl.prove(witness_path, compiled_model_path, pk_path, proof_path, srs_path=None)`
8. `ezkl.verify(proof_path, settings_path, vk_path, srs_path=None)`

## 4. 已确认的 EZKL 关键 API

以下签名来自官方 Python bindings 文档与源码接口摘要：

```python
ezkl.gen_settings(model_path, settings_path, py_run_args=None)
ezkl.calibrate_settings(data_path, model_path, settings_path, target, lookup_safety_margin=..., scales=None, scale_rebase_multiplier=..., max_logrows=None)
ezkl.compile_circuit(model_path, compiled_model_path, settings_path)
ezkl.get_srs(settings_path, logrows=None, srs_path=None)
ezkl.gen_witness(data_path, compiled_model_path, witness_path, vk_path=None, srs_path=None)
ezkl.setup(compiled_model_path, vk_path, pk_path, srs_path=None, witness_path=None, disable_selector_compression=False)
ezkl.prove(witness_path, compiled_model_path, pk_path, proof_path, srs_path=None)
ezkl.verify(proof_path, settings_path, vk_path, srs_path=None, reduced_srs=True)
```

## 5. 代码实现时的注意事项

1. 不要跳过 `compile_circuit`。
2. 不要默认写死旧版 API 名称或过时 CLI 命令。
3. `calibrate_settings(..., "resources")` 更适合先做最小可运行 demo。
4. `setup` 之前需要已有 compiled circuit，通常也需要 SRS。
5. `prove` 使用 witness、compiled circuit、pk 和 proof 输出路径。
6. `verify` 使用 proof、settings、vk，可选传入 SRS。

## 6. 本项目第一阶段最小样例应包含的产物

第一阶段单机 demo 建议输出：

1. 一个完整 PyTorch 小模型。
2. 两个切片子模型。
3. 两个 ONNX 文件。
4. 两套 settings / compiled / witness / pk / vk / proof 文件。
5. 一个本地脚本校验 `hash(output_slice_1) == hash(input_slice_2)`。
6. 一份 metrics 日志，至少包含时间与内存数据。

## 7. 推荐的目录规划

建议后续代码结构采用：

```text
project/
  models/
    full_model.py
    slice_1.onnx
    slice_2.onnx
  artifacts/
    slice_1/
    slice_2/
  scripts/
    run_single_machine_demo.py
  metrics/
    latest_run.json
```

## 8. 任务书指标到代码字段的映射建议

1. 证明生成时间 -> `proof_gen_ms`
2. 验证时间 -> `verify_ms`
3. 端到端推理延迟 -> `e2e_latency_ms`
4. 单节点峰值内存占用 -> `peak_rss_mb`
5. 系统吞吐量 -> `throughput_req_per_sec`
6. 恶意节点检测准确率 -> `malicious_detection_accuracy`

## 9. 推荐的哈希链语义

对每个切片都记录：

```python
hash_in = sha256(serialized_public_input)
hash_out = sha256(serialized_public_output)
```

Master 做：

```python
assert prev.hash_out == curr.hash_in
```

若断链：

1. 标记 `consistency_ok = False`
2. 记录可疑节点 ID
3. 计入恶意节点检测实验统计

## 10. 当前最重要的工程纪律

现阶段先做单机 proof-splitting demo，不提前做复杂分布式系统。只有当以下内容都跑通后，才进入 FastAPI / gRPC 阶段：

1. 两个子模型能独立导出 ONNX。
2. 两个子模型都能分别生成 proof 并验证通过。
3. 哈希链一致性检查通过。
4. 基本 metrics 能稳定产出。