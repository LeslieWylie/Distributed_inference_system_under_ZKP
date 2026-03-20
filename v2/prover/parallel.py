"""
v2/prover/parallel.py — 基于子进程的真正 CPU 并行 proving。

解决 ThreadPoolExecutor + GIL 无法并行 EZKL prove 的问题。
每个切片的 proving 在独立子进程中执行，实现真正的多核利用。
"""

import json
import os
import subprocess
import sys
import time
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON = sys.executable


def prove_slices_parallel(
    tasks: list[dict],
    max_workers: int = 2,
) -> dict[int, dict]:
    """
    并行 proving 多个切片（子进程级真正 CPU 并行）。

    每个 task 包含:
      slice_id, input_tensor, compiled_path, pk_path, srs_path, work_dir, tag

    返回: {slice_id: prove_result_dict}
    """
    # 准备所有子进程
    pending = []
    for task in tasks:
        sid = task["slice_id"]
        work_dir = task["work_dir"]
        os.makedirs(work_dir, exist_ok=True)

        # 写入输入张量
        input_json_path = os.path.join(work_dir, f"{task['tag']}_input_tensor.json")
        with open(input_json_path, "w") as f:
            json.dump(task["input_tensor"], f)

        # 结果输出路径
        result_json_path = os.path.join(work_dir, f"{task['tag']}_prove_result.json")

        cmd = [
            PYTHON, "-u", "-m", "v2.prover.prove_worker",
            "--input-json", input_json_path,
            "--compiled", task["compiled_path"],
            "--pk", task["pk_path"],
            "--srs", task["srs_path"],
            "--work-dir", work_dir,
            "--tag", task["tag"],
            "--result-json", result_json_path,
        ]

        pending.append({
            "slice_id": sid,
            "cmd": cmd,
            "result_path": result_json_path,
            "input_json_path": input_json_path,
            "proc": None,
            "started": False,
        })

    # 分批启动子进程（限制并行度）
    results = {}
    active = []
    idx = 0

    while idx < len(pending) or active:
        # 启动新进程（直到达到并行上限）
        while len(active) < max_workers and idx < len(pending):
            item = pending[idx]
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                item["cmd"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                cwd=PROJECT_ROOT,
            )
            item["proc"] = proc
            item["start_time"] = time.perf_counter()
            item["started"] = True
            active.append(item)
            idx += 1

        # 检查完成的进程
        still_active = []
        for item in active:
            ret = item["proc"].poll()
            if ret is not None:
                # 进程结束，读取结果
                wall_ms = (time.perf_counter() - item["start_time"]) * 1000
                sid = item["slice_id"]
                try:
                    with open(item["result_path"], "r") as f:
                        result = json.load(f)
                    result["subprocess_wall_ms"] = round(wall_ms, 2)
                    results[sid] = result
                except Exception as e:
                    results[sid] = {
                        "success": False,
                        "error": f"Failed to read result: {e}",
                        "subprocess_wall_ms": round(wall_ms, 2),
                    }

                # 清理临时输入文件
                try:
                    os.remove(item["input_json_path"])
                except OSError:
                    pass
            else:
                still_active.append(item)

        active = still_active
        if active:
            time.sleep(0.05)  # 50ms poll interval

    return results
