"""
阶段 2 一键启动脚本：启动 2 个 Worker + Master 流水线。

用法:
    python run_stage2.py                    # 正常模式
    python run_stage2.py --fault-at 2       # 在 slice 2 注入故障
"""

import argparse
import os
import signal
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable

WORKERS = [
    {
        "slice_id": 1,
        "port": 8001,
        "onnx": os.path.join(PROJECT_ROOT, "models", "slice_1.onnx"),
        "cal": os.path.join(PROJECT_ROOT, "models", "slice_1_cal.json"),
    },
    {
        "slice_id": 2,
        "port": 8002,
        "onnx": os.path.join(PROJECT_ROOT, "models", "slice_2.onnx"),
        "cal": os.path.join(PROJECT_ROOT, "models", "slice_2_cal.json"),
    },
]


def main():
    parser = argparse.ArgumentParser(description="Stage 2 Launcher")
    parser.add_argument("--fault-at", type=int, default=None)
    args = parser.parse_args()

    # 先确保 ONNX 文件存在（阶段 1 应该已经生成过）
    for w in WORKERS:
        if not os.path.isfile(w["onnx"]):
            print(f"[Launcher] ONNX 文件不存在: {w['onnx']}")
            print("[Launcher] 请先运行阶段 1 生成模型切片:")
            print(f"  {PYTHON} scripts/run_single_machine_demo.py")
            sys.exit(1)

    worker_procs = []
    worker_script = os.path.join(PROJECT_ROOT, "distributed", "worker.py")
    master_script = os.path.join(PROJECT_ROOT, "distributed", "master.py")

    try:
        # ── 启动 Workers ──
        for w in WORKERS:
            cmd = [
                PYTHON, worker_script,
                "--slice-id", str(w["slice_id"]),
                "--port", str(w["port"]),
                "--onnx", w["onnx"],
                "--cal", w["cal"],
            ]
            print(f"[Launcher] 启动 Worker {w['slice_id']} on port {w['port']}...")
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            worker_procs.append(proc)

        # 等待 Workers 初始化
        print("[Launcher] 等待 Workers 初始化 (EZKL 编译 + 密钥生成)...")
        print("[Launcher] 这需要约 10-20 秒，请耐心等待...")

        # ── 启动 Master ──
        master_cmd = [PYTHON, "-u", master_script]
        if args.fault_at is not None:
            master_cmd += ["--fault-at", str(args.fault_at)]

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        master_proc = subprocess.run(master_cmd, env=env)
        sys.exit(master_proc.returncode)

    except KeyboardInterrupt:
        print("\n[Launcher] 收到中断信号，清理...")
    finally:
        for proc in worker_procs:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("[Launcher] 所有进程已停止")


if __name__ == "__main__":
    main()
