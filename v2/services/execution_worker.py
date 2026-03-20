"""
v2/services/execution_worker.py — 分布式执行 Worker (FastAPI)。

每个 Worker 封装一个 ONNX 切片，通过 HTTP 接收推理请求。
职责: 仅执行推理 + 返回输出，不做 proving、不做 verify、不宣称 correctness。

启动方式:
    python -m v2.services.execution_worker --slice-id 1 --port 9001

API:
    POST /execute  — 执行推理，返回 output_tensor
    GET  /health   — 健康检查
"""

import argparse
import os
import sys
import time
import uuid

import numpy as np
import onnxruntime as rt
from fastapi import FastAPI, Query
from pydantic import BaseModel
import uvicorn

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


class ExecuteRequest(BaseModel):
    req_id: str
    input_tensor: list[float]


class ExecuteResponse(BaseModel):
    req_id: str
    slice_id: int
    output_tensor: list[float]
    exec_ms: float
    fault_injected: bool


def create_app(slice_id: int, onnx_path: str) -> FastAPI:
    app = FastAPI(title=f"ExecutionWorker-{slice_id}")

    onnx_abs = os.path.abspath(onnx_path)
    session = rt.InferenceSession(onnx_abs)
    input_name = session.get_inputs()[0].name

    @app.get("/health")
    def health():
        return {"status": "ok", "slice_id": slice_id, "role": "execution_worker"}

    @app.post("/execute", response_model=ExecuteResponse)
    def execute(req: ExecuteRequest, fault_type: str = Query("none")):
        t0 = time.perf_counter()

        input_array = np.array([req.input_tensor], dtype=np.float32)
        ort_output = session.run(None, {input_name: input_array})
        output_tensor = ort_output[0].flatten().tolist()

        fault_injected = False
        if fault_type and fault_type != "none":
            fault_injected = True
            import random as _random
            if fault_type == "tamper":
                output_tensor[0] += 999.0
            elif fault_type == "skip":
                output_tensor = [0.0] * len(output_tensor)
            elif fault_type == "random":
                output_tensor = [_random.uniform(-10, 10) for _ in output_tensor]
            elif fault_type == "replay":
                output_tensor = [0.42] * len(output_tensor)

        exec_ms = (time.perf_counter() - t0) * 1000

        return ExecuteResponse(
            req_id=req.req_id,
            slice_id=slice_id,
            output_tensor=output_tensor,
            exec_ms=round(exec_ms, 2),
            fault_injected=fault_injected,
        )

    return app


def main():
    parser = argparse.ArgumentParser(description="v2 Execution Worker")
    parser.add_argument("--slice-id", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--onnx", type=str, required=True)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    app = create_app(args.slice_id, args.onnx)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
