---
name: zkp-compile
description: "Build EZKL circuits, manage slice registry, and handle model compilation for the v2 architecture. Use when: compiling circuits, building registry, exporting ONNX slices, fixing scale alignment, debugging EZKL gen_settings/calibrate/compile/setup, checking slice_registry.json, model_digest computation, visibility mode configuration (public/hashed/polycommit), understanding quantization scales."
---

# ZKP Circuit Compilation & Registry Management

## When to Use
- Building or rebuilding EZKL circuits for model slices
- Debugging compilation failures (scale errors, calibration issues)
- Managing `slice_registry.json`
- Configuring EZKL visibility modes
- Aligning quantization scales across slices

## EZKL Compilation Pipeline
```
export_slices() → gen_settings() → calibrate() → compile() → get_srs() → setup()
```

Each step in `v2/compile/build_circuits.py`.

## Visibility Mode Decision

| Mode | Input | Output | Use Case | Linking |
|---|---|---|---|---|
| `public` | public | public | **Current v2 default** | rescaled float ≈-comparison |
| `hashed` | hashed/public | hashed/public | Poseidon commitment | Cross-circuit scale mismatch ⚠️ |
| `polycommit` | polycommit | polycommit | **Future upgrade** | `swap_proof_commitments()` |

**Why `public` not `hashed`**: EZKL independently calibrates each slice, producing different quantization scales. Same tensor → different Poseidon hash in two circuits. `public` mode still binds values via proof soundness.

## Registry Structure
```json
{
  "slice_id": 1,
  "model_path": "...",
  "compiled_path": "...",
  "settings_path": "...",
  "pk_path": "...",
  "vk_path": "...",
  "srs_path": "...",
  "model_digest": "SHA-256(ONNX file)",
  "input_scale": 13,
  "output_scale": null,
  "param_scale": 13
}
```

## Common Issues

### Scale Mismatch
If linking fails between slices, check `input_scale` in settings.json for each slice. EZKL calibration may assign different scales.

**Fix**: Force consistent scale via `run_args.input_scale = prev_settings["model_output_scales"][0]` (see EZKL `proof_splitting.ipynb`).

### Calibration Errors
`[tensor] decomposition error: integer X is too large` — these are calibration warnings, not failures. EZKL tries multiple scale combinations and reports failures for those that don't work.

### model_digest Verification
`compute_file_digest()` in `v2/common/commitments.py` computes SHA-256 of the ONNX file. The verifier re-computes this at chain verification time to detect model substitution.

## Rebuild Commands
```powershell
$PY = "C:\Users\$env:USERNAME\AppData\Local\miniconda3\python.exe"
# Rebuild 4-slice circuits
& $PY -u -m v2.experiments.e2e_certified --slices 4 --rebuild
# Or programmatically
& $PY -c "from v2.compile.build_circuits import build_registry; build_registry(4)"
```

## Future: polycommit + swap_proof_commitments
EZKL's `proof_splitting.ipynb` shows the upgrade path:
1. Use `polycommit` visibility for I/O
2. After proving, swap commitments: `witness["processed_inputs"] = prev_witness["processed_outputs"]`
3. Call `ezkl.swap_proof_commitments(proof_path, witness_path)`
This achieves cryptographically exact commitment linking (no ε-comparison needed).
