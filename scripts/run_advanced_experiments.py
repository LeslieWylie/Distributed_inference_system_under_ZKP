"""
P1+P3 综合实验脚本

⚠ 该脚本使用简化评估管线（覆盖 L1 + L3 + edge-cover 选点），
   未走 Master 完整逻辑（无独立 proof verify、无 L2 linking、无随机挑战）。
   用于选择性验证开销评估和 L1/L3 检测能力对比。

实验矩阵:
  P1 选择性验证: {4,8 切片} × {1.0, 0.5, 0.25 请求验证率} × {正常, tamper故障}
  P3 多攻击场景: {4 切片} × {tamper, skip, random, replay} × {1.0, 0.5 请求验证率}

用法:
    python run_advanced_experiments.py
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
from distributed.master import _select_verified_slices


# ---------------------------------------------------------------------------
# 基础设施（复用 run_experiments.py 的逻辑）
# ---------------------------------------------------------------------------

def export_model(num_slices: int) -> dict:
    from models.configurable_model import split_and_export
    output_dir = os.path.join(PROJECT_ROOT, "models", f"exp_{num_slices}s")
    return split_and_export(num_slices=num_slices, num_layers=8, output_dir=output_dir)


def start_workers(slices_info: list, base_port: int = 9001) -> list:
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
        workers.append({"proc": proc, "url": f"http://127.0.0.1:{port}", "slice_id": s["id"]})
    return workers


def wait_workers_ready(workers: list, timeout: int = 180):
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


def stop_workers(workers: list):
    for w in workers:
        w["proc"].terminate()
        try:
            w["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            w["proc"].kill()


# ---------------------------------------------------------------------------
# 单次流水线执行（嵌入 Master 逻辑）
# ---------------------------------------------------------------------------

def run_single_pipeline(
    workers: list,
    initial_input: list,
    fault_at: int | None = None,
    fault_type: str = "none",
    verify_ratio: float = 1.0,
) -> dict:
    """执行一次完整的流水线推理并返回指标。"""
    num_slices = len(workers)

    # 使用 master 的统一选择策略（edge_cover）
    verified_set = _select_verified_slices(num_slices, verify_ratio)

    e2e_start = time.perf_counter()
    current_input = initial_input
    results = []
    hash_chain_ok = True
    malicious_nodes = []

    for i, w in enumerate(workers):
        sid = w["slice_id"]
        use_proof = (sid in verified_set)
        inject = (fault_at == sid)
        ft = fault_type if inject else "none"

        endpoint = "/infer" if use_proof else "/infer_light"

        resp = requests.post(
            f"{w['url']}{endpoint}",
            json={"input_data": current_input,
                  "request_id": f"exp-{sid}-{int(time.time()*1000)}"},
            params={"fault_type": ft},
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()

        # 输出完整性校验
        actual_output_hash = sha256_of_list(data["output_data"])
        if data["hash_out"] != actual_output_hash:
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

    total_proof_ms = sum(r["metrics"]["proof_gen_ms"] for r in results)
    total_verify_ms = sum(r["metrics"]["verify_ms"] for r in results)
    max_rss = max(r["metrics"]["peak_rss_mb"] for r in results)

    if fault_at is not None:
        detection_accuracy = 1.0 if fault_at in malicious_nodes else 0.0
    else:
        detection_accuracy = 1.0 if len(malicious_nodes) == 0 else 0.0

    actual_proof_fraction = len(verified_set) / len(workers) if workers else 0

    return {
        "e2e_latency_ms": round(e2e_ms, 2),
        "total_proof_gen_ms": round(total_proof_ms, 2),
        "total_verify_ms": round(total_verify_ms, 2),
        "peak_rss_mb": round(max_rss, 2),
        "hash_chain_ok": hash_chain_ok,
        "detection_accuracy": detection_accuracy,
        "fault_at": fault_at,
        "fault_type": fault_type if fault_at else None,
        "verify_ratio": verify_ratio,
        "actual_proof_fraction": round(actual_proof_fraction, 4),
        "verified_slices": sorted(verified_set),
        "num_slices": len(workers),
        "malicious_detected": malicious_nodes,
        "evaluation_scope": "simplified_L1_L3_with_edge_cover",
    }


# ---------------------------------------------------------------------------
# 实验主流程
# ---------------------------------------------------------------------------

def run_all_experiments():
    all_results = []

    # ===== P1: 选择性验证实验 =====
    print("\n" + "=" * 70)
    print("P1: 选择性验证 — 验证粒度对开销与检测率的影响")
    print("=" * 70)

    for num_slices in [4, 8]:
        model_info = export_model(num_slices)
        with open(model_info["slices"][0]["data"]) as f:
            initial_input = json.load(f)["input_data"][0]

        workers = start_workers(model_info["slices"])
        try:
            wait_workers_ready(workers)
            print(f"\n  [{num_slices}s] Workers 就绪")

            for vr in [1.0, 0.5, 0.25]:
                # 正常模式
                tag = f"P1_{num_slices}s_vr{vr:.2f}_normal"
                print(f"\n  [{tag}] 运行中...")
                r = run_single_pipeline(workers, initial_input, verify_ratio=vr)
                r["experiment"] = tag
                all_results.append(r)
                print(f"    e2e={r['e2e_latency_ms']:.0f}ms proof={r['total_proof_gen_ms']:.0f}ms "
                      f"verify={r['total_verify_ms']:.0f}ms chain={'OK' if r['hash_chain_ok'] else 'FAIL'}")

                # 故障注入（在最后一个切片）
                tag = f"P1_{num_slices}s_vr{vr:.2f}_fault"
                print(f"  [{tag}] 运行中...")
                r = run_single_pipeline(workers, initial_input,
                                        fault_at=num_slices, fault_type="tamper", verify_ratio=vr)
                r["experiment"] = tag
                all_results.append(r)
                print(f"    e2e={r['e2e_latency_ms']:.0f}ms detected={r['detection_accuracy']:.0%}")

        finally:
            stop_workers(workers)
            time.sleep(3)

    # ===== P3: 多攻击场景实验 =====
    print("\n" + "=" * 70)
    print("P3: 多攻击场景 — 不同攻击类型的检测能力")
    print("=" * 70)

    num_slices = 4
    model_info = export_model(num_slices)
    with open(model_info["slices"][0]["data"]) as f:
        initial_input = json.load(f)["input_data"][0]

    workers = start_workers(model_info["slices"])
    try:
        wait_workers_ready(workers)
        print(f"\n  [4s] Workers 就绪")

        for attack in ["tamper", "skip", "random", "replay"]:
            for vr in [1.0, 0.5]:
                tag = f"P3_4s_{attack}_vr{vr:.2f}"
                print(f"\n  [{tag}] 运行中...")
                r = run_single_pipeline(workers, initial_input,
                                        fault_at=num_slices, fault_type=attack, verify_ratio=vr)
                r["experiment"] = tag
                all_results.append(r)
                print(f"    e2e={r['e2e_latency_ms']:.0f}ms detected={r['detection_accuracy']:.0%} "
                      f"chain={'OK' if r['hash_chain_ok'] else 'FAIL'}")
    finally:
        stop_workers(workers)
        time.sleep(3)

    # ===== 写入结果 =====
    results_dir = os.path.join(PROJECT_ROOT, "metrics")
    os.makedirs(results_dir, exist_ok=True)
    results_path = os.path.join(results_dir, "advanced_experiments.json")
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2)

    # 打印汇总
    print("\n" + "=" * 70)
    print("全部实验完成！")
    print("=" * 70)
    print(f"{'实验':>30} {'e2e(ms)':>8} {'proof(ms)':>10} {'verify(ms)':>11} "
          f"{'VR':>5} {'检测':>5} {'链':>5}")
    print("-" * 80)
    for r in all_results:
        chain = "OK" if r["hash_chain_ok"] else "FAIL"
        det = f"{r['detection_accuracy']:.0%}"
        print(f"{r['experiment']:>30} {r['e2e_latency_ms']:>8.0f} {r['total_proof_gen_ms']:>10.0f} "
              f"{r['total_verify_ms']:>11.0f} {r['verify_ratio']:>5.0%} {det:>5} {chain:>5}")

    print(f"\n  结果已写入: {results_path}")
    return all_results


if __name__ == "__main__":
    import traceback
    os.environ["PYTHONIOENCODING"] = "utf-8"

    log_path = os.path.join(PROJECT_ROOT, "metrics", "advanced_exp_log.txt")
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
        run_all_experiments()
    except Exception:
        traceback.print_exc()
    finally:
        log_file.close()
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
