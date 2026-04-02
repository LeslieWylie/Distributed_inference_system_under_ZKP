"""
v2/verifier/bundle_verifier.py — 客户端独立验证入口。

消费 ProofBundle + Registry artifacts，返回最终可信 verdict。
不依赖 Coordinator 或 Worker 的任何声明。

信任根:
  - 本地 verifier 程序
  - Registry 工件 (vk/settings/srs/model_digest)
  - 密码学假设 (EZKL/Halo2 soundness)

Fail-closed 语义:
  - 任何结构错误、字段异常、版本不匹配均返回 status="invalid"
  - 不抛出未处理异常
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
from v2.common.registry_manifest import (
    build_client_registry_manifest,
    compute_registry_digest,
)
from v2.verifier.verify_chain import verify_chain, build_client_verification_result


SUPPORTED_BUNDLE_VERSIONS = {"1.0"}


def _fail(req_id: str, reason: str, t0: float) -> ClientVerificationResult:
    """Helper: return a fail-closed invalid result."""
    return ClientVerificationResult(
        req_id=req_id,
        status="invalid",
        all_single_proofs_verified=False,
        all_links_verified=False,
        failure_reasons=[{"reason": reason}],
        metrics={"verification_ms": round((time.perf_counter() - t0) * 1000, 2)},
    )


def _write_temp_proof(req_id: str, slice_id: int, proof_json: dict) -> str:
    """将 proof dict 写入临时文件，供 ezkl.verify() 使用。"""
    temp_dir = Path(tempfile.gettempdir()) / "zkp_bundle_verify" / req_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    proof_path = temp_dir / f"slice_{slice_id}_proof.json"
    proof_path.write_text(json.dumps(proof_json), encoding="utf-8")
    return str(proof_path)


def _validate_bundle_structure(
    bundle: ProofBundle,
    artifacts: list[SliceArtifact],
    t0: float,
) -> ClientVerificationResult | None:
    """Pre-validation: fail-closed on any structural anomaly.

    Returns None if all checks pass, or a ClientVerificationResult(invalid) otherwise.
    """
    req_id = bundle.req_id

    # 1. bundle_version
    if bundle.bundle_version not in SUPPORTED_BUNDLE_VERSIONS:
        return _fail(req_id, f"unsupported bundle_version: {bundle.bundle_version}", t0)

    # 2. slice_count consistency
    if bundle.slice_count != len(bundle.slices):
        return _fail(req_id,
            f"slice_count ({bundle.slice_count}) != actual slices ({len(bundle.slices)})", t0)

    # 3. slice_id strictly increasing + no duplicates
    slice_ids = [s.slice_id for s in bundle.slices]
    if len(slice_ids) != len(set(slice_ids)):
        return _fail(req_id, f"duplicate slice_ids in bundle: {slice_ids}", t0)
    if slice_ids != sorted(slice_ids):
        return _fail(req_id, f"slice_ids not in ascending order: {slice_ids}", t0)

    # 4. every bundle slice must have a matching registry artifact
    artifact_ids = {a.slice_id for a in artifacts}
    for sid in slice_ids:
        if sid not in artifact_ids:
            return _fail(req_id, f"slice {sid} not found in registry", t0)

    # 5. registry_digest must match canonical manifest
    manifest = build_client_registry_manifest(artifacts)
    expected_digest = compute_registry_digest(manifest)
    if bundle.registry_digest != expected_digest:
        return _fail(req_id,
            f"registry_digest mismatch: bundle={bundle.registry_digest[:16]}... "
            f"expected={expected_digest[:16]}...", t0)

    # 6. per-slice model_digest must match registry
    artifact_map = {a.slice_id: a for a in artifacts}
    for s in bundle.slices:
        expected_md = artifact_map[s.slice_id].model_digest
        if s.model_digest != expected_md:
            return _fail(req_id,
                f"model_digest mismatch at slice {s.slice_id}: "
                f"bundle={s.model_digest[:16]}... registry={expected_md[:16]}...", t0)

    return None  # all pre-checks passed


def verify_bundle(
    bundle: ProofBundle,
    artifacts: list[SliceArtifact],
) -> ClientVerificationResult:
    """
    客户端独立验证 ProofBundle（fail-closed）。

    流程:
      0. 结构预验证 (版本/切片数/排序/去重/registry_digest/model_digest)
      1. 按 slice_id 顺序逐片验证 proof
      2. 从 proof 公开实例提取 rescaled I/O
      3. 首端输入绑定检查
      4. 相邻切片 linking 检查
      5. 终端绑定检查
      6. 返回最终 verdict (唯一可信结果)

    任何异常均 fail-closed 到 status="invalid"，不抛出未处理异常。
    """
    t0 = time.perf_counter()
    req_id = getattr(bundle, "req_id", "unknown")

    try:
        # Step 0: structural pre-validation
        pre_fail = _validate_bundle_structure(bundle, artifacts, t0)
        if pre_fail is not None:
            return pre_fail

        artifact_map = {a.slice_id: a for a in artifacts}
        proof_jobs: list[ProofJob] = []

        for slice_item in bundle.slices:
            sid = slice_item.slice_id
            artifact = artifact_map[sid]

            # 将 proof dict 写入临时文件供 ezkl.verify() 使用
            proof_path = _write_temp_proof(req_id, sid, slice_item.proof_json)

            proof_jobs.append(ProofJob(
                job_id=f"bundle-{req_id}-{sid}",
                req_id=req_id,
                slice_id=sid,
                input_commit="bundle-input-unused",
                output_commit="bundle-output-unused",
                artifact=artifact,
                proof_path=proof_path,
                proof_data=slice_item.proof_json,
                status=ProofJobStatus.DONE,
            ))

        chain_result = verify_chain(
            req_id=req_id,
            proof_jobs=proof_jobs,
            artifacts=artifacts,
            initial_input=bundle.initial_input,
            provisional_output=bundle.claimed_final_output,
        )

        verify_ms = (time.perf_counter() - t0) * 1000
        return build_client_verification_result(chain_result, verify_ms)

    except Exception as e:
        # Fail-closed: any uncaught exception → invalid, not crash
        return _fail(req_id, f"verification error: {type(e).__name__}: {e}", t0)
