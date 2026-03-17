"""
Worker 节点：封装单个模型切片的推理 + ZKP 证明服务。

每个 Worker 进程负责一个切片，启动时完成 EZKL 初始化（编译电路 + 生成密钥），
之后通过 FastAPI 接收推理请求并返回 proof。

启动方式：
    python worker.py --slice-id 1 --port 8001 --onnx models/slice_1.onnx --cal models/slice_1_cal.json

API:
    POST /infer   — 接收输入数据，执行推理 + 证明，返回输出 + proof + metrics
    GET  /health  — 健康检查
    POST /infer?fault=true — 故障注入模式（篡改输出）
"""

import argparse
import json
import os
import sys
import time
import uuid

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
    proof: dict
    verified: bool
    metrics: dict
    fault_injected: bool


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

    @app.post("/infer", response_model=InferResponse)
    def infer(req: InferRequest, fault: bool = Query(False)):
        request_id = req.request_id or str(uuid.uuid4())[:8]
        t_start = time.perf_counter()

        # 输入哈希
        hash_in = sha256_of_list(req.input_data)

        # 1. ONNX 前向推理
        input_array = np.array([req.input_data], dtype=np.float32)
        ort_output = state["session"].run(None, {state["input_name"]: input_array})
        output_data = ort_output[0].flatten().tolist()

        # 输出哈希 — 始终基于正确的推理结果（与 proof 承诺一致）
        hash_out = sha256_of_list(ort_output[0].flatten().tolist())

        # 2. 故障注入：篡改返回给 Master 的输出数据
        #    但 hash_out 仍基于正确结果，模拟恶意节点返回错误数据
        fault_injected = False
        if fault:
            output_data[0] += 999.0
            fault_injected = True

        # 3. 写入输入数据文件供 EZKL 使用
        data_path = os.path.join(state["artifacts_dir"], f"input_{request_id}.json")
        write_input_json(req.input_data, data_path)

        # 4. 生成 proof
        result = ezkl_prove(data_path, state["paths"], state["artifacts_dir"])

        # 清理临时输入文件
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
            metrics=result["metrics"],
            fault_injected=fault_injected,
        )

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
    args = parser.parse_args()

    # EZKL 初始化在 uvicorn 启动前（无事件循环冲突）
    onnx_abs = os.path.abspath(args.onnx)
    cal_abs = os.path.abspath(args.cal)
    artifacts_dir = os.path.join(PROJECT_ROOT, "artifacts", f"worker_{args.slice_id}")

    print(f"[Worker {args.slice_id}] 初始化 EZKL...")
    t0 = time.perf_counter()
    paths = ezkl_init(onnx_abs, cal_abs, artifacts_dir)
    init_ms = (time.perf_counter() - t0) * 1000
    print(f"[Worker {args.slice_id}] EZKL 初始化完成 ({init_ms:.0f} ms)")

    app = create_app(args.slice_id, args.onnx, args.cal, paths)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
