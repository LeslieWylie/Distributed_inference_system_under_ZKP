# 开发报告：面向分布式推理的零知识证明框架 — 阶段 1

> 编写日期：2026-03-17
> 当前阶段：阶段 1（单机 2 切片 Demo）已跑通

---

## 一、项目概述

本项目实现一个 **应用层** 的分布式推理 + 零知识证明可验证性框架：

- 将深度学习模型按层切片，每个节点各自推理
- 每个节点用 EZKL 为自己的切片生成局部 ZK proof
- Master 通过哈希链校验跨节点输入输出一致性
- 参考架构：DSperse（Model Slicing + Targeted Verification）
- **不是** Distributed Prover（不涉及分布式 FFT/MSM/多项式承诺）

## 二、目录结构

```
C:\ZKP\
├── models/                    # 模型定义与导出产物
│   ├── full_model.py          # PyTorch 模型定义 + 切片 + ONNX 导出
│   ├── slice_1.onnx           # 切片 1 的 ONNX 文件
│   ├── slice_2.onnx           # 切片 2 的 ONNX 文件
│   ├── slice_1_input.json     # 切片 1 的推理输入数据
│   ├── slice_2_input.json     # 切片 2 的推理输入数据
│   ├── slice_1_cal.json       # 切片 1 的校准数据
│   └── slice_2_cal.json       # 切片 2 的校准数据
├── scripts/
│   └── run_single_machine_demo.py   # 阶段 1 主脚本
├── artifacts/                 # EZKL 证明产物
│   ├── slice_1/               # settings, compiled, pk, vk, srs, witness, proof
│   └── slice_2/
├── metrics/
│   └── latest_run.json        # 最近一次运行的指标
├── survey/                    # 开题报告与参考文献
│   ├── 开题报告重写稿.md
│   └── reference/
│       ├── COPILOT_SYSTEM_PROMPT_GUIDE.md
│       ├── DEVELOPMENT_STEPS.md
│       ├── EZKL_DSPERSE_REFERENCE.md
│       └── 系统Prompt指南解析.md
└── DEVELOPMENT_REPORT.md      # 本文件
```

## 三、环境配置（★ 重点）

### 3.1 推荐方案：Miniconda

经过大量测试，**Miniconda** 是 Windows 上运行 EZKL 最稳定的方案。

#### 安装 Miniconda

```powershell
winget install --id Anaconda.Miniconda3 -e --accept-source-agreements --accept-package-agreements --silent
```

安装完成后路径为：`C:\Users\<用户名>\AppData\Local\miniconda3`

#### 首次使用需接受 Terms of Service

```powershell
# 用 Miniconda 自带的 Python 执行
$CONDA_PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
& $CONDA_PY -m conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
& $CONDA_PY -m conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
& $CONDA_PY -m conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/msys2
```

#### 安装项目依赖

```powershell
$CONDA_PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
& $CONDA_PY -m pip install ezkl torch onnx onnxscript psutil
```

#### 验证安装

```powershell
& $CONDA_PY -c "import sys, ezkl, torch, onnx, psutil; print(sys.version); print('ezkl', ezkl.__version__); print('torch', torch.__version__)"
```

预期输出（版本号可能不同）：

```
3.13.x (packaged by Anaconda, Inc.) ...
ezkl 23.0.5
torch 2.10.0+cpu
```

### 3.2 已验证的依赖版本

| 包 | 版本 | 说明 |
|---|---|---|
| Python | 3.13.12 (Anaconda) | **不要用 3.14**，EZKL 在 3.14 上不稳定 |
| ezkl | 23.0.5 | ZK 证明引擎 |
| torch | 2.10.0+cpu | 模型定义与 ONNX 导出 |
| onnx | 1.20.1 | ONNX 格式支持 |
| onnxscript | 0.6.2 | torch.onnx.export 的依赖 |
| psutil | 7.2.2 | 内存监测 |

