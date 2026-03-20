"""
v2/common/commitments.py — 统一承诺计算模块。

所有 commitment 必须经过此模块生成，保证域分离一致性。
commitment = SHA-256(req_id || slice_id || model_digest || tensor_json)

第一版使用 SHA-256；接口预留未来切换到 Poseidon / polycommit。
"""

import hashlib
import json


def compute_commitment(
    req_id: str,
    slice_id: int,
    model_digest: str,
    tensor: list[float],
) -> str:
    """
    计算带域分离的 commitment。

    域分离字段: req_id, slice_id, model_digest
    目的:
      - 防止跨请求 replay
      - 防止不同切片之间误拼接
      - 防止不同模型版本之间复用旧 commitment
    """
    payload = json.dumps({
        "req_id": req_id,
        "slice_id": slice_id,
        "model_digest": model_digest,
        "tensor": tensor,
    }, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_tensor_digest(tensor: list[float]) -> str:
    """计算张量本身的 SHA-256 摘要（不含域分离信息）。"""
    serialized = json.dumps(tensor, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def compute_file_digest(file_path: str) -> str:
    """计算文件的 SHA-256 摘要，用于 model_digest。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
