"""
v2/verifier/verify_single.py — 独立验证单片 proof。

Verifier 独立于 Worker 运行：
  - 从 registry 获取 vk/settings/srs
  - 用 ezkl.verify() 验证 proof
  - 提取 proof 中的 commitments
  - 不信任 Worker 自报的任何结果
"""

import json
import time
from pathlib import Path
import os

os.environ.setdefault("HOME", str(Path.home()))
os.environ.setdefault("EZKL_REPO_PATH", os.path.join(str(Path.home()), ".ezkl"))

import ezkl

from v2.common.types import SliceArtifact, SingleVerifyResult


def verify_proof(
    proof_path: str,
    artifact: SliceArtifact,
) -> SingleVerifyResult:
    """
    独立验证单片 proof。

    只依赖 registry 中的 vk/settings/srs，不信任 Worker 端的任何声明。
    同时提取 proof 中的 processed_inputs/processed_outputs 作为 commitments。
    """
    t0 = time.perf_counter()
    try:
        verified = bool(ezkl.verify(
            proof_path,
            artifact.settings_path,
            artifact.vk_path,
            srs_path=artifact.srs_path,
        ))
    except Exception as e:
        return SingleVerifyResult(
            slice_id=artifact.slice_id,
            verified=False,
            error=str(e),
            verify_ms=round((time.perf_counter() - t0) * 1000, 2),
        )
    verify_ms = round((time.perf_counter() - t0) * 1000, 2)

    # 提取 proof 中的公开实例
    # public 模式: 使用 rescaled_inputs/outputs (浮点空间, 受 proof soundness 绑定)
    # 这些值已被 ezkl.verify() 验证 — 无法伪造
    input_commit_from_proof = None
    output_commit_from_proof = None
    try:
        with open(proof_path, "r") as f:
            proof_data = json.load(f)
        ppi = proof_data.get("pretty_public_inputs", {})
        # 优先用 rescaled (float-space), 其次用 processed (quantized)
        ri = ppi.get("rescaled_inputs") or ppi.get("processed_inputs", [])
        ro = ppi.get("rescaled_outputs") or ppi.get("processed_outputs", [])
        if ri:
            input_commit_from_proof = json.dumps(ri, sort_keys=True)
        if ro:
            output_commit_from_proof = json.dumps(ro, sort_keys=True)
    except Exception:
        pass

    return SingleVerifyResult(
        slice_id=artifact.slice_id,
        verified=verified,
        input_commit_from_proof=input_commit_from_proof,
        output_commit_from_proof=output_commit_from_proof,
        verify_ms=verify_ms,
    )
