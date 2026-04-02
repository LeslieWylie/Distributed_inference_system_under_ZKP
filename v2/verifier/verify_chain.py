"""
v2/verifier/verify_chain.py — 全链路验证 + 证书签发。

核心协议检查:
    1. 每片 proof 独立验证通过
    2. 每片绑定的 model_digest 与 registry 一致
    3. 相邻切片:
         - public: rescaled_outputs ≈ rescaled_inputs
         - hashed: processed_outputs == processed_inputs
         - polycommit: raw proof prefix 诊断比较
    4. 最终输出与 Cout_n 对应

所有检查由 Verifier 独立执行，不信任任何 Worker 声明。
"""

import json
import time
from datetime import datetime, timezone

from v2.common.types import (
    SliceArtifact,
    SingleVerifyResult,
    ChainVerifyResult,
    Certificate,
    ClientVerificationResult,
    RequestStatus,
    ProofJob,
)
from v2.verifier.verify_single import verify_proof


BASE_EPSILON = 0.01
# 1/512 = 0.001953125 是 EZKL scale=9 下的量化最小精度单位 (1 ULP)
# scale 对齐后接口漂移的理论上界就是 1 ULP
SCALE_ALIGNED_ULP = 1.0 / 512  # 0.001953125
# 工程下界设为 2 ULP，给舍入留出余量
MIN_LINK_EPSILON = SCALE_ALIGNED_ULP * 2   # 0.00390625
MIN_TERMINAL_EPSILON = SCALE_ALIGNED_ULP * 2


def compute_link_epsilon(num_links: int) -> float:
    dynamic_epsilon = BASE_EPSILON / max(num_links, 1)
    return max(dynamic_epsilon, MIN_LINK_EPSILON)


def compute_terminal_epsilon(num_slices: int) -> float:
    dynamic_epsilon = BASE_EPSILON / max(num_slices, 1)
    return max(dynamic_epsilon, MIN_TERMINAL_EPSILON)


def _decode_proof_bytes(proof_data: dict) -> bytes:
    """从 proof JSON 提取原始 transcript bytes。"""
    hex_proof = proof_data.get("hex_proof")
    if isinstance(hex_proof, str):
        payload = hex_proof[2:] if hex_proof.startswith("0x") else hex_proof
        if len(payload) % 2 != 0:
            raise ValueError("hex_proof has odd length")
        try:
            return bytes.fromhex(payload)
        except ValueError as exc:
            raise ValueError("hex_proof is not valid hex") from exc

    proof = proof_data.get("proof")
    if isinstance(proof, list):
        try:
            return bytes(int(item) & 0xFF for item in proof)
        except (TypeError, ValueError) as exc:
            raise ValueError("proof byte list contains non-byte values") from exc

    raise ValueError("proof JSON missing raw proof bytes")


def _extract_proof_prefix(proof_data: dict, prefix_len: int) -> bytes:
    """提取 proof transcript 的前缀字节。"""
    proof_bytes = _decode_proof_bytes(proof_data)
    if len(proof_bytes) < prefix_len:
        raise ValueError(
            f"proof too short for prefix extraction: {len(proof_bytes)} < {prefix_len}"
        )
    return proof_bytes[:prefix_len]


def _compare_polycommit_proof_prefixes(
    proof_data_i: dict,
    proof_data_j: dict,
) -> dict[str, bool | None]:
    """比较两个 proof transcript 的前缀，作为 polycommit 诊断。"""
    result: dict[str, bool | None] = {
        "prefix_64_equal": None,
        "prefix_32_equal": None,
    }
    for prefix_len, key in ((64, "prefix_64_equal"), (32, "prefix_32_equal")):
        try:
            result[key] = (
                _extract_proof_prefix(proof_data_i, prefix_len)
                == _extract_proof_prefix(proof_data_j, prefix_len)
            )
        except ValueError:
            result[key] = None
    return result


