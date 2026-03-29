"""
v2/services/distributed_coordinator.py — 不可信 Coordinator (bundle 组装层)。

Coordinator 职责:
  - 请求编排: 按序调用 Prover-Workers
  - Proof 收集: 从 Worker 接收 (output, proof)
  - Bundle 组装: 打包为 ProofBundle 返回给客户端
  - 可选生成 server-side advisory (非信任来源)

Coordinator 默认不被信任。客户端使用本地 verifier 独立验证 bundle。
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from v2.common.types import (
    SliceArtifact, ExecutionRecord, ProofJob, ProofJobStatus,
    ProofBundle, ProofBundleSlice,
)
from v2.common.commitments import compute_commitment
from v2.common.logging import log_event
from v2.compile.build_circuits import compute_registry_digest
from v2.verifier.verify_chain import verify_chain, issue_certificate


def run_distributed_pipeline(
    initial_input: list[float],
    artifacts: list[SliceArtifact],
    worker_urls: list[dict],
    fault_at: int | None = None,
    fault_type: str = "tamper",
) -> dict:
    """
    分布式 deferred certification pipeline (重构版)。

    Stage 1: 通过 HTTP 顺序调用 Prover-Workers
             每个 Worker 执行推理 + 生成 proof, 返回 (output, proof)
    Stage 2: 独立验证全链路 (Verifier 从 proof 提取 I/O)

    注意: 不再有 "Stage 2: Proving" — 因为 proving 已在 Worker 端完成。
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
    # STAGE 1: DISTRIBUTED EXECUTION + PROVING (在 Worker 端)
    # ══════════════════════════════════════════════════════════
    exec_start = time.perf_counter()
    current_input = initial_input
    execution_records: list[ExecutionRecord] = []
    proof_data_list: list[dict | None] = []
    per_slice_metrics = []

    for artifact, worker in zip(artifacts, worker_urls):
        sid = artifact.slice_id
        url = worker["url"]
        inject = (fault_at == sid)
        ft = fault_type if inject else "none"

        t0 = time.perf_counter()

        # HTTP POST 到 Prover-Worker: /infer_and_prove
        resp = requests.post(
            f"{url}/infer_and_prove",
            json={"req_id": req_id, "input_tensor": current_input},
            params={"fault_type": ft},
            timeout=600,  # proving 可能需要较长时间
        )
        resp.raise_for_status()
        data = resp.json()

        rtt_ms = (time.perf_counter() - t0) * 1000
        output_tensor = data["output_tensor"]
        proof_json = data.get("proof_json")

        # 审计 commitment (仅日志)
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
            exec_ms=round(data.get("exec_ms", 0), 2),
        ))
        proof_data_list.append(proof_json)

        per_slice_metrics.append({
            "slice_id": sid,
            "exec_ms": data.get("exec_ms", 0),
            "prove_ms": data.get("prove_ms", 0),
            "worker_total_ms": data.get("total_ms", 0),
            "rtt_ms": round(rtt_ms, 2),
            "fault_injected": data.get("fault_injected", False),
        })

        print(f"  Worker {sid} ({url}): exec={data.get('exec_ms', 0):.0f}ms "
              f"prove={data.get('prove_ms', 0):.0f}ms "
              f"total={rtt_ms:.0f}ms"
              + (" [FAULT]" if data.get("fault_injected") else " ✓"))

        current_input = output_tensor

    provisional_output = current_input
    execution_ms = (time.perf_counter() - exec_start) * 1000
    print(f"  [Stage 1] Distributed exec+prove done: {execution_ms:.0f}ms")
    log_event(req_id, "EXECUTED_AND_PROVED", execution_ms=round(execution_ms, 2))

    # ══════════════════════════════════════════════════════════
    # STAGE 2: SAVE PROOFS + BUILD ProofJobs
    # ══════════════════════════════════════════════════════════
    proof_jobs: list[ProofJob] = []
    proofs_dir = os.path.join(PROJECT_ROOT, "v2", "artifacts", "received_proofs", req_id)
    os.makedirs(proofs_dir, exist_ok=True)

    for i, (rec, artifact) in enumerate(zip(execution_records, artifacts)):
        sid = artifact.slice_id
        proof_data = proof_data_list[i]

        proof_path = None
        error = None
        if proof_data:
            # 将 Worker 返回的 proof 保存到本地文件 (Verifier 需要文件路径)
            proof_path = os.path.join(proofs_dir, f"slice_{sid}_proof.json")
            with open(proof_path, "w") as f:
                json.dump(proof_data, f)
        else:
            error = "no proof received from worker"

        job = ProofJob(
            job_id=f"job-{sid}-{uuid.uuid4().hex[:8]}",
            req_id=req_id,
            slice_id=sid,
            input_commit=rec.input_commit,
            output_commit=rec.output_commit,
            artifact=artifact,
            proof_path=proof_path,
            proof_data=proof_data,
            status=ProofJobStatus.DONE if not error else ProofJobStatus.FAILED,
            error=error,
        )
        proof_jobs.append(job)

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

    print(f"  [Stage 2] Verification done: {verify_ms:.0f}ms")
    print(f"\n[Verifier] Proofs: {'ALL PASS' if chain_result.all_single_proofs_verified else 'FAILED'}")
    print(f"[Verifier] Links: {'ALL PASS' if chain_result.all_links_verified else 'FAILED'}")
    if chain_result.link_failures:
        for lf in chain_result.link_failures:
            print(f"  LINK FAILURE: {lf['edge']} — {lf['reason']}")
    print(f"[Certificate] Status: {certificate.status}")
    print(f"  Total: {total_ms:.0f}ms (exec+prove={execution_ms:.0f}ms verify={verify_ms:.0f}ms)")

    log_event(req_id, certificate.status.upper(),
              total_ms=round(total_ms, 2),
              execution_ms=round(execution_ms, 2),
              verify_ms=round(verify_ms, 2))

    total_prove_ms = sum(m["prove_ms"] for m in per_slice_metrics)
    total_exec_ms = sum(m["exec_ms"] for m in per_slice_metrics)

    # ══════════════════════════════════════════════════════════
    # BUILD ProofBundle — Coordinator 的主产物
    # ══════════════════════════════════════════════════════════
    bundle_slices = []
    for i, (artifact, proof_data) in enumerate(zip(artifacts, proof_data_list)):
        bundle_slices.append(ProofBundleSlice(
            slice_id=artifact.slice_id,
            model_digest=artifact.model_digest,
            proof_json=proof_data or {},
            worker_claimed_output=execution_records[i].output_tensor,
            metrics=per_slice_metrics[i],
        ))

    registry_data = [
        {
            "slice_id": a.slice_id,
            "model_digest": a.model_digest,
            "vk_path": a.vk_path,
            "settings_path": a.settings_path,
            "srs_path": a.srs_path,
        }
        for a in artifacts
    ]

    bundle = ProofBundle(
        bundle_version="1.0",
        req_id=req_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        model_id="mnist_mlp",
        registry_digest=compute_registry_digest(registry_data),
        slice_count=num_slices,
        initial_input=list(initial_input),
        claimed_final_output=list(provisional_output),
        slices=bundle_slices,
        server_side_advisory={
            "status": certificate.status,
            "all_single_proofs_verified": certificate.all_single_proofs_verified,
            "all_links_verified": certificate.all_links_verified,
            "note": "non-authoritative advisory only",
        },
    )

    return {
        "req_id": req_id,
        "proof_bundle": bundle,
        "server_side_advisory": bundle.server_side_advisory,
        "metrics": {
            "total_ms": round(total_ms, 2),
            "execution_ms": round(execution_ms, 2),
            "total_exec_ms": round(total_exec_ms, 2),
            "total_prove_ms": round(total_prove_ms, 2),
            "verification_ms": round(verify_ms, 2),
            "num_slices": num_slices,
            "per_slice": per_slice_metrics,
            "architecture": "prover_worker",
        },
    }
