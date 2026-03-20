"""
v2/experiments/e2e_certified.py — Phase A 端到端认证实验。

实验目标:
  验证新协议在正常和攻击场景下的 end-to-end correctness。

实验矩阵:
  1. 正常推理 → 应获得 CERTIFIED
  2. tamper 攻击 → 应检测到 commitment link failure → INVALID
  3. skip 攻击 → INVALID
  4. random 攻击 → INVALID
  5. replay 攻击 → INVALID

每组实验记录:
  - execution_latency_ms
  - total_proof_gen_ms
  - verification_ms
  - certification_status
  - link_failures (具体哪条边失败)
  - proof_failures (具体哪片失败)

用法:
    python -m v2.experiments.e2e_certified
"""

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from v2.compile.build_circuits import build_registry, load_registry
from v2.execution.pipeline import run_certified_pipeline


def run_experiments(num_slices: int = 4, rebuild: bool = False):
    """运行 Phase A 完整实验矩阵。"""
    registry_dir = os.path.join(PROJECT_ROOT, "v2", "artifacts")
    registry_path = os.path.join(registry_dir, "registry", "slice_registry.json")

    # 编译阶段 (只需做一次)
    if rebuild or not os.path.exists(registry_path):
        print("=" * 60)
        print("COMPILE PHASE: Building circuits...")
        print("=" * 60)
        compile_start = time.perf_counter()
        artifacts = build_registry(num_slices=num_slices)
        compile_ms = (time.perf_counter() - compile_start) * 1000
        print(f"Compile complete: {compile_ms:.0f}ms")
    else:
        print(f"Loading existing registry: {registry_path}")
        artifacts = load_registry(registry_path)

    # 读取初始输入
    input_path = os.path.join(registry_dir, "models", "slice_1_input.json")
    with open(input_path, "r") as f:
        initial_input = json.load(f)["input_data"][0]

    # 实验矩阵
    experiments = [
        {"name": "normal", "fault_at": None, "fault_type": "none",
         "expected_status": "certified"},
        {"name": "tamper_last", "fault_at": num_slices, "fault_type": "tamper",
         "expected_status": "invalid"},
        {"name": "tamper_mid", "fault_at": max(1, num_slices // 2), "fault_type": "tamper",
         "expected_status": "invalid"},
        {"name": "skip", "fault_at": num_slices, "fault_type": "skip",
         "expected_status": "invalid"},
        {"name": "random", "fault_at": num_slices, "fault_type": "random",
         "expected_status": "invalid"},
        {"name": "replay", "fault_at": num_slices, "fault_type": "replay",
         "expected_status": "invalid"},
    ]

    results = []
    print("\n" + "=" * 60)
    print("EXPERIMENT PHASE")
    print("=" * 60)

    for exp in experiments:
        print(f"\n{'─' * 60}")
        print(f"Experiment: {exp['name']}")
        print(f"{'─' * 60}")

        result = run_certified_pipeline(
            initial_input=initial_input,
            artifacts=artifacts,
            fault_at=exp["fault_at"],
            fault_type=exp["fault_type"],
        )

        cert_status = result["certificate"]["status"]
        expected = exp["expected_status"]
        passed = (cert_status == expected)

        result_entry = {
            "experiment": exp["name"],
            "fault_at": exp["fault_at"],
            "fault_type": exp["fault_type"],
            "expected_status": expected,
            "actual_status": cert_status,
            "test_passed": passed,
            "metrics": result["metrics"],
            "certificate": result["certificate"],
        }
        results.append(result_entry)

        status_mark = "PASS" if passed else "FAIL"
        print(f"\n  Result: {cert_status} (expected: {expected}) [{status_mark}]")

    # 汇总
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    all_passed = True
    for r in results:
        mark = "✓" if r["test_passed"] else "✗"
        print(f"  {mark} {r['experiment']:15s} → {r['actual_status']:12s} "
              f"(expected: {r['expected_status']:12s}) "
              f"total={r['metrics']['total_ms']:.0f}ms")
        if not r["test_passed"]:
            all_passed = False

    print(f"\n  Overall: {'ALL PASSED' if all_passed else 'SOME FAILED'}")

    # 写入结果
    metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    out_path = os.path.join(metrics_dir, "e2e_certified_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results written: {out_path}")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slices", type=int, default=4)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    run_experiments(num_slices=args.slices, rebuild=args.rebuild)
