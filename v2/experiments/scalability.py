"""
v2/experiments/scalability.py — 多切片可扩展性实验。

G4: 不同切片数 (2/4/8) 下的:
  - 编译时间
  - 证明开销
  - 验证开销
  - 端到端延迟
  - 证书状态 (正常 + 攻击)
"""

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from v2.compile.build_circuits import build_registry, load_registry
from v2.execution.pipeline import run_certified_pipeline


def run_scalability_experiments(slice_counts=None):
    """运行多切片可扩展性实验。"""
    if slice_counts is None:
        slice_counts = [2, 4, 8]

    results = []

    for n in slice_counts:
        print(f"\n{'='*60}")
        print(f"SCALABILITY: {n} slices")
        print(f"{'='*60}")

        # 编译
        registry_dir = os.path.join(PROJECT_ROOT, "v2", "artifacts", f"scale_{n}s")
        registry_path = os.path.join(registry_dir, "registry", "slice_registry.json")

        compile_start = time.perf_counter()
        artifacts = build_registry(
            num_slices=n, num_layers=8,
            registry_dir=registry_dir,
        )
        compile_ms = (time.perf_counter() - compile_start) * 1000

        # 加载输入
        input_path = os.path.join(registry_dir, "models", "slice_1_input.json")
        with open(input_path) as f:
            inp = json.load(f)["input_data"][0]

        # 正常推理
        r_normal = run_certified_pipeline(inp, artifacts)

        # 篡改攻击 (最后一片)
        r_tamper = run_certified_pipeline(
            inp, artifacts, fault_at=n, fault_type="tamper",
        )

        entry = {
            "num_slices": n,
            "compile_ms": round(compile_ms, 2),
            "normal": {
                "status": r_normal["certificate"]["status"],
                "total_ms": r_normal["metrics"]["total_ms"],
                "execution_ms": r_normal["metrics"]["execution_ms"],
                "proof_gen_ms": r_normal["metrics"]["total_proof_gen_ms"],
                "verification_ms": r_normal["metrics"]["verification_ms"],
                "per_slice_proof_ms": [
                    s["prove_ms"] for s in r_normal["metrics"]["per_slice"]
                ],
            },
            "tamper": {
                "status": r_tamper["certificate"]["status"],
                "detected": r_tamper["certificate"]["status"] == "invalid",
                "total_ms": r_tamper["metrics"]["total_ms"],
            },
        }
        results.append(entry)

        print(f"\n  {n}s: compile={compile_ms:.0f}ms "
              f"total={entry['normal']['total_ms']:.0f}ms "
              f"proof={entry['normal']['proof_gen_ms']:.0f}ms "
              f"verify={entry['normal']['verification_ms']:.0f}ms")
        print(f"  normal={entry['normal']['status']} "
              f"tamper={entry['tamper']['status']}")

    # 汇总
    print(f"\n{'='*60}")
    print("SCALABILITY SUMMARY")
    print(f"{'='*60}")
    print(f"{'Slices':>8} {'Compile':>10} {'Total':>10} {'Proof':>10} "
          f"{'Verify':>10} {'Normal':>10} {'Tamper':>10}")
    for r in results:
        print(f"{r['num_slices']:>8} {r['compile_ms']:>10.0f} "
              f"{r['normal']['total_ms']:>10.0f} "
              f"{r['normal']['proof_gen_ms']:>10.0f} "
              f"{r['normal']['verification_ms']:>10.0f} "
              f"{r['normal']['status']:>10} "
              f"{r['tamper']['status']:>10}")

    # 保存
    metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    out_path = os.path.join(metrics_dir, "scalability_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results: {out_path}")

    return results


if __name__ == "__main__":
    run_scalability_experiments()
