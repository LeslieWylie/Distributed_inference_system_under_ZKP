"""
v2/experiments/distributed_e2e.py — 真正分布式端到端实验。

启动多个 FastAPI Execution Worker 子进程，
Master 通过 HTTP 调用 Worker 完成推理，
后台 proving + 独立验证 + 认证。

这是对 GPT 批评 "不是真分布式" 的直接回应。
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


def start_workers(artifacts, base_port=9001):
    """启动多个 FastAPI Execution Worker 子进程。"""
    workers = []
    for a in artifacts:
        port = base_port + a.slice_id - 1
        cmd = [
            PYTHON, "-u", "-m", "v2.services.execution_worker",
            "--slice-id", str(a.slice_id),
            "--port", str(port),
            "--onnx", a.model_path,
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


def wait_workers_ready(workers, timeout=120):
    """等待所有 Worker 就绪。"""
    deadline = time.time() + timeout
    for w in workers:
        while time.time() < deadline:
            try:
                r = requests.get(f"{w['url']}/health", timeout=3)
                if r.status_code == 200:
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


def run_distributed_experiments(num_slices=4):
    """运行真正分布式的端到端实验。"""
    from v2.compile.build_circuits import load_registry
    from v2.services.master_coordinator import run_distributed_pipeline

    registry_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "registry", "slice_registry.json",
    )
    artifacts = load_registry(registry_path)

    input_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "models", "slice_1_input.json",
    )
    with open(input_path) as f:
        initial_input = json.load(f)["input_data"][0]

    # 启动分布式 Workers
    print("Starting distributed Execution Workers...")
    workers = start_workers(artifacts)
    worker_urls = [{"slice_id": w["slice_id"], "url": w["url"]} for w in workers]

    try:
        wait_workers_ready(workers)
        print(f"All {len(workers)} workers ready.\n")

        tests = [
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
        for test in tests:
            print(f"\n{'─' * 50}\nTest: {test['name']}\n{'─' * 50}")
            r = run_distributed_pipeline(
                initial_input, artifacts, worker_urls,
                fault_at=test["fault_at"],
                fault_type=test["fault_type"],
                max_prove_workers=2,
            )
            status = r["certificate"]["status"]
            passed = (status == test["expected"])
            results.append({
                "name": test["name"],
                "expected": test["expected"],
                "actual": status,
                "passed": passed,
                "distributed": True,
                "metrics": r["metrics"],
            })
            print(f"  [{'PASS' if passed else 'FAIL'}] {test['name']}: {status}")

        # 汇总
        print(f"\n{'=' * 50}")
        print("DISTRIBUTED E2E SUMMARY")
        print(f"{'=' * 50}")
        all_ok = True
        for r in results:
            mark = "✓" if r["passed"] else "✗"
            m = r["metrics"]
            print(f"  {mark} {r['name']:15s} → {r['actual']:10s} "
                  f"exec={m['execution_ms']:.0f}ms cert={m['certification_ms']:.0f}ms")
            if not r["passed"]:
                all_ok = False
        print(f"\n  Overall: {'ALL PASSED' if all_ok else 'SOME FAILED'}")

        # 保存
        metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
        os.makedirs(metrics_dir, exist_ok=True)
        out_path = os.path.join(metrics_dir, "distributed_e2e_results.json")
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"  Results: {out_path}")

    finally:
        print("\nStopping workers...")
        stop_workers(workers)

    return results


if __name__ == "__main__":
    run_distributed_experiments()
