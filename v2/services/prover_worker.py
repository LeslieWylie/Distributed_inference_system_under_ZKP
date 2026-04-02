"""
v2/services/prover_worker.py — Prover-Worker: 推理 + 证明一体化节点。

与旧 execution_worker.py 的核心区别:
  - Worker 本地执行 ONNX 推理 AND 生成 EZKL proof
  - 返回 (output_tensor, proof_json) 给 Master
  - 证明开销分摊到各 Worker 节点（真正的分布式证明）
  - Master/Verifier 从 proof 公开实例提取 I/O，不信任 Worker 明文声称

安全属性:
  - 恶意 Worker 无法返回假 output + 合法 proof
    (proof 的 public instances 绑定了真实计算的 I/O)
  - Verifier 独立验证 proof + 从中提取 I/O 做链式链接

启动方式:
    python -m v2.services.prover_worker \
        --slice-id 1 --port 9001 \
        --onnx path/to/slice.onnx \
        --compiled path/to/network.compiled \
        --pk path/to/pk.key \
        --srs path/to/kzg.srs \
        --settings path/to/settings.json

API:
    POST /infer_and_prove  — 推理 + 证明, 返回 output + proof
    GET  /health           — 健康检查
"""

import argparse
import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import onnxruntime as rt
from fastapi import FastAPI, Query
from pydantic import BaseModel
import uvicorn

# Windows / EZKL 环境修复
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("HOME", str(Path.home()))
os.environ.setdefault("EZKL_REPO_PATH", os.path.join(str(Path.home()), ".ezkl"))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class InferAndProveRequest(BaseModel):
    req_id: str
    input_tensor: list[float]


class InferAndProveResponse(BaseModel):
    req_id: str
    slice_id: int
    output_tensor: list[float]  # proof-bound output handed to the next slice
    proof_json: dict  # 完整 proof.json 内容 (含 pretty_public_inputs)
    exec_ms: float
    prove_ms: float
    total_ms: float
    fault_injected: bool


# ---------------------------------------------------------------------------
# Worker App
# ---------------------------------------------------------------------------

def create_app(
    slice_id: int,
    onnx_path: str,
    compiled_path: str,
    pk_path: str,
    srs_path: str,
    settings_path: str,
) -> FastAPI:
    app = FastAPI(title=f"ProverWorker-{slice_id}")

    onnx_abs = os.path.abspath(onnx_path)
    session = rt.InferenceSession(onnx_abs)
    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape  # e.g. [1, 784] or [1, 1, 28, 28]

    # 预加载 EZKL adapter
    from v2.prover.ezkl_adapter import prove_slice

    # 每个 Worker 的 proof 工作目录
    work_base = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "worker_proofs", f"worker_{slice_id}",
    )
    os.makedirs(work_base, exist_ok=True)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "slice_id": slice_id,
            "role": "prover_worker",
            "has_pk": os.path.exists(pk_path),
            "has_compiled": os.path.exists(compiled_path),
        }

    @app.post("/infer_and_prove", response_model=InferAndProveResponse)
    def infer_and_prove(
        req: InferAndProveRequest,
        fault_type: str = Query("none"),
    ):
        t_total = time.perf_counter()

        # ── Stage A: ONNX 推理 ──
        t0 = time.perf_counter()
        input_array = np.array(req.input_tensor, dtype=np.float32).reshape(input_shape)
        ort_output = session.run(None, {input_name: input_array})
        output_tensor = ort_output[0].flatten().tolist()
        exec_ms = (time.perf_counter() - t0) * 1000

        # 故障注入 (仅测试用)
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

        # ── Stage B: EZKL Prove ──
        work_dir = os.path.join(work_base, req.req_id)
        prove_result = prove_slice(
            input_tensor=req.input_tensor,
            compiled_path=compiled_path,
            pk_path=pk_path,
            srs_path=srs_path,
            work_dir=work_dir,
            tag=f"slice_{slice_id}",
        )
        prove_ms = prove_result["proof_gen_ms"]
        proof_bound_output = prove_result.get("proof_bound_outputs") or output_tensor

        total_ms = (time.perf_counter() - t_total) * 1000

        return InferAndProveResponse(
            req_id=req.req_id,
            slice_id=slice_id,
            output_tensor=proof_bound_output,
            proof_json=prove_result["proof_data"],
            exec_ms=round(exec_ms, 2),
            prove_ms=round(prove_ms, 2),
            total_ms=round(total_ms, 2),
            fault_injected=fault_injected,
        )

    # ── /infer: 仅推理，不生成证明（用于流水线模式） ──
    @app.post("/infer")
    def infer_only(
        req: InferAndProveRequest,
        fault_type: str = Query("none"),
    ):
        t0 = time.perf_counter()
        input_array = np.array(req.input_tensor, dtype=np.float32).reshape(input_shape)
        ort_output = session.run(None, {input_name: input_array})
        output_tensor = ort_output[0].flatten().tolist()
        exec_ms = (time.perf_counter() - t0) * 1000

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

        return {
            "req_id": req.req_id,
            "slice_id": slice_id,
            "output_tensor": output_tensor,
            "exec_ms": round(exec_ms, 2),
            "fault_injected": fault_injected,
        }

    # ── /prove: 仅生成证明（用于流水线模式） ──
    @app.post("/prove")
    def prove_only(req: InferAndProveRequest):
        t0 = time.perf_counter()
        work_dir = os.path.join(work_base, req.req_id)
        prove_result = prove_slice(
            input_tensor=req.input_tensor,
            compiled_path=compiled_path,
            pk_path=pk_path,
            srs_path=srs_path,
            work_dir=work_dir,
            tag=f"slice_{slice_id}",
        )
        prove_ms = (time.perf_counter() - t0) * 1000

        return {
            "req_id": req.req_id,
            "slice_id": slice_id,
            "proof_json": prove_result["proof_data"],
            "prove_ms": round(prove_ms, 2),
        }

    return app


def main():
    parser = argparse.ArgumentParser(description="v2 Prover-Worker")
    parser.add_argument("--slice-id", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--onnx", type=str, required=True)
    parser.add_argument("--compiled", type=str, required=True)
    parser.add_argument("--pk", type=str, required=True)
    parser.add_argument("--srs", type=str, required=True)
    parser.add_argument("--settings", type=str, required=True)
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="Bind address (0.0.0.0 for cross-host)")
    args = parser.parse_args()

    app = create_app(
        args.slice_id, args.onnx,
        args.compiled, args.pk, args.srs, args.settings,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
