"""Phase B/C quick smoke test: normal + tamper with subprocess parallel proving."""
import sys, os, json
sys.path.insert(0, "C:\\ZKP")


def main():
    from v2.compile.build_circuits import load_registry
    from v2.execution.deferred_pipeline import run_deferred_pipeline

    artifacts = load_registry("C:\\ZKP\\v2\\artifacts\\registry\\slice_registry.json")
    with open("C:\\ZKP\\v2\\artifacts\\models\\slice_1_input.json") as f:
        inp = json.load(f)["input_data"][0]

    print("=== Normal (2 subprocess workers) ===")
    r1 = run_deferred_pipeline(inp, artifacts, max_prove_workers=2)
    s1 = r1["certificate"]["status"]
    print(f"Status: {s1}")
    print(f"Provisional latency: {r1['metrics']['execution_ms']:.0f}ms")
    print(f"Proving (wall): {r1['metrics']['proving_ms']:.0f}ms")
    print(f"Certification: {r1['metrics']['certification_ms']:.0f}ms")

    print("\n=== Tamper (2 subprocess workers) ===")
    r2 = run_deferred_pipeline(inp, artifacts, fault_at=4, fault_type="tamper",
                               max_prove_workers=2)
    s2 = r2["certificate"]["status"]
    print(f"Status: {s2}")

    passed = (s1 == "certified" and s2 == "invalid")
    print(f"\nPhase C validation: {'PASSED' if passed else 'FAILED'}")


if __name__ == "__main__":
    main()
