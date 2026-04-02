from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime

import numpy as np
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON = sys.executable


def _tail_log(log_path: str, max_lines: int = 40) -> str:
    if not log_path or not os.path.exists(log_path):
        return ""
    with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    return "".join(lines[-max_lines:]).strip()


def flatten_float_values(data) -> list[float]:
    values: list[float] = []
    if isinstance(data, list):
        for item in data:
            values.extend(flatten_float_values(item))
        return values
    return [float(data)]


def extract_pretty_public_values(proof_json: dict, field: str) -> list[float]:
    ppi = (proof_json or {}).get("pretty_public_inputs", {})
    values = ppi.get(field, [])
    if not values:
        return []
    return flatten_float_values(values)


def summarize_error(reference, candidate) -> dict:
    ref = np.asarray(reference, dtype=np.float64)
    cand = np.asarray(candidate, dtype=np.float64)
    diff = np.abs(ref - cand)
    return {
        "max_abs_error": float(np.max(diff)) if diff.size else 0.0,
        "mean_abs_error": float(np.mean(diff)) if diff.size else 0.0,
        "l1_distance": float(np.sum(diff)),
        "l2_distance": float(np.linalg.norm(diff)),
    }


def load_registry_input(registry_dir: str) -> list[float]:
    input_path = os.path.join(registry_dir, "models", "slice_1_input.json")
    with open(input_path, "r", encoding="utf-8") as handle:
        return json.load(handle)["input_data"][0]


def start_prover_workers(artifacts, base_port: int = 9001):
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = os.path.join(PROJECT_ROOT, "v2", "logs", "prover_workers", run_id)
    os.makedirs(log_dir, exist_ok=True)
    workers = []
    for artifact in artifacts:
        port = base_port + artifact.slice_id - 1
        log_path = os.path.join(log_dir, f"slice_{artifact.slice_id}.log")
        log_handle = open(log_path, "w", encoding="utf-8")
        cmd = [
            PYTHON, "-u", "-m", "v2.services.prover_worker",
            "--slice-id", str(artifact.slice_id),
            "--port", str(port),
            "--onnx", artifact.model_path,
            "--compiled", artifact.compiled_path,
            "--pk", artifact.pk_path,
            "--srs", artifact.srs_path,
            "--settings", artifact.settings_path,
            "--host", "0.0.0.0",
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=PROJECT_ROOT,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        workers.append({
            "slice_id": artifact.slice_id,
            "url": f"http://127.0.0.1:{port}",
            "proc": proc,
            "log_path": log_path,
            "log_handle": log_handle,
        })
    return workers


def wait_workers_ready(workers, timeout: int = 180):
    deadline = time.time() + timeout
    for worker in workers:
        while time.time() < deadline:
            exit_code = worker["proc"].poll()
            if exit_code is not None:
                log_tail = _tail_log(worker.get("log_path"))
                detail = f"\n{log_tail}" if log_tail else ""
                raise RuntimeError(
                    f"Worker {worker['slice_id']} exited before ready "
                    f"(code={exit_code}, url={worker['url']}){detail}"
                )
            try:
                response = requests.get(f"{worker['url']}/health", timeout=5)
                if response.status_code == 200:
                    info = response.json()
                    if info.get("role") == "prover_worker":
                        break
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(1)
        else:
            raise TimeoutError(f"Worker {worker['slice_id']} at {worker['url']} not ready")


def stop_workers(workers):
    for worker in workers:
        if worker["proc"].poll() is None:
            worker["proc"].terminate()
        try:
            worker["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker["proc"].kill()
        log_handle = worker.get("log_handle")
        if log_handle:
            log_handle.close()


def run_client_verified_case(
    initial_input: list[float],
    artifacts,
    worker_urls: list[dict],
    fault_at: int | None = None,
    fault_type: str = "none",
) -> dict:
    from v2.services.distributed_coordinator import run_distributed_pipeline
    from v2.verifier.bundle_verifier import verify_bundle

    pipeline_result = run_distributed_pipeline(
        initial_input,
        artifacts,
        worker_urls,
        fault_at=fault_at,
        fault_type=fault_type,
    )
    bundle = pipeline_result["proof_bundle"]
    client_result = verify_bundle(bundle, artifacts)

    proof_bound_outputs = {}
    for slice_item in bundle.slices:
        proof_bound_outputs[slice_item.slice_id] = extract_pretty_public_values(
            slice_item.proof_json,
            "rescaled_outputs",
        )

    metrics = dict(pipeline_result["metrics"])
    metrics["client_verification_ms"] = client_result.metrics.get("verification_ms", 0.0)

    return {
        "bundle": bundle,
        "pipeline_result": pipeline_result,
        "client_result": client_result,
        "client_verdict": client_result.status,
        "failure_reasons": client_result.failure_reasons,
        "proof_bound_outputs": proof_bound_outputs,
        "proof_bound_final_output": proof_bound_outputs.get(bundle.slices[-1].slice_id, []),
        "metrics": metrics,
    }