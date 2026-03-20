"""
v2/experiments/resource_metrics.py — 任务书要求的资源指标采集。

采集任务书明确要求的指标:
  - 证明生成时间 ✓ (已有)
  - 验证时间 ✓ (已有)
  - 推理延迟 ✓ (已有)
  - 单节点 CPU 占用率 ← 本模块补齐
  - 单节点内存占用 ← 本模块补齐 (真实峰值采样)
  - 系统吞吐量 ← 本模块补齐
  - 恶意节点检测准确率 ← 本模块补齐 (precision/recall/accuracy)
"""

import json
import os
import sys
import time
import threading

import psutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


class ResourceMonitor:
    """后台线程采样 CPU% 和 RSS 峰值。"""

    def __init__(self, interval_ms=100):
        self.interval = interval_ms / 1000
        self.cpu_samples = []
        self.rss_samples = []
        self._stop = False
        self._thread = None
        self._proc = psutil.Process()

    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=2)

    def _sample(self):
        while not self._stop:
            try:
                self.cpu_samples.append(self._proc.cpu_percent(interval=None))
                self.rss_samples.append(
                    self._proc.memory_info().rss / (1024 * 1024)
                )
            except Exception:
                pass
            time.sleep(self.interval)

    def summary(self):
        return {
            "cpu_percent_avg": round(sum(self.cpu_samples) / max(len(self.cpu_samples), 1), 2),
            "cpu_percent_max": round(max(self.cpu_samples) if self.cpu_samples else 0, 2),
            "rss_mb_avg": round(sum(self.rss_samples) / max(len(self.rss_samples), 1), 2),
            "rss_mb_peak": round(max(self.rss_samples) if self.rss_samples else 0, 2),
            "samples": len(self.cpu_samples),
        }


def run_resource_experiments(num_slices=4, num_requests=5):
    """采集完整资源指标 + 吞吐量 + 检测准确率。"""
    from v2.compile.build_circuits import load_registry
    from v2.execution.pipeline import run_certified_pipeline
    from v2.execution.deferred_pipeline import run_deferred_pipeline

    registry_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "registry", "slice_registry.json",
    )
    artifacts = load_registry(registry_path)

    input_path = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", "models", "slice_1_input.json",
    )
    with open(input_path) as f:
        initial_input = json.load(f)["input_data"][0]

    # ── 1. 资源采样 (单次 normal 请求) ──
    print("=== Resource Profiling (normal request) ===")
    monitor = ResourceMonitor(interval_ms=50)
    monitor.start()
    r_normal = run_certified_pipeline(initial_input, artifacts)
    monitor.stop()
    resource_profile = monitor.summary()
    print(f"  CPU avg: {resource_profile['cpu_percent_avg']}%")
    print(f"  CPU max: {resource_profile['cpu_percent_max']}%")
    print(f"  RSS peak: {resource_profile['rss_mb_peak']} MB")

    # ── 2. 吞吐量 (连续 N 次请求) ──
    print(f"\n=== Throughput ({num_requests} sequential requests) ===")
    throughput_start = time.perf_counter()
    for i in range(num_requests):
        run_certified_pipeline(initial_input, artifacts)
    throughput_ms = (time.perf_counter() - throughput_start) * 1000
    throughput_rps = num_requests / (throughput_ms / 1000)
    print(f"  {num_requests} requests in {throughput_ms:.0f}ms")
    print(f"  Throughput: {throughput_rps:.3f} req/s")

    # ── 3. 恶意节点检测准确率 ──
    # 使用 deferred pipeline: Stage 1 传递原始执行输出（包括篡改值），
    # linking/terminal binding 负责检测。
    # Phase A 中篡改会被 proof-bound output 直接预防（certified），
    # 所以检测统计必须用 deferred pipeline。
    print("\n=== Detection Accuracy (deferred pipeline) ===")
    attacks = [
        {"name": "tamper", "fault_at": num_slices, "fault_type": "tamper"},
        {"name": "skip", "fault_at": num_slices, "fault_type": "skip"},
        {"name": "random", "fault_at": num_slices, "fault_type": "random"},
        {"name": "replay", "fault_at": num_slices, "fault_type": "replay"},
        {"name": "tamper_mid", "fault_at": max(1, num_slices // 2), "fault_type": "tamper"},
    ]

    tp = 0  # True positive: attack present, detected (invalid)
    fp = 0  # False positive: no attack, but detected
    fn = 0  # False negative: attack present, not detected (certified)
    tn = 0  # True negative: no attack, not detected (certified)

    # Normal cases (should be certified)
    for _ in range(3):
        r = run_deferred_pipeline(initial_input, artifacts, max_prove_workers=1)
        if r["certificate"]["status"] == "certified":
            tn += 1
        else:
            fp += 1

    # Attack cases (should be invalid)
    for attack in attacks:
        r = run_deferred_pipeline(
            initial_input, artifacts,
            fault_at=attack["fault_at"],
            fault_type=attack["fault_type"],
            max_prove_workers=1,
        )
        if r["certificate"]["status"] == "invalid":
            tp += 1
        else:
            fn += 1

    total = tp + fp + fn + tn
    accuracy = (tp + tn) / total if total > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    detection = {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "note": "Phase A prevents attacks (proof-bound output replaces tampered data). "
                "Detection metrics use deferred pipeline where tampered data flows to "
                "next slice and is caught by linking/terminal binding.",
    }

    print(f"  TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"  Accuracy:  {accuracy:.2%}")
    print(f"  Precision: {precision:.2%}")
    print(f"  Recall:    {recall:.2%}")
    print(f"  F1:        {f1:.2%}")

    # ── 汇总 ──
    results = {
        "num_slices": num_slices,
        "resource_profile": resource_profile,
        "throughput": {
            "num_requests": num_requests,
            "total_ms": round(throughput_ms, 2),
            "requests_per_sec": round(throughput_rps, 4),
        },
        "detection_accuracy": detection,
        "per_slice_proof_ms": [
            s["prove_ms"] for s in r_normal["metrics"]["per_slice"]
        ],
        "per_slice_exec_ms": [
            s["exec_ms"] for s in r_normal["metrics"]["per_slice"]
        ],
        "total_proof_gen_ms": r_normal["metrics"]["total_proof_gen_ms"],
        "verification_ms": r_normal["metrics"]["verification_ms"],
    }

    metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    out_path = os.path.join(metrics_dir, "resource_metrics.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results: {out_path}")

    return results


if __name__ == "__main__":
    run_resource_experiments()
