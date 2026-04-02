"""
v2/common/types.py — 核心数据结构定义。

所有模块共用的类型，保证协议语义一致。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 请求状态机
# ---------------------------------------------------------------------------

class RequestStatus(enum.Enum):
    SUBMITTED = "submitted"
    EXECUTING = "executing"
    EXECUTED_UNCERTIFIED = "executed_uncertified"
    PROVING = "proving"
    VERIFYING = "verifying"
    CERTIFIED = "certified"
    INVALID = "invalid"


# ---------------------------------------------------------------------------
# 切片静态工件注册信息
# ---------------------------------------------------------------------------

@dataclass
class SliceArtifact:
    slice_id: int
    model_path: str          # ONNX 路径
    compiled_path: str       # 编译后电路
    settings_path: str       # EZKL settings
    pk_path: str
    vk_path: str
    srs_path: str
    model_digest: str        # SHA-256(ONNX file)
    input_scale: int | None = None
    output_scale: int | None = None
    param_scale: int | None = None


# ---------------------------------------------------------------------------
# 执行记录
# ---------------------------------------------------------------------------

@dataclass
class ExecutionRecord:
    req_id: str
    slice_id: int
    input_commit: str
    output_commit: str
    output_tensor: list[float]
    input_tensor: list[float]
    started_at: float = 0.0
    finished_at: float = 0.0
    exec_ms: float = 0.0


# ---------------------------------------------------------------------------
# Proof 作业
# ---------------------------------------------------------------------------

class ProofJobStatus(enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class ProofJob:
    job_id: str
    req_id: str
    slice_id: int
    input_commit: str
    output_commit: str
    artifact: SliceArtifact
    witness_path: str | None = None
    proof_path: str | None = None
    status: ProofJobStatus = ProofJobStatus.QUEUED
    error: str | None = None
    proof_gen_ms: float = 0.0
    proof_data: dict | None = None


# ---------------------------------------------------------------------------
# 单片验证结果
# ---------------------------------------------------------------------------

@dataclass
class SingleVerifyResult:
    slice_id: int
    verified: bool
    input_commit_from_proof: str | None = None
    output_commit_from_proof: str | None = None
    error: str | None = None
    verify_ms: float = 0.0


# ---------------------------------------------------------------------------
# 链路验证结果
# ---------------------------------------------------------------------------

@dataclass
class ChainVerifyResult:
    req_id: str
    all_single_proofs_verified: bool
    all_links_verified: bool
    status: RequestStatus = RequestStatus.INVALID
    link_failures: list[dict] = field(default_factory=list)
    proof_failures: list[dict] = field(default_factory=list)
    final_output_commit: str | None = None


# ---------------------------------------------------------------------------
# 最终证书 (服务端 advisory，非客户端信任来源)
# ---------------------------------------------------------------------------

@dataclass
class Certificate:
    """Server-side advisory result only. Not a trust root for clients.
    客户端最终可信判断由 ClientVerificationResult 提供。"""
    req_id: str
    status: str                   # "certified" | "invalid"
    slice_count: int
    final_output_commit: str
    all_single_proofs_verified: bool
    all_links_verified: bool
    timestamp: str = ""
    model_digests: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Proof Bundle — Coordinator 返回给客户端的主产物
# ---------------------------------------------------------------------------

@dataclass
class ProofBundleSlice:
    """Bundle 中的单片证据。"""
    slice_id: int
    model_digest: str
    proof_json: dict
    worker_claimed_output: list[float] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProofBundle:
    """Coordinator 返回的主产物。客户端独立验证此 bundle。"""
    bundle_version: str
    req_id: str
    created_at: str
    model_id: str
    registry_digest: str
    slice_count: int
    initial_input: list[float]
    claimed_final_output: list[float]
    slices: list[ProofBundleSlice] = field(default_factory=list)
    server_side_advisory: dict[str, Any] = field(default_factory=dict)

    def strip_intermediate_values(self) -> "ProofBundle":
        """
        返回隐私过滤后的 ProofBundle 副本。

        过滤规则:
          - 每片 proof_json 中的 pretty_public_inputs 被清除
            (rescaled_inputs / rescaled_outputs / processed_inputs / processed_outputs)
          - 首片 initial_input 保留 (输入绑定需要)
          - 末片 claimed_final_output 保留 (终端绑定需要)
          - 各片的 worker_claimed_output 被清除 (中间激活不暴露)
          - proof 的密码学部分 (hex proof bytes, instances) 保留不动

        用途: 在不需要中间激活可见性的场景下,
              验证方仍可通过 ezkl.verify() 验证密码学有效性,
              通过 scale 对齐后的 rescaled 值做链接。
        """
        import copy
        bundle = copy.deepcopy(self)
        for i, s in enumerate(bundle.slices):
            # 清除 proof_json 中的可读激活值
            ppi = s.proof_json.get("pretty_public_inputs", {})
            first_slice = (i == 0)
            last_slice = (i == len(bundle.slices) - 1)
            if not first_slice:
                ppi.pop("rescaled_inputs", None)
            if not last_slice:
                ppi.pop("rescaled_outputs", None)
            # 清除所有中间片的 worker_claimed_output
            if not last_slice:
                s.worker_claimed_output = []
        return bundle


# ---------------------------------------------------------------------------
# 客户端独立验证结果 — 唯一最终可信判断
# ---------------------------------------------------------------------------

@dataclass
class ClientVerificationResult:
    """客户端本地验证结果，是系统唯一的最终可信判断。"""
    req_id: str
    status: str                   # "certified" | "invalid"
    all_single_proofs_verified: bool
    all_links_verified: bool
    final_output_commit: str | None = None
    failure_reasons: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
