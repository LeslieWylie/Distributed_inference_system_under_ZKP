"""
阶段 1: 单机最小可运行验证 Demo

严格基于 EZKL 官方 Python Bindings 文档 (https://pythonbindings.ezkl.xyz/en/stable/)
和官方示例 notebook (simple_demo_all_public.ipynb) 编写。

流程:
  1. 导出两个 ONNX 切片
  2. 对每个切片执行完整 EZKL 证明流程
  3. 跨切片哈希链一致性校验
  4. 可选故障注入，验证恶意检测能力
  5. 输出 metrics 日志
"""

import hashlib
import json
import os
import sys
import time
import asyncio
from pathlib import Path

# Windows 默认 GBK 编码无法处理 torch.onnx 输出的 ✅ emoji，强制 UTF-8
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import psutil

# 将项目根目录加入 sys.path，以支持 from models.full_model import ...
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# EZKL 在 Windows 下会回退到读取 HOME 来确定 .ezkl 目录；若 HOME 缺失，
# setup/prove 等阶段会在 Rust 层直接 panic(NotPresent)。
USER_HOME = str(Path.home())
os.environ.setdefault("HOME", USER_HOME)
os.environ.setdefault("EZKL_REPO_PATH", os.path.join(USER_HOME, ".ezkl"))
os.makedirs(os.environ["EZKL_REPO_PATH"], exist_ok=True)

import ezkl

