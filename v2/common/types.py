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
