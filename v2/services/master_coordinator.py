"""
v2/services/master_coordinator.py — 分布式 Master 协调器。

真正的分布式运行时：通过 HTTP 调用多个 Execution Worker，
收集输出后提交 proving jobs，最后独立验证全链路。

与 v2/execution/pipeline.py 的区别:
  pipeline.py 是本地函数调用级 (单进程 for-loop)
  本模块是 HTTP 请求级 (真正多节点通信)

用法:
    python -m v2.services.master_coordinator --config workers.json
"""

import json
import os
import sys
import time
import uuid

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from v2.common.types import (
    SliceArtifact, ExecutionRecord, ProofJob, ProofJobStatus,
)
from v2.common.commitments import compute_commitment
from v2.common.logging import log_event
from v2.prover.ezkl_adapter import prove_slice
from v2.prover.parallel import prove_slices_parallel
from v2.verifier.verify_chain import verify_chain, issue_certificate


def run_distributed_pipeline(
    initial_input: list[float],
    artifacts: list[SliceArtifact],
    worker_urls: list[dict],
    fault_at: int | None = None,
    fault_type: str = "tamper",
    max_prove_workers: int = 2,
) -> dict:
    """
    分布式 deferred certification pipeline。

    Stage 1: 通过 HTTP 顺序调用 Execution Workers (真正分布式)
    Stage 2: 后台并行 proving (子进程)
    Stage 3: 独立验证全链路

    参数:
      initial_input: 初始输入
      artifacts: 切片工件 (按 slice_id 排序)
      worker_urls: [{"slice_id": 1, "url": "http://127.0.0.1:9001"}, ...]
      fault_at/fault_type: 故障注入
      max_prove_workers: proving 并行度
    """
    req_id = f"req-{uuid.uuid4().hex[:8]}-{int(time.time() * 1000)}"
    artifacts = sorted(artifacts, key=lambda a: a.slice_id)
    num_slices = len(artifacts)

    print("=" * 60)
    print(f"[Distributed] Pipeline: {num_slices} slices, req_id={req_id}")
    print(f"  Workers: {[w['url'] for w in worker_urls]}")
    print(f"  Fault: {f'type={fault_type} at slice {fault_at}' if fault_at else 'None'}")
    print("=" * 60)

    total_start = time.perf_counter()
    log_event(req_id, "SUBMITTED", num_slices=num_slices)

    # ══════════════════════════════════════════════════════════
    # STAGE 1: DISTRIBUTED EXECUTION (HTTP 调用真实 Worker)
    # ══════════════════════════════════════════════════════════
    exec_start = time.perf_counter()
    current_input = initial_input
    execution_records: list[ExecutionRecord] = []

    for artifact, worker in zip(artifacts, worker_urls):
        sid = artifact.slice_id
        url = worker["url"]
        inject = (fault_at == sid)
        ft = fault_type if inject else "none"

        t0 = time.perf_counter()

        # HTTP POST 到远程 Worker
        resp = requests.post(
            f"{url}/execute",
            json={"req_id": req_id, "input_tensor": current_input},
            params={"fault_type": ft},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        rtt_ms = (time.perf_counter() - t0) * 1000
        output_tensor = data["output_tensor"]

        # 审计 commitment (仅日志，安全绑定来自 proof 公开实例)
        input_commit = compute_commitment(
            req_id, sid, artifact.model_digest, current_input,
        )
        output_commit = compute_commitment(
            req_id, sid, artifact.model_digest, output_tensor,
        )

        execution_records.append(ExecutionRecord(
            req_id=req_id,
            slice_id=sid,
            input_commit=input_commit,
            output_commit=output_commit,
            output_tensor=output_tensor,
            input_tensor=list(current_input),
            exec_ms=round(rtt_ms, 2),
        ))

        print(f"  Worker {sid} ({url}): {rtt_ms:.0f}ms"
              + (" [FAULT]" if data.get("fault_injected") else ""))

        current_input = output_tensor

    provisional_output = current_input
    execution_ms = (time.perf_counter() - exec_start) * 1000
    print(f"  [Stage 1] Distributed execution done: {execution_ms:.0f}ms")
    log_event(req_id, "EXECUTED_UNCERTIFIED", execution_ms=round(execution_ms, 2))

    # ══════════════════════════════════════════════════════════
    # STAGE 2: PROVING (后台并行子进程)
    # ══════════════════════════════════════════════════════════
    prove_start = time.perf_counter()

    prove_tasks = []
    for rec, artifact in zip(execution_records, artifacts):
        work_dir = os.path.join(
            os.path.dirname(artifact.compiled_path), "proofs", req_id,
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

    prove_results_raw = prove_slices_parallel(prove_tasks, max_workers=max_prove_workers)

    proof_jobs: list[ProofJob] = []
    per_slice_metrics = []
    for task in prove_tasks:
        sid = task["slice_id"]
        raw = prove_results_raw.get(sid, {})
        error = raw.get("error") if not raw.get("success", True) else None

        proof_data = None
        proof_path = raw.get("proof_path")
        if proof_path and os.path.exists(proof_path):
            with open(proof_path, "r") as f:
                proof_data = json.load(f)

        job = ProofJob(
            job_id=f"job-{sid}-{uuid.uuid4().hex[:8]}",
            req_id=req_id,
            slice_id=sid,
            input_commit=task["input_commit"],
            output_commit=task["output_commit"],
            artifact=task["artifact"],
            witness_path=raw.get("witness_path"),
            proof_path=proof_path,
            status=ProofJobStatus.DONE if not error else ProofJobStatus.FAILED,
            proof_gen_ms=raw.get("proof_gen_ms", 0),
            proof_data=proof_data,
            error=error,
        )
        proof_jobs.append(job)

        per_slice_metrics.append({
            "slice_id": sid,
            "exec_ms": execution_records[sid - 1].exec_ms,
            "prove_ms": raw.get("proof_gen_ms", 0),
            "proof_size_bytes": raw.get("proof_size_bytes", 0),
            "peak_rss_mb": raw.get("peak_rss_mb", 0),
            "fault_injected": (fault_at == sid),
        })

        if not error:
            print(f"    Slice {sid}: prove={raw.get('proof_gen_ms', 0):.0f}ms ✓")
        else:
            print(f"    Slice {sid}: prove FAILED — {error}")

    proving_ms = (time.perf_counter() - prove_start) * 1000
    print(f"  [Stage 2] Proving done: {proving_ms:.0f}ms")
    log_event(req_id, "PROVING_DONE", proving_ms=round(proving_ms, 2))

    # ══════════════════════════════════════════════════════════
    # STAGE 3: INDEPENDENT VERIFICATION
    # ══════════════════════════════════════════════════════════
    verify_start = time.perf_counter()
    chain_result = verify_chain(
        req_id, proof_jobs, artifacts,
        initial_input=initial_input,
        provisional_output=provisional_output,
    )
    verify_ms = (time.perf_counter() - verify_start) * 1000
    certificate = issue_certificate(chain_result, artifacts, verify_ms)

    total_ms = (time.perf_counter() - total_start) * 1000
    certification_ms = proving_ms + verify_ms

    print(f"  [Stage 3] Verification done: {verify_ms:.0f}ms")
    print(f"\n[Verifier] Proofs: {'ALL PASS' if chain_result.all_single_proofs_verified else 'FAILED'}")
    print(f"[Verifier] Links: {'ALL PASS' if chain_result.all_links_verified else 'FAILED'}")
    if chain_result.link_failures:
        for lf in chain_result.link_failures:
            print(f"  LINK FAILURE: {lf['edge']} — {lf['reason']}")
    print(f"[Certificate] Status: {certificate.status}")
    print(f"  Execution (distributed): {execution_ms:.0f}ms")
    print(f"  Certification: {certification_ms:.0f}ms")
    print(f"  Total: {total_ms:.0f}ms")

    log_event(req_id, certificate.status.upper(),
              total_ms=round(total_ms, 2),
              execution_ms=round(execution_ms, 2),
              certification_ms=round(certification_ms, 2))

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
        "distributed": True,
    }