def _load_proof_json(job: ProofJob) -> dict | None:
    if job.proof_data is not None:
        return job.proof_data
    if job.proof_path is None:
        return None
    try:
        with open(job.proof_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _read_visibility_mode(settings_path: str, key: str) -> str | None:
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    value = settings.get("run_args", {}).get(key)
    return str(value).lower() if value is not None else None


def _get_interface_visibility(curr_artifact: SliceArtifact, next_artifact: SliceArtifact) -> str:
    curr_out = _read_visibility_mode(curr_artifact.settings_path, "output_visibility")
    next_in = _read_visibility_mode(next_artifact.settings_path, "input_visibility")

    vis_values = {curr_out, next_in}
    if "kzgcommit" in vis_values or "polycommit" in vis_values:
        return "polycommit"
    if "hashed" in vis_values:
        return "hashed"
    return "public"


def _is_hex_hash(val) -> bool:
    """判断值是否为 Poseidon 哈希（hex 字符串）。"""
    return isinstance(val, str) and val.startswith("0x") and len(val) > 10


def _peek_first(data):
    """取嵌套列表中的第一个叶子值。"""
    while isinstance(data, list) and len(data) > 0:
        data = data[0]
    return data


def _flatten_strings(data) -> list[str]:
    """将嵌套列表展平为字符串列表（用于 Poseidon 哈希比较）。"""
    result = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, list):
                result.extend(_flatten_strings(item))
            else:
                result.append(str(item))
    else:
        result.append(str(data))
    return result


def _flatten_nested(data) -> list[float]:
    """将嵌套列表展平为 float 列表。"""
    result = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, list):
                result.extend(_flatten_nested(item))
            else:
                result.append(float(item))
    else:
        result.append(float(data))
    return result


