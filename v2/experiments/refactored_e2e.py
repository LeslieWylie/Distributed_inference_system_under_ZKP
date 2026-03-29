"""
v2/experiments/refactored_e2e.py — 重构版端到端实验。

与旧 distributed_e2e.py 的区别:
  1. 使用 MNIST MLP (真实模型, ~110K 参数)
  2. Worker 是 Prover-Worker (推理 + 证明一体化)
  3. Master 不参与 proving (证明开销完全分摊到 Worker)
  4. 全链路信任: proof 中的 public instances 绑定真实 I/O

用法:
    python -m v2.experiments.refactored_e2e [--slices 2] [--rebuild]
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


def start_prover_workers(artifacts, base_port=9001):
    """启动多个 Prover-Worker 子进程。"""
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
    """等待所有 Worker 就绪。"""
    deadline = time.time() + timeout
    for w in workers:
        while time.time() < deadline:
            try:
                r = requests.get(f"{w['url']}/health", timeout=5)
                if r.status_code == 200:
                    info = r.json()
                    if info.get("role") == "prover_worker":
                        print(f"  Worker {w['slice_id']} ready at {w['url']}")
                        break
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(1)
        else:
            raise TimeoutError(f"Worker {w['slice_id']} at {w['url']} not ready")


def stop_workers(workers):
    for w in workers:
        w["proc"].terminate()
        try:
            w["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            w["proc"].kill()


def run_refactored_e2e(num_slices=2, rebuild=False):
    """运行重构版端到端实验。"""
    from v2.compile.build_circuits import build_registry, load_registry

    registry_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "registry", "slice_registry.json",
    )

    if rebuild or not os.path.exists(registry_path):
        print(f"[Build] Compiling {num_slices}-slice MNIST MLP circuits...")
        build_registry(num_slices=num_slices, model_type="mnist")
    else:
        print(f"[Build] Using existing registry: {registry_path}")

    artifacts = load_registry(registry_path)

    # 读取初始输入
    input_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "models", "slice_1_input.json",
    )
    with open(input_path) as f:
        initial_input = json.load(f)["input_data"][0]

    print(f"\n[Info] Model: MNIST MLP, {num_slices} slices")
    print(f"[Info] Input dim: {len(initial_input)}")

    # 启动 Prover-Workers
    print("\n[Workers] Starting Prover-Workers...")
    workers = start_prover_workers(artifacts)
    worker_urls = [{"slice_id": w["slice_id"], "url": w["url"]} for w in workers]

    try:
        wait_workers_ready(workers)
        print(f"[Workers] All {len(workers)} Prover-Workers ready.\n")

        # 导入新的分布式协调器 + 客户端验证器
        from v2.services.distributed_coordinator import run_distributed_pipeline
        from v2.verifier.bundle_verifier import verify_bundle

        # 测试用例
        tests = [
            {"name": "normal", "fault_at": None, "fault_type": "none",
             "expected": "certified"},
            {"name": "tamper_last", "fault_at": num_slices, "fault_type": "tamper",
             "expected": "invalid"},
            {"name": "skip_last", "fault_at": num_slices, "fault_type": "skip",
             "expected": "invalid"},
            {"name": "random_last", "fault_at": num_slices, "fault_type": "random",
             "expected": "invalid"},
            {"name": "replay_last", "fault_at": num_slices, "fault_type": "replay",
             "expected": "invalid"},
        ]

        # 如果有 >= 3 片, 加入中间节点攻击
        if num_slices >= 3:
            tests.append({
                "name": "tamper_mid",
                "fault_at": max(1, num_slices // 2),
                "fault_type": "tamper",
                "expected": "invalid",
            })

        results = []
        for test in tests:
            print(f"\n{'─' * 60}\nTest: {test['name']}\n{'─' * 60}")
            r = run_distributed_pipeline(
                initial_input, artifacts, worker_urls,
                fault_at=test["fault_at"],
                fault_type=test["fault_type"],
            )

            # 客户端独立验证 bundle (最终可信判断)
            bundle = r["proof_bundle"]
            client_result = verify_bundle(bundle, artifacts)
            status = client_result.status
            passed = (status == test["expected"])

            advisory = r.get("server_side_advisory", {})
            print(f"  [Client] verdict={status}  advisory={advisory.get('status', 'unknown')}")

            results.append({
                "name": test["name"],
                "expected": test["expected"],
                "actual": status,
                "passed": passed,
                "architecture": "prover_worker_client_verify",
                "model": "mnist_mlp",
                "num_slices": num_slices,
                "server_side_advisory": advisory,
                "client_verification": {
                    "status": client_result.status,
                    "all_single_proofs_verified": client_result.all_single_proofs_verified,
                    "all_links_verified": client_result.all_links_verified,
                    "failure_reasons": client_result.failure_reasons,
                    "metrics": client_result.metrics,
                },
                "metrics": r["metrics"],
            })
            mark = "PASS" if passed else "FAIL"
            print(f"  [{mark}] {test['name']}: client={status} advisory={advisory.get('status', 'unknown')}")

        # 汇总
        print(f"\n{'=' * 60}")
        print("CLIENT-VERIFIED E2E SUMMARY")
        print(f"  Model: MNIST MLP (~110K params)")
        print(f"  Architecture: Prover-Worker + Client Verification")
        print(f"  Slices: {num_slices}")
        print(f"{'=' * 60}")

        all_ok = True
        for r in results:
            mark = "✓" if r["passed"] else "✗"
            m = r["metrics"]
            exec_ms = m.get("execution_ms", 0)
            verify_ms = r.get("client_verification", {}).get("metrics", {}).get("verification_ms", 0)
            total_prove = m.get("total_prove_ms", 0)
            adv = r.get("server_side_advisory", {}).get("status", "?")
            print(f"  {mark} {r['name']:15s} → client={r['actual']:10s} advisory={adv:10s} "
                  f"prove={total_prove:.0f}ms client_verify={verify_ms:.0f}ms")
            if not r["passed"]:
                all_ok = False

        print(f"\n  Overall: {'ALL PASSED' if all_ok else 'SOME FAILED'}")

        # 保存结果
        metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
        os.makedirs(metrics_dir, exist_ok=True)
        out_path = os.path.join(metrics_dir, "refactored_e2e_results.json")
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

    run_refactored_e2e(num_slices=args.slices, rebuild=args.rebuild)
