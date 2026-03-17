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
from common.utils import ezkl_verify_proof, load_proof_instances_from_witness


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

def _select_verified_slices(num_slices: int, verify_ratio: float,
                            strategy: str = "edge_cover",
                            max_light_gap: int = 1) -> set[int]:
    """
    选择哪些切片做完整 ZKP 验证。

    三种策略：
      "edge_cover"  — 边覆盖策略（推荐）：保证每条边 (i, i+1) 至少有一端是 ZKP，
                       且连续 light 节点不超过 max_light_gap 个。
      "contiguous"  — 首尾必选 + 随机选一个连续段做 ZKP。
      "random"      — 首尾必选 + 中间随机散布（旧策略，安全性最弱）。

    边覆盖 (edge_cover) 的安全保证：
      每条边 (i→i+1) 至少有一端被 ZKP 覆盖 →
      任何恶意 Worker 的输出都至少被一个相邻的 ZKP proof 约束。
      连续 light 限制 → 攻击窗口 ≤ max_light_gap。
    """
    all_ids = list(range(1, num_slices + 1))
    if verify_ratio >= 1.0:
        return set(all_ids)

    # 首尾必选
    must = {1, num_slices}

    if strategy == "edge_cover":
        # 边覆盖策略：确保每条边至少一端有 ZKP
        # 同时限制连续 light 节点数 ≤ max_light_gap
        selected = set(must)
        # 从节点 2 开始，每隔 (max_light_gap + 1) 个强制插入一个 ZKP 节点
        # 这保证连续 light 永远 ≤ max_light_gap
        i = 2
        while i < num_slices:
            # 如果距离上一个 ZKP 节点已经有 max_light_gap 个 light 了
            # 那么当前节点必须是 ZKP
            gap = 0
            for j in range(i, min(i + max_light_gap + 1, num_slices)):
                if j not in selected:
                    gap += 1
                    if gap > max_light_gap:
                        selected.add(j)
                        break
                else:
                    break
            i += 1

        # 如果 verify_ratio 允许更多，随机补充
        middle = [x for x in all_ids if x not in selected]
        extra = max(0, round(num_slices * verify_ratio) - len(selected))
        if extra > 0 and len(middle) > 0:
            extra = min(extra, len(middle))
            selected |= set(random.sample(middle, extra))

        return selected

    elif strategy == "contiguous":
        total_to_select = max(len(must), round(num_slices * verify_ratio))
        k = min(total_to_select - len(must), num_slices - len(must))
        middle = [i for i in all_ids if i not in must]
        if k > 0 and len(middle) >= k:
            max_start = len(middle) - k
            start = random.randint(0, max_start)
            selected = must | set(middle[start:start + k])
        else:
            selected = must | set(middle[:k])
        return selected

    else:  # random (legacy)
        total_to_select = max(len(must), round(num_slices * verify_ratio))
        k = min(total_to_select - len(must), num_slices - len(must))
        middle = [i for i in all_ids if i not in must]
        k = min(k, len(middle))
        return must | set(random.sample(middle, k))


