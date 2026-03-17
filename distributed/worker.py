"""
Worker 节点：封装单个模型切片的推理 + ZKP 证明服务。

每个 Worker 进程负责一个切片，启动时完成 EZKL 初始化（编译电路 + 生成密钥），
之后通过 FastAPI 接收推理请求并返回 proof。

启动方式：
    python worker.py --slice-id 1 --port 8001 --onnx models/slice_1.onnx --cal models/slice_1_cal.json

API:
    POST /infer        — 推理 + ZKP 证明（完整验证）
    POST /infer_light  — 仅推理 + 哈希（轻量级，无 proof）
    GET  /health       — 健康检查

故障注入参数：
    fault_type: tamper | skip | random | replay
"""

import argparse
import json
import os
import sys
import time
import uuid

import random as _random

import numpy as np
import torch
import torch.nn as nn
import onnxruntime as rt
from fastapi import FastAPI, Query
from pydantic import BaseModel
import uvicorn

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from common.utils import (
    ezkl_init,
    ezkl_prove,
    sha256_of_list,
    write_input_json,
    get_memory_mb,
)

# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------

class InferRequest(BaseModel):
    """推理请求：一组 flat 浮点数作为输入。"""
    input_data: list[float]
    request_id: str | None = None


class InferResponse(BaseModel):
    """推理响应：包含输出、proof 和 metrics。"""
    request_id: str
    slice_id: int
    output_data: list[float]
    hash_in: str
    hash_out: str
    proof: dict | None = None
    verified: bool | None = None
    proof_instances: dict | None = None  # ZKP 公开实例（含 Poseidon 哈希）
    metrics: dict
    fault_injected: bool
    proof_mode: str = "full"  # "full" or "light"


# ---------------------------------------------------------------------------
# Worker 应用
# ---------------------------------------------------------------------------

