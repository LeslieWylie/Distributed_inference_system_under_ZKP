"""
v2/prover/prove_worker.py — 独立子进程 prover 入口。

被 deferred_pipeline 通过 subprocess.Popen 调用，实现真正 CPU 并行。
通信方式: 命令行参数输入 → JSON 文件输出。

用法:
    python -m v2.prover.prove_worker \
        --input-json <path> \
        --compiled <path> --pk <path> --srs <path> \
        --work-dir <dir> --tag <tag> \
        --result-json <output_path>
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Windows / EZKL 环境修复
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("HOME", str(Path.home()))
os.environ.setdefault("EZKL_REPO_PATH", os.path.join(str(Path.home()), ".ezkl"))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


def main():
    parser = argparse.ArgumentParser(description="Subprocess prover worker")
    parser.add_argument("--input-json", required=True, help="JSON with input_tensor")
    parser.add_argument("--compiled", required=True)
    parser.add_argument("--pk", required=True)
    parser.add_argument("--srs", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--result-json", required=True, help="Output result path")
    args = parser.parse_args()

    # 读取输入
    with open(args.input_json, "r") as f:
        input_tensor = json.load(f)

    # 执行 proving
    from v2.prover.ezkl_adapter import prove_slice

    t0 = time.perf_counter()
    try:
        result = prove_slice(
            input_tensor=input_tensor,
            compiled_path=args.compiled,
            pk_path=args.pk,
            srs_path=args.srs,
            work_dir=args.work_dir,
            tag=args.tag,
        )
        result["wall_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        # proof_data 太大，只保留 pretty_public_inputs
        if "proof_data" in result and result["proof_data"]:
            ppi = result["proof_data"].get("pretty_public_inputs", {})
            result["pretty_public_inputs"] = ppi
        result.pop("proof_data", None)
        result["success"] = True
    except Exception as e:
        result = {
            "success": False,
            "error": str(e),
            "wall_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    # 写出结果
    os.makedirs(os.path.dirname(args.result_json), exist_ok=True)
    with open(args.result_json, "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
