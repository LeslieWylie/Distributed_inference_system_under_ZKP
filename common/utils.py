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


# ---------------------------------------------------------------------------
# EZKL 初始化（gen_settings → calibrate → compile → get_srs → setup）
# ---------------------------------------------------------------------------

def ezkl_init(onnx_path: str, cal_path: str, artifacts_dir: str) -> dict:
    """
    对一个 ONNX 切片执行 EZKL 的一次性初始化流程，生成所有密钥和编译产物。
    只需在 Worker 启动时调用一次。

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

    # 1. gen_settings
    py_run_args = ezkl.PyRunArgs()
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
    """
    artifacts_dir = os.path.abspath(artifacts_dir)
    witness_path = os.path.join(artifacts_dir, "witness.json")
    proof_path = os.path.join(artifacts_dir, "proof.json")

    mem_start = get_memory_mb()

    # gen_witness
    ezkl.gen_witness(data_path, paths["compiled"], witness_path)

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

    return {
        "proof": proof_data,
        "proof_path": proof_path,
        "verified": verified,
        "metrics": {
            "proof_gen_ms": round(proof_gen_ms, 2),
            "verify_ms": round(verify_ms, 2),
            "peak_rss_mb": round(max(mem_start, mem_end), 2),
        },
    }


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