def create_app(slice_id: int, onnx_path: str, cal_path: str, paths: dict) -> FastAPI:
    """创建 FastAPI 应用。paths 由外部预初始化传入。"""
    app = FastAPI(title=f"Worker-{slice_id}")

    # 共享状态（在 uvicorn 启动前已完成初始化）
    onnx_abs = os.path.abspath(onnx_path)
    session = rt.InferenceSession(onnx_abs)
    input_name = session.get_inputs()[0].name
    artifacts_dir = os.path.join(PROJECT_ROOT, "artifacts", f"worker_{slice_id}")

    state = {
        "paths": paths,
        "artifacts_dir": artifacts_dir,
        "session": session,
        "input_name": input_name,
        "slice_id": slice_id,
    }

    @app.get("/health")
    def health():
        return {"status": "ok", "slice_id": slice_id}

    # ----- 共用的推理 + 故障注入逻辑 -----
    def _do_inference(req: InferRequest, fault_type: str | None = None):
        """执行 ONNX 推理并可选地注入故障。返回 (output_data, hash_in, hash_out, fault_injected)。"""
        hash_in = sha256_of_list(req.input_data)

        # ONNX 前向推理
        input_array = np.array([req.input_data], dtype=np.float32)
        ort_output = state["session"].run(None, {state["input_name"]: input_array})
        correct_output = ort_output[0].flatten().tolist()

        # hash_out 始终基于正确推理结果
        hash_out = sha256_of_list(correct_output)

        # 故障注入
        output_data = list(correct_output)
        fault_injected = False
        if fault_type and fault_type != "none":
            fault_injected = True
            if fault_type == "tamper":
                output_data[0] += 999.0
            elif fault_type == "skip":
                output_data = [0.0] * len(output_data)
            elif fault_type == "random":
                output_data = [_random.uniform(-10, 10) for _ in output_data]
            elif fault_type == "replay":
                output_data = [0.42] * len(output_data)

        return output_data, hash_in, hash_out, fault_injected

    # ----- /infer: 完整验证（推理 + ZKP proof） -----
    @app.post("/infer", response_model=InferResponse)
    def infer(req: InferRequest, fault_type: str = Query("none")):
        request_id = req.request_id or str(uuid.uuid4())[:8]
        t_start = time.perf_counter()

        output_data, hash_in, hash_out, fault_injected = _do_inference(req, fault_type)

        # 写入输入数据文件供 EZKL 使用
        data_path = os.path.join(state["artifacts_dir"], f"input_{request_id}.json")
        write_input_json(req.input_data, data_path)

        # 生成 proof
        result = ezkl_prove(data_path, state["paths"], state["artifacts_dir"])

        try:
            os.remove(data_path)
        except OSError:
            pass

        forward_ms = (time.perf_counter() - t_start) * 1000
        result["metrics"]["forward_ms"] = round(forward_ms, 2)
        result["metrics"]["request_id"] = request_id

        return InferResponse(
            request_id=request_id,
            slice_id=state["slice_id"],
            output_data=output_data,
            hash_in=hash_in,
            hash_out=hash_out,
            proof=result["proof"],
            verified=result["verified"],
            proof_instances=result.get("proof_instances"),
            metrics=result["metrics"],
            fault_injected=fault_injected,
            proof_mode="full",
        )

    # ----- /infer_light: 轻量级（仅推理 + 哈希，无 proof） -----
    @app.post("/infer_light", response_model=InferResponse)
    def infer_light(req: InferRequest, fault_type: str = Query("none")):
        request_id = req.request_id or str(uuid.uuid4())[:8]
        t_start = time.perf_counter()

        output_data, hash_in, hash_out, fault_injected = _do_inference(req, fault_type)

        forward_ms = (time.perf_counter() - t_start) * 1000

        return InferResponse(
            request_id=request_id,
            slice_id=state["slice_id"],
            output_data=output_data,
            hash_in=hash_in,
            hash_out=hash_out,
            proof=None,
            verified=None,
            metrics={
                "proof_gen_ms": 0.0,
                "verify_ms": 0.0,
                "forward_ms": round(forward_ms, 2),
                "peak_rss_mb": round(get_memory_mb(), 2),
                "request_id": request_id,
            },
            fault_injected=fault_injected,
            proof_mode="light",
        )

    # ----- /re_prove: 随机挑战重验证（Master 事后抽查） -----
    @app.post("/re_prove")
    def re_prove(req: InferRequest):
        """
        Master 随机挑战：对一个之前走 /infer_light 的请求重新做 ZKP prove。
        防止 Worker 预计算或 replay 攻击。
        """
        request_id = str(uuid.uuid4())[:8]
        data_path = os.path.join(state["artifacts_dir"], f"challenge_{request_id}.json")
        write_input_json(req.input_data, data_path)

        result = ezkl_prove(data_path, state["paths"], state["artifacts_dir"])

        try:
            os.remove(data_path)
        except OSError:
            pass

        return {
            "slice_id": state["slice_id"],
            "verified": result["verified"],
            "proof_instances": result.get("proof_instances"),
            "metrics": result["metrics"],
        }

    return app


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ZKP Worker Node")
    parser.add_argument("--slice-id", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--onnx", type=str, required=True)
    parser.add_argument("--cal", type=str, required=True)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--visibility-mode", type=str, default="all_public",
                        choices=["all_public", "hashed", "private"],
                        help="EZKL visibility mode for privacy level")
    args = parser.parse_args()

    # EZKL 初始化在 uvicorn 启动前（无事件循环冲突）
    onnx_abs = os.path.abspath(args.onnx)
    cal_abs = os.path.abspath(args.cal)
    artifacts_dir = os.path.join(PROJECT_ROOT, "artifacts", f"worker_{args.slice_id}")

    print(f"[Worker {args.slice_id}] 初始化 EZKL (mode={args.visibility_mode})...")
    t0 = time.perf_counter()
    paths = ezkl_init(onnx_abs, cal_abs, artifacts_dir,
                      visibility_mode=args.visibility_mode)
    init_ms = (time.perf_counter() - t0) * 1000
    print(f"[Worker {args.slice_id}] EZKL 初始化完成 ({init_ms:.0f} ms)")

    app = create_app(args.slice_id, args.onnx, args.cal, paths)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
