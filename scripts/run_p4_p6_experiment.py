"""
P4：切片保真度采集 + P6：完整性检查机制对比

⚠ P6 不是严格的跨节点 proof linking 实证，而是三种完整性检查机制的对比：
  1. 外部哈希链 (SHA-256, all_public mode)
  2. 电路内 Poseidon 哈希绑定 (hashed mode)
  3. 完全隐私模式 (private mode)

P4 采集切片保真度 (PyTorch 切片一致性验证，非 ONNXRuntime/EZKL 量化路径)。

产出: metrics/p4_p6_results.json
"""

import json
import os
import subprocess
import sys
import time

import requests
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

PYTHON = sys.executable

from common.utils import sha256_of_list


def export_model(num_slices=4):
    from models.configurable_model import split_and_export
    return split_and_export(num_slices=num_slices, num_layers=8,
                            output_dir=os.path.join(PROJECT_ROOT, "models", f"exp_{num_slices}s"))


def start_workers(slices_info, visibility_mode="all_public", base_port=9001):
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


def run_pipeline_with_zk_check(workers, initial_input, fault_at=None, fault_type="none"):
    """执行流水线并收集 ZK 链相关指标。"""
    e2e_start = time.perf_counter()
    current_input = initial_input
    results = []
    hash_checks = []
    malicious_detected = []

    for i, w in enumerate(workers):
        sid = w["slice_id"]
        inject = (fault_at == sid)
        ft = fault_type if inject else "none"

        resp = requests.post(
            f"{w['url']}/infer",
            json={"input_data": current_input, "request_id": f"zk-{sid}"},
            params={"fault_type": ft},
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()

        # 1. 外部哈希一致性检查
        actual_hash = sha256_of_list(data["output_data"])
        external_integrity = (data["hash_out"] == actual_hash)

        # 2. 内部哈希检查（如果 proof 存在且使用了 hashed 模式，
        #    proof 的公开实例中包含 Poseidon 哈希，verify 通过即表示电路内哈希正确）
        circuit_integrity = data.get("verified", False)

        # 3. 哈希链检查
        chain_ok = True
        if i > 0:
            prev = results[-1]
            chain_ok = (prev["hash_out"] == data["hash_in"])

        if not external_integrity:
            malicious_detected.append(sid)

        hash_checks.append({
            "slice_id": sid,
            "external_integrity": external_integrity,
            "circuit_verified": circuit_integrity,
            "chain_ok": chain_ok if i > 0 else True,
            "proof_gen_ms": data["metrics"]["proof_gen_ms"],
            "verify_ms": data["metrics"]["verify_ms"],
        })

        results.append(data)
        current_input = data["output_data"]

    e2e_ms = (time.perf_counter() - e2e_start) * 1000

    return {
        "e2e_latency_ms": round(e2e_ms, 2),
        "total_proof_gen_ms": round(sum(c["proof_gen_ms"] for c in hash_checks), 2),
        "total_verify_ms": round(sum(c["verify_ms"] for c in hash_checks), 2),
        "all_external_ok": all(c["external_integrity"] for c in hash_checks),
        "all_circuit_ok": all(c["circuit_verified"] for c in hash_checks),
        "all_chain_ok": all(c["chain_ok"] for c in hash_checks),
        "malicious_detected": malicious_detected,
        "fault_at": fault_at,
        "fault_type": fault_type if fault_at else None,
        "checks": hash_checks,
    }


def run_p4_p6_experiments():
    all_results = []
    num_slices = 4

    # === P4: 保真度采集 ===
    print("=" * 60)
    print("P4: 保真度测试 (Fidelity)")
    print("=" * 60)

    fidelity_data = {}
    for ns in [2, 4, 8]:
        model_info = export_model(ns)
        fidelity_data[f"{ns}_slices"] = model_info.get("fidelity", {})
        print(f"  {ns} slices: {model_info.get('fidelity', {})}")

    # === P6: ZK 链对比 ===
    # 方案 1: 外部哈希链 (all_public)
    print(f"\n{'=' * 60}")
    print("P6-A: 外部哈希链 (all_public mode)")
    print(f"{'=' * 60}")

    model_info = export_model(num_slices)
    with open(model_info["slices"][0]["data"]) as f:
        initial_input = json.load(f)["input_data"][0]

    workers = start_workers(model_info["slices"], "all_public")
    try:
        wait_workers_ready(workers)

        # 正常
        r = run_pipeline_with_zk_check(workers, initial_input)
        r["scheme"] = "external_sha256"
        r["visibility_mode"] = "all_public"
        all_results.append(r)
        print(f"  正常: e2e={r['e2e_latency_ms']:.0f}ms ext={r['all_external_ok']} "
              f"circuit={r['all_circuit_ok']} chain={r['all_chain_ok']}")

        # 故障
        r = run_pipeline_with_zk_check(workers, initial_input, fault_at=4, fault_type="tamper")
        r["scheme"] = "external_sha256"
        r["visibility_mode"] = "all_public"
        all_results.append(r)
        print(f"  故障: e2e={r['e2e_latency_ms']:.0f}ms detected={r['malicious_detected']}")
    finally:
        stop_workers(workers)
        time.sleep(5)

    # 方案 2: 电路内哈希绑定 (hashed mode)
    print(f"\n{'=' * 60}")
    print("P6-B: 电路内 Poseidon 哈希绑定 (hashed mode)")
    print(f"{'=' * 60}")

    workers = start_workers(model_info["slices"], "hashed")
    try:
        wait_workers_ready(workers)

        r = run_pipeline_with_zk_check(workers, initial_input)
        r["scheme"] = "in_circuit_poseidon"
        r["visibility_mode"] = "hashed"
        all_results.append(r)
        print(f"  正常: e2e={r['e2e_latency_ms']:.0f}ms ext={r['all_external_ok']} "
              f"circuit={r['all_circuit_ok']} chain={r['all_chain_ok']}")

        r = run_pipeline_with_zk_check(workers, initial_input, fault_at=4, fault_type="tamper")
        r["scheme"] = "in_circuit_poseidon"
        r["visibility_mode"] = "hashed"
        all_results.append(r)
        print(f"  故障: e2e={r['e2e_latency_ms']:.0f}ms detected={r['malicious_detected']}")
    finally:
        stop_workers(workers)
        time.sleep(5)

    # 方案 3: private mode (最强隐私)
    print(f"\n{'=' * 60}")
    print("P6-C: 完全隐私模式 (private mode)")
    print(f"{'=' * 60}")

    workers = start_workers(model_info["slices"], "private")
    try:
        wait_workers_ready(workers)

        r = run_pipeline_with_zk_check(workers, initial_input)
        r["scheme"] = "private_input"
        r["visibility_mode"] = "private"
        all_results.append(r)
        print(f"  正常: e2e={r['e2e_latency_ms']:.0f}ms ext={r['all_external_ok']} "
              f"circuit={r['all_circuit_ok']} chain={r['all_chain_ok']}")

        r = run_pipeline_with_zk_check(workers, initial_input, fault_at=4, fault_type="tamper")
        r["scheme"] = "private_input"
        r["visibility_mode"] = "private"
        all_results.append(r)
        print(f"  故障: e2e={r['e2e_latency_ms']:.0f}ms detected={r['malicious_detected']}")
    finally:
        stop_workers(workers)
        time.sleep(5)

    # === 写结果 ===
    final = {
        "fidelity": fidelity_data,
        "zk_chain_comparison": all_results,
    }

    results_path = os.path.join(PROJECT_ROOT, "metrics", "p4_p6_results.json")
    with open(results_path, "w") as f:
        json.dump(final, f, indent=2)

    # 汇总
    print(f"\n{'=' * 60}")
    print("汇总")
    print(f"{'=' * 60}")
    print("\n保真度:")
    for k, v in fidelity_data.items():
        if v:
            print(f"  {k}: L1={v.get('l1_distance', 'N/A'):.2e}  "
                  f"L2={v.get('l2_distance', 'N/A'):.2e}  "
                  f"RelErr={v.get('relative_error', 'N/A'):.2e}")

    print("\nZK 链对比:")
    print(f"{'方案':>25} {'模式':>10} {'e2e(ms)':>8} {'proof(ms)':>10} {'verify(ms)':>11} {'检测':>5}")
    for r in all_results:
        det = "OK" if not r["malicious_detected"] else f"@{r['malicious_detected']}"
        print(f"{r['scheme']:>25} {'normal' if not r['fault_at'] else 'fault':>10} "
              f"{r['e2e_latency_ms']:>8.0f} {r['total_proof_gen_ms']:>10.0f} "
              f"{r['total_verify_ms']:>11.0f} {det:>5}")

    print(f"\n结果: {results_path}")
    return final


if __name__ == "__main__":
    import traceback
    os.environ["PYTHONIOENCODING"] = "utf-8"

    log_path = os.path.join(PROJECT_ROOT, "metrics", "p4_p6_log.txt")
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
        run_p4_p6_experiments()
    except Exception:
        traceback.print_exc()
    finally:
        log_file.close()
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