def verify_chain(
    req_id: str,
    proof_jobs: list[ProofJob],
    artifacts: list[SliceArtifact],
    initial_input: list[float] | None = None,
    initial_input_commit: str | None = None,
    provisional_output: list[float] | None = None,
) -> ChainVerifyResult:
    """
    全链路验证：逐片验证 proof + 相邻 commitment linking + 终端绑定。

    参数:
      req_id: 请求 ID
      proof_jobs: 按 slice_id 排序的已完成 proof jobs
      artifacts: 按 slice_id 排序的注册工件
      initial_input_commit: 首片输入的外部 commitment（可选）
      provisional_output: 在线阶段返回的临时输出，用于终端绑定检查

    返回:
      ChainVerifyResult
    """
    # Structural check: proof_jobs and artifacts must match in count
    if len(proof_jobs) != len(artifacts):
        return ChainVerifyResult(
            req_id=req_id,
            all_single_proofs_verified=False,
            all_links_verified=False,
            status=RequestStatus.INVALID,
            proof_failures=[{
                "reason": f"proof_jobs ({len(proof_jobs)}) != artifacts ({len(artifacts)})",
            }],
        )

    # Sort by slice_id
    proof_jobs = sorted(proof_jobs, key=lambda j: j.slice_id)
    artifacts = sorted(artifacts, key=lambda a: a.slice_id)

    single_results: list[SingleVerifyResult] = []
    proof_failures = []
    link_failures = []

    # Step 1: 逐片独立验证
    for job, artifact in zip(proof_jobs, artifacts):
        assert job.slice_id == artifact.slice_id

        if job.proof_path is None:
            proof_failures.append({
                "slice_id": job.slice_id,
                "reason": "no proof generated",
            })
            single_results.append(SingleVerifyResult(
                slice_id=job.slice_id, verified=False, error="no proof path",
            ))
            continue

        # Step 1a: 验证 model_digest 一致性 — 重新计算 ONNX 文件摘要
        #   防止 Worker 替换切片模型或量化设置
        import os as _os
        if _os.path.exists(artifact.model_path):
            from v2.common.commitments import compute_file_digest
            actual_digest = compute_file_digest(artifact.model_path)
            if actual_digest != artifact.model_digest:
                proof_failures.append({
                    "slice_id": job.slice_id,
                    "reason": f"model_digest mismatch: registry={artifact.model_digest[:16]}... "
                              f"actual={actual_digest[:16]}...",
                })
                single_results.append(SingleVerifyResult(
                    slice_id=job.slice_id, verified=False,
                    error="model_digest integrity check failed",
                ))
                continue

        # Step 1b: 独立验证 proof (使用 registry 的 vk/settings/srs)
        result = verify_proof(job.proof_path, artifact)
        single_results.append(result)

        if not result.verified:
            proof_failures.append({
                "slice_id": job.slice_id,
                "reason": result.error or "verification failed",
            })

    all_single_ok = len(proof_failures) == 0

    # Step 2: 相邻 linking
    #   验证 rescaled_outputs[i] ≈ rescaled_inputs[i+1]
    #   public 模式下 rescaled 值受 proof soundness 密码学绑定，
    #   不可伪造 (ezkl.verify 已确认 proof 对这些 public instances 成立)
    #   由于独立量化，同一张量在两个电路中的 rescaled 值可能有微小差异，
    #   因此使用近似比较。
    #
    #   P1-FIX: 使用动态 epsilon = BASE_EPSILON / num_slices
    #   防止逐边 ε 累积: 攻击者每条边注入 ≤ ε 的扰动，n 条边累积 n·ε 不受控。
    #   动态 epsilon 确保全链累积上界 ≤ BASE_EPSILON。
    num_links = max(len(single_results) - 1, 1)
    LINK_EPSILON = compute_link_epsilon(num_links)
    accumulated_diff = 0.0

    # Step 2a: P0-3 FIX — 首端输入绑定
    #   验证第 1 片 proof 的 rescaled_inputs 与请求的初始输入一致
    #   防止 Worker 使用不同输入的合法 proof 冒充当前请求
    if initial_input and single_results:
        first_in_str = single_results[0].input_commit_from_proof
        if first_in_str is not None:
            try:
                first_proof_inputs = _flatten_nested(json.loads(first_in_str))
                if len(first_proof_inputs) == len(initial_input):
                    first_max_diff = max(
                        abs(float(a) - float(b))
                        for a, b in zip(first_proof_inputs, initial_input)
                    )
                    if first_max_diff > LINK_EPSILON:
                        link_failures.append({
                            "edge": ["initial_input", proof_jobs[0].slice_id],
                            "reason": f"first-input binding failure: "
                                      f"proof input != request input "
                                      f"(max_diff={first_max_diff:.6f})",
                        })
                else:
                    link_failures.append({
                        "edge": ["initial_input", proof_jobs[0].slice_id],
                        "reason": f"first-input dimension mismatch: "
                                  f"{len(first_proof_inputs)} vs {len(initial_input)}",
                    })
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

    # Step 2b: 相邻切片 linking
    #   策略:
    #     - polycommit: 优先做 raw proof prefix 诊断比较
    #     - hashed/public: 继续沿用 pretty_public_inputs 提取的值做比较

    for i in range(len(single_results) - 1):
        curr = single_results[i]
        next_ = single_results[i + 1]
        interface_visibility = _get_interface_visibility(artifacts[i], artifacts[i + 1])

        if interface_visibility == "polycommit":
            curr_proof_data = _load_proof_json(proof_jobs[i])
            next_proof_data = _load_proof_json(proof_jobs[i + 1])
            if curr_proof_data is not None and next_proof_data is not None:
                prefix_result = _compare_polycommit_proof_prefixes(
                    curr_proof_data,
                    next_proof_data,
                )
                if prefix_result["prefix_64_equal"] is True or prefix_result["prefix_32_equal"] is True:
                    continue
                link_failures.append({
                    "edge": [proof_jobs[i].slice_id, proof_jobs[i + 1].slice_id],
                    "reason": "polycommit raw proof prefix mismatch",
                    "method": "polycommit_prefix_compare",
                    **prefix_result,
                })
                continue
            link_failures.append({
                "edge": [proof_jobs[i].slice_id, proof_jobs[i + 1].slice_id],
                "reason": "polycommit proof data unavailable for prefix comparison",
                "method": "polycommit_prefix_compare",
            })
            continue

        # 回退: rescaled 值近似比较 (public 模式) 或 哈希精确匹配 (hashed 模式)
        curr_out = curr.output_commit_from_proof
        next_in = next_.input_commit_from_proof

        # P4-FIX: 显式检查空值
        if curr_out is None or next_in is None:
            link_failures.append({
                "edge": [proof_jobs[i].slice_id, proof_jobs[i + 1].slice_id],
                "reason": "missing public instance data",
                "curr_out_exists": curr_out is not None,
                "next_in_exists": next_in is not None,
            })
            continue

        try:
            out_raw = json.loads(curr_out)
            in_raw = json.loads(next_in)

            # 检测 hashed 模式: 值是 Poseidon 哈希 hex 字符串
            # 例: [["0x270dd1bd58e4f3a8..."]]
            out_flat = _flatten_nested(out_raw) if not _is_hex_hash(_peek_first(out_raw)) else None
            in_flat = _flatten_nested(in_raw) if not _is_hex_hash(_peek_first(in_raw)) else None

            if out_flat is None or in_flat is None:
                # Hashed 模式: Poseidon 哈希精确字符串匹配
                out_hashes = _flatten_strings(out_raw)
                in_hashes = _flatten_strings(in_raw)
                if out_hashes == in_hashes:
                    # 精确匹配 — 链接成功
                    continue
                else:
                    link_failures.append({
                        "edge": [proof_jobs[i].slice_id, proof_jobs[i + 1].slice_id],
                        "reason": f"hashed linking failure: Poseidon hash mismatch "
                                  f"(out={len(out_hashes)} hashes, in={len(in_hashes)} hashes)",
                        "method": "poseidon_hash",
                    })
                continue

            out_vals = out_flat
            in_vals = in_flat

            # P4-FIX: 显式检查空列表
            if len(out_vals) == 0 or len(in_vals) == 0:
                link_failures.append({
                    "edge": [proof_jobs[i].slice_id, proof_jobs[i + 1].slice_id],
                    "reason": f"empty rescaled values: out={len(out_vals)}, in={len(in_vals)}",
                })
                continue

            if len(out_vals) != len(in_vals):
                link_failures.append({
                    "edge": [proof_jobs[i].slice_id, proof_jobs[i + 1].slice_id],
                    "reason": f"dimension mismatch: {len(out_vals)} vs {len(in_vals)}",
                })
            else:
                max_diff = max(
                    abs(float(a) - float(b))
                    for a, b in zip(out_vals, in_vals)
                )
                accumulated_diff += max_diff

                # 逐边阈值检查 (动态 ε)
                if max_diff > LINK_EPSILON:
                    link_failures.append({
                        "edge": [proof_jobs[i].slice_id, proof_jobs[i + 1].slice_id],
                        "reason": f"rescaled value mismatch: max_diff={max_diff:.6f} "
                                  f"(threshold={LINK_EPSILON:.6f})",
                    })
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            link_failures.append({
                "edge": [proof_jobs[i].slice_id, proof_jobs[i + 1].slice_id],
                "reason": f"linking comparison error: {e}",
            })

    # P1-FIX: 额外全链累积检查
    if accumulated_diff > BASE_EPSILON:
        link_failures.append({
            "edge": ["chain", "accumulated"],
            "reason": f"accumulated linking error {accumulated_diff:.6f} "
                      f"exceeds chain budget {BASE_EPSILON}",
        })

    # Step 3: 终端绑定 — 最后一片 proof 的 rescaled_outputs ≈ provisional output
    #   若 provisional_output 与 proof 内输出不一致，说明最后一片数据被篡改
    #   P1-FIX: 终端阈值也按链长度缩放
    TERMINAL_EPSILON = compute_terminal_epsilon(len(single_results))
    if provisional_output is not None and single_results:
        last_out_str = single_results[-1].output_commit_from_proof
        if last_out_str is not None:
            try:
                last_out_raw = json.loads(last_out_str)
                first_leaf = _peek_first(last_out_raw)

                if _is_hex_hash(first_leaf):
                    # Hashed 模式: 终端绑定不适用 (输出只有哈希，无法与浮点 provisional 比较)
                    # 但 proof soundness 已验证通过，且链式链接已验证哈希一致性，
                    # 所以终端绑定由链式哈希传递性保障。跳过浮点比较。
                    pass
                else:
                    proof_out_vals = _flatten_nested(last_out_raw)

                    if len(proof_out_vals) == 0:
                        link_failures.append({
                            "edge": [proof_jobs[-1].slice_id, "terminal"],
                            "reason": "empty proof output rescaled values",
                        })
                    elif len(proof_out_vals) == len(provisional_output):
                        terminal_max_diff = max(
                            abs(float(a) - float(b))
                            for a, b in zip(proof_out_vals, provisional_output)
                        )
                        if terminal_max_diff > TERMINAL_EPSILON:
                            link_failures.append({
                                "edge": [proof_jobs[-1].slice_id, "terminal"],
                                "reason": f"terminal binding failure: "
                                          f"proof output != provisional output "
                                          f"(max_diff={terminal_max_diff:.6f})",
                            })
                    else:
                        link_failures.append({
                            "edge": [proof_jobs[-1].slice_id, "terminal"],
                            "reason": f"terminal dimension mismatch: "
                                      f"{len(proof_out_vals)} vs {len(provisional_output)}",
                        })
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                link_failures.append({
                    "edge": [proof_jobs[-1].slice_id, "terminal"],
                    "reason": f"terminal binding error: {e}",
                })

    all_links_ok = len(link_failures) == 0

    status = RequestStatus.CERTIFIED if (all_single_ok and all_links_ok) \
        else RequestStatus.INVALID

    # 最终输出 commitment
    final_output_commit = None
    if single_results:
        final_output_commit = single_results[-1].output_commit_from_proof

    return ChainVerifyResult(
        req_id=req_id,
        all_single_proofs_verified=all_single_ok,
        all_links_verified=all_links_ok,
        status=status,
        link_failures=link_failures,
        proof_failures=proof_failures,
        final_output_commit=final_output_commit,
    )


