"""G4 scalability experiment on the ProofBundle + client verification mainline."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import matplotlib.pyplot as plt
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from v2.compile.build_circuits import build_registry, load_registry
from v2.experiments.mainline_utils import (
    load_registry_input,
    run_client_verified_case,
    start_prover_workers,
    stop_workers,
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


def _stringify_failure_reasons(failure_reasons: list[dict]) -> list[str]:
    reasons = []
    for item in failure_reasons:
        edge = item.get("edge")
        reason = item.get("reason", "unknown failure")
        if edge is not None:
            reasons.append(f"{edge}: {reason}")
        else:
            reasons.append(reason)
    return reasons


def _record_case(case: dict) -> dict:
    metrics = case["metrics"]
    return {
        "client_verdict": case["client_verdict"],
        "failure_reasons": _stringify_failure_reasons(case["failure_reasons"]),
        "server_side_advisory": case["pipeline_result"]["server_side_advisory"],
        "execution_ms": metrics["execution_ms"],
        "total_exec_ms": metrics["total_exec_ms"],
        "total_prove_ms": metrics["total_prove_ms"],
        "client_verification_ms": metrics["client_verification_ms"],
        "total_ms": metrics["total_ms"],
        "per_slice": metrics["per_slice"],
        "proof_bound_final_output": case["proof_bound_final_output"],
    }


def _plot_total_latency(results: list[dict], figure_dir: str):
    slice_counts = [entry["num_slices"] for entry in results]
    compile_ms = [entry["compile_ms"] for entry in results]
    total_ms = [entry["normal"]["total_ms"] for entry in results]
    exec_ms = [entry["normal"]["total_exec_ms"] for entry in results]
    prove_ms = [entry["normal"]["total_prove_ms"] for entry in results]
    verify_ms = [entry["normal"]["client_verification_ms"] for entry in results]

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    x = np.arange(len(slice_counts))
    width = 0.35

    axes[0].bar(x - width / 2, compile_ms, width=width, label="Offline compile")
    axes[0].bar(x + width / 2, total_ms, width=width, label="Online total")
    axes[0].set_xticks(x, [str(value) for value in slice_counts])
    axes[0].set_xlabel("Slice count")
    axes[0].set_ylabel("Latency (ms)")
    axes[0].set_title("Compile vs online latency")
    axes[0].legend()

    axes[1].bar(slice_counts, exec_ms, label="ONNX exec")
    axes[1].bar(slice_counts, prove_ms, bottom=exec_ms, label="Proof generation")
    axes[1].bar(
        slice_counts,
        verify_ms,
        bottom=np.array(exec_ms) + np.array(prove_ms),
        label="Client verification",
    )
    axes[1].set_xlabel("Slice count")
    axes[1].set_ylabel("Latency (ms)")
    axes[1].set_title("Runtime decomposition")
    axes[1].legend()

    fig.tight_layout()
    fig.savefig(os.path.join(figure_dir, "scalability_total_latency.png"), dpi=200)
    plt.close(fig)


def _plot_proving_breakdown(results: list[dict], figure_dir: str):
    fig, axes = plt.subplots(len(results), 1, figsize=(10, 3.2 * len(results)))
    if len(results) == 1:
        axes = [axes]

    for axis, entry in zip(axes, results):
        per_slice = entry["normal"]["per_slice"]
        slice_ids = [item["slice_id"] for item in per_slice]
        prove_ms = [item["prove_ms"] for item in per_slice]
        axis.bar(slice_ids, prove_ms, color="#4c72b0")
        axis.set_title(f"{entry['num_slices']}-slice proving breakdown")
        axis.set_xlabel("Slice id")
        axis.set_ylabel("prove_ms")

    fig.tight_layout()
    fig.savefig(os.path.join(figure_dir, "scalability_proving_breakdown.png"), dpi=200)
    plt.close(fig)


def _plot_detection(results: list[dict], figure_dir: str):
    slice_counts = [entry["num_slices"] for entry in results]
    detected = [100 if entry["tamper_last"]["detection_success"] else 0 for entry in results]
    verdicts = [entry["tamper_last"]["client_verdict"] for entry in results]

    fig, axis = plt.subplots(figsize=(8, 4.5))
    bars = axis.bar(slice_counts, detected, color="#55a868")
    axis.set_ylim(0, 110)
    axis.set_xlabel("Slice count")
    axis.set_ylabel("Detection success (%)")
    axis.set_title("tamper_last detection under client verification")

    for bar, verdict in zip(bars, verdicts):
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 3,
            verdict,
            ha="center",
            va="bottom",
        )

    fig.tight_layout()
    fig.savefig(os.path.join(figure_dir, "scalability_detection.png"), dpi=200)
    plt.close(fig)


def generate_scalability_figures(results: list[dict], figure_dir: str):
    os.makedirs(figure_dir, exist_ok=True)
    _plot_total_latency(results, figure_dir)
    _plot_proving_breakdown(results, figure_dir)
    _plot_detection(results, figure_dir)


def run_scalability_experiments(slice_counts=None, rebuild: bool = False):
    if slice_counts is None:
        slice_counts = [2, 4, 8]

    results = []
    for num_slices in slice_counts:
        print(f"\n{'=' * 60}")
        print(f"SCALABILITY MAINLINE: {num_slices} slices")
        print(f"{'=' * 60}")

        registry_dir = os.path.join(PROJECT_ROOT, "v2", "artifacts", f"scale_{num_slices}s")
        artifacts, compile_ms = _ensure_registry(num_slices, registry_dir, rebuild)
        initial_input = load_registry_input(registry_dir)

        workers = start_prover_workers(artifacts, base_port=9000 + (num_slices * 10))
        worker_urls = [{"slice_id": worker["slice_id"], "url": worker["url"]} for worker in workers]

        try:
            wait_workers_ready(workers)
            normal_case = run_client_verified_case(initial_input, artifacts, worker_urls)
            tamper_case = run_client_verified_case(
                initial_input,
                artifacts,
                worker_urls,
                fault_at=num_slices,
                fault_type="tamper",
            )

            # Interior-slice attacks (only meaningful for >= 2 slices)
            mid_slice = max(1, num_slices // 2)
            tamper_mid_case = run_client_verified_case(
                initial_input,
                artifacts,
                worker_urls,
                fault_at=mid_slice,
                fault_type="tamper",
            )
            skip_last_case = run_client_verified_case(
                initial_input,
                artifacts,
                worker_urls,
                fault_at=num_slices,
                fault_type="skip",
            )
        finally:
            stop_workers(workers)

        entry = {
            "architecture": "proof_bundle_client_verification",
            "model": "mnist_mlp",
            "num_slices": num_slices,
            "registry_dir": registry_dir,
            "compile_ms": compile_ms,
            "normal": _record_case(normal_case),
            "tamper_last": {
                **_record_case(tamper_case),
                "detection_success": tamper_case["client_verdict"] == "invalid",
            },
            "tamper_mid": {
                **_record_case(tamper_mid_case),
                "fault_at": mid_slice,
                "detection_success": tamper_mid_case["client_verdict"] == "invalid",
            },
            "skip_last": {
                **_record_case(skip_last_case),
                "detection_success": skip_last_case["client_verdict"] == "invalid",
            },
        }
        results.append(entry)

        print(
            f"  normal={entry['normal']['client_verdict']} "
            f"total={entry['normal']['total_ms']:.0f}ms "
            f"prove={entry['normal']['total_prove_ms']:.0f}ms "
            f"verify={entry['normal']['client_verification_ms']:.0f}ms"
        )
        print(
            f"  tamper_last={entry['tamper_last']['client_verdict']} "
            f"detected={entry['tamper_last']['detection_success']}"
        )
        print(
            f"  tamper_mid(slice {mid_slice})={entry['tamper_mid']['client_verdict']} "
            f"detected={entry['tamper_mid']['detection_success']}"
        )
        print(
            f"  skip_last={entry['skip_last']['client_verdict']} "
            f"detected={entry['skip_last']['detection_success']}"
        )

    metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    metrics_path = os.path.join(metrics_dir, "scalability_results.json")
    with open(metrics_path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)

    figure_dir = os.path.join(PROJECT_ROOT, "figures", "midterm2")
    generate_scalability_figures(results, figure_dir)

    print(f"\nResults written to: {metrics_path}")
    print(f"Figures written to: {figure_dir}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--slice-counts", type=int, nargs="*", default=[2, 4, 8])
    args = parser.parse_args()
    run_scalability_experiments(slice_counts=args.slice_counts, rebuild=args.rebuild)
