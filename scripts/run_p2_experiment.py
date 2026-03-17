"""
P2 隐私模式对比实验

对比三种 EZKL 可见性模式的证明开销：
  - all_public: input=public, output=public, param=fixed（无隐私）
  - hashed:     input=hashed, output=public, param=hashed（哈希隐私）
  - private:    input=private, output=public, param=fixed（完全隐私）

实验矩阵: {4 切片} × {all_public, hashed, private} × 正常模式
产出: metrics/p2_visibility_modes.json
"""

import json
import os
import subprocess
import sys
import time

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

PYTHON = sys.executable

from common.utils import sha256_of_list


def export_model(num_slices: int = 4):
    from models.configurable_model import split_and_export
    return split_and_export(num_slices=num_slices, num_layers=8,
                            output_dir=os.path.join(PROJECT_ROOT, "models", f"exp_{num_slices}s"))


def start_workers_with_mode(slices_info, visibility_mode, base_port=9001):
    workers = []
    worker_script = os.path.join(PROJECT_ROOT, "distributed", "worker.py")
    for s in slices_info:
        port = base_port + s["id"] - 1
        # 每种模式用不同的 artifacts 目录，避免冲突
        cmd = [
            PYTHON, "-u", worker_script,
            "--slice-id", str(s["id"]),
            "--port", str(port),
            "--onnx", s["onnx"],
            "--cal", s["cal"],
            "--visibility-mode", visibility_mode,
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            cwd=PROJECT_ROOT,
        )
        workers.append({"proc": proc, "url": f"http://127.0.0.1:{port}", "slice_id": s["id"]})
    return workers


def wait_workers_ready(workers, timeout=240):
    deadline = time.time() + timeout
    for w in workers:
        while time.time() < deadline:
            try:
                r = requests.get(f"{w['url']}/health", timeout=5)
                if r.status_code == 200:
                    break
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(3)
        else:
            raise TimeoutError(f"Worker {w['slice_id']} not ready")


def stop_workers(workers):
    for w in workers:
        w["proc"].terminate()
        try:
            w["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            w["proc"].kill()


def run_pipeline(workers, initial_input):
    """执行一次正常模式流水线，返回指标。"""
    e2e_start = time.perf_counter()
    current_input = initial_input
    results = []

    for w in workers:
        resp = requests.post(
            f"{w['url']}/infer",
            json={"input_data": current_input, "request_id": f"p2-{w['slice_id']}"},
            params={"fault_type": "none"},
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        results.append(data)
        current_input = data["output_data"]

    e2e_ms = (time.perf_counter() - e2e_start) * 1000
    total_proof = sum(r["metrics"]["proof_gen_ms"] for r in results)
    total_verify = sum(r["metrics"]["verify_ms"] for r in results)
    max_rss = max(r["metrics"]["peak_rss_mb"] for r in results)

    total_proof_bytes = sum(r["metrics"].get("proof_size_bytes", 0) for r in results)
    total_witness_bytes = sum(r["metrics"].get("witness_size_bytes", 0) for r in results)

    return {
        "e2e_latency_ms": round(e2e_ms, 2),
        "total_proof_gen_ms": round(total_proof, 2),
        "total_verify_ms": round(total_verify, 2),
        "avg_proof_gen_ms": round(total_proof / len(results), 2),
        "avg_verify_ms": round(total_verify / len(results), 2),
        "peak_rss_mb": round(max_rss, 2),
        "total_proof_size_bytes": total_proof_bytes,
        "total_witness_size_bytes": total_witness_bytes,
        "slices": [
            {
                "slice_id": r["slice_id"],
                "proof_gen_ms": r["metrics"]["proof_gen_ms"],
                "verify_ms": r["metrics"]["verify_ms"],
                "peak_rss_mb": r["metrics"]["peak_rss_mb"],
                "proof_size_bytes": r["metrics"].get("proof_size_bytes", 0),
                "witness_size_bytes": r["metrics"].get("witness_size_bytes", 0),
            }
            for r in results
        ],
    }


def run_p2_experiments():
    modes = ["all_public", "hashed", "private"]
    num_slices = 4
    all_results = []

    model_info = export_model(num_slices)
    with open(model_info["slices"][0]["data"]) as f:
        initial_input = json.load(f)["input_data"][0]

    for mode in modes:
        print(f"\n{'=' * 60}")
        print(f"P2: visibility_mode = {mode}")
        print(f"{'=' * 60}")

        # 每种模式需要独立的 EZKL 初始化
        workers = start_workers_with_mode(model_info["slices"], mode)
        try:
            print(f"  等待 Workers 初始化 (mode={mode})...")
            wait_workers_ready(workers)
            print(f"  Workers 就绪")

            # 跑 3 次取平均
            runs = []
            for trial in range(3):
                print(f"  Trial {trial + 1}/3...")
                r = run_pipeline(workers, initial_input)
                runs.append(r)
                print(f"    e2e={r['e2e_latency_ms']:.0f}ms proof={r['total_proof_gen_ms']:.0f}ms "
                      f"verify={r['total_verify_ms']:.0f}ms")

            # 取平均
            avg_result = {
                "visibility_mode": mode,
                "num_slices": num_slices,
                "num_trials": 3,
                "avg_e2e_latency_ms": round(sum(r["e2e_latency_ms"] for r in runs) / 3, 2),
                "avg_total_proof_gen_ms": round(sum(r["total_proof_gen_ms"] for r in runs) / 3, 2),
                "avg_total_verify_ms": round(sum(r["total_verify_ms"] for r in runs) / 3, 2),
                "avg_peak_rss_mb": round(sum(r["peak_rss_mb"] for r in runs) / 3, 2),
                "trials": runs,
            }
            all_results.append(avg_result)
            print(f"  平均: e2e={avg_result['avg_e2e_latency_ms']:.0f}ms "
                  f"proof={avg_result['avg_total_proof_gen_ms']:.0f}ms "
                  f"verify={avg_result['avg_total_verify_ms']:.0f}ms")

        finally:
            stop_workers(workers)
            time.sleep(5)

    # 写入结果
    results_dir = os.path.join(PROJECT_ROOT, "metrics")
    os.makedirs(results_dir, exist_ok=True)
    results_path = os.path.join(results_dir, "p2_visibility_modes.json")
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2)

    # 汇总表
    print(f"\n{'=' * 60}")
    print("P2 隐私模式对比 — 汇总")
    print(f"{'=' * 60}")
    print(f"{'模式':>12} {'e2e(ms)':>10} {'proof(ms)':>10} {'verify(ms)':>11} {'RSS(MB)':>9}")
    print("-" * 55)
    for r in all_results:
        print(f"{r['visibility_mode']:>12} {r['avg_e2e_latency_ms']:>10.0f} "
              f"{r['avg_total_proof_gen_ms']:>10.0f} {r['avg_total_verify_ms']:>11.0f} "
              f"{r['avg_peak_rss_mb']:>9.0f}")

    print(f"\n结果已写入: {results_path}")
    return all_results


if __name__ == "__main__":
    import traceback
    os.environ["PYTHONIOENCODING"] = "utf-8"

    log_path = os.path.join(PROJECT_ROOT, "metrics", "p2_exp_log.txt")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    class Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, data):
            for s in self.streams:
                s.write(data)
                s.flush()
        def flush(self):
            for s in self.streams:
                s.flush()

    log_file = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_file)
    sys.stderr = Tee(sys.__stderr__, log_file)

    try:
        run_p2_experiments()
    except Exception:
        traceback.print_exc()
    finally:
        log_file.close()
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
