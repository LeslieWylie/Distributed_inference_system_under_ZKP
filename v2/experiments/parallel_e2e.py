"""
v2/experiments/parallel_e2e.py — 并行证明全链路端到端实验。

与 refactored_e2e.py 的区别:
  - 使用流水线并行模式: 先串行推理(~1ms/片), 再并行证明
  - 对比串行模式的性能差异
  - 同样覆盖 normal + 攻击场景, 验证安全性不因并行化而降级

用法:
    python -m v2.experiments.parallel_e2e [--slices 2] [--rebuild]
"""

import json
import os
import subprocess
import sys
import time

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

PYTHON = sys.executable


def start_prover_workers(artifacts, base_port=9101):
    """启动多个 Prover-Worker 子进程 (用不同端口避免与其他实验冲突)。"""
    workers = []
    for a in artifacts:
        port = base_port + a.slice_id - 1
        cmd = [
            PYTHON, "-u", "-m", "v2.services.prover_worker",
            "--slice-id", str(a.slice_id),
            "--port", str(port),
            "--onnx", a.model_path,
            "--compiled", a.compiled_path,
            "--pk", a.pk_path,
            "--srs", a.srs_path,
            "--settings", a.settings_path,
            "--host", "0.0.0.0",
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd, env=env, cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        workers.append({
            "slice_id": a.slice_id,
            "url": f"http://127.0.0.1:{port}",
            "proc": proc,
        })
    return workers


def wait_workers_ready(workers, timeout=180):
    deadline = time.time() + timeout
    for w in workers:
        while time.time() < deadline:
            try:
                r = requests.get(f"{w['url']}/health", timeout=5)
                if r.status_code == 200 and r.json().get("role") == "prover_worker":
                    print(f"  Worker {w['slice_id']} ready at {w['url']}")
                    break
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(1)
        else:
            raise TimeoutError(f"Worker {w['slice_id']} not ready")


def stop_workers(workers):
    for w in workers:
        w["proc"].terminate()
        try:
            w["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            w["proc"].kill()


def run_parallel_e2e(num_slices=2, rebuild=False):
    from v2.compile.build_circuits import build_registry, load_registry
    from v2.services.distributed_coordinator import (
        run_distributed_pipeline,
        run_distributed_pipeline_parallel,
    )
    from v2.verifier.bundle_verifier import verify_bundle

    registry_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "registry", "slice_registry.json",
    )

    if rebuild or not os.path.exists(registry_path):
        print(f"[Build] Compiling {num_slices}-slice circuits...")
        build_registry(num_slices=num_slices, model_type="mnist")
    else:
        print(f"[Build] Using existing registry: {registry_path}")

    artifacts = load_registry(registry_path)

    input_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "models", "slice_1_input.json",
    )
    with open(input_path) as f:
        initial_input = json.load(f)["input_data"][0]

    print(f"\n[Info] Model: MNIST MLP, {num_slices} slices")
    print(f"[Info] Input dim: {len(initial_input)}")

    # 启动 Workers
    print("\n[Workers] Starting Prover-Workers...")
    workers = start_prover_workers(artifacts)
    worker_urls = [{"slice_id": w["slice_id"], "url": w["url"]} for w in workers]

    try:
        wait_workers_ready(workers)
        print(f"[Workers] All {len(workers)} ready.\n")

        tests = [
            {"name": "normal", "fault_at": None, "fault_type": "none",
             "expected": "certified"},
            {"name": "tamper_last", "fault_at": num_slices, "fault_type": "tamper",
             "expected": "invalid"},
            {"name": "skip_last", "fault_at": num_slices, "fault_type": "skip",
             "expected": "invalid"},
        ]
        if num_slices >= 3:
            tests.append({
                "name": "tamper_mid",
                "fault_at": max(1, num_slices // 2),
                "fault_type": "tamper",
                "expected": "invalid",
            })

        results = []

        # ══════════════════════════════════════════════════
        # PART A: 串行模式 (baseline)
        # ══════════════════════════════════════════════════
        print("=" * 60)
        print("PART A: SERIAL MODE (baseline)")
        print("=" * 60)

        for test in tests:
            print(f"\n{'─'*50}\n[Serial] {test['name']}\n{'─'*50}")
            r = run_distributed_pipeline(
                initial_input, artifacts, worker_urls,
                fault_at=test["fault_at"],
                fault_type=test["fault_type"],
            )
            bundle = r["proof_bundle"]
            client_result = verify_bundle(bundle, artifacts)
            status = client_result.status
            passed = (status == test["expected"])

            entry = {
                "mode": "serial",
                "name": test["name"],
                "expected": test["expected"],
                "actual": status,
                "passed": passed,
                "total_ms": r["metrics"]["total_ms"],
                "total_prove_ms": r["metrics"]["total_prove_ms"],
                "client_verification_ms": client_result.metrics.get("verification_ms", 0),
            }
            results.append(entry)
            mark = "PASS" if passed else "FAIL"
            print(f"  [{mark}] client={status} total={entry['total_ms']:.0f}ms "
                  f"prove={entry['total_prove_ms']:.0f}ms")

        # ══════════════════════════════════════════════════
        # PART B: 并行模式
        # ══════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("PART B: PARALLEL MODE (pipeline)")
        print("=" * 60)

        for test in tests:
            print(f"\n{'─'*50}\n[Parallel] {test['name']}\n{'─'*50}")
            r = run_distributed_pipeline_parallel(
                initial_input, artifacts, worker_urls,
                fault_at=test["fault_at"],
                fault_type=test["fault_type"],
            )
            bundle = r["proof_bundle"]
            client_result = verify_bundle(bundle, artifacts)
            status = client_result.status
            passed = (status == test["expected"])

            entry = {
                "mode": "parallel",
                "name": test["name"],
                "expected": test["expected"],
                "actual": status,
                "passed": passed,
                "total_ms": r["metrics"]["total_ms"],
                "prove_wall_ms": r["metrics"].get("prove_wall_ms", 0),
                "total_prove_ms": r["metrics"]["total_prove_ms"],
                "infer_serial_ms": r["metrics"].get("infer_serial_ms", 0),
                "client_verification_ms": client_result.metrics.get("verification_ms", 0),
            }
            results.append(entry)
            mark = "PASS" if passed else "FAIL"
            print(f"  [{mark}] client={status} total={entry['total_ms']:.0f}ms "
                  f"prove_wall={entry['prove_wall_ms']:.0f}ms "
                  f"infer_serial={entry['infer_serial_ms']:.0f}ms")

        # ══════════════════════════════════════════════════
        # SUMMARY
        # ══════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("PARALLEL E2E SUMMARY")
        print("=" * 60)

        all_ok = True
        for r in results:
            mark = "✓" if r["passed"] else "✗"
            mode_tag = r["mode"].upper()[:3]
            print(f"  {mark} [{mode_tag}] {r['name']:15s} → {r['actual']:10s} "
                  f"total={r['total_ms']:.0f}ms")
            if not r["passed"]:
                all_ok = False

        # 性能对比
        serial_normal = [r for r in results if r["mode"] == "serial" and r["name"] == "normal"]
        parallel_normal = [r for r in results if r["mode"] == "parallel" and r["name"] == "normal"]
        if serial_normal and parallel_normal:
            s = serial_normal[0]
            p = parallel_normal[0]
            speedup = s["total_ms"] / p["total_ms"] if p["total_ms"] > 0 else 0
            print(f"\n  Normal path comparison:")
            print(f"    Serial:   total={s['total_ms']:.0f}ms")
            print(f"    Parallel: total={p['total_ms']:.0f}ms "
                  f"(prove_wall={p['prove_wall_ms']:.0f}ms)")
            print(f"    Speedup:  {speedup:.2f}x")

        print(f"\n  Overall: {'ALL PASSED' if all_ok else 'SOME FAILED'}")

        # Save results
        metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
        os.makedirs(metrics_dir, exist_ok=True)
        out_path = os.path.join(metrics_dir, "parallel_e2e_results.json")
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"  Results: {out_path}")

        return results

    finally:
        print("\n[Workers] Stopping workers...")
        stop_workers(workers)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slices", type=int, default=2)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    run_parallel_e2e(num_slices=args.slices, rebuild=args.rebuild)
