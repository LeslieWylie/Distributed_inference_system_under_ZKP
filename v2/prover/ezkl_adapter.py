"""
v2/prover/ezkl_adapter.py — EZKL proving 适配器。

职责：
  - gen_witness
  - prove (仅生成 proof，不做 verify)
  - 提取 proof 公开实例中的 commitments

不包含 verify 逻辑 — verify 由 verifier 模块独立完成。
"""

import json
import os
import time
from pathlib import Path

# Windows / EZKL 环境修复
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
USER_HOME = str(Path.home())
os.environ.setdefault("HOME", USER_HOME)
os.environ.setdefault("EZKL_REPO_PATH", os.path.join(USER_HOME, ".ezkl"))

import ezkl
import psutil


def get_memory_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _flatten_float_values(data) -> list[float]:
    values: list[float] = []
    if isinstance(data, list):
        for item in data:
            values.extend(_flatten_float_values(item))
        return values
    return [float(data)]


def _extract_float_field(payload: dict | None, field: str) -> list[float]:
    if not isinstance(payload, dict):
        return []
    values = payload.get(field, [])
    if not values:
        return []
    return _flatten_float_values(values)


def extract_proof_bound_tensor(
    proof_data: dict | None,
    witness_path: str | None,
    field: str,
) -> list[float]:
    """Read proof-bound tensors from proof JSON, falling back to witness JSON.

    public visibility exposes `pretty_public_inputs` directly in the proof.
    hidden visibility modes omit those values from the proof, so the prover side
    must recover the same canonical tensor from the generated witness.
    """
    proof_values = _extract_float_field(
        (proof_data or {}).get("pretty_public_inputs", {}),
        field,
    )
    if proof_values:
        return proof_values

    if not witness_path:
        return []

    try:
        with open(witness_path, "r", encoding="utf-8") as handle:
            witness_data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []

    witness_values = _extract_float_field(
        witness_data.get("pretty_elements", {}),
        field,
    )
    if witness_values:
        return witness_values

    return _extract_float_field(witness_data, field)


def write_input_json(data_list: list, path: str):
    """将 flat 数据列表写成 EZKL 输入格式。"""
    with open(path, "w") as f:
        json.dump({"input_data": [data_list]}, f)


def prove_slice(
    input_tensor: list[float],
    compiled_path: str,
    pk_path: str,
    srs_path: str,
    work_dir: str,
    tag: str = "proof",
) -> dict:
    """
    为单个切片生成 proof。

    步骤:
      1. 写入输入 JSON
      2. gen_witness → witness.json
      3. prove → proof.json
      4. 从 proof 提取 public instances (commitments)

    返回:
      {
        "proof_path": str,
        "witness_path": str,
        "proof_data": dict,            # proof.json 完整内容
        "proof_gen_ms": float,
        "peak_rss_mb": float,
        "commitments": {
          "processed_inputs": [...],    # hashed input commitment (Poseidon)
          "processed_outputs": [...],   # hashed output commitment (Poseidon)
          "rescaled_outputs": [...],    # rescaled float outputs
        }
      }
    """
    os.makedirs(work_dir, exist_ok=True)

    data_path = os.path.join(work_dir, f"{tag}_input.json")
    witness_path = os.path.join(work_dir, f"{tag}_witness.json")
    proof_path = os.path.join(work_dir, f"{tag}_proof.json")

    write_input_json(input_tensor, data_path)

    mem_start = get_memory_mb()

    # gen_witness
    ezkl.gen_witness(data_path, compiled_path, witness_path)  # pyright: ignore[reportCallIssue]

    # prove
    t0 = time.perf_counter()
    ezkl.prove(witness_path, compiled_path, pk_path, proof_path, srs_path=srs_path)
    proof_gen_ms = (time.perf_counter() - t0) * 1000

    mem_end = get_memory_mb()

    # 读取 proof
    with open(proof_path, "r") as f:
        proof_data = json.load(f)

    # 提取 commitments (来自 proof 的 pretty_public_inputs)
    ppi = proof_data.get("pretty_public_inputs", {})
    commitments = {
        "processed_inputs": ppi.get("processed_inputs", []),
        "processed_outputs": ppi.get("processed_outputs", []),
        "rescaled_outputs": ppi.get("rescaled_outputs", []),
        "rescaled_inputs": ppi.get("rescaled_inputs", []),
    }
    proof_bound_outputs = extract_proof_bound_tensor(
        proof_data,
        witness_path,
        "rescaled_outputs",
    )
    proof_bound_inputs = extract_proof_bound_tensor(
        proof_data,
        witness_path,
        "rescaled_inputs",
    )

    # 清理临时输入文件
    try:
        os.remove(data_path)
    except OSError:
        pass

    return {
        "proof_path": proof_path,
        "witness_path": witness_path,
        "proof_data": proof_data,
        "proof_gen_ms": round(proof_gen_ms, 2),
        "peak_rss_mb": round(max(mem_start, mem_end), 2),
        "proof_size_bytes": os.path.getsize(proof_path),
        "commitments": commitments,
        "proof_bound_inputs": proof_bound_inputs,
        "proof_bound_outputs": proof_bound_outputs,
    }


def extract_commitments_from_proof(proof_path: str) -> dict:
    """从已有 proof 文件提取 commitments。"""
    with open(proof_path, "r") as f:
        proof_data = json.load(f)
    ppi = proof_data.get("pretty_public_inputs", {})
    return {
        "processed_inputs": ppi.get("processed_inputs", []),
        "processed_outputs": ppi.get("processed_outputs", []),
        "rescaled_outputs": ppi.get("rescaled_outputs", []),
        "rescaled_inputs": ppi.get("rescaled_inputs", []),
    }
