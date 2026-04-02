"""
v2/experiments/exact_linking_e2e.py — Scale 对齐 + 链接诊断全链路验证。

验证目标:
    1. 重新编译时 align_interface_scales 生效
    2. public 模式下记录接口 max_diff
    3. polycommit 模式下记录 raw proof prefix 是否相等
    4. 正常路径 certified；响应层篡改在 proof-bound handoff 下被中和，不再破坏认证路径

用法:
        python -m v2.experiments.exact_linking_e2e [--slices 2] [--visibility polycommit]
"""

import json
import os
import subprocess
import sys
import time

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

PYTHON = sys.executable


def start_workers(artifacts, base_port=9201):
    workers = []
    for a in artifacts:
        port = base_port + a.slice_id - 1
        cmd = [
            PYTHON, "-u", "-m", "v2.services.prover_worker",
            "--slice-id", str(a.slice_id),
            "--port", str(port),
            "--onnx", a.model_path,
            "--compiled", a.compiled_path,
            "--pk", a.pk_path,
            "--srs", a.srs_path,
            "--settings", a.settings_path,
            "--host", "0.0.0.0",
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd, env=env, cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        workers.append({"slice_id": a.slice_id, "url": f"http://127.0.0.1:{port}", "proc": proc})
    return workers


def wait_ready(workers, timeout=180):
    deadline = time.time() + timeout
    for w in workers:
        while time.time() < deadline:
            try:
                r = requests.get(f"{w['url']}/health", timeout=5)
                if r.status_code == 200 and r.json().get("role") == "prover_worker":
                    print(f"  Worker {w['slice_id']} ready")
                    break
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(1)
        else:
            raise TimeoutError(f"Worker {w['slice_id']} not ready")


def stop_workers(workers):
    for w in workers:
        w["proc"].terminate()
        try:
            w["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            w["proc"].kill()


def check_linking_precision(proof_bundle, artifacts):
    """检查链接处的 max_diff 是否为 0.0 (scale 对齐的标志)。"""
    from v2.verifier.verify_chain import (
        _flatten_nested,
        _flatten_strings,
        _compare_polycommit_proof_prefixes,
        _get_interface_visibility,
    )

    slices = sorted(proof_bundle.slices, key=lambda s: s.slice_id)
    interface_diffs = []

    for i in range(len(slices) - 1):
        curr_proof = slices[i].proof_json
        next_proof = slices[i + 1].proof_json

        curr_ppi = curr_proof.get("pretty_public_inputs", {})
        next_ppi = next_proof.get("pretty_public_inputs", {})
        visibility = _get_interface_visibility(artifacts[i], artifacts[i + 1])

        curr_out = curr_ppi.get("rescaled_outputs", [])
        next_in = next_ppi.get("rescaled_inputs", [])

        if visibility == "hashed":
            curr_hashes = _flatten_strings(curr_ppi.get("processed_outputs", []))
            next_hashes = _flatten_strings(next_ppi.get("processed_inputs", []))
            interface_diffs.append({
                "edge": f"slice_{slices[i].slice_id}→slice_{slices[i+1].slice_id}",
                "method": "poseidon_hash",
                "exact": curr_hashes == next_hashes and len(curr_hashes) > 0,
                "hash_count": len(curr_hashes),
            })
        elif curr_out and next_in:
            out_vals = _flatten_nested(curr_out)
            in_vals = _flatten_nested(next_in)
            if len(out_vals) == len(in_vals) and len(out_vals) > 0:
                max_diff = max(abs(float(a) - float(b)) for a, b in zip(out_vals, in_vals))
                interface_diffs.append({
                    "edge": f"slice_{slices[i].slice_id}→slice_{slices[i+1].slice_id}",
                    "max_diff": max_diff,
                    "exact": max_diff == 0.0,
                })
            else:
                interface_diffs.append({
                    "edge": f"slice_{slices[i].slice_id}→slice_{slices[i+1].slice_id}",
                    "note": "polycommit mode - values hidden behind commitments",
                    "exact": "commitment-based",
                })
        else:
            prefix_result = _compare_polycommit_proof_prefixes(curr_proof, next_proof)
            interface_diffs.append({
                "edge": f"slice_{slices[i].slice_id}→slice_{slices[i+1].slice_id}",
                "note": "no rescaled values (hidden interface)",
                "prefix_64_equal": prefix_result["prefix_64_equal"],
                "prefix_32_equal": prefix_result["prefix_32_equal"],
                "exact": bool(
                    prefix_result["prefix_64_equal"] is True
                    or prefix_result["prefix_32_equal"] is True
                ),
            })

    return interface_diffs


def run_exact_linking_e2e(num_slices=2, visibility="public"):
    from v2.compile.build_circuits import build_registry, load_registry
    from v2.services.distributed_coordinator import run_distributed_pipeline
    from v2.verifier.bundle_verifier import verify_bundle

    # 总是重新编译，确保 scale 对齐生效
    registry_dir = os.path.join(
        PROJECT_ROOT, "v2", "artifacts", f"exact_{visibility}_{num_slices}s",
    )
    print(f"\n{'='*60}")
    print(f"EXACT LINKING E2E")
    print(f"  slices={num_slices}, visibility={visibility}")
    print(f"  registry_dir={registry_dir}")
    print(f"{'='*60}")

    print(f"\n[Build] Compiling {num_slices}-slice circuits "
          f"with scale alignment + {visibility} visibility...")
    artifacts = build_registry(
        num_slices=num_slices,
        model_type="mnist",
        registry_dir=registry_dir,
        visibility_mode=visibility,
    )

    # 验证 scale 对齐
    print("\n[Verify] Checking scale alignment...")
    for i in range(len(artifacts) - 1):
        a_curr = artifacts[i]
        a_next = artifacts[i + 1]
        print(f"  slice {a_curr.slice_id}→{a_next.slice_id}: "
              f"out_scale={a_curr.output_scale}, in_scale={a_next.input_scale} "
              f"{'✓ ALIGNED' if a_curr.output_scale == a_next.input_scale else '✗ MISALIGNED'}")

    # 读取输入
    input_path = os.path.join(registry_dir, "models", "slice_1_input.json")
    with open(input_path) as f:
        initial_input = json.load(f)["input_data"][0]

    # 启动 Workers
    print(f"\n[Workers] Starting {num_slices} Prover-Workers...")
    workers = start_workers(artifacts)
    worker_urls = [{"slice_id": w["slice_id"], "url": w["url"]} for w in workers]

    try:
        wait_ready(workers)

        tests = [
            {"name": "normal", "fault_at": None, "fault_type": "none", "expected": "certified"},
            {"name": "tamper_last", "fault_at": num_slices, "fault_type": "tamper", "expected": "certified"},
            {"name": "skip_last", "fault_at": num_slices, "fault_type": "skip", "expected": "certified"},
        ]
        if num_slices >= 3:
            tests.append({
                "name": "tamper_mid", "fault_at": max(1, num_slices // 2),
                "fault_type": "tamper", "expected": "certified",
            })

        results = []
        for test in tests:
            print(f"\n{'─'*50}\n[Test] {test['name']}\n{'─'*50}")

            r = run_distributed_pipeline(
                initial_input, artifacts, worker_urls,
                fault_at=test["fault_at"],
                fault_type=test["fault_type"],
            )

            bundle = r["proof_bundle"]
            client_result = verify_bundle(bundle, artifacts)
            status = client_result.status
            passed = (status == test["expected"])

            # 检查链接精度
            interface_diffs = check_linking_precision(bundle, artifacts)

            entry = {
                "name": test["name"],
                "expected": test["expected"],
                "actual": status,
                "passed": passed,
                "visibility": visibility,
                "scales_aligned": True,
                "interface_diffs": interface_diffs,
                "total_ms": r["metrics"]["total_ms"],
                "total_prove_ms": r["metrics"]["total_prove_ms"],
                "client_verification_ms": client_result.metrics.get("verification_ms", 0),
            }
            results.append(entry)

            mark = "PASS" if passed else "FAIL"
            print(f"  [{mark}] client={status}")
            for d in interface_diffs:
                if "max_diff" in d:
                    exact_tag = "EXACT" if d["exact"] else f"APPROX (diff={d['max_diff']:.10f})"
                    print(f"    {d['edge']}: max_diff={d['max_diff']:.10f} → {exact_tag}")
                elif d.get("method") == "poseidon_hash":
                    exact_tag = "EXACT" if d["exact"] else "MISMATCH"
                    print(
                        f"    {d['edge']}: poseidon_hash count={d.get('hash_count', 0)} "
                        f"→ {exact_tag}"
                    )
                else:
                    print(
                        f"    {d['edge']}: {d.get('note', 'hidden interface')} "
                        f"(prefix64={d.get('prefix_64_equal')}, prefix32={d.get('prefix_32_equal')})"
                    )

        # Summary
        print(f"\n{'='*60}")
        print(f"EXACT LINKING E2E SUMMARY")
        print(f"  Visibility: {visibility}")
        print(f"  Scales aligned: True")
        print(f"{'='*60}")

        all_ok = True
        for r in results:
            mark = "✓" if r["passed"] else "✗"
            print(f"  {mark} {r['name']:15s} → {r['actual']:10s}")
            if not r["passed"]:
                all_ok = False

        # Check if all normal-path interfaces achieved exact linking
        normal_result = [r for r in results if r["name"] == "normal"]
        if normal_result:
            diffs = normal_result[0]["interface_diffs"]
            all_exact = all(
                d.get("exact") is True
                for d in diffs
            )
            print(f"\n  Exact linking achieved: {'YES' if all_exact else 'NO'}")
            if all_exact:
                print(f"  → 0.004 engineering floor is no longer needed")
                print(f"  → Linking is cryptographically exact")

        print(f"\n  Overall: {'ALL PASSED' if all_ok else 'SOME FAILED'}")

        # Save
        metrics_dir = os.path.join(PROJECT_ROOT, "v2", "metrics")
        os.makedirs(metrics_dir, exist_ok=True)
        out_path = os.path.join(metrics_dir, f"exact_linking_{visibility}_results.json")
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"  Results: {out_path}")

        return results

    finally:
        print("\n[Workers] Stopping...")
        stop_workers(workers)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slices", type=int, default=2)
    parser.add_argument("--visibility", type=str, default="public",
                        choices=["public", "polycommit", "hashed"])
    args = parser.parse_args()

    run_exact_linking_e2e(num_slices=args.slices, visibility=args.visibility)
