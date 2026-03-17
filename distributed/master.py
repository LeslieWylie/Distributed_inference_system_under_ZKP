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
import math
import os
import random
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

def _select_verified_slices(num_slices: int, verify_ratio: float) -> set[int]:
    """
    选择哪些切片做完整 ZKP 验证。
    策略：首尾切片必须验证，中间按比例随机选。
    """
    all_ids = list(range(1, num_slices + 1))
    if verify_ratio >= 1.0:
        return set(all_ids)

    # 首尾必选
    must = {1, num_slices}
    middle = [i for i in all_ids if i not in must]
    k = max(0, round(len(all_ids) * verify_ratio) - len(must))
    k = min(k, len(middle))
    selected = must | set(random.sample(middle, k))
    return selected


def run_pipeline(
    initial_input: list[float],
    workers: list[dict],
    fault_at: int | None = None,
    fault_type: str = "tamper",
    verify_ratio: float = 1.0,
) -> dict:
    """
    按流水线顺序调用各 Worker，收集结果并执行哈希链校验。

    参数:
        initial_input: 第一个切片的输入数据
        workers: Worker 配置列表 [{"slice_id": 1, "url": "http://..."}]
        fault_at: 在哪个 slice_id 上注入故障（None 表示不注入）
        fault_type: 故障类型 tamper/skip/random/replay
        verify_ratio: 做完整 ZKP 验证的切片比例 (0.0-1.0)

    返回:
        包含所有结果和指标的字典。
    """
    num_slices = len(workers)
    verified_set = _select_verified_slices(num_slices, verify_ratio)

    print("=" * 60)
    print("Master: 分布式推理流水线启动")
    print(f"  Workers: {num_slices}")
    print(f"  Verify ratio: {verify_ratio:.0%} -> proof at slices {sorted(verified_set)}")
    print(f"  Fault: {f'type={fault_type} at slice {fault_at}' if fault_at else 'None'}")
    print("=" * 60)

    e2e_start = time.perf_counter()

    current_input = initial_input
    results = []
    hash_chain_ok = True
    malicious_nodes = []

    for i, worker in enumerate(workers):
        sid = worker["slice_id"]
        url = worker["url"]
        use_proof = (sid in verified_set)
        inject_fault = (fault_at == sid)
        ft = fault_type if inject_fault else "none"

        endpoint = "/infer" if use_proof else "/infer_light"
        mode_tag = "PROOF" if use_proof else "LIGHT"

        print(f"\n[Master] -> Worker {sid} ({url}) [{mode_tag}]"
              + (f" [FAULT: {fault_type}]" if inject_fault else ""))

        # 发起推理请求
        t0 = time.perf_counter()
        resp = requests.post(
            f"{url}{endpoint}",
            json={"input_data": current_input, "request_id": f"req-{sid}"},
            params={"fault_type": ft},
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

        # ══════════════════════════════════════════════════════════
        # 三层校验体系
        # ══════════════════════════════════════════════════════════

        # 层 1：输出完整性校验 (外部 SHA-256)
        #   → Worker 返回的 output_data 的哈希必须等于 hash_out
        #   → 检测：Worker 篡改 output_data 但保留正确 hash_out
        actual_output_hash = sha256_of_list(data["output_data"])
        output_integrity = (data["hash_out"] == actual_output_hash)
        if not output_integrity:
            hash_chain_ok = False
            malicious_nodes.append({
                "type": "output_tamper",
                "layer": "L1_external_hash",
                "slice_id": sid,
                "expected": data["hash_out"][:16],
                "actual": actual_output_hash[:16],
            })
            print(f"    ⚠ L1 OUTPUT TAMPER at slice {sid}")
        else:
            print(f"    ✓ L1 Output integrity OK (slice {sid})")

        # 层 2：ZKP Proof Linking (电路内 Poseidon 哈希)
        #   → 如果当前切片和上一个切片都有 proof_instances，
        #     则比对 prev.processed_outputs == curr.processed_inputs
        #   → 这是 ZKP 公开实例，verify 通过 = 数学保证正确
        #   → 不可被 Worker 伪造（哈希在算术电路内计算）
        if i > 0 and use_proof:
            prev = results[-1]
            prev_instances = prev.get("proof_instances")
            curr_instances = data.get("proof_instances")

            if prev_instances and curr_instances:
                prev_out = prev_instances.get("processed_outputs")
                curr_in = curr_instances.get("processed_inputs")
                if prev_out is not None and curr_in is not None:
                    proof_linked = (prev_out == curr_in)
                    if not proof_linked:
                        hash_chain_ok = False
                        malicious_nodes.append({
                            "type": "proof_link_break",
                            "layer": "L2_zkp_linking",
                            "between": [prev["slice_id"], sid],
                        })
                        print(f"    ⚠ L2 PROOF LINK BREAK: slice {prev['slice_id']} → {sid} "
                              f"(ZKP instances mismatch)")
                    else:
                        print(f"    ✓ L2 Proof linked OK (slice {prev['slice_id']} → {sid})")
                else:
                    print(f"    ℹ L2 Skip (no processed instances)")
            else:
                print(f"    ℹ L2 Skip (proof_instances not available)")

        # 层 3：外部哈希链 (传统 SHA-256 fallback)
        #   → 作为 L2 不可用时的退化方案
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
        "fault_type": fault_type if fault_at else None,
        "verify_ratio": verify_ratio,
        "verified_slices": sorted(verified_set),
        "slices": [
            {
                "slice_id": r["slice_id"],
                "proof_mode": r.get("proof_mode", "full"),
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
    parser.add_argument("--fault-type", type=str, default="tamper",
                        choices=["tamper", "skip", "random", "replay"],
                        help="Type of fault to inject")
    parser.add_argument("--verify-ratio", type=float, default=1.0,
                        help="Fraction of slices to verify with ZKP (0.0-1.0)")
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

    run_pipeline(initial_input, workers, fault_at=args.fault_at,
                 fault_type=args.fault_type, verify_ratio=args.verify_ratio)


if __name__ == "__main__":
    main()
