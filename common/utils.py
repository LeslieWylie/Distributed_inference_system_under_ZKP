"""
公共工具模块：EZKL 流程、哈希、metrics 等跨阶段复用的函数。
"""

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

# Windows 编码修复
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# EZKL Windows HOME 修复 — 必须在 import ezkl 前执行
USER_HOME = str(Path.home())
os.environ.setdefault("HOME", USER_HOME)
os.environ.setdefault("EZKL_REPO_PATH", os.path.join(USER_HOME, ".ezkl"))
os.makedirs(os.environ["EZKL_REPO_PATH"], exist_ok=True)

import ezkl
import psutil


# ---------------------------------------------------------------------------
# 哈希
# ---------------------------------------------------------------------------

def sha256_of_list(data_list: list) -> str:
    """对浮点数列表计算 SHA-256。"""
    serialized = json.dumps(data_list, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


# ---------------------------------------------------------------------------
# 内存
# ---------------------------------------------------------------------------

def get_memory_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def load_proof_instances_from_witness(witness_path: str) -> dict:
    """从 witness 文件中提取 processed_inputs/processed_outputs。"""
    with open(witness_path, "r") as f:
        witness_data = json.load(f)

    return {
        "processed_inputs": witness_data.get("processed_inputs", None),
        "processed_outputs": witness_data.get("processed_outputs", None),
    }


# ---------------------------------------------------------------------------
# EZKL 初始化（gen_settings → calibrate → compile → get_srs → setup）
# ---------------------------------------------------------------------------

def ezkl_init(onnx_path: str, cal_path: str, artifacts_dir: str,
              visibility_mode: str = "all_public") -> dict:
    """
    对一个 ONNX 切片执行 EZKL 的一次性初始化流程，生成所有密钥和编译产物。
    只需在 Worker 启动时调用一次。

    visibility_mode:
        "all_public" — 输入输出公开，参数固定（默认，无隐私保护）
        "hashed"     — 输入和参数以 Poseidon 哈希形式暴露（隐私保护）
        "private"    — 输入完全不可见（最强隐私）

    返回路径字典供后续 prove/verify 使用。
    """
    os.makedirs(artifacts_dir, exist_ok=True)
    artifacts_dir = os.path.abspath(artifacts_dir)

    paths = {
        "settings": os.path.join(artifacts_dir, "settings.json"),
        "compiled": os.path.join(artifacts_dir, "network.compiled"),
        "pk": os.path.join(artifacts_dir, "pk.key"),
        "vk": os.path.join(artifacts_dir, "vk.key"),
        "srs": os.path.join(artifacts_dir, "kzg.srs"),
    }

    # 1. gen_settings — 根据 visibility_mode 设置可见性
    py_run_args = ezkl.PyRunArgs()
    if visibility_mode == "hashed":
        py_run_args.input_visibility = "hashed"
        py_run_args.output_visibility = "public"
        py_run_args.param_visibility = "hashed"
    elif visibility_mode == "private":
        py_run_args.input_visibility = "private"
        py_run_args.output_visibility = "public"
        py_run_args.param_visibility = "fixed"
    else:  # all_public
        py_run_args.input_visibility = "public"
        py_run_args.output_visibility = "public"
        py_run_args.param_visibility = "fixed"
    assert ezkl.gen_settings(onnx_path, paths["settings"], py_run_args=py_run_args)

    # 2. calibrate_settings
    assert ezkl.calibrate_settings(cal_path, onnx_path, paths["settings"], "resources")

    # 3. compile_circuit
    assert ezkl.compile_circuit(onnx_path, paths["compiled"], paths["settings"])

    # 4. get_srs (async — 兼容嵌套事件循环)
    async def _fetch():
        return await ezkl.get_srs(settings_path=paths["settings"], srs_path=paths["srs"])

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 已有事件循环 (如在 uvicorn/FastAPI 内)，用 nest_asyncio 方案
        import nest_asyncio
        nest_asyncio.apply()
        asyncio.run(_fetch())
    else:
        asyncio.run(_fetch())

    # 5. setup
    assert ezkl.setup(paths["compiled"], paths["vk"], paths["pk"], srs_path=paths["srs"])

    return paths


# ---------------------------------------------------------------------------
# EZKL prove + verify
# ---------------------------------------------------------------------------

def ezkl_prove(data_path: str, paths: dict, artifacts_dir: str) -> dict:
    """
    对给定输入执行 prove + verify，返回 proof 内容和 metrics。

    关键：从 witness 中提取 processed_inputs/processed_outputs，
    这些是 ZKP 公开实例中的 Poseidon 哈希（hashed 模式下）或原始值（public 模式下）。
    proof linking 依赖这些值的跨切片比对。
    """
    artifacts_dir = os.path.abspath(artifacts_dir)
    proof_tag = os.path.splitext(os.path.basename(data_path))[0]
    witness_path = os.path.join(artifacts_dir, f"{proof_tag}_witness.json")
    proof_path = os.path.join(artifacts_dir, f"{proof_tag}_proof.json")

    mem_start = get_memory_mb()

    # gen_witness — 产生 witness 文件，包含 processed_inputs/processed_outputs
    ezkl.gen_witness(data_path, paths["compiled"], witness_path)

    # 提取 witness 中的公开实例（proof linking 的关键数据）
    # processed_inputs/processed_outputs 是 ZKP 公开实例
    # 在 hashed 模式下 = Poseidon hash（电路内部计算，不可伪造）
    # 在 public 模式下 = 原始量化值
    proof_instances = load_proof_instances_from_witness(witness_path)

    # prove
    t0 = time.perf_counter()
    ezkl.prove(witness_path, paths["compiled"], paths["pk"], proof_path, srs_path=paths["srs"])
    proof_gen_ms = (time.perf_counter() - t0) * 1000

    # verify
    t0 = time.perf_counter()
    verified = ezkl.verify(proof_path, paths["settings"], paths["vk"], srs_path=paths["srs"])
    verify_ms = (time.perf_counter() - t0) * 1000

    # 读取 proof 文件
    with open(proof_path, "r") as f:
        proof_data = json.load(f)

    mem_end = get_memory_mb()

    # 记录 proof 文件大小
    proof_size_bytes = os.path.getsize(proof_path) if os.path.exists(proof_path) else 0
    witness_size_bytes = os.path.getsize(witness_path) if os.path.exists(witness_path) else 0

    return {
        "proof": proof_data,
        "proof_path": proof_path,
        "witness_path": witness_path,
        "verified": verified,
        "proof_instances": proof_instances,
        "artifact_paths": {
            "proof_path": proof_path,
            "witness_path": witness_path,
            "settings": paths["settings"],
            "vk": paths["vk"],
            "srs": paths["srs"],
        },
        "metrics": {
            "proof_gen_ms": round(proof_gen_ms, 2),
            "verify_ms": round(verify_ms, 2),
            # 近似 RSS：仅取 prove 前后两点的 RSS 最大值，非真实峰值
            "peak_rss_mb": round(max(mem_start, mem_end), 2),
            "proof_size_bytes": proof_size_bytes,
            "witness_size_bytes": witness_size_bytes,
        },
    }


def ezkl_verify_proof(proof_path: str, paths: dict) -> bool:
    """使用本地 proof/settings/vk/srs 独立验证 proof。"""
    return bool(
        ezkl.verify(
            proof_path,
            paths["settings"],
            paths["vk"],
            srs_path=paths["srs"],
        )
    )


# ---------------------------------------------------------------------------
# 数据 I/O 辅助
# ---------------------------------------------------------------------------

def write_input_json(data_list: list, path: str):
    """将 flat 数据列表写成 EZKL 输入格式。"""
    with open(path, "w") as f:
        json.dump({"input_data": [data_list]}, f)


def read_input_json(path: str) -> list:
    """读取 EZKL 输入 JSON 并返回 flat 数据列表。"""
    with open(path, "r") as f:
        return json.load(f)["input_data"][0]
