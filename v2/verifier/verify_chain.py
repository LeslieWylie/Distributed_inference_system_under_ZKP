"""
v2/verifier/verify_chain.py — 全链路验证 + 证书签发。

核心协议检查:
  1. 每片 proof 独立验证通过
  2. 每片绑定的 model_digest 与 registry 一致
  3. 相邻切片: Cout_i == Cin_{i+1}  (processed_outputs_i == processed_inputs_{i+1})
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
    RequestStatus,
    ProofJob,
)
from v2.verifier.verify_single import verify_proof


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
    assert len(proof_jobs) == len(artifacts), \
        f"proof_jobs ({len(proof_jobs)}) != artifacts ({len(artifacts)})"

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
    BASE_EPSILON = 0.01
    num_links = max(len(single_results) - 1, 1)
    LINK_EPSILON = BASE_EPSILON / num_links
    accumulated_diff = 0.0

    for i in range(len(single_results) - 1):
        curr = single_results[i]
        next_ = single_results[i + 1]

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
            out_vals = _flatten_nested(json.loads(curr_out))
            in_vals = _flatten_nested(json.loads(next_in))

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
    TERMINAL_EPSILON = BASE_EPSILON / max(len(single_results), 1)
    if provisional_output is not None and single_results:
        last_out_str = single_results[-1].output_commit_from_proof
        if last_out_str is not None:
            try:
                proof_out_vals = _flatten_nested(json.loads(last_out_str))

                # P4-FIX: 显式检查空值
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