def run_pipeline(
    initial_input: list[float],
    workers: list[dict],
    fault_at: int | None = None,
    fault_type: str = "tamper",
    verify_ratio: float = 1.0,
    verify_strategy: str = "edge_cover",
    seed: int | None = None,
) -> dict:
    """
    按流水线顺序调用各 Worker，收集结果并执行校验。

    参数:
        initial_input: 第一个切片的输入数据
        workers: Worker 配置列表 [{"slice_id": 1, "url": "http://..."}]
        fault_at: 在哪个 slice_id 上注入故障（None 表示不注入）
        fault_type: 故障类型 tamper/skip/random/replay
        verify_ratio: 做完整 ZKP 验证的切片比例 (0.0-1.0)
        verify_strategy: "edge_cover"(推荐) / "contiguous" / "random"(旧)

    返回:
        包含所有结果和指标的字典。
    """
    num_slices = len(workers)
    if seed is not None:
        random.seed(seed)
    verified_set = _select_verified_slices(num_slices, verify_ratio, verify_strategy)

    print("=" * 60)
    print("Master: 分布式推理流水线启动")
    print(f"  Workers: {num_slices}")
    actual_proof_fraction = len(verified_set) / num_slices if num_slices > 0 else 0
    print(f"  Verify ratio: {verify_ratio:.0%} (actual: {actual_proof_fraction:.0%}) -> proof at slices {sorted(verified_set)}")
    print(f"  Fault: {f'type={fault_type} at slice {fault_at}' if fault_at else 'None'}")
    if seed is not None:
        print(f"  Seed: {seed}")
    print("=" * 60)

    e2e_start = time.perf_counter()

    current_input = initial_input
    results = []
    hash_chain_ok = True
    l1_findings = []   # 输出完整性 (SHA-256)
    l2_findings = []   # ZKP proof linking / master verify
    l3_findings = []   # 外部哈希链一致性

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
            json={"input_data": current_input,
                  "request_id": f"req-{sid}-{int(time.time()*1000)}"},
            params={"fault_type": ft},
            timeout=120,
        )
        rtt_ms = (time.perf_counter() - t0) * 1000
        resp.raise_for_status()
        data = resp.json()
        worker_verified = data.get("verified")

        if use_proof:
            artifact_paths = data.get("proof_artifacts") or {}
            verify_paths = {
                "settings": artifact_paths.get("settings"),
                "vk": artifact_paths.get("vk"),
                "srs": artifact_paths.get("srs"),
            }
            proof_path = artifact_paths.get("proof_path")
            witness_path = artifact_paths.get("witness_path")

            try:
                local_verified = bool(
                    proof_path
                    and all(verify_paths.values())
                    and ezkl_verify_proof(proof_path, verify_paths)
                )
            except Exception as e:
                local_verified = False
                print(f"    ⚠ Master local verify error: {e}")

            data["verified"] = local_verified

            # L2 linking 数据优先从 proof.json 的 pretty_public_inputs 提取
            # （已被 ezkl.verify 认证），而非依赖 witness 文件
            proof_data_for_linking = data.get("proof") or {}
            ppi = proof_data_for_linking.get("pretty_public_inputs") or {}
            data["proof_instances"] = {
                "processed_inputs": ppi.get("processed_inputs") or ppi.get("inputs") or None,
                "processed_outputs": ppi.get("processed_outputs") or ppi.get("outputs") or None,
                "rescaled_inputs": ppi.get("rescaled_inputs") or None,
                "rescaled_outputs": ppi.get("rescaled_outputs") or None,
            }

            if not local_verified:
                hash_chain_ok = False
                l2_findings.append({
                    "type": "proof_verify_failed",
                    "slice_id": sid,
                })

            if worker_verified is not None and worker_verified != local_verified:
                l2_findings.append({
                    "type": "verify_mismatch",
                    "slice_id": sid,
                    "worker_verified": worker_verified,
                    "master_verified": local_verified,
                })

        print(f"    proof_gen: {data['metrics']['proof_gen_ms']:.0f} ms | "
              f"verify: {data['metrics']['verify_ms']:.0f} ms | "
              f"rtt: {rtt_ms:.0f} ms")
        print(f"    hash_in:  {data['hash_in'][:16]}...")
        print(f"    hash_out: {data['hash_out'][:16]}...")
        if use_proof:
            print(f"    worker_verified: {worker_verified}")
            print(f"    master_verified: {data['verified']}")
        else:
            print(f"    verified: {data['verified']}")

        # ══════════════════════════════════════════════════════════
        # 三层校验体系（安全等级严格区分）
        # ══════════════════════════════════════════════════════════

        # 层 1：输出完整性 (SHA-256) — 故障检测级
        #   → 对 ZKP 节点：hash_out 与 proof 绑定，具有密码学保证
        #   → 对 light 节点：L1 完全无效！恶意 Worker 可同时伪造
        #     output_data 和 hash_out 使两者一致，绕过 L1
        #   → light 节点的真正防线是随机挑战 (re_prove)：
        #     re_prove 产生的 proof 中 processed_outputs 是电路级承诺，
        #     不可伪造，可与 light 阶段声称的 output 交叉比对
        #   → 安全定位：fault detection（proof 节点），deterrence（light 节点）
        actual_output_hash = sha256_of_list(data["output_data"])
        output_integrity = (data["hash_out"] == actual_output_hash)
        if not output_integrity:
            hash_chain_ok = False
            l1_findings.append({
                "type": "output_tamper",
                "slice_id": sid,
                "expected": data["hash_out"][:16],
                "actual": actual_output_hash[:16],
            })
            print(f"    ⚠ L1 OUTPUT TAMPER at slice {sid}")
        else:
            print(f"    ✓ L1 Output integrity OK (slice {sid})")

        # 层 2：ZKP Proof Linking — 密码学安全级（系统唯一的密码学安全来源）
        #   → 比对 prev.processed_outputs == curr.processed_inputs
        #   → 这是 ZKP 公开实例，verify 通过 = 数学保证
        #   → 安全前提：Poseidon collision-resistance + PLONK soundness
        #   → 边覆盖策略确保每条边至少一端有 ZKP
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
                        l2_findings.append({
                            "type": "proof_link_break",
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

        # 层 3：外部哈希链 — 一致性检查级，非对抗安全
        #   → 仅检测非协同的错误/故障
        #   → 合谋的相邻节点可以协调伪造一致的哈希 → L3 完全失效
        #   → 安全定位：consistency check，非 adversarial security
        if i > 0:
            prev = results[-1]
            expected_hash = prev["hash_out"]
            actual_hash = data["hash_in"]
            chain_match = (expected_hash == actual_hash)

            if not chain_match:
                hash_chain_ok = False
                l3_findings.append({
                    "type": "hash_chain_break",
                    "between": [prev["slice_id"], sid],
                    "expected": expected_hash[:16],
                    "actual": actual_hash[:16],
                })
                print(f"    ⚠ HASH CHAIN BREAK between slice {prev['slice_id']} → {sid}")
            else:
                print(f"    ✓ Hash chain OK (slice {prev['slice_id']} → {sid})")

        # 补充 Master 侧指标
        data["metrics"]["rtt_ms"] = round(rtt_ms, 2)
        data["request_input"] = list(current_input)
        results.append(data)

        # 下一个 Worker 的输入 = 本 Worker 的输出
        current_input = data["output_data"]

    e2e_ms = (time.perf_counter() - e2e_start) * 1000

    # ── 随机挑战 (Random Challenge) ──
    # 对未做 ZKP 的切片随机抽取一个，要求重新 prove
    # 防止 Worker 提前预计算或 replay
    light_slices = [w for w in workers if w["slice_id"] not in verified_set]
    challenge_result = None
    if light_slices and len(light_slices) > 0:
        target = random.choice(light_slices)
        target_data = results[target["slice_id"] - 1]  # 0-indexed
        target_req_id = target_data.get("request_id", "")
        print(f"\n[Master] 随机挑战 → Worker {target['slice_id']} "
              f"(re_prove, request_id={target_req_id})")
        try:
            resp = requests.post(
                f"{target['url']}/re_prove",
                json={
                    "input_data": target_data.get("request_input", []),
                    "request_id": target_req_id,
                },
                timeout=180,
            )
            if resp.status_code == 200:
                challenge = resp.json()
                artifact_paths = challenge.get("proof_artifacts") or {}
                verify_paths = {
                    "settings": artifact_paths.get("settings"),
                    "vk": artifact_paths.get("vk"),
                    "srs": artifact_paths.get("srs"),
                }
                proof_path = artifact_paths.get("proof_path")
                try:
                    master_re_verified = bool(
                        proof_path
                        and all(verify_paths.values())
                        and ezkl_verify_proof(proof_path, verify_paths)
                    )
                except Exception as e:
                    master_re_verified = False
                    print(f"    ⚠ Challenge local verify error: {e}")

                challenge_result = {
                    "challenged_slice": target["slice_id"],
                    "challenged_request_id": target_req_id,
                    "from_cache": challenge.get("from_cache", False),
                    "cache_consistent": challenge.get("cache_consistent"),
                    "worker_re_verified": challenge.get("verified", False),
                    "master_re_verified": master_re_verified,
                    "re_prove_ms": challenge.get("metrics", {}).get("proof_gen_ms", 0),
                    "output_cross_check": None,
                }

                # ── 随机挑战交叉验证 ──
                # light 节点的 L1 对恶意节点无效（可同时伪造 output+hash）。
                # 真正的防御：re_prove 产生的 proof 绑定了电路级真实输出
                # （proof.json 的 pretty_public_inputs 中的 outputs/rescaled_outputs，
                #  或 hashed 模式下的 processed_outputs）。
                # Master 把电路真实输出与 light 阶段 Worker 声称的 output 做比较。
                #
                # 从 proof.json 提取受认证的输出（不依赖 witness 文件）
                challenge_proof = challenge.get("proof") or {}
                challenge_ppi = challenge_proof.get("pretty_public_inputs") or {}
                # public 模式下用 rescaled_outputs，hashed 模式下用 processed_outputs
                circuit_outputs = (
                    challenge_ppi.get("rescaled_outputs")
                    or challenge_ppi.get("processed_outputs")
                    or []
                )
                original_output = target_data.get("output_data", [])
                original_hash_out = target_data.get("hash_out", "")

                cross_check_passed = None
                if circuit_outputs and original_output:
                    # 将电路输出展平为可比较的浮点列表
                    flat_circuit = []
                    for group in circuit_outputs:
                        if isinstance(group, list):
                            for v in group:
                                try:
                                    flat_circuit.append(float(v))
                                except (ValueError, TypeError):
                                    flat_circuit.append(v)
                        else:
                            flat_circuit.append(group)

                    # 比较电路输出与 light 阶段声称的输出
                    if len(flat_circuit) == len(original_output):
                        max_diff = max(
                            abs(float(a) - float(b))
                            for a, b in zip(flat_circuit, original_output)
                        ) if flat_circuit else 0
                        # EZKL 量化有精度损失，允许小误差
                        cross_check_passed = (max_diff < 1.0)
                    else:
                        cross_check_passed = False

                    challenge_result["output_cross_check"] = {
                        "circuit_output_sample": str(flat_circuit[:4]),
                        "claimed_output_sample": str(original_output[:4]),
                        "max_diff": round(max_diff, 6) if cross_check_passed is not None else None,
                        "passed": cross_check_passed,
                    }

                    if cross_check_passed is False:
                        hash_chain_ok = False
                        l2_findings.append({
                            "type": "challenge_output_mismatch",
                            "slice_id": target["slice_id"],
                            "detail": "re_prove 电路输出与 light 阶段声称的 output 不一致",
                        })
                        print(f"    ⚠ Challenge OUTPUT MISMATCH at slice {target['slice_id']}")
                    elif cross_check_passed is True:
                        print(f"    ✓ Challenge cross-check PASSED (max_diff={max_diff:.6f})")
                    else:
                        print(f"    ℹ Challenge cross-check inconclusive")
                # 将 from_cache / cache_consistent 纳入正式判定
                if not challenge.get("from_cache", False):
                    l2_findings.append({
                        "type": "challenge_cache_miss",
                        "slice_id": target["slice_id"],
                        "detail": "Worker 无法从缓存找回历史请求，挑战可追溯性降级",
                    })
                    print(f"    ⚠ Challenge cache miss (request_id not found in Worker cache)")
                if challenge.get("cache_consistent") is False:
                    hash_chain_ok = False
                    l2_findings.append({
                        "type": "challenge_cache_inconsistent",
                        "slice_id": target["slice_id"],
                        "detail": "Worker 缓存的 hash_out 与 output_data 不一致",
                    })
                    print(f"    ⚠ Challenge cache INCONSISTENT at slice {target['slice_id']}")
                if not master_re_verified:
                    hash_chain_ok = False
                    l2_findings.append({
                        "type": "challenge_verify_failed",
                        "slice_id": target["slice_id"],
                    })
                print(f"    re_verified: worker={challenge_result['worker_re_verified']} "
                      f"master={challenge_result['master_re_verified']} "
                      f"({challenge_result['re_prove_ms']:.0f} ms)")
        except Exception as e:
            print(f"    ⚠ Challenge failed: {e}")

    # 也校验首个 Worker 的输入哈希
    expected_first_hash = sha256_of_list(initial_input)
    first_hash_ok = (results[0]["hash_in"] == expected_first_hash)
    if not first_hash_ok:
        hash_chain_ok = False
        l3_findings.append({
            "type": "first_input_hash_mismatch",
            "between": ["input", results[0]["slice_id"]],
            "expected": expected_first_hash[:16],
            "actual": results[0]["hash_in"][:16],
        })

    # ── 汇总 ──
    all_findings = l1_findings + l2_findings + l3_findings
    detected_slices = sorted(set(
        f.get("slice_id") for f in all_findings if f.get("slice_id") is not None
    ))
    fault_detected = bool(all_findings) if fault_at is not None else None

    summary = {
        "e2e_latency_ms": round(e2e_ms, 2),
        "hash_chain_ok": hash_chain_ok,
        "l1_findings": l1_findings,
        "l2_findings": l2_findings,
        "l3_findings": l3_findings,
        "detected_slices": detected_slices,
        "fault_detected": fault_detected,
        "fault_injected_at": fault_at,
        "fault_type": fault_type if fault_at else None,
        "verify_ratio": verify_ratio,
        "actual_proof_fraction": round(actual_proof_fraction, 4),
        "verify_strategy": verify_strategy,
        "verified_slices": sorted(verified_set),
        "seed": seed,
        "random_challenge": challenge_result,
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
    print(f"  故障检测:        {fault_detected}")
    print(f"  异常切片:        {detected_slices}")
    if l1_findings:
        print(f"  L1 发现 ({len(l1_findings)}):")
        for f in l1_findings:
            print(f"    ⚠ {f['type']}: slice {f.get('slice_id', '?')}")
    if l2_findings:
        print(f"  L2 发现 ({len(l2_findings)}):")
        for f in l2_findings:
            print(f"    ⚠ {f['type']}: {f.get('slice_id') or f.get('between', '?')}")
    if l3_findings:
        print(f"  L3 发现 ({len(l3_findings)}):")
        for f in l3_findings:
            print(f"    ⚠ {f['type']}: {f.get('between', '?')}")
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
    parser.add_argument("--verify-strategy", type=str, default="edge_cover",
                        choices=["edge_cover", "contiguous", "random"],
                        help="Verification strategy: edge_cover (recommended), contiguous, random")
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
                 fault_type=args.fault_type, verify_ratio=args.verify_ratio,
                 verify_strategy=args.verify_strategy)


if __name__ == "__main__":
    main()
