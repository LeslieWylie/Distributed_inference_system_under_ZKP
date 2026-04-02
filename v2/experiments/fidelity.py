"""F1/F2/F3 fidelity experiment on the real MNIST MLP mainline."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import matplotlib.pyplot as plt
import numpy as np
import torch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from models.mnist_model import (
    build_slice_models,
    load_model_state,
    sample_mnist_inputs,
)
from v2.compile.build_circuits import build_registry, load_registry
from v2.experiments.mainline_utils import (
    run_client_verified_case,
    start_prover_workers,
    stop_workers,
    summarize_error,
    wait_workers_ready,
)


def _ensure_registry(num_slices: int, registry_dir: str, rebuild: bool):
    registry_path = os.path.join(registry_dir, "registry", "slice_registry.json")
    compile_ms = 0.0
    if rebuild or not os.path.exists(registry_path):
        compile_start = time.perf_counter()
        build_registry(num_slices=num_slices, registry_dir=registry_dir, model_type="mnist")
        compile_ms = (time.perf_counter() - compile_start) * 1000
    artifacts = load_registry(registry_path)
    return artifacts, round(compile_ms, 2)


def _aggregate_metric(samples: list[dict], key: str) -> dict:
    values = [sample[key] for sample in samples]
    return {
        "mean": float(np.mean(values)),
        "max": float(np.max(values)),
        "min": float(np.min(values)),
    }


def _aggregate_f2_per_slice(f2_samples: list[dict], num_slices: int) -> list[dict]:
    aggregates = []
    for slice_id in range(1, num_slices + 1):
        slice_entries = [
            entry
            for sample in f2_samples
            for entry in sample["per_slice"]
            if entry["slice_id"] == slice_id and "max_abs_error" in entry
        ]
        if not slice_entries:
            aggregates.append({
                "slice_id": slice_id,
                "note": "no valid proof-bound outputs",
            })
            continue
        aggregates.append({
            "slice_id": slice_id,
            "max_abs_error": _aggregate_metric(slice_entries, "max_abs_error"),
            "mean_abs_error": _aggregate_metric(slice_entries, "mean_abs_error"),
            "l1_distance": _aggregate_metric(slice_entries, "l1_distance"),
            "l2_distance": _aggregate_metric(slice_entries, "l2_distance"),
        })
    return aggregates


def _plot_f1(summary: dict, figure_dir: str):
    samples = summary["F1_partition_fidelity"]["samples"]
    x = np.arange(len(samples))
    max_abs = [max(sample["max_abs_error"], 1e-12) for sample in samples]
    mean_abs = [max(sample["mean_abs_error"], 1e-12) for sample in samples]

    fig, axis = plt.subplots(figsize=(9, 4.5))
    axis.bar(x - 0.18, max_abs, width=0.36, label="max_abs_error")
    axis.bar(x + 0.18, mean_abs, width=0.36, label="mean_abs_error")
    axis.set_yscale("log")
    axis.set_xticks(x, [str(sample["sample_index"]) for sample in samples])
    axis.set_xlabel("Sample index")
    axis.set_ylabel("Error (log scale)")
    axis.set_title("F1 partition fidelity")
    axis.legend()

    fig.tight_layout()
    fig.savefig(os.path.join(figure_dir, "fidelity_f1_partition.png"), dpi=200)
    plt.close(fig)


def _plot_f2(summary: dict, figure_dir: str):
    aggregates = [entry for entry in summary["F2_quantization_fidelity"]["per_slice_aggregates"] if "mean_abs_error" in entry]
    slice_ids = [entry["slice_id"] for entry in aggregates]
    mean_abs = [entry["mean_abs_error"]["mean"] for entry in aggregates]
    max_abs = [entry["max_abs_error"]["max"] for entry in aggregates]

    fig, axis = plt.subplots(figsize=(9, 4.5))
    axis.bar(np.array(slice_ids) - 0.18, mean_abs, width=0.36, label="mean_abs_error")
    axis.bar(np.array(slice_ids) + 0.18, max_abs, width=0.36, label="max_abs_error")
    axis.set_xlabel("Slice id")
    axis.set_ylabel("Error")
    axis.set_title("F2 per-slice circuit fidelity")
    axis.legend()

    fig.tight_layout()
    fig.savefig(os.path.join(figure_dir, "fidelity_f2_per_slice.png"), dpi=200)
    plt.close(fig)


def _plot_f3(summary: dict, figure_dir: str):
    samples = summary["F3_end_to_end_certified_fidelity"]["samples"]
    x = np.arange(len(samples))
    max_abs = [sample["max_abs_error"] for sample in samples]
    mean_abs = [sample["mean_abs_error"] for sample in samples]

    fig, axis = plt.subplots(figsize=(9, 4.5))
    axis.bar(x - 0.18, max_abs, width=0.36, label="max_abs_error")
    axis.bar(x + 0.18, mean_abs, width=0.36, label="mean_abs_error")
    axis.set_xticks(x, [str(sample["sample_index"]) for sample in samples])
    axis.set_xlabel("Sample index")
    axis.set_ylabel("Error")
    axis.set_title("F3 end-to-end certified fidelity")
    axis.legend()

    fig.tight_layout()
    fig.savefig(os.path.join(figure_dir, "fidelity_f3_e2e.png"), dpi=200)
    plt.close(fig)


def generate_fidelity_figures(summary: dict, figure_dir: str):
    os.makedirs(figure_dir, exist_ok=True)
    _plot_f1(summary, figure_dir)
    _plot_f2(summary, figure_dir)
    _plot_f3(summary, figure_dir)


def run_fidelity_experiments(num_slices: int = 2, num_samples: int = 5, rebuild: bool = False):
    registry_dir = os.path.join(PROJECT_ROOT, "v2", "artifacts", f"fidelity_{num_slices}s")
    artifacts, compile_ms = _ensure_registry(num_slices, registry_dir, rebuild)

    model_state_path = os.path.join(registry_dir, "models", "full_model_state.pt")
    full_model = load_model_state(model_state_path)
    slice_models = build_slice_models(full_model, num_slices)
    samples = sample_mnist_inputs(num_samples=num_samples, seed=42)

    f1_samples = []
    f2_samples = []
    f3_samples = []

    workers = start_prover_workers(artifacts, base_port=9400)
    worker_urls = [{"slice_id": worker["slice_id"], "url": worker["url"]} for worker in workers]

    try:
        wait_workers_ready(workers)

        for sample_index, sample in enumerate(samples):
            input_list = sample["input_tensor"]
            input_tensor = torch.tensor([input_list], dtype=torch.float32)

            with torch.no_grad():
                full_output = full_model(input_tensor).detach().numpy().flatten().tolist()
                current = input_tensor
                per_slice_float_outputs = {}
                for slice_id, slice_model in enumerate(slice_models, start=1):
                    current = slice_model(current)
                    per_slice_float_outputs[slice_id] = current.detach().numpy().flatten().tolist()
                sliced_output = current.detach().numpy().flatten().tolist()

            f1_metrics = summarize_error(full_output, sliced_output)
            f1_samples.append({
                "sample_index": sample_index,
                "dataset_index": sample["index"],
                "label": sample["label"],
                "full_output": full_output,
                "sliced_float_output": sliced_output,
                **f1_metrics,
            })

            case = run_client_verified_case(input_list, artifacts, worker_urls)
            proof_bound_final_output = case["proof_bound_final_output"]
            f3_metrics = summarize_error(full_output, proof_bound_final_output)
            f3_samples.append({
                "sample_index": sample_index,
                "dataset_index": sample["index"],
                "label": sample["label"],
                "client_verdict": case["client_verdict"],
                "claimed_final_output": case["bundle"].claimed_final_output,
                "proof_bound_final_output": proof_bound_final_output,
                **f3_metrics,
            })

            per_slice_f2 = []
            for slice_id in range(1, num_slices + 1):
                float_output = per_slice_float_outputs[slice_id]
                proof_output = case["proof_bound_outputs"].get(slice_id, [])
                if proof_output and len(proof_output) == len(float_output):
                    metrics = summarize_error(float_output, proof_output)
                    per_slice_f2.append({
                        "slice_id": slice_id,
                        "float_output": float_output,
                        "proof_bound_output": proof_output,
                        **metrics,
                    })
                else:
                    per_slice_f2.append({
                        "slice_id": slice_id,
                        "float_output": float_output,
                        "proof_bound_output": proof_output,
                        "note": "missing proof output or dimension mismatch",
                    })

            f2_samples.append({
                "sample_index": sample_index,
                "dataset_index": sample["index"],
                "label": sample["label"],
                "per_slice": per_slice_f2,
            })
    finally:
        stop_workers(workers)

    summary = {
        "architecture": "proof_bundle_client_verification",
        "model": "mnist_mlp",
        "num_slices": num_slices,
        "num_samples": num_samples,
        "registry_dir": registry_dir,
        "compile_ms": compile_ms,
        "model_state_path": model_state_path,
        "comparison_representation": {
            "F1": "full float model output vs sliced float output",
            "F2": "per-slice float outputs vs proof_json.pretty_public_inputs.rescaled_outputs",
            "F3": "full float model output vs last-slice proof-bound output from a certified bundle",
        },
        "F1_partition_fidelity": {
            "description": "Full float MNIST MLP vs sliced float pipeline",
            "max_abs_error": _aggregate_metric(f1_samples, "max_abs_error"),
            "mean_abs_error": _aggregate_metric(f1_samples, "mean_abs_error"),
            "l1_distance": _aggregate_metric(f1_samples, "l1_distance"),
            "l2_distance": _aggregate_metric(f1_samples, "l2_distance"),
            "samples": f1_samples,
        },
        "F2_quantization_fidelity": {
            "description": "Per-slice float outputs vs proof-bound circuit outputs",
            "per_slice_aggregates": _aggregate_f2_per_slice(f2_samples, num_slices),
            "samples": f2_samples,
        },
        "F3_end_to_end_certified_fidelity": {
            "description": "Full float model vs client-certified proof-bound final output",
            "certification_rate": sum(1 for sample in f3_samples if sample["client_verdict"] == "certified") / len(f3_samples),
            "max_abs_error": _aggregate_metric(f3_samples, "max_abs_error"),
            "mean_abs_error": _aggregate_metric(f3_samples, "mean_abs_error"),
            "l1_distance": _aggregate_metric(f3_samples, "l1_distance"),
            "l2_distance": _aggregate_metric(f3_samples, "l2_distance"),
            "samples": f3_samples,
        },
    }

    metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    metrics_path = os.path.join(metrics_dir, "fidelity_results.json")
    with open(metrics_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    figure_dir = os.path.join(PROJECT_ROOT, "figures", "midterm2")
    generate_fidelity_figures(summary, figure_dir)

    print("\nFidelity summary")
    print(f"  F1 max_abs_error mean: {summary['F1_partition_fidelity']['max_abs_error']['mean']:.6e}")
    print(f"  F3 certification_rate: {summary['F3_end_to_end_certified_fidelity']['certification_rate']:.0%}")
    print(f"  Results: {metrics_path}")
    print(f"  Figures: {figure_dir}")
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--slices', type=int, default=2)
    args = parser.parse_args()
    run_fidelity_experiments(num_slices=args.slices, num_samples=args.samples, rebuild=args.rebuild)
