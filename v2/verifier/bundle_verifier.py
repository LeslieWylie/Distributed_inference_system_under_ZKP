"""
v2/verifier/bundle_verifier.py — 客户端独立验证入口。

消费 ProofBundle + Registry artifacts，返回最终可信 verdict。
不依赖 Coordinator 或 Worker 的任何声明。

信任根:
  - 本地 verifier 程序
  - Registry 工件 (vk/settings/srs/model_digest)
  - 密码学假设 (EZKL/Halo2 soundness)
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from v2.common.types import (
    ProofBundle,
    ProofJob,
    ProofJobStatus,
    SliceArtifact,
    ClientVerificationResult,
)
from v2.verifier.verify_chain import verify_chain, build_client_verification_result


def _write_temp_proof(req_id: str, slice_id: int, proof_json: dict) -> str:
    """将 proof dict 写入临时文件，供 ezkl.verify() 使用。"""
    temp_dir = Path(tempfile.gettempdir()) / "zkp_bundle_verify" / req_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    proof_path = temp_dir / f"slice_{slice_id}_proof.json"
    proof_path.write_text(json.dumps(proof_json), encoding="utf-8")
    return str(proof_path)


def verify_bundle(
    bundle: ProofBundle,
    artifacts: list[SliceArtifact],
) -> ClientVerificationResult:
    """
    客户端独立验证 ProofBundle。

    流程:
      1. 按 slice_id 顺序逐片验证 proof
      2. 从 proof 公开实例提取 rescaled I/O
      3. 首端输入绑定检查
      4. 相邻切片 linking 检查
      5. 终端绑定检查
      6. 返回最终 verdict (唯一可信结果)

    参数:
      bundle: Coordinator 返回的 ProofBundle
      artifacts: 本地 registry 中加载的 SliceArtifact 列表

    返回:
      ClientVerificationResult — 客户端唯一可信判断
    """
    t0 = time.perf_counter()
    artifact_map = {a.slice_id: a for a in artifacts}
    proof_jobs: list[ProofJob] = []

    for slice_item in bundle.slices:
        sid = slice_item.slice_id
        artifact = artifact_map.get(sid)
        if artifact is None:
            return ClientVerificationResult(
                req_id=bundle.req_id,
                status="invalid",
                all_single_proofs_verified=False,
                all_links_verified=False,
                failure_reasons=[{"reason": f"slice {sid} not in registry"}],
                metrics={"verification_ms": 0},
            )

        # 将 proof dict 写入临时文件供 ezkl.verify() 使用
        proof_path = _write_temp_proof(bundle.req_id, sid, slice_item.proof_json)

        proof_jobs.append(ProofJob(
            job_id=f"bundle-{bundle.req_id}-{sid}",
            req_id=bundle.req_id,
            slice_id=sid,
            input_commit="bundle-input-unused",
            output_commit="bundle-output-unused",
            artifact=artifact,
            proof_path=proof_path,
            proof_data=slice_item.proof_json,
            status=ProofJobStatus.DONE,
        ))

    chain_result = verify_chain(
        req_id=bundle.req_id,
        proof_jobs=proof_jobs,
        artifacts=artifacts,
        initial_input=bundle.initial_input,
        provisional_output=bundle.claimed_final_output,
    )

    verify_ms = (time.perf_counter() - t0) * 1000
    return build_client_verification_result(chain_result, verify_ms)
