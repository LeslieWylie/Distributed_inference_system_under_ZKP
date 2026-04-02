"""
v2/experiments/resource_metrics.py — 资源指标采集 (客户端验证主链)。

采集任务书要求的指标:
  - 证明生成时间 (per-Worker)
  - 验证时间 (客户端独立验证)
  - 推理延迟
  - 单节点 CPU/内存 占用
  - 系统吞吐量
  - 恶意节点检测准确率 (客户端 verdict 为准)

主链: Worker proving → Coordinator bundling → Client verifying
"""

import json
import os
import subprocess
import sys
import time
import threading

import psutil
import requests as http_requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

PYTHON = sys.executable


class ResourceMonitor:
    """后台线程采样 CPU% 和 RSS 峰值。支持监控指定 PID 列表。"""

    def __init__(self, pids=None, interval_ms=100):
        self.interval = interval_ms / 1000
        self.cpu_samples = {}   # pid → [samples]
        self.rss_samples = {}   # pid → [samples]
        self._stop = False
        self._thread = None
        self._pids = pids or [os.getpid()]
        for pid in self._pids:
            self.cpu_samples[pid] = []
            self.rss_samples[pid] = []

    def start(self):
        self._stop = False
        # 初始化 cpu_percent 计数器
        for pid in self._pids:
            try:
                psutil.Process(pid).cpu_percent(interval=None)
            except Exception:
                pass
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=2)

    def _sample(self):
        while not self._stop:
            for pid in self._pids:
                try:
                    p = psutil.Process(pid)
                    # interval=0.1 让 cpu_percent 做一次自阻塞采样，拿到真实 CPU%
                    self.cpu_samples[pid].append(p.cpu_percent(interval=0.1))
                    self.rss_samples[pid].append(
                        p.memory_info().rss / (1024 * 1024)
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

    def summary(self):
        result = {}
        for pid in self._pids:
            cpu = self.cpu_samples.get(pid, [])
            rss = self.rss_samples.get(pid, [])
            result[pid] = {
                "cpu_percent_avg": round(sum(cpu) / max(len(cpu), 1), 2),
                "cpu_percent_max": round(max(cpu) if cpu else 0, 2),
                "rss_mb_avg": round(sum(rss) / max(len(rss), 1), 2),
                "rss_mb_peak": round(max(rss) if rss else 0, 2),
                "samples": len(cpu),
            }
        return result


def _start_workers(artifacts, base_port=9001):
    workers = []
    for a in artifacts:
        port = base_port + a.slice_id - 1
        cmd = [
            PYTHON, "-u", "-m", "v2.services.prover_worker",
            "--slice-id", str(a.slice_id), "--port", str(port),
            "--onnx", a.model_path, "--compiled", a.compiled_path,
            "--pk", a.pk_path, "--srs", a.srs_path, "--settings", a.settings_path,
            "--host", "0.0.0.0",
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(cmd, env=env, cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        workers.append({"slice_id": a.slice_id, "url": f"http://127.0.0.1:{port}", "proc": proc})
    return workers


def _wait_workers(workers, timeout=120):
    deadline = time.time() + timeout
    for w in workers:
        while time.time() < deadline:
            try:
                r = http_requests.get(f"{w['url']}/health", timeout=3)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)


def _stop_workers(workers):
    for w in workers:
        w["proc"].terminate()
        try:
            w["proc"].wait(timeout=5)
        except subprocess.TimeoutExpired:
            w["proc"].kill()


def run_resource_experiments(num_slices=2, num_requests=3):
    """采集资源指标 — 基于 Coordinator bundling + Client verification 主链。"""
    from v2.compile.build_circuits import load_registry
    from v2.services.distributed_coordinator import run_distributed_pipeline
    from v2.verifier.bundle_verifier import verify_bundle

    registry_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "registry", "slice_registry.json",
    )
    artifacts = load_registry(registry_path)

    input_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "models", "slice_1_input.json",
    )
    with open(input_path) as f:
        initial_input = json.load(f)["input_data"][0]

    # Start workers
    print("Starting Prover-Workers...")
    workers = _start_workers(artifacts)
    worker_urls = [{"slice_id": w["slice_id"], "url": w["url"]} for w in workers]
    _wait_workers(workers)
    print(f"All {len(workers)} workers ready.\n")

    try:
        # ── 1. Resource profiling: 监控 Coordinator + 各 Worker 进程 ──
        print("=== Resource Profiling (normal request) ===")
        all_pids = [os.getpid()] + [w["proc"].pid for w in workers]
        pid_labels = {os.getpid(): "coordinator"}
        for w in workers:
            pid_labels[w["proc"].pid] = f"worker_{w['slice_id']}"

        monitor = ResourceMonitor(pids=all_pids, interval_ms=50)
        monitor.start()

        bundle_start = time.perf_counter()
        r_normal = run_distributed_pipeline(initial_input, artifacts, worker_urls)
        bundle_ms = (time.perf_counter() - bundle_start) * 1000

        client_start = time.perf_counter()
        client_result = verify_bundle(r_normal["proof_bundle"], artifacts)
        client_verify_ms = (time.perf_counter() - client_start) * 1000

        monitor.stop()
        raw_profile = monitor.summary()

        # 重新组织为可读格式
        resource_profile = {}
        for pid, label in pid_labels.items():
            resource_profile[label] = raw_profile.get(pid, {})

        print(f"  Client verdict: {client_result.status}")
        for label, stats in resource_profile.items():
            print(f"  {label}: CPU avg={stats.get('cpu_percent_avg', 0):.1f}%  "
                  f"peak RSS={stats.get('rss_mb_peak', 0):.1f} MB")

        # ── 2. Throughput: 连续请求测量 ──
        print(f"\n=== Throughput ({num_requests} sequential requests) ===")
        tp_start = time.perf_counter()
        for _ in range(num_requests):
            r = run_distributed_pipeline(initial_input, artifacts, worker_urls)
            verify_bundle(r["proof_bundle"], artifacts)
        tp_ms = (time.perf_counter() - tp_start) * 1000
        tp_rps = num_requests / (tp_ms / 1000)
        print(f"  {num_requests} requests in {tp_ms:.0f}ms = {tp_rps:.4f} req/s")

        # ── 3. Detection accuracy: 覆盖 6 种攻击 ──
        print("\n=== Detection Accuracy ===")
        attacks = [
            {"name": "tamper_last", "fault_at": num_slices, "fault_type": "tamper"},
            {"name": "skip_last", "fault_at": num_slices, "fault_type": "skip"},
            {"name": "random_last", "fault_at": num_slices, "fault_type": "random"},
            {"name": "replay_last", "fault_at": num_slices, "fault_type": "replay"},
        ]
        if num_slices >= 3:
            attacks.append(
                {"name": "tamper_mid", "fault_at": max(1, num_slices // 2), "fault_type": "tamper"},
            )

        tp_count = 0
        tn_count = 0
        fp_count = 0
        fn_count = 0
        per_attack = []

        # Normal (expect certified) — 2 runs
        for i in range(2):
            r = run_distributed_pipeline(initial_input, artifacts, worker_urls)
            cv = verify_bundle(r["proof_bundle"], artifacts)
            if cv.status == "certified":
                tn_count += 1
            else:
                fp_count += 1

        # Attack runs (expect invalid)
        for attack in attacks:
            r = run_distributed_pipeline(initial_input, artifacts, worker_urls,
                fault_at=attack["fault_at"], fault_type=attack["fault_type"])
            cv = verify_bundle(r["proof_bundle"], artifacts)
            detected = cv.status == "invalid"
            if detected:
                tp_count += 1
            else:
                fn_count += 1
            per_attack.append({
                "name": attack["name"],
                "client_verdict": cv.status,
                "detected": detected,
                "failure_reasons": cv.failure_reasons,
            })
            print(f"  {attack['name']:15s}: {cv.status} {'✓' if detected else '✗'}")

        total = tp_count + fp_count + fn_count + tn_count
        accuracy = (tp_count + tn_count) / total if total > 0 else 0
        precision = tp_count / (tp_count + fp_count) if (tp_count + fp_count) > 0 else 0
        recall = tp_count / (tp_count + fn_count) if (tp_count + fn_count) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        detection = {
            "true_positive": tp_count, "false_positive": fp_count,
            "false_negative": fn_count, "true_negative": tn_count,
            "accuracy": round(accuracy, 4), "precision": round(precision, 4),
            "recall": round(recall, 4), "f1_score": round(f1, 4),
            "per_attack": per_attack,
        }
        print(f"  TP={tp_count} FP={fp_count} FN={fn_count} TN={tn_count}")
        print(f"  Accuracy: {accuracy:.2%}  F1: {f1:.2%}")

        # ── Output ──
        results = {
            "num_slices": num_slices,
            "resource_profile": resource_profile,
            "resource_profile_scope": "coordinator_and_workers",
            "bundle_generation_ms": round(bundle_ms, 2),
            "client_verification_ms": round(client_verify_ms, 2),
            "client_verdict": client_result.status,
            "throughput": {
                "num_requests": num_requests,
                "total_ms": round(tp_ms, 2),
                "requests_per_sec": round(tp_rps, 4),
            },
            "detection_accuracy": detection,
            "per_slice_proof_ms": [s["prove_ms"] for s in r_normal["metrics"]["per_slice"]],
            "per_slice_exec_ms": [s["exec_ms"] for s in r_normal["metrics"]["per_slice"]],
            "total_proof_gen_ms": r_normal["metrics"]["total_prove_ms"],
        }

        metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
        os.makedirs(metrics_dir, exist_ok=True)
        out_path = os.path.join(metrics_dir, "resource_metrics.json")
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n  Results: {out_path}")
        return results

    finally:
        print("\nStopping workers...")
        _stop_workers(workers)


if __name__ == "__main__":
    run_resource_experiments()
