"""
Master 调度节点：编排分布式推理流水线 + 哈希链校验 + 指标采集。

Master 按顺序把输入喂给 Worker 1 → Worker 2 → ... → Worker N，
收集每个 Worker 的 output + proof + hash，执行一致性校验。

启动方式：
    python master.py [--fault-at 2]

    --fault-at N : 在第 N 个 Worker 上注入故障
"""

import argparse
import json
import os
import sys
import time

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from common.utils import sha256_of_list


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

DEFAULT_WORKERS = [
    {"slice_id": 1, "url": "http://127.0.0.1:8001"},
    {"slice_id": 2, "url": "http://127.0.0.1:8002"},
]


# ---------------------------------------------------------------------------
# Master 核心逻辑
# ---------------------------------------------------------------------------

def run_pipeline(
    initial_input: list[float],
    workers: list[dict],
    fault_at: int | None = None,
) -> dict:
    """
    按流水线顺序调用各 Worker，收集结果并执行哈希链校验。

    参数:
        initial_input: 第一个切片的输入数据
        workers: Worker 配置列表 [{"slice_id": 1, "url": "http://..."}]
        fault_at: 在哪个 slice_id 上注入故障（None 表示不注入）

    返回:
        包含所有结果和指标的字典。
    """
    print("=" * 60)
    print("Master: 分布式推理流水线启动")
    print(f"  Workers: {len(workers)}")
    print(f"  Fault injection at slice: {fault_at or 'None'}")
    print("=" * 60)

    e2e_start = time.perf_counter()

    current_input = initial_input
    results = []
    hash_chain_ok = True
    malicious_nodes = []

    for i, worker in enumerate(workers):
        sid = worker["slice_id"]
        url = worker["url"]
        inject = (fault_at == sid)

        print(f"\n[Master] -> Worker {sid} ({url})"
              + (" [FAULT INJECT]" if inject else ""))

        # 发起推理请求
        t0 = time.perf_counter()
        resp = requests.post(
            f"{url}/infer",
            json={"input_data": current_input, "request_id": f"req-{sid}"},
            params={"fault": str(inject).lower()},
            timeout=120,
        )
        rtt_ms = (time.perf_counter() - t0) * 1000
        resp.raise_for_status()
        data = resp.json()

        print(f"    proof_gen: {data['metrics']['proof_gen_ms']:.0f} ms | "
              f"verify: {data['metrics']['verify_ms']:.0f} ms | "
              f"rtt: {rtt_ms:.0f} ms")
        print(f"    hash_in:  {data['hash_in'][:16]}...")
        print(f"    hash_out: {data['hash_out'][:16]}...")
        print(f"    verified: {data['verified']}")

        # ── 输出完整性校验：hash_out 必须等于 output_data 的实际哈希 ──
        actual_output_hash = sha256_of_list(data["output_data"])
        output_integrity = (data["hash_out"] == actual_output_hash)
        if not output_integrity:
            hash_chain_ok = False
            malicious_nodes.append({
                "type": "output_tamper",
                "slice_id": sid,
                "expected": data["hash_out"][:16],
                "actual": actual_output_hash[:16],
            })
            print(f"    ⚠ OUTPUT TAMPER at slice {sid}: hash_out != hash(output_data)")
        else:
            print(f"    ✓ Output integrity OK (slice {sid})")

        # ── 哈希链校验：前一个 Worker 的输出 hash == 当前 Worker 的输入 hash ──
        if i > 0:
            prev = results[-1]
            expected_hash = prev["hash_out"]
            actual_hash = data["hash_in"]
            chain_match = (expected_hash == actual_hash)

            if not chain_match:
                hash_chain_ok = False
                malicious_nodes.append({
                    "between": [prev["slice_id"], sid],
                    "expected": expected_hash[:16],
                    "actual": actual_hash[:16],
                })
                print(f"    ⚠ HASH CHAIN BREAK between slice {prev['slice_id']} → {sid}")
            else:
                print(f"    ✓ Hash chain OK (slice {prev['slice_id']} → {sid})")

        # 补充 Master 侧指标
        data["metrics"]["rtt_ms"] = round(rtt_ms, 2)
        results.append(data)

        # 下一个 Worker 的输入 = 本 Worker 的输出
        current_input = data["output_data"]

    e2e_ms = (time.perf_counter() - e2e_start) * 1000

    # 也校验首个 Worker 的输入哈希
    expected_first_hash = sha256_of_list(initial_input)
    first_hash_ok = (results[0]["hash_in"] == expected_first_hash)
    if not first_hash_ok:
        hash_chain_ok = False
        malicious_nodes.append({
            "between": ["input", results[0]["slice_id"]],
            "expected": expected_first_hash[:16],
            "actual": results[0]["hash_in"][:16],
        })

    # ── 汇总 ──
    total_nodes = len(workers)
    detected_count = len(malicious_nodes)
    # 如果注入了故障，检测准确率 = 是否检测到了
    if fault_at is not None:
        actually_faulty = 1
        detection_accuracy = min(detected_count, 1) / actually_faulty
    else:
        detection_accuracy = 1.0 if detected_count == 0 else 0.0

    summary = {
        "e2e_latency_ms": round(e2e_ms, 2),
        "hash_chain_ok": hash_chain_ok,
        "malicious_nodes": malicious_nodes,
        "detection_accuracy": detection_accuracy,
        "fault_injected_at": fault_at,
        "slices": [
            {
                "slice_id": r["slice_id"],
                "proof_gen_ms": r["metrics"]["proof_gen_ms"],
                "verify_ms": r["metrics"]["verify_ms"],
                "rtt_ms": r["metrics"]["rtt_ms"],
                "peak_rss_mb": r["metrics"]["peak_rss_mb"],
                "fault_injected": r["fault_injected"],
            }
            for r in results
        ],
        "final_output": current_input,
    }

    # 输出汇总
    print("\n" + "=" * 60)
    print("Master: 汇总")
    print("=" * 60)
    print(f"  端到端延迟:      {e2e_ms:.0f} ms")
    for s in summary["slices"]:
        print(f"  Slice {s['slice_id']}: prove={s['proof_gen_ms']:.0f}ms "
              f"verify={s['verify_ms']:.0f}ms rtt={s['rtt_ms']:.0f}ms"
              + (" [FAULT]" if s["fault_injected"] else ""))
    print(f"  哈希链:          {'PASS ✓' if hash_chain_ok else 'FAIL ✗'}")
    print(f"  恶意检测准确率:  {detection_accuracy:.0%}")
    if malicious_nodes:
        for m in malicious_nodes:
            if "between" in m:
                print(f"    ⚠ 断链: {m['between']}")
            elif "slice_id" in m:
                print(f"    ⚠ 输出篡改: slice {m['slice_id']}")
    print("=" * 60)

    # 写入 metrics
    metrics_dir = os.path.join(PROJECT_ROOT, "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    metrics_path = os.path.join(metrics_dir, "stage2_latest.json")
    with open(metrics_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  Metrics 已写入: {metrics_path}")

    return summary


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ZKP Master Orchestrator")
    parser.add_argument("--fault-at", type=int, default=None,
                        help="Inject fault at this slice_id")
    parser.add_argument("--workers", type=str, default=None,
                        help="JSON file with worker config (optional)")
    parser.add_argument("--input", type=str, default=None,
                        help="JSON file with initial input (optional)")
    args = parser.parse_args()

    # Worker 配置
    if args.workers:
        with open(args.workers) as f:
            workers = json.load(f)
    else:
        workers = DEFAULT_WORKERS

    # 等待所有 Worker 就绪
    print("[Master] 等待 Workers 就绪...")
    for w in workers:
        for attempt in range(30):
            try:
                r = requests.get(f"{w['url']}/health", timeout=5)
                if r.status_code == 200:
                    print(f"  Worker {w['slice_id']} ({w['url']}) ✓")
                    break
            except requests.ConnectionError:
                pass
            time.sleep(2)
        else:
            print(f"  ✗ Worker {w['slice_id']} ({w['url']}) 未就绪，退出")
            sys.exit(1)

    # 初始输入
    if args.input:
        with open(args.input) as f:
            initial_input = json.load(f)["input_data"][0]
    else:
        # 使用阶段 1 的输入
        default_input_path = os.path.join(PROJECT_ROOT, "models", "slice_1_input.json")
        with open(default_input_path) as f:
            initial_input = json.load(f)["input_data"][0]

    run_pipeline(initial_input, workers, fault_at=args.fault_at)


if __name__ == "__main__":
    main()
