"""
v2/execution/deferred_pipeline.py — Phase B 执行-证明解耦 pipeline。

与 Phase A 的区别:
  - 在线阶段只做推理, 立即返回 provisional output (低延迟)
  - proving 在后台异步完成
  - 所有 proof 完成后, verifier 独立做全链路认证
  - 请求状态机: SUBMITTED → EXECUTING → EXECUTED_UNCERTIFIED → PROVING → VERIFYING → CERTIFIED/INVALID

核心思想:
  低延迟与立即最终可信不能同时免费获得。
  provisional output 是未认证的; certified output 才是最终可信的。
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
    RequestStatus,
)
from v2.common.commitments import compute_commitment
from v2.common.logging import log_event
from v2.prover.ezkl_adapter import prove_slice
from v2.prover.parallel import prove_slices_parallel
from v2.verifier.verify_chain import verify_chain, issue_certificate


# ---------------------------------------------------------------------------
# Phase B: 执行-证明解耦 pipeline
# ---------------------------------------------------------------------------

def run_deferred_pipeline(
    initial_input: list[float],
    artifacts: list[SliceArtifact],
    fault_at: int | None = None,
    fault_type: str = "tamper",
    max_prove_workers: int = 2,
) -> dict:
    """
    Phase B 执行-证明解耦 pipeline。

    阶段 1 (在线): 顺序执行所有切片推理, 返回 provisional output
    阶段 2 (后台): 并行生成所有切片 proof
    阶段 3 (验证): 独立验证全链路 + 签发证书

    参数:
      initial_input: 初始输入
      artifacts: 按 slice_id 排序的切片工件
      fault_at: 故障注入位置
      fault_type: 故障类型
      max_prove_workers: 后台 proving 并行度

    返回:
      包含 provisional_output, certificate, 各阶段时延的完整结果
    """
    req_id = f"req-{uuid.uuid4().hex[:8]}-{int(time.time() * 1000)}"
    artifacts = sorted(artifacts, key=lambda a: a.slice_id)
    num_slices = len(artifacts)

    print("=" * 60)
    print(f"[Deferred] Pipeline: {num_slices} slices, req_id={req_id}")
    print(f"  Fault: {f'type={fault_type} at slice {fault_at}' if fault_at else 'None'}")
    print(f"  Prove workers: {max_prove_workers}")
    print("=" * 60)

    total_start = time.perf_counter()

    # ══════════════════════════════════════════════════════════
    # STAGE 1: EXECUTION (在线关键路径)
    # ══════════════════════════════════════════════════════════
    exec_start = time.perf_counter()
    current_input = initial_input
    execution_records: list[ExecutionRecord] = []

    for artifact in artifacts:
        sid = artifact.slice_id
        t0 = time.perf_counter()

        input_commit = compute_commitment(
            req_id, sid, artifact.model_digest, current_input,
        )

        # ONNX 推理
        session = rt.InferenceSession(artifact.model_path)
        input_name = session.get_inputs()[0].name
        input_array = np.array([current_input], dtype=np.float32)
        ort_output = session.run(None, {input_name: input_array})
        output_tensor = ort_output[0].flatten().tolist()

        # 故障注入 (仅测试)
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

        output_commit = compute_commitment(
            req_id, sid, artifact.model_digest, output_tensor,
        )

        exec_ms = (time.perf_counter() - t0) * 1000

        execution_records.append(ExecutionRecord(
            req_id=req_id,
            slice_id=sid,
            input_commit=input_commit,
            output_commit=output_commit,
            output_tensor=output_tensor,
            input_tensor=list(current_input),
            exec_ms=round(exec_ms, 2),
        ))

        current_input = output_tensor

    provisional_output = current_input
    execution_ms = (time.perf_counter() - exec_start) * 1000
    print(f"  [Stage 1] Execution done: {execution_ms:.0f}ms → provisional output ready")
    log_event(req_id, "EXECUTED_UNCERTIFIED",
              execution_ms=round(execution_ms, 2), num_slices=num_slices)

    # ══════════════════════════════════════════════════════════
    # STAGE 2: PROVING (后台, 可并行)
    # ══════════════════════════════════════════════════════════
    prove_start = time.perf_counter()
    proof_jobs: list[ProofJob] = []

    # 准备 proving 任务
    prove_tasks = []
    for rec, artifact in zip(execution_records, artifacts):
        work_dir = os.path.join(
            os.path.dirname(artifact.compiled_path),
            "proofs", req_id,
        )
        prove_tasks.append({
            "slice_id": artifact.slice_id,
            "input_tensor": rec.input_tensor,
            "compiled_path": artifact.compiled_path,
            "pk_path": artifact.pk_path,
            "srs_path": artifact.srs_path,
            "work_dir": work_dir,
            "tag": f"slice_{artifact.slice_id}",
            "artifact": artifact,
            "input_commit": rec.input_commit,
            "output_commit": rec.output_commit,
        })

    # 并行 proving (子进程级真正 CPU 并行)
    prove_results_raw = prove_slices_parallel(prove_tasks, max_workers=max_prove_workers)

    # 读取 proof 文件以获取 proof_data（chain verify 需要 pretty_public_inputs）
    prove_results = {}
    for sid, raw in prove_results_raw.items():
        if raw.get("success"):
            # 从 prove_worker 结果中重建完整信息
            proof_path = raw.get("proof_path")
            proof_data = None
            if proof_path and os.path.exists(proof_path):
                import json as _json
                with open(proof_path, "r") as f:
                    proof_data = _json.load(f)
            prove_results[sid] = {
                "proof_path": proof_path,
                "witness_path": raw.get("witness_path"),
                "proof_data": proof_data,
                "proof_gen_ms": raw.get("proof_gen_ms", 0),
                "proof_size_bytes": raw.get("proof_size_bytes", 0),
                "peak_rss_mb": raw.get("peak_rss_mb", 0),
                "subprocess_wall_ms": raw.get("subprocess_wall_ms", 0),
            }
            print(f"    Slice {sid}: prove={raw.get('proof_gen_ms', 0):.0f}ms "
                  f"(wall={raw.get('subprocess_wall_ms', 0):.0f}ms) ✓")
        else:
            prove_results[sid] = {"error": raw.get("error", "unknown")}
            print(f"    Slice {sid}: prove FAILED — {raw.get('error')}")

    # 按 slice_id 顺序组装 ProofJob
    per_slice_metrics = []
    for task in prove_tasks:
        sid = task["slice_id"]
        pr = prove_results.get(sid, {})
        error = pr.get("error")

        job = ProofJob(
            job_id=f"job-{sid}-{uuid.uuid4().hex[:8]}",
            req_id=req_id,
            slice_id=sid,
            input_commit=task["input_commit"],
            output_commit=task["output_commit"],
            artifact=task["artifact"],
            witness_path=pr.get("witness_path"),
            proof_path=pr.get("proof_path"),
            status=ProofJobStatus.DONE if not error else ProofJobStatus.FAILED,
            proof_gen_ms=pr.get("proof_gen_ms", 0),
            proof_data=pr.get("proof_data"),
            error=error,
        )
        proof_jobs.append(job)

        per_slice_metrics.append({
            "slice_id": sid,
            "exec_ms": execution_records[sid - 1].exec_ms,
            "prove_ms": pr.get("proof_gen_ms", 0),
            "proof_size_bytes": pr.get("proof_size_bytes", 0),
            "peak_rss_mb": pr.get("peak_rss_mb", 0),
            "fault_injected": (fault_at == sid),
            "prove_error": error,
        })

    proving_ms = (time.perf_counter() - prove_start) * 1000
    print(f"  [Stage 2] Proving done: {proving_ms:.0f}ms (parallel, {max_prove_workers} workers)")
    log_event(req_id, "PROVING_DONE",
              proving_ms=round(proving_ms, 2), workers=max_prove_workers)

    # ══════════════════════════════════════════════════════════
    # STAGE 3: VERIFICATION (独立验证)
    # ══════════════════════════════════════════════════════════
    verify_start = time.perf_counter()

    chain_result = verify_chain(
        req_id, proof_jobs, artifacts,
        provisional_output=provisional_output,
    )

    verify_ms = (time.perf_counter() - verify_start) * 1000
    certificate = issue_certificate(chain_result, artifacts, verify_ms)

    total_ms = (time.perf_counter() - total_start) * 1000

    # 认证延迟 = proving + verification (不含 execution)
    certification_ms = proving_ms + verify_ms

    print(f"  [Stage 3] Verification done: {verify_ms:.0f}ms")
    print(f"\n[Verifier] Proofs: {'ALL PASS' if chain_result.all_single_proofs_verified else 'FAILED'}")
    print(f"[Verifier] Links: {'ALL PASS' if chain_result.all_links_verified else 'FAILED'}")
    if chain_result.link_failures:
        for lf in chain_result.link_failures:
            print(f"  LINK FAILURE: {lf['edge']} — {lf['reason']}")
    print(f"[Certificate] Status: {certificate.status}")
    print(f"  Execution (provisional): {execution_ms:.0f}ms")
    print(f"  Certification (prove+verify): {certification_ms:.0f}ms")
    print(f"  Total: {total_ms:.0f}ms")
    log_event(req_id, certificate.status.upper(),
              total_ms=round(total_ms, 2),
              execution_ms=round(execution_ms, 2),
              certification_ms=round(certification_ms, 2),
              all_proofs_ok=chain_result.all_single_proofs_verified,
              all_links_ok=chain_result.all_links_verified)

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
            "execution_ms": round(execution_ms, 2),
            "provisional_latency_ms": round(execution_ms, 2),
            "proving_ms": round(proving_ms, 2),
            "verification_ms": round(verify_ms, 2),
            "certification_ms": round(certification_ms, 2),
            "total_proof_gen_ms": round(total_proof_ms, 2),
            "prove_parallelism": max_prove_workers,
            "per_slice": per_slice_metrics,
        },
        "provisional_output": provisional_output,
        "num_slices": num_slices,
        "fault_at": fault_at,
        "fault_type": fault_type if fault_at else None,
    }