### 3.3 不推荐的方案

| 方案 | 问题 |
|---|---|
| `python -m venv`（Python 3.14） | EZKL 在 `setup()` 步骤 Rust 层 panic |
| `python -m venv`（Python 3.13） | 同样遇到 `HOME` 环境变量问题 |
| 系统全局 Python | 容易污染，不推荐 |

> venv 方案的核心问题已通过代码层面修复（见第四节），理论上也可用，但 Miniconda 更简单。

## 四、Windows 上的两个关键坑（必读）

### 坑 1：EZKL 读取 `HOME` 环境变量 → Rust panic

**现象**：`ezkl.setup()` / `ezkl.prove()` 抛出 `pyo3_runtime.PanicException: called Result::unwrap() on an Err value: NotPresent`

**根因**：EZKL 源码 `src/execute.rs` 第 75 行使用 `std::env::var("HOME").unwrap()` 来确定 `~/.ezkl/` 目录。Windows 没有 `HOME` 环境变量（只有 `HOMEDRIVE` + `HOMEPATH`），所以 Rust 直接 panic。

**解决方案**（已写入 `run_single_machine_demo.py` 开头）：

```python
from pathlib import Path
USER_HOME = str(Path.home())
os.environ.setdefault("HOME", USER_HOME)
os.environ.setdefault("EZKL_REPO_PATH", os.path.join(USER_HOME, ".ezkl"))
os.makedirs(os.environ["EZKL_REPO_PATH"], exist_ok=True)
```

**要求**：这段代码必须在 `import ezkl` **之前** 执行。

### 坑 2：`torch.onnx` 输出 emoji → GBK 编码崩溃

**现象**：重定向输出到文件时，`torch.onnx.export` 打印的 ✅ emoji 导致 `UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'`

**解决方案**：运行脚本前设置环境变量：

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

或在脚本最前面加：

```python
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
```

## 五、如何运行阶段 1 Demo

### 一键运行命令

```powershell
cd C:\ZKP
$env:PYTHONIOENCODING = "utf-8"
C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe -u scripts/run_single_machine_demo.py
```

### 预期输出

```
============================================================
阶段 1: 单机最小可运行验证 Demo
============================================================

[Step 1] 导出模型切片...
[Model] Slice 1 ONNX  -> ...\models\slice_1.onnx
[Model] Slice 2 ONNX  -> ...\models\slice_2.onnx
[Model] 切片组合验证通过 ✓

[Step 2] Slice 1 EZKL 流程...
  [Slice 1] gen_settings ✓
  [Slice 1] calibrate_settings ✓
  [Slice 1] compile_circuit ✓
  [Slice 1] get_srs ✓
  [Slice 1] gen_witness ✓
  [Slice 1] setup ✓
  [Slice 1] prove ✓  (~2000 ms)
  [Slice 1] verify ✓  (~70 ms)

[Step 3] Slice 2 EZKL 流程...
  ... (同上)

[Step 4] 哈希链一致性校验...
  Consistency: PASS ✓

[Step 5] 故障注入测试...
  Malicious detected: YES ✓
```

### 产出文件

运行后 `metrics/latest_run.json` 包含全部指标：

```json
{
  "e2e_latency_ms": 23479.48,
  "slices": [
    { "slice_id": 1, "proof_gen_ms": 2062.82, "verify_ms": 71.79, "peak_rss_mb": 362.14 },
    { "slice_id": 2, "proof_gen_ms": 2765.45, "verify_ms": 31.22, "peak_rss_mb": 362.69 }
  ],
  "hash_chain": { "consistency_ok": true },
  "fault_injection": { "malicious_detected": true, "detection_accuracy": 1.0 }
}
```

## 六、EZKL API 调用链（官方文档确认）

每个切片的完整证明流程：

```
gen_settings → calibrate_settings → compile_circuit → get_srs
→ gen_witness → setup → prove → verify
```

