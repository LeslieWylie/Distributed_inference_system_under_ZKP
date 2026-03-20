"""
阶段 3 实验运行器：自动化批量实验。

⚠ 该脚本使用简化评估管线（覆盖 L1 输出完整性 + L3 哈希链），
   未走 Master 完整逻辑（无独立 proof verify、无 L2 linking、无随机挑战）。
   用于多切片开销采集和基础 L1/L3 检测能力验证。

实验维度:
  - 切片数: 2, 4, 8
  - 故障注入比例: 0%, 50% (故障注入在最后一个切片)

采集指标:
  1. 证明生成时间 (proof_gen_ms)
  2. 验证时间 (verify_ms)
  3. 端到端推理延迟 (e2e_latency_ms)
  4. 单节点峰值内存 (peak_rss_mb)
  5. 系统吞吐量 (throughput_req_per_sec)  — 通过多次请求计算
  6. 恶意节点检测准确率 (detection_accuracy)

用法:
    python run_experiments.py
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


def export_model(num_slices: int) -> dict:
    """导出 N 切片模型并返回切片信息。"""
    from models.configurable_model import split_and_export

    output_dir = os.path.join(PROJECT_ROOT, "models", f"exp_{num_slices}s")
    return split_and_export(
        num_slices=num_slices,
        num_layers=8,
        output_dir=output_dir,
    )


def start_workers(slices_info: list, base_port: int = 9001) -> list:
    """启动一组 Worker 子进程，返回 (proc, url, slice_id) 列表。"""
    workers = []
    worker_script = os.path.join(PROJECT_ROOT, "distributed", "worker.py")

    for s in slices_info:
        port = base_port + s["id"] - 1
        cmd = [
            PYTHON, "-u", worker_script,
            "--slice-id", str(s["id"]),
            "--port", str(port),
            "--onnx", s["onnx"],
            "--cal", s["cal"],
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            cwd=PROJECT_ROOT,
        )
        workers.append({
            "proc": proc,
            "url": f"http://127.0.0.1:{port}",
            "slice_id": s["id"],
        })

    return workers


def wait_workers_ready(workers: list, timeout: int = 180):
    """等待所有 Worker 就绪。"""
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
            raise TimeoutError(f"Worker {w['slice_id']} at {w['url']} not ready")


def stop_workers(workers: list):
    """停止所有 Worker 子进程。"""
    for w in workers:
        w["proc"].terminate()
        try:
            w["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            w["proc"].kill()


def run_single_pipeline(
    workers: list,
    initial_input: list,
    fault_at: int | None = None,
) -> dict:
    """
    执行一次完整的流水线推理（与 master.py 逻辑相同但直接嵌入）。
    返回包含所有指标的字典。
    """
    e2e_start = time.perf_counter()
    current_input = initial_input
    results = []
    hash_chain_ok = True
    malicious_nodes = []

    for i, w in enumerate(workers):
        sid = w["slice_id"]
        inject = (fault_at == sid)

        resp = requests.post(
            f"{w['url']}/infer",
            json={"input_data": current_input,
                  "request_id": f"exp-{sid}-{int(time.time()*1000)}"},
            params={"fault_type": "tamper" if inject else "none"},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        # 输出完整性校验
        actual_output_hash = sha256_of_list(data["output_data"])
        output_integrity = (data["hash_out"] == actual_output_hash)
        if not output_integrity:
            hash_chain_ok = False
            malicious_nodes.append(sid)

        # 哈希链校验
        if i > 0:
            prev = results[-1]
            if prev["hash_out"] != data["hash_in"]:
                hash_chain_ok = False
                if sid not in malicious_nodes:
                    malicious_nodes.append(sid)

        results.append(data)
        current_input = data["output_data"]

    e2e_ms = (time.perf_counter() - e2e_start) * 1000

    # 指标汇总
    total_proof_ms = sum(r["metrics"]["proof_gen_ms"] for r in results)
    total_verify_ms = sum(r["metrics"]["verify_ms"] for r in results)
    avg_proof_ms = total_proof_ms / len(results)
    avg_verify_ms = total_verify_ms / len(results)
    max_rss = max(r["metrics"]["peak_rss_mb"] for r in results)

    # proof-bound output 预防：proof 节点上的篡改被 proof 绑定的输出预防
    fault_prevented = False
    if fault_at is not None and fault_at not in malicious_nodes:
        for r in results:
            if r.get("slice_id") == fault_at and r.get("fault_injected"):
                fault_prevented = True

    if fault_at is not None:
        detection_accuracy = 1.0 if (fault_at in malicious_nodes or fault_prevented) else 0.0
    else:
        detection_accuracy = 1.0 if len(malicious_nodes) == 0 else 0.0

    fault_detected = bool(malicious_nodes) if fault_at is not None else None

    return {
        "e2e_latency_ms": round(e2e_ms, 2),
        "total_proof_gen_ms": round(total_proof_ms, 2),
        "total_verify_ms": round(total_verify_ms, 2),
        "avg_proof_gen_ms": round(avg_proof_ms, 2),
        "avg_verify_ms": round(avg_verify_ms, 2),
        "peak_rss_mb": round(max_rss, 2),
        "hash_chain_ok": hash_chain_ok,
        "fault_detected": fault_detected,
        "fault_prevented": fault_prevented,
        "fault_at": fault_at,
        "malicious_detected": malicious_nodes,
        "evaluation_scope": "simplified_L1_L3_only",
        "slices": [
            {
                "slice_id": r["slice_id"],
                "proof_gen_ms": r["metrics"]["proof_gen_ms"],
                "verify_ms": r["metrics"]["verify_ms"],
                "peak_rss_mb": r["metrics"]["peak_rss_mb"],
            }
            for r in results
        ],
    }


def run_throughput_test(workers: list, initial_input: list, num_requests: int = 5) -> float:
    """连续发 N 次请求，计算吞吐量 (req/s)。"""
    t0 = time.perf_counter()
    for _ in range(num_requests):
        current = initial_input
        for w in workers:
            resp = requests.post(
                f"{w['url']}/infer",
                json={"input_data": current,
                      "request_id": f"tp-{int(time.time()*1000)}"},
                params={"fault_type": "none"},
                timeout=120,
            )
            resp.raise_for_status()
            current = resp.json()["output_data"]
    elapsed = time.perf_counter() - t0
    return round(num_requests / elapsed, 4)


def run_experiment_suite():
    """运行全部实验组合。"""
    slice_configs = [2, 4, 8]
    all_results = []

    for num_slices in slice_configs:
        print(f"\n{'=' * 60}")
        print(f"实验: {num_slices} 切片")
        print(f"{'=' * 60}")

        # 1. 导出模型
        model_info = export_model(num_slices)
        initial_input_path = model_info["slices"][0]["data"]
        with open(initial_input_path) as f:
            initial_input = json.load(f)["input_data"][0]

        # 2. 启动 Workers
        print(f"\n[Exp] 启动 {num_slices} 个 Workers...")
        workers = start_workers(model_info["slices"], base_port=9001)
        try:
            wait_workers_ready(workers)
            print(f"[Exp] 所有 Workers 就绪")

            # 3. 正常模式
            print(f"\n[Exp] 正常模式...")
            normal_result = run_single_pipeline(workers, initial_input, fault_at=None)
            normal_result["experiment"] = f"{num_slices}s_normal"
            normal_result["num_slices"] = num_slices
            print(f"  e2e={normal_result['e2e_latency_ms']:.0f}ms "
                  f"proof={normal_result['total_proof_gen_ms']:.0f}ms "
                  f"verify={normal_result['total_verify_ms']:.0f}ms "
                  f"chain={'OK' if normal_result['hash_chain_ok'] else 'FAIL'}")

            # 4. 故障注入（在最后一个切片）
            print(f"\n[Exp] 故障注入 (slice {num_slices})...")
            fault_result = run_single_pipeline(
                workers, initial_input, fault_at=num_slices
            )
            fault_result["experiment"] = f"{num_slices}s_fault_last"
            fault_result["num_slices"] = num_slices
            print(f"  e2e={fault_result['e2e_latency_ms']:.0f}ms "
                  f"detected={fault_result['fault_detected']} "
                  f"chain={'OK' if fault_result['hash_chain_ok'] else 'FAIL'}")

            # 5. 吞吐量测试 (正常模式 × 3 次)
            print(f"\n[Exp] 吞吐量测试 (3 requests)...")
            throughput = run_throughput_test(workers, initial_input, num_requests=3)
            normal_result["throughput_req_per_sec"] = throughput
            fault_result["throughput_req_per_sec"] = throughput
            print(f"  throughput={throughput:.4f} req/s")

            all_results.append(normal_result)
            all_results.append(fault_result)

        finally:
            stop_workers(workers)
            print(f"[Exp] Workers 已停止")
            time.sleep(2)  # 等待端口释放

    # 写入结果
    results_dir = os.path.join(PROJECT_ROOT, "metrics")
    os.makedirs(results_dir, exist_ok=True)
    results_path = os.path.join(results_dir, "stage3_experiments.json")
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n{'=' * 60}")
    print(f"所有实验完成！结果已写入: {results_path}")
    print(f"{'=' * 60}")

    # 打印汇总表
    print(f"\n{'切片数':>6} {'模式':>12} {'e2e(ms)':>10} {'proof(ms)':>10} "
          f"{'verify(ms)':>11} {'RSS(MB)':>9} {'吞吐(r/s)':>10} {'故障检测':>10}")
    print("-" * 90)
    for r in all_results:
        mode = "正常" if r["fault_at"] is None else f"故障@{r['fault_at']}"
        print(f"{r['num_slices']:>6} {mode:>12} {r['e2e_latency_ms']:>10.0f} "
              f"{r['total_proof_gen_ms']:>10.0f} {r['total_verify_ms']:>11.0f} "
              f"{r['peak_rss_mb']:>9.0f} {r.get('throughput_req_per_sec', 0):>10.4f} "
              f"{r['fault_detected']!s:>10}")

    return all_results


if __name__ == "__main__":
    import traceback
    os.environ["PYTHONIOENCODING"] = "utf-8"

    log_path = os.path.join(PROJECT_ROOT, "metrics", "exp_log.txt")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # 同时写文件和 stdout
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
        run_experiment_suite()
    except Exception:
        traceback.print_exc()
    finally:
        log_file.close()
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
