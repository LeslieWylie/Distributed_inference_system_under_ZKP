---
name: zkp-experiment
description: "Run and analyze v2 experiments for the deferred certification architecture. Use when: running experiments, analyzing results, comparing baseline vs v2, generating experiment data, checking G2 correctness, G3 latency, G4 scalability, fidelity F1/F2/F3, interpreting metrics JSON, adding new attack scenarios."
---

# ZKP Experiment Runner & Analyzer

## When to Use
- Running any v2 experiment (correctness, latency, fidelity, scalability)
- Analyzing experiment results from `v2/metrics/*.json`
- Adding new attack types or experiment configurations
- Comparing v1 baseline with v2 deferred certification results

## Key Constraints
1. **Never mix v1 and v2 results** in the same conclusion — v1 is baseline only
2. **Never use `worker returned verified`** as a security metric — only independent verifier results
3. **Separate circuit correctness from float fidelity** — they are different dimensions
4. **All fault injection must use correct parameter names** (`fault_type`, not `fault`)

## Experiment Categories

### G2: Protocol Correctness
Validates end-to-end security: all attacks must produce `status: invalid`.
```powershell
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
& $PY -u -m v2.experiments.e2e_certified --slices 4
& $PY -u -m v2.experiments.deferred_certified
```

### G3: Latency Decomposition
Measures execution vs proving vs verification independently.
Key metrics: `provisional_latency_ms`, `certification_ms`, `proving_ms`

### G4: Scalability
Tests 2/4/8 slices. Requires circuit rebuild per config.
```powershell
& $PY -u -m v2.experiments.scalability
```

### F1/F2/F3: Fidelity (三层分离)
- **F1**: Float model vs sliced float model (should be 0.0)
- **F2**: Float model vs EZKL quantized circuit output
- **F3**: Float model vs certified pipeline output
```powershell
& $PY -u -m v2.experiments.fidelity
```

## Result Files
| File | Content |
|---|---|
| `v2/metrics/e2e_certified_results.json` | Phase A correctness |
| `v2/metrics/deferred_certified_results.json` | Phase B/C correctness + latency |
| `v2/metrics/fidelity_results.json` | F1/F2/F3 |
| `v2/metrics/scalability_results.json` | 2/4/8 slices |

## Adding New Experiments
1. Create script in `v2/experiments/`
2. Use `run_certified_pipeline()` or `run_deferred_pipeline()` — never simplified pipeline
3. Results must include `certificate.status`, not `worker.verified`
4. Write results to `v2/metrics/` as JSON

## Verification Checklist
After running experiments, verify:
- [ ] All `normal` cases → `certified`
- [ ] All attack cases → `invalid`
- [ ] `provisional_latency_ms` < 100ms for 4 slices
- [ ] `certification_ms` includes proving + verification only
- [ ] Fidelity F1 = 0.0 (bit-exact partition)
