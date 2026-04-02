"""
v2/experiments/cnn_e2e.py — CNN 模型全链路端到端实验。

证明框架不限于 MLP，也适用于卷积网络。

用法:
    python -m v2.experiments.cnn_e2e
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


def run_cnn_e2e():
    from v2.compile.build_circuits import build_registry, load_registry
    from v2.services.distributed_coordinator import run_distributed_pipeline
    from v2.verifier.bundle_verifier import verify_bundle

    registry_dir = os.path.join(PROJECT_ROOT, "v2", "artifacts", "cnn_2s")

    print("=" * 60)
    print("CNN E2E: MNIST CNN (Conv→ReLU→Flatten→FC), 2 slices")
    print("=" * 60)

    # 编译（仅在需要时）
    registry_path = os.path.join(registry_dir, "registry", "slice_registry.json")
    if os.path.exists(registry_path):
        print("\n[Build] Using existing CNN circuits...")
        artifacts = load_registry(registry_path)
    else:
        print("\n[Build] Compiling CNN circuits with scale alignment...")
        artifacts = build_registry(
            num_slices=2,
            model_type="mnist_cnn",
            registry_dir=registry_dir,
        )

    # 读输入
    input_path = os.path.join(registry_dir, "models", "slice_1_input.json")
    with open(input_path) as f:
        initial_input = json.load(f)["input_data"][0]

    print(f"\n[Info] CNN model, 2 slices, input_dim={len(initial_input)}")

    # 启动 Workers
    print("\n[Workers] Starting...")
    log_dir = os.path.join(PROJECT_ROOT, "v2", "logs", "cnn_workers")
    os.makedirs(log_dir, exist_ok=True)
    workers = []
    for a in artifacts:
        port = 9301 + a.slice_id - 1
        log_path = os.path.join(log_dir, f"slice_{a.slice_id}.log")
        log_handle = open(log_path, "w", encoding="utf-8")
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
            stdout=log_handle, stderr=log_handle,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        workers.append({
            "slice_id": a.slice_id, "url": f"http://127.0.0.1:{port}",
            "proc": proc, "log_handle": log_handle, "log_path": log_path,
        })

    worker_urls = [{"slice_id": w["slice_id"], "url": w["url"]} for w in workers]

    # 等 Workers 就绪
    for w in workers:
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                r = requests.get(f"{w['url']}/health", timeout=5)
                if r.status_code == 200 and r.json().get("role") == "prover_worker":
                    print(f"  Worker {w['slice_id']} ready")
                    break
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(1)

    try:
        tests = [
            {"name": "normal", "fault_at": None, "fault_type": "none", "expected": "certified"},
            {"name": "tamper_last", "fault_at": 2, "fault_type": "tamper", "expected": "invalid"},
            {"name": "skip_last", "fault_at": 2, "fault_type": "skip", "expected": "invalid"},
        ]

        results = []
        for test in tests:
            print(f"\n{'─'*50}\n[CNN] {test['name']}\n{'─'*50}")

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
                "model": "mnist_cnn",
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

        # Summary
        print(f"\n{'='*60}")
        print("CNN E2E SUMMARY")
        print(f"{'='*60}")
        all_ok = True
        for r in results:
            mark = "✓" if r["passed"] else "✗"
            print(f"  {mark} {r['name']:15s} → {r['actual']}")
            if not r["passed"]:
                all_ok = False
        print(f"\n  Overall: {'ALL PASSED' if all_ok else 'SOME FAILED'}")

        # Save
        out_path = os.path.join(PROJECT_ROOT, "v2", "metrics", "cnn_e2e_results.json")
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"  Results: {out_path}")

        return results

    finally:
        print("\n[Workers] Stopping...")
        for w in workers:
            w["proc"].terminate()
            try:
                w["proc"].wait(timeout=10)
            except subprocess.TimeoutExpired:
                w["proc"].kill()
            if "log_handle" in w:
                w["log_handle"].close()


if __name__ == "__main__":
    run_cnn_e2e()