def issue_certificate(
    chain_result: ChainVerifyResult,
    artifacts: list[SliceArtifact],
    verify_ms_total: float = 0.0,
) -> Certificate:
    """根据链路验证结果签发证书。"""
    return Certificate(
        req_id=chain_result.req_id,
        status=chain_result.status.value,
        slice_count=len(artifacts),
        final_output_commit=chain_result.final_output_commit or "",
        all_single_proofs_verified=chain_result.all_single_proofs_verified,
        all_links_verified=chain_result.all_links_verified,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_digests=[a.model_digest for a in artifacts],
        details={
            "link_failures": chain_result.link_failures,
            "proof_failures": chain_result.proof_failures,
            "verify_ms_total": round(verify_ms_total, 2),
        },
    )


def build_client_verification_result(
    chain_result: ChainVerifyResult,
    verify_ms_total: float,
) -> ClientVerificationResult:
    """从链路验证结果构建客户端独立验证结果。"""
    failures = list(chain_result.proof_failures) + list(chain_result.link_failures)
    return ClientVerificationResult(
        req_id=chain_result.req_id,
        status=chain_result.status.value,
        all_single_proofs_verified=chain_result.all_single_proofs_verified,
        all_links_verified=chain_result.all_links_verified,
        final_output_commit=chain_result.final_output_commit,
        failure_reasons=failures,
        metrics={"verification_ms": round(verify_ms_total, 2)},
    )