from models.full_model import export_slices


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def sha256_hash(data_list: list) -> str:
    """对一组浮点数列表计算 SHA-256 哈希，用于跨切片一致性校验。"""
    serialized = json.dumps(data_list, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def get_memory_mb() -> float:
    """获取当前进程 RSS (MB)。"""
    return psutil.Process().memory_info().rss / (1024 * 1024)


# ---------------------------------------------------------------------------
# 单切片 EZKL 完整流程
# ---------------------------------------------------------------------------

def run_ezkl_pipeline(
    slice_id: int,
    onnx_path: str,
    data_path: str,
    cal_path: str,
    artifacts_dir: str,
) -> dict:
    """
    对单个切片执行完整的 EZKL 证明与验证流程。

    API 调用链 (官方文档确认):
      gen_settings → calibrate_settings → compile_circuit → get_srs
      → gen_witness → setup → prove → verify

    返回:
      包含该切片指标的字典。
    """
    os.makedirs(artifacts_dir, exist_ok=True)

    # 产物路径定义 (全部用绝对路径，避免 CWD 问题)
    artifacts_dir = os.path.abspath(artifacts_dir)
    settings_path = os.path.join(artifacts_dir, "settings.json")
    compiled_path = os.path.join(artifacts_dir, "network.compiled")
    pk_path = os.path.join(artifacts_dir, "pk.key")
    vk_path = os.path.join(artifacts_dir, "vk.key")
    srs_path = os.path.join(artifacts_dir, "kzg.srs")
    witness_path = os.path.join(artifacts_dir, "witness.json")
    proof_path = os.path.join(artifacts_dir, "proof.json")

    metrics = {"slice_id": slice_id}
    mem_start = get_memory_mb()

    # ── 1. gen_settings ──
    # 官方签名: ezkl.gen_settings(model, output, py_run_args=None)
    # 参考: PyRunArgs.input_visibility / output_visibility / param_visibility
    py_run_args = ezkl.PyRunArgs()
    py_run_args.input_visibility = "public"
    py_run_args.output_visibility = "public"
    py_run_args.param_visibility = "fixed"

    res = ezkl.gen_settings(onnx_path, settings_path, py_run_args=py_run_args)
    assert res, f"Slice {slice_id}: gen_settings failed"
    print(f"  [Slice {slice_id}] gen_settings ✓")

    # ── 2. calibrate_settings ──
    # 官方签名: ezkl.calibrate_settings(data, model, settings, target, ...)
    # target="resources" 适用于初始 demo
    res = ezkl.calibrate_settings(cal_path, onnx_path, settings_path, "resources")
    assert res, f"Slice {slice_id}: calibrate_settings failed"
    print(f"  [Slice {slice_id}] calibrate_settings ✓")

    # ── 3. compile_circuit ──
    # 官方签名: ezkl.compile_circuit(model, compiled_circuit, settings_path)
    res = ezkl.compile_circuit(onnx_path, compiled_path, settings_path)
    assert res, f"Slice {slice_id}: compile_circuit failed"
    print(f"  [Slice {slice_id}] compile_circuit ✓")

    # ── 4. get_srs (异步函数，需要 await) ──
    # 官方签名: ezkl.get_srs(settings_path, logrows=None, srs_path=None)
    # EZKL 的 get_srs 基于 pyo3-asyncio，必须在 async 上下文中 await
    async def _fetch_srs():
        return await ezkl.get_srs(settings_path=settings_path, srs_path=srs_path)

    res = asyncio.run(_fetch_srs())
    assert res, f"Slice {slice_id}: get_srs failed"
    print(f"  [Slice {slice_id}] get_srs ✓")

    # ── 5. gen_witness ──
    # 官方签名: ezkl.gen_witness(data, model, output, vk_path=None, srs_path=None)
    witness = ezkl.gen_witness(data_path, compiled_path, witness_path)
    assert os.path.isfile(witness_path), f"Slice {slice_id}: gen_witness failed"
    print(f"  [Slice {slice_id}] gen_witness ✓")

    # ── 6. setup ──
    # 官方签名: ezkl.setup(model, vk_path, pk_path, srs_path=None, ...)
    res = ezkl.setup(compiled_path, vk_path, pk_path, srs_path=srs_path)
    assert res, f"Slice {slice_id}: setup failed"
    assert os.path.isfile(vk_path)
    assert os.path.isfile(pk_path)
    print(f"  [Slice {slice_id}] setup ✓")

    # ── 7. prove (计时) ──
    # 官方签名: ezkl.prove(witness, model, pk_path, proof_path=None, srs_path=None)
    t0 = time.perf_counter()
    res = ezkl.prove(witness_path, compiled_path, pk_path, proof_path, srs_path=srs_path)
    proof_gen_ms = (time.perf_counter() - t0) * 1000
    assert res, f"Slice {slice_id}: prove failed"
    assert os.path.isfile(proof_path)
    metrics["proof_gen_ms"] = round(proof_gen_ms, 2)
    print(f"  [Slice {slice_id}] prove ✓  ({proof_gen_ms:.1f} ms)")

    # ── 8. verify (计时) ──
    # 官方签名: ezkl.verify(proof_path, settings_path, vk_path, srs_path=None, ...)
    t0 = time.perf_counter()
    res = ezkl.verify(proof_path, settings_path, vk_path, srs_path=srs_path)
    verify_ms = (time.perf_counter() - t0) * 1000
    assert res, f"Slice {slice_id}: verify failed"
    metrics["verify_ms"] = round(verify_ms, 2)
    print(f"  [Slice {slice_id}] verify ✓  ({verify_ms:.1f} ms)")

    # 内存度量
    mem_end = get_memory_mb()
    metrics["peak_rss_mb"] = round(max(mem_end, mem_start), 2)

    return metrics


# ---------------------------------------------------------------------------
# 哈希链一致性校验 (Master 逻辑)
# ---------------------------------------------------------------------------

def hash_chain_check(mid_output_from_slice1: list, input_to_slice2: list) -> dict:
    """
    Master 侧哈希链校验:
      H(public_output_slice1) == H(public_input_slice2)

    在真实分布式系统中，这两个值分别来自不同节点。
    """
    hash_out = sha256_hash(mid_output_from_slice1)
    hash_in = sha256_hash(input_to_slice2)
    consistency_ok = (hash_out == hash_in)
    return {
        "hash_out_slice1": hash_out,
        "hash_in_slice2": hash_in,
        "consistency_ok": consistency_ok,
    }


# ---------------------------------------------------------------------------
# 故障注入: 模拟恶意节点
# ---------------------------------------------------------------------------

def inject_fault(data_path: str, output_path: str) -> str:
    """
    读取正常数据文件，对 input_data 注入微小扰动，写入新文件。
    模拟恶意节点返回篡改的中间结果。
    """
    with open(data_path, "r") as f:
        data = json.load(f)

    # 对第一个值加扰动
    if data["input_data"] and data["input_data"][0]:
        data["input_data"][0][0] += 999.0  # 明显偏移

    with open(output_path, "w") as f:
        json.dump(data, f)

    return output_path


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("阶段 1: 单机最小可运行验证 Demo")
    print("=" * 60)

    e2e_start = time.perf_counter()

    # ── Step 1: 导出模型切片 ──
    print("\n[Step 1] 导出模型切片...")
    model_dir = os.path.join(PROJECT_ROOT, "models")
    info = export_slices(output_dir=model_dir)

    # ── Step 2: 对每个切片执行 EZKL 流程 ──
    artifacts_base = os.path.join(PROJECT_ROOT, "artifacts")
    all_metrics = []

    print("\n[Step 2] Slice 1 EZKL 流程...")
    m1 = run_ezkl_pipeline(
        slice_id=1,
        onnx_path=info["onnx_1"],
        data_path=info["data_1"],
        cal_path=info["cal_1"],
        artifacts_dir=os.path.join(artifacts_base, "slice_1"),
    )
    all_metrics.append(m1)

    print("\n[Step 3] Slice 2 EZKL 流程...")
    m2 = run_ezkl_pipeline(
        slice_id=2,
        onnx_path=info["onnx_2"],
        data_path=info["data_2"],
        cal_path=info["cal_2"],
        artifacts_dir=os.path.join(artifacts_base, "slice_2"),
    )
    all_metrics.append(m2)

    # ── Step 3: 哈希链一致性校验 ──
    print("\n[Step 4] 哈希链一致性校验...")

    # Slice 1 的输出 = mid_output
    mid_output_list = info["mid_output"].reshape([-1]).tolist()

    # Slice 2 的输入 = 与 mid_output 相同 (正常情况)
    with open(info["data_2"], "r") as f:
        slice2_data = json.load(f)
    input_to_slice2_list = slice2_data["input_data"][0]

    chain_result = hash_chain_check(mid_output_list, input_to_slice2_list)
    print(f"  Hash(output_slice1): {chain_result['hash_out_slice1'][:16]}...")
    print(f"  Hash(input_slice2):  {chain_result['hash_in_slice2'][:16]}...")
    print(f"  Consistency: {'PASS ✓' if chain_result['consistency_ok'] else 'FAIL ✗'}")

    # ── Step 4: 故障注入测试 ──
    print("\n[Step 5] 故障注入测试...")
    tampered_path = os.path.join(model_dir, "slice_2_input_tampered.json")
    inject_fault(info["data_2"], tampered_path)

    with open(tampered_path, "r") as f:
        tampered_data = json.load(f)
    tampered_input = tampered_data["input_data"][0]

    fault_result = hash_chain_check(mid_output_list, tampered_input)
    malicious_detected = not fault_result["consistency_ok"]
    print(f"  Tampered Hash(input_slice2): {fault_result['hash_in_slice2'][:16]}...")
    print(f"  Malicious detected: {'YES ✓' if malicious_detected else 'NO ✗'}")

    # ── 汇总 metrics ──
    e2e_ms = (time.perf_counter() - e2e_start) * 1000

    summary = {
        "e2e_latency_ms": round(e2e_ms, 2),
        "slices": all_metrics,
        "hash_chain": chain_result,
        "fault_injection": {
            "malicious_detected": malicious_detected,
            "detection_accuracy": 1.0 if malicious_detected else 0.0,
        },
    }

    metrics_dir = os.path.join(PROJECT_ROOT, "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    metrics_path = os.path.join(metrics_dir, "latest_run.json")
    with open(metrics_path, "w") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    print(f"  端到端延迟:        {e2e_ms:.1f} ms")
    print(f"  Slice 1 证明时间:  {all_metrics[0]['proof_gen_ms']:.1f} ms")
    print(f"  Slice 1 验证时间:  {all_metrics[0]['verify_ms']:.1f} ms")
    print(f"  Slice 2 证明时间:  {all_metrics[1]['proof_gen_ms']:.1f} ms")
    print(f"  Slice 2 验证时间:  {all_metrics[1]['verify_ms']:.1f} ms")
    print(f"  哈希链一致性:      {'PASS' if chain_result['consistency_ok'] else 'FAIL'}")
    print(f"  恶意节点检测:      {'成功' if malicious_detected else '失败'}")
    print(f"  Metrics 已写入:    {metrics_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