| 步骤 | 函数 | 同步/异步 | 说明 |
|---|---|---|---|
| 1 | `ezkl.gen_settings(model, output, py_run_args)` | 同步 | 生成电路设置 |
| 2 | `ezkl.calibrate_settings(data, model, settings, "resources")` | 同步 | 校准参数 |
| 3 | `ezkl.compile_circuit(model, compiled, settings)` | 同步 | 编译电路 |
| 4 | `ezkl.get_srs(settings_path, srs_path=...)` | **异步** | 下载/加载 SRS，需要 `await` |
| 5 | `ezkl.gen_witness(data, compiled, output)` | 同步 | 生成 witness |
| 6 | `ezkl.setup(compiled, vk_path, pk_path, srs_path=...)` | 同步 | 生成 pk/vk 密钥对 |
| 7 | `ezkl.prove(witness, compiled, pk_path, proof_path, srs_path=...)` | 同步 | 生成证明 |
| 8 | `ezkl.verify(proof_path, settings, vk_path, srs_path=...)` | 同步 | 验证证明 |

**注意**：`get_srs` 是唯一的异步函数，必须在 `async def` 中用 `await` 调用：

```python
async def _fetch_srs():
    return await ezkl.get_srs(settings_path=settings_path, srs_path=srs_path)

res = asyncio.run(_fetch_srs())
```

## 七、EZKL 数据格式

EZKL 要求的输入数据 JSON 格式为：

```json
{
  "input_data": [[flat_list_of_floats]]
}
```

**不要**加 `input_shapes`、`output_data` 等多余字段，否则可能导致报错。

## 八、三阶段开发路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段 1 | 单机 2 切片 Demo：证明 + 验证 + 哈希链 + metrics | ✅ 已完成 |
| 阶段 2 | 本地 Master/Worker 原型（FastAPI/gRPC）| 待开始 |
| 阶段 3 | 实验：不同切片粒度/节点数/故障注入比例 | 待开始 |

## 九、6 项核心评估指标

| 指标 | 代码字段 | 阶段 1 结果 |
|---|---|---|
| 证明生成时间 | `proof_gen_ms` | ~2000-2800 ms |
| 验证时间 | `verify_ms` | ~30-70 ms |
| 端到端推理延迟 | `e2e_latency_ms` | ~23500 ms |
| 单节点峰值内存 | `peak_rss_mb` | ~363 MB |
| 系统吞吐量 | `throughput_req_per_sec` | 阶段 2 测 |
| 恶意节点检测准确率 | `detection_accuracy` | 100% |

## 十、技术栈约束（必须遵守）

| 组件 | 技术选型 | 备注 |
|---|---|---|
| 模型定义与切分 | PyTorch | |
| 模型导出 | ONNX (opset 18) | |
| 零知识证明引擎 | EZKL Python API | 不要自己实现密码学 |
| 分布式通信 | FastAPI + requests 或 gRPC | 阶段 2 |
| 性能监控 | psutil + time | |

**禁止方向**：分布式 FFT、分布式 MSM、分布式多项式承诺、自定义底层 zk prover。

## 十一、常见问题 FAQ

### Q: 首次运行 `get_srs` 很慢？

A: 正常，首次会从远程下载 SRS 文件（约 4MB），后续运行会复用本地缓存（`~/.ezkl/srs/` 目录）。

### Q: `calibrate_settings` 输出的 Numerical Fidelity Report 是什么？

A: EZKL 在量化模型权重时的精度报告。`mean_error` 在 1e-4 量级是正常的。

### Q: 如何更换验证粒度？

A: 修改 `models/full_model.py` 中的切片逻辑，将模型切成更多或更少的 slice，每个 slice 独立导出 ONNX 并走完 EZKL 流程。

### Q: 如何在 Linux/macOS 上运行？

A: 不需要 `HOME` 环境变量的 workaround（Linux/macOS 本身就有）。其余流程完全一致。
