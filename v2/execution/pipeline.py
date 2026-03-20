"""
v2/execution/pipeline.py — Phase A 同步全链路 pipeline。

Phase A: "慢但正确"版本
  - 所有切片同步执行推理 + 生成 proof
  - Master 独立验证所有 proof
  - 检查所有相邻 commitment linking
  - 签发证书或标记 invalid

整个流程对应请求状态机:
  SUBMITTED → EXECUTING → PROVING → VERIFYING → CERTIFIED / INVALID
"""

import os
import sys
import time
import uuid

import numpy as np
import onnxruntime as rt

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from v2.common.types import (
    SliceArtifact,
    ExecutionRecord,
    ProofJob,
    ProofJobStatus,
    Certificate,
    RequestStatus,
)
from v2.common.commitments import compute_commitment
from v2.prover.ezkl_adapter import prove_slice
from v2.verifier.verify_chain import verify_chain, issue_certificate


def run_certified_pipeline(
    initial_input: list[float],
    artifacts: list[SliceArtifact],
    fault_at: int | None = None,
    fault_type: str = "tamper",
) -> dict:
    """
    Phase A 同步全链路 pipeline。

    每片同步：执行推理 → 生成 proof → 传递给下一片。
    最后由 Verifier 独立验证全部 proof + commitment linking。

    参数:
      initial_input: 初始输入张量 (flat list)
      artifacts: 按 slice_id 排序的切片工件
      fault_at: 在此 slice_id 上注入故障 (测试用)
      fault_type: 故障类型 tamper/skip/random/replay

    返回:
      包含 certificate, metrics, execution_records, proof_jobs 的完整结果字典
    """
    req_id = f"req-{uuid.uuid4().hex[:8]}-{int(time.time() * 1000)}"
    artifacts = sorted(artifacts, key=lambda a: a.slice_id)
    num_slices = len(artifacts)

    print("=" * 60)
    print(f"Pipeline: {num_slices} slices, req_id={req_id}")
    print(f"  Fault: {f'type={fault_type} at slice {fault_at}' if fault_at else 'None'}")
    print("=" * 60)

    pipeline_start = time.perf_counter()

    current_input = initial_input
    execution_records: list[ExecutionRecord] = []
    proof_jobs: list[ProofJob] = []
    per_slice_metrics = []

    for artifact in artifacts:
        sid = artifact.slice_id
        slice_start = time.perf_counter()

        # ── 1. 计算输入 commitment (审计用，安全绑定由 proof 公开实例提供) ──
        input_commit = compute_commitment(
            req_id, sid, artifact.model_digest, current_input,
        )

        # ── 2. 执行推理 (ONNX) ──
        session = rt.InferenceSession(artifact.model_path)
        input_name = session.get_inputs()[0].name
        input_array = np.array([current_input], dtype=np.float32)
        ort_output = session.run(None, {input_name: input_array})
        output_tensor = ort_output[0].flatten().tolist()

        # ── 故障注入 (仅测试) ──
        fault_injected = False
        if fault_at == sid:
            fault_injected = True
            if fault_type == "tamper":
                output_tensor[0] += 999.0
            elif fault_type == "skip":
                output_tensor = [0.0] * len(output_tensor)
            elif fault_type == "random":
                import random
                output_tensor = [random.uniform(-10, 10) for _ in output_tensor]
            elif fault_type == "replay":
                output_tensor = [0.42] * len(output_tensor)

        # ── 3. 计算输出 commitment (审计用) ──
        output_commit = compute_commitment(
            req_id, sid, artifact.model_digest, output_tensor,
        )

        exec_ms = (time.perf_counter() - slice_start) * 1000

        exec_record = ExecutionRecord(
            req_id=req_id,
            slice_id=sid,
            input_commit=input_commit,
            output_commit=output_commit,
            output_tensor=output_tensor,
            input_tensor=list(current_input),
            exec_ms=round(exec_ms, 2),
        )
        execution_records.append(exec_record)

        # ── 4. 同步 proving (Phase A: 不做解耦) ──
        #   注意: prover 使用的是原始执行的输入，不是故障注入后的输出
        #   prover 从 ONNX 独立计算结果并生成 witness
        #   如果 Worker 篡改了输出，proof 里的 rescaled_outputs 会与篡改值不同
        prove_start = time.perf_counter()

        work_dir = os.path.join(
            os.path.dirname(artifact.compiled_path),
            "proofs", req_id,
        )
        proof_result = prove_slice(
            input_tensor=exec_record.input_tensor,
            compiled_path=artifact.compiled_path,
            pk_path=artifact.pk_path,
            srs_path=artifact.srs_path,
            work_dir=work_dir,
            tag=f"slice_{sid}",
        )

        job = ProofJob(
            job_id=f"job-{sid}-{uuid.uuid4().hex[:8]}",
            req_id=req_id,
            slice_id=sid,
            input_commit=input_commit,
            output_commit=output_commit,
            artifact=artifact,
            witness_path=proof_result["witness_path"],
            proof_path=proof_result["proof_path"],
            status=ProofJobStatus.DONE,
            proof_gen_ms=proof_result["proof_gen_ms"],
            proof_data=proof_result["proof_data"],
        )
        proof_jobs.append(job)

        prove_ms = proof_result["proof_gen_ms"]
        total_slice_ms = (time.perf_counter() - slice_start) * 1000

        per_slice_metrics.append({
            "slice_id": sid,
            "exec_ms": round(exec_ms, 2),
            "prove_ms": round(prove_ms, 2),
            "total_ms": round(total_slice_ms, 2),
            "proof_size_bytes": proof_result["proof_size_bytes"],
            "peak_rss_mb": proof_result["peak_rss_mb"],
            "fault_injected": fault_injected,
        })

        print(f"  Slice {sid}: exec={exec_ms:.0f}ms prove={prove_ms:.0f}ms"
              + (" [FAULT]" if fault_injected else ""))

        # ── 5. P0-6 FIX: 下一片输入 = proof-bound 输出 ──
        #   Phase A 的正确语义：每片同步认证后，才把 proof 绑定的输出传给下一片。
        #   如果 Worker 篡改了 output_tensor，proof 里的 rescaled_outputs 不同，
        #   下一片会使用 proof 绑定的正确输出，篡改数据不会传播。
        #   这才是真正的 "每片先认证再传" 语义。
        proof_ppi = (proof_result.get("proof_data") or {}).get(
            "pretty_public_inputs", {})
        rescaled = proof_ppi.get("rescaled_outputs", [])
        if rescaled:
            proof_output = []
            for group in rescaled:
                if isinstance(group, list):
                    for v in group:
                        proof_output.append(float(v))
                else:
                    proof_output.append(float(group))
            if proof_output:
                current_input = proof_output
            else:
                current_input = output_tensor
        else:
            current_input = output_tensor

    exec_total_ms = (time.perf_counter() - pipeline_start) * 1000

    # ══════════════════════════════════════════════════════════
    # VERIFICATION PLANE — 独立验证 (不信任任何 Worker 声明)
    # ══════════════════════════════════════════════════════════
    print("\n[Verifier] Independent verification starting...")
    verify_start = time.perf_counter()

    chain_result = verify_chain(
        req_id, proof_jobs, artifacts,
        initial_input=initial_input,
        provisional_output=current_input,
    )

    verify_total_ms = (time.perf_counter() - verify_start) * 1000

    # 签发证书
    certificate = issue_certificate(chain_result, artifacts, verify_total_ms)

    total_ms = (time.perf_counter() - pipeline_start) * 1000

    print(f"\n[Verifier] Single proofs: {'ALL PASS' if chain_result.all_single_proofs_verified else 'FAILED'}")
    print(f"[Verifier] Commitment links: {'ALL PASS' if chain_result.all_links_verified else 'FAILED'}")
    if chain_result.link_failures:
        for lf in chain_result.link_failures:
            print(f"  LINK FAILURE: edge {lf['edge']} — {lf['reason']}")
    if chain_result.proof_failures:
        for pf in chain_result.proof_failures:
            print(f"  PROOF FAILURE: slice {pf['slice_id']} — {pf['reason']}")
    print(f"[Certificate] Status: {certificate.status}")
    print(f"  Total: {total_ms:.0f}ms (exec={exec_total_ms:.0f}ms, verify={verify_total_ms:.0f}ms)")

    # 汇总 metrics
    total_proof_ms = sum(m["prove_ms"] for m in per_slice_metrics)

    return {
        "req_id": req_id,
        "certificate": {
            "status": certificate.status,
            "all_single_proofs_verified": certificate.all_single_proofs_verified,
            "all_links_verified": certificate.all_links_verified,
            "final_output_commit": certificate.final_output_commit,
            "timestamp": certificate.timestamp,
            "model_digests": certificate.model_digests,
            "details": certificate.details,
        },
        "metrics": {
            "total_ms": round(total_ms, 2),
            "execution_ms": round(exec_total_ms, 2),
            "total_proof_gen_ms": round(total_proof_ms, 2),
            "verification_ms": round(verify_total_ms, 2),
            "per_slice": per_slice_metrics,
        },
        "provisional_output": current_input,
        "num_slices": num_slices,
        "fault_at": fault_at,
        "fault_type": fault_type if fault_at else None,
        "_proof_jobs": proof_jobs,  # exposed for F2 fidelity analysis
    }
