"""
v2/experiments/fidelity.py — Fidelity 分层实验。

严格区分两层 fidelity (参考 Non-Composability Note):

F1. Partition Fidelity
    完整浮点模型 vs 切片后浮点语义串联
    目的: 证明切片本身不改变函数组合

F2. Quantization / Circuit Fidelity
    完整浮点模型 vs EZKL proof-bound rescaled_outputs
    目的: 衡量 zkML 量化+电路化引入的误差

F3. End-to-End Certified Fidelity
    完整浮点模型 vs certified pipeline 最终输出
    目的: 衡量经过全链路证明的系统输出与原始模型的偏差
"""

import json
import os
import sys
import time

import numpy as np
import torch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


def run_fidelity_experiments(num_slices: int = 4, num_samples: int = 5):
    """运行 F1 + F2 + F3 fidelity 实验。"""
    from models.configurable_model import ConfigurableModel, SliceModel
    from v2.compile.build_circuits import load_registry
    from v2.execution.pipeline import run_certified_pipeline

    seed = 42
    torch.manual_seed(seed)

    # 创建完整模型 (与 registry 相同参数)
    full_model = ConfigurableModel(
        input_dim=8, hidden_dim=8, output_dim=4, num_layers=8,
    )
    full_model.eval()

    # 切片模型 (PyTorch 级)
    all_layers = list(full_model.layers)
    total = len(all_layers)
    slice_sizes = [total // num_slices] * num_slices
    for i in range(total % num_slices):
        slice_sizes[i] += 1

    slice_models = []
    idx = 0
    for size in slice_sizes:
        sm = SliceModel(torch.nn.Sequential(*all_layers[idx:idx + size]))
        sm.eval()
        slice_models.append(sm)
        idx += size

    # 加载 EZKL registry
    registry_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "registry", "slice_registry.json",
    )
    artifacts = load_registry(registry_path)

    # 生成随机输入
    np.random.seed(seed)
    inputs = [np.random.randn(8).astype(np.float32).tolist() for _ in range(num_samples)]

    f1_results = []
    f2_results = []
    f3_results = []

    for i, inp in enumerate(inputs):
        inp_t = torch.tensor([inp])

        # ── F1: 完整模型 vs 切片 PyTorch 串联 ──
        with torch.no_grad():
            full_out = full_model(inp_t).numpy().flatten()
            # 逐切片串联
            x = inp_t
            for sm in slice_models:
                x = sm(x)
            sliced_out = x.numpy().flatten()

        f1_diff = np.abs(full_out - sliced_out)
        f1_results.append({
            "sample": i,
            "l1_distance": float(np.sum(f1_diff)),
            "l2_distance": float(np.linalg.norm(f1_diff)),
            "max_abs_error": float(np.max(f1_diff)),
            "mean_abs_error": float(np.mean(f1_diff)),
        })

        # ── F2 + F3: 需要跑 certified pipeline 获取 proof-bound outputs ──
        result = run_certified_pipeline(inp, artifacts)

        # F3: certified output vs full model
        certified_out = np.array(result["provisional_output"])
        f3_diff = np.abs(full_out - certified_out)
        f3_results.append({
            "sample": i,
            "status": result["certificate"]["status"],
            "l1_distance": float(np.sum(f3_diff)),
            "l2_distance": float(np.linalg.norm(f3_diff)),
            "max_abs_error": float(np.max(f3_diff)),
            "mean_abs_error": float(np.mean(f3_diff)),
        })

        # ── F2: 逐切片 circuit fidelity ──
        #   对每片: 从 proof 的 rescaled_outputs 中提取电路输出
        #   与对应的 PyTorch 切片浮点输出比较
        #   这才是真正的 per-slice quantization fidelity
        with torch.no_grad():
            float_x = torch.tensor([inp])
            per_slice_f2 = []
            for j, sm in enumerate(slice_models):
                float_out_j = sm(float_x).numpy().flatten()

                # 从 pipeline result 中提取该切片的 proof rescaled_outputs
                proof_jobs = result.get("_proof_jobs", [])
                circuit_out_j = None
                if j < len(proof_jobs) and proof_jobs[j].proof_data:
                    ppi = proof_jobs[j].proof_data.get("pretty_public_inputs", {})
                    ro = ppi.get("rescaled_outputs", [])
                    if ro:
                        flat = []
                        for g in ro:
                            if isinstance(g, list):
                                for v in g:
                                    flat.append(float(v))
                            else:
                                flat.append(float(g))
                        if flat:
                            circuit_out_j = np.array(flat)

                if circuit_out_j is not None and len(circuit_out_j) == len(float_out_j):
                    diff_j = np.abs(float_out_j - circuit_out_j)
                    per_slice_f2.append({
                        "slice_id": j + 1,
                        "max_abs_error": float(np.max(diff_j)),
                        "mean_abs_error": float(np.mean(diff_j)),
                        "l1_distance": float(np.sum(diff_j)),
                    })
                else:
                    per_slice_f2.append({
                        "slice_id": j + 1,
                        "max_abs_error": None,
                        "mean_abs_error": None,
                        "note": "rescaled_outputs unavailable",
                    })

                float_x = sm(float_x)  # cascade

        f2_results.append({
            "sample": i,
            "per_slice": per_slice_f2,
            "e2e_circuit_vs_float": {
                "l1_distance": float(np.sum(f3_diff)),
                "max_abs_error": float(np.max(f3_diff)),
                "mean_abs_error": float(np.mean(f3_diff)),
            },
        })

    # 汇总
    def _agg(results, key):
        vals = [r[key] for r in results]
        return {
            "mean": float(np.mean(vals)),
            "max": float(np.max(vals)),
            "min": float(np.min(vals)),
        }

    summary = {
        "num_slices": num_slices,
        "num_samples": num_samples,
        "F1_partition_fidelity": {
            "description": "Full float model vs sliced float model (PyTorch)",
            "max_abs_error": _agg(f1_results, "max_abs_error"),
            "mean_abs_error": _agg(f1_results, "mean_abs_error"),
            "l1_distance": _agg(f1_results, "l1_distance"),
            "samples": f1_results,
        },
        "F2_quantization_fidelity": {
            "description": "Full float model vs EZKL circuit outputs (quantized)",
            "samples": f2_results,
        },
        "F3_certified_fidelity": {
            "description": "Full float model vs certified pipeline output",
            "max_abs_error": _agg(f3_results, "max_abs_error"),
            "mean_abs_error": _agg(f3_results, "mean_abs_error"),
            "l1_distance": _agg(f3_results, "l1_distance"),
            "certification_rate": sum(
                1 for r in f3_results if r["status"] == "certified"
            ) / len(f3_results),
            "samples": f3_results,
        },
    }

    # 打印结果
    print("\n" + "=" * 60)
    print("FIDELITY RESULTS")
    print("=" * 60)

    print(f"\nF1 Partition Fidelity (float vs float-sliced):")
    print(f"  Max absolute error: {summary['F1_partition_fidelity']['max_abs_error']}")
    print(f"  Mean absolute error: {summary['F1_partition_fidelity']['mean_abs_error']}")

    print(f"\nF3 Certified Fidelity (float vs certified output):")
    print(f"  Max absolute error: {summary['F3_certified_fidelity']['max_abs_error']}")
    print(f"  Mean absolute error: {summary['F3_certified_fidelity']['mean_abs_error']}")
    print(f"  Certification rate: {summary['F3_certified_fidelity']['certification_rate']:.0%}")

    # 写入文件
    metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    out_path = os.path.join(metrics_dir, "fidelity_results.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  Results: {out_path}")

    return summary


if __name__ == "__main__":
    run_fidelity_experiments()
