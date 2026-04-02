"""Minimal smoke test: normal + tamper + skip with Prover-Worker + client verification."""
import json, os, sys, subprocess, time, requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
PYTHON = sys.executable

from v2.compile.build_circuits import load_registry

registry_path = os.path.join(PROJECT_ROOT, "v2", "artifacts", "registry", "slice_registry.json")
artifacts = load_registry(registry_path)

with open(os.path.join(PROJECT_ROOT, "v2", "artifacts", "models", "slice_1_input.json")) as f:
    initial_input = json.load(f)["input_data"][0]

print(f"Input dim: {len(initial_input)}, Slices: {len(artifacts)}")

# Start workers
workers = []
for a in artifacts:
    port = 9001 + a.slice_id - 1
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

# Wait for workers
for w in workers:
    for _ in range(60):
        try:
            r = requests.get(f"{w['url']}/health", timeout=3)
            if r.status_code == 200:
                print(f"Worker {w['slice_id']} ready")
                break
        except:
            pass
        time.sleep(1)

try:
    from v2.services.distributed_coordinator import run_distributed_pipeline
    from v2.verifier.bundle_verifier import verify_bundle
    worker_urls = [{"slice_id": w["slice_id"], "url": w["url"]} for w in workers]

    # Test 1: Normal
    print("\n=== TEST: NORMAL ===")
    r1 = run_distributed_pipeline(initial_input, artifacts, worker_urls)
    cv1 = verify_bundle(r1["proof_bundle"], artifacts)
    s1 = cv1.status
    print(f"Client verdict: {s1} (expected: certified) {'PASS' if s1=='certified' else 'FAIL'}")

    # Test 2: Tamper last
    print("\n=== TEST: TAMPER LAST ===")
    r2 = run_distributed_pipeline(initial_input, artifacts, worker_urls, fault_at=2, fault_type="tamper")
    cv2 = verify_bundle(r2["proof_bundle"], artifacts)
    s2 = cv2.status
    print(f"Client verdict: {s2} (expected: invalid) {'PASS' if s2=='invalid' else 'FAIL'}")

    # Test 3: Skip
    print("\n=== TEST: SKIP LAST ===")
    r3 = run_distributed_pipeline(initial_input, artifacts, worker_urls, fault_at=2, fault_type="skip")
    cv3 = verify_bundle(r3["proof_bundle"], artifacts)
    s3 = cv3.status
    print(f"Client verdict: {s3} (expected: invalid) {'PASS' if s3=='invalid' else 'FAIL'}")

    # Summary
    results = [
        {"name": "normal", "expected": "certified", "actual": s1},
        {"name": "tamper_last", "expected": "invalid", "actual": s2},
        {"name": "skip_last", "expected": "invalid", "actual": s3},
    ]
    all_pass = all(r["expected"] == r["actual"] for r in results)
    print(f"\n{'='*40}")
    print(f"SMOKE TEST (client-verified): {'ALL PASSED' if all_pass else 'SOME FAILED'}")
    for r in results:
        mark = "✓" if r["expected"] == r["actual"] else "✗"
        print(f"  {mark} {r['name']}: client={r['actual']}")

    # Save
    out = os.path.join(PROJECT_ROOT, "v2", "metrics", "smoke_test_results.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump({"results": results, "all_pass": all_pass,
                   "verification_mode": "client_side",
                   "metrics": {"normal": r1["metrics"], "tamper": r2["metrics"], "skip": r3["metrics"]}},
                  f, indent=2, default=str)
    print(f"Saved: {out}")

finally:
    for w in workers:
        w["proc"].terminate()
        try:
            w["proc"].wait(timeout=5)
        except:
            w["proc"].kill()
    print("Workers stopped.")
