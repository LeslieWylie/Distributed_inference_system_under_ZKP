"""
v2/experiments/deferred_certified.py — Phase B 延迟认证实验。

实验目标:
  1. 验证 deferred certification 的正确性 (与 Phase A 一致)
  2. 测量 provisional latency vs certification latency
  3. 测量并行 proving 的加速效果

实验矩阵:
  - 正常推理 + tamper/skip/random/replay 攻击
  - 不同并行度 (1 / 2 / 4 workers)

用法:
    python -m v2.experiments.deferred_certified
"""

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from v2.compile.build_circuits import load_registry
from v2.execution.deferred_pipeline import run_deferred_pipeline


def run_experiments(num_slices: int = 4):
    """运行 Phase B 完整实验矩阵。"""
    registry_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "registry", "slice_registry.json",
    )
    artifacts = load_registry(registry_path)

    input_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "models", "slice_1_input.json",
    )
    with open(input_path, "r") as f:
        initial_input = json.load(f)["input_data"][0]

    # G2: Protocol correctness (same attacks as Phase A)
    correctness_tests = [
        {"name": "normal", "fault_at": None, "fault_type": "none",
         "expected": "certified"},
        {"name": "tamper_last", "fault_at": num_slices, "fault_type": "tamper",
         "expected": "invalid"},
        {"name": "tamper_mid", "fault_at": max(1, num_slices // 2),
         "fault_type": "tamper", "expected": "invalid"},
        {"name": "skip", "fault_at": num_slices, "fault_type": "skip",
         "expected": "invalid"},
        {"name": "random", "fault_at": num_slices, "fault_type": "random",
         "expected": "invalid"},
        {"name": "replay", "fault_at": num_slices, "fault_type": "replay",
         "expected": "invalid"},
    ]

    results = []

    # ── G2: Protocol correctness with deferred proving ──
    print("\n" + "=" * 60)
    print("G2: PROTOCOL CORRECTNESS (Deferred)")
    print("=" * 60)

    for test in correctness_tests:
        print(f"\n{'─' * 50}\n{test['name']}\n{'─' * 50}")
        r = run_deferred_pipeline(
            initial_input, artifacts,
            fault_at=test["fault_at"],
            fault_type=test["fault_type"],
            max_prove_workers=2,
        )
        status = r["certificate"]["status"]
        passed = (status == test["expected"])
        results.append({
            "group": "G2_correctness",
            "name": test["name"],
            "expected": test["expected"],
            "actual": status,
            "passed": passed,
            "metrics": r["metrics"],
        })
        print(f"  [{('PASS' if passed else 'FAIL')}] {test['name']}: {status}")

    # ── G3: Latency decomposition (different parallelism) ──
    print("\n" + "=" * 60)
    print("G3: LATENCY DECOMPOSITION")
    print("=" * 60)

    for workers in [1, 2, 4]:
        print(f"\n{'─' * 50}\nParallelism: {workers} workers\n{'─' * 50}")
        r = run_deferred_pipeline(
            initial_input, artifacts,
            max_prove_workers=workers,
        )
        results.append({
            "group": "G3_latency",
            "name": f"parallel_{workers}w",
            "prove_workers": workers,
            "metrics": r["metrics"],
        })

    # 汇总
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    g2 = [r for r in results if r["group"] == "G2_correctness"]
    g3 = [r for r in results if r["group"] == "G3_latency"]

    print("\nG2 Protocol Correctness:")
    all_g2_pass = True
    for r in g2:
        mark = "✓" if r["passed"] else "✗"
        m = r["metrics"]
        print(f"  {mark} {r['name']:15s} → {r['actual']:10s} "
              f"exec={m['execution_ms']:.0f}ms "
              f"cert={m['certification_ms']:.0f}ms")
        if not r["passed"]:
            all_g2_pass = False

    print(f"\n  G2 Overall: {'ALL PASSED' if all_g2_pass else 'SOME FAILED'}")

    print("\nG3 Latency Decomposition:")
    for r in g3:
        m = r["metrics"]
        print(f"  {r['name']:15s} exec={m['execution_ms']:.0f}ms "
              f"prove={m['proving_ms']:.0f}ms "
              f"verify={m['verification_ms']:.0f}ms "
              f"total={m['total_ms']:.0f}ms")

    # 保存
    metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    out_path = os.path.join(metrics_dir, "deferred_certified_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results: {out_path}")

    return results


if __name__ == "__main__":
    run_experiments()
