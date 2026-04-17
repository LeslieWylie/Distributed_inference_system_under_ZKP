"""Generate 100 MNIST test cases for the v3-circuit consistency suite.

Output JSON schema follows ``docs/refactor/v3/99-interfaces.md §2``::

    {
      "version": "v3-cases-0.1",
      "scale": 16,
      "num_cases": 100,
      "cases": [
        {
          "input": [...784 ints at scale s...],
          "slice_outputs": [
            [...64 ints at scale s...],   // after slice 0 (post-ReLU)
            [...10 ints at scale s...]    // after slice 1 (raw logits)
          ],
          "float_output": [...10 floats...],
          "pytorch_pred": 7
        },
        ...
      ]
    }

The fixed-point forward pass used here MUST match
``v3-circuit::mnist_slice::MnistSliceXCircuit::step_native`` bit-for-bit.

Semantics (a.k.a. the "Slice Boundary Convention", documented in
``v3/rust/crates/v3-circuit/README.md``):

* Inputs are quantized at scale ``s`` (``x_int = round(x_float * 2^s)``).
* Linear layer: ``y_2s = W_int @ x_int + b_int_2s``. ``W_int`` is at scale
  ``s``, ``b_int_2s`` at scale ``2s`` (so it can be added directly).
* Shift: ``y_int = y_2s >> s`` using arithmetic (floor-division) shift.
* ReLU: ``z_int = max(y_int, 0)``.
* Slice boundary: slice 0 emits the post-ReLU 64-vector; slice 1 consumes
  exactly that same vector as its 64-int input (no re-quantization, no
  padding). Slice 1's output is the raw 10-logit vector (no final ReLU).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import torch

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from models.mnist_model import sample_mnist_inputs  # type: ignore  # noqa: E402
from v3.python.models.mnist_export import (  # noqa: E402
    DEFAULT_OUT_DIR,
    DEFAULT_SCALE,
    MODEL_ID,
    export_slices,
    load_trained_model,
    quantize_int,
)

VERSION = "v3-cases-0.1"
DEFAULT_NUM_CASES = 100
DEFAULT_SEED = 42


def _forward_slice_fixed_point(
    x_int: np.ndarray, layers: list[dict], scale: int
) -> np.ndarray:
    """Run one slice in fixed-point integer arithmetic.

    ``x_int`` is (input_dim,) int64 at scale ``s``.
    Returns (output_dim,) int64 at scale ``s``.
    """
    cur = x_int.astype(np.int64)
    for layer in layers:
        lt = layer["type"]
        if lt == "linear":
            w = np.asarray(layer["weight"], dtype=np.int64)  # (out, in)
            b = np.asarray(layer["bias"], dtype=np.int64)    # (out,) at scale 2s
            # y_2s has dtype int64; weights and inputs are bounded so the
            # accumulator stays well below 2**62 for our MNIST MLP.
            y_2s = w @ cur + b
            cur = y_2s >> scale  # arithmetic (floor) right-shift for signed
        elif lt == "relu":
            cur = np.maximum(cur, np.int64(0))
        else:
            raise ValueError(f"unsupported layer type {lt}")
    return cur


def build_cases(
    model, slices_payload: dict, num_cases: int, seed: int
) -> list[dict]:
    scale = slices_payload["scale"]
    samples = sample_mnist_inputs(num_samples=num_cases, seed=seed)

    cases: list[dict] = []
    for sample in samples:
        x_float = np.asarray(sample["input_tensor"], dtype=np.float32)  # (784,)
        x_int = quantize_int(x_float, scale)

        # Fixed-point forward through each slice.
        slice_outputs: list[list[int]] = []
        cur = x_int
        for slc in slices_payload["slices"]:
            cur = _forward_slice_fixed_point(cur, slc["layers"], scale)
            slice_outputs.append(cur.astype(np.int64).tolist())

        # Floating-point ground truth from the full PyTorch model.
        with torch.no_grad():
            x_t = torch.tensor(x_float, dtype=torch.float32).unsqueeze(0)
            y_float = model(x_t).squeeze(0).cpu().numpy()

        cases.append(
            {
                "input": x_int.astype(np.int64).tolist(),
                "slice_outputs": slice_outputs,
                "float_output": [float(v) for v in y_float.tolist()],
                "pytorch_pred": int(np.argmax(y_float)),
                "label": int(sample["label"]),
            }
        )
    return cases


def dequantized_error_report(slices_payload: dict, cases: list[dict]) -> dict:
    """Compute max/avg ε = |dequantized_circuit_output - pytorch_float|."""
    scale = slices_payload["scale"]
    denom = float(1 << scale)
    per_case_err: list[float] = []
    pred_match = 0
    worst_case_idx = -1
    worst_err = 0.0
    for idx, case in enumerate(cases):
        logits_int = np.asarray(case["slice_outputs"][-1], dtype=np.int64)
        logits_dequant = logits_int.astype(np.float64) / denom
        logits_float = np.asarray(case["float_output"], dtype=np.float64)
        err = float(np.max(np.abs(logits_dequant - logits_float)))
        per_case_err.append(err)
        if err > worst_err:
            worst_err = err
            worst_case_idx = idx
        circuit_pred = int(np.argmax(logits_dequant))
        if circuit_pred == case["pytorch_pred"]:
            pred_match += 1
    return {
        "max_epsilon": worst_err,
        "mean_epsilon": float(np.mean(per_case_err)),
        "worst_case_index": worst_case_idx,
        "pred_match_rate": pred_match / max(1, len(cases)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate MNIST v3 test cases.")
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE)
    parser.add_argument("--num-cases", type=int, default=DEFAULT_NUM_CASES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out-dir", type=str, default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args()

    model = load_trained_model()
    slices_payload = export_slices(model, args.scale)
    cases = build_cases(model, slices_payload, args.num_cases, args.seed)

    report = dequantized_error_report(slices_payload, cases)

    payload = {
        "version": VERSION,
        "scale": args.scale,
        "num_cases": len(cases),
        "seed": args.seed,
        "model_id": MODEL_ID,
        "cases": cases,
        "report": report,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{MODEL_ID}_cases.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    size = out_path.stat().st_size
    print(
        f"[gen_test_cases] wrote {out_path} "
        f"(scale={args.scale}, num_cases={len(cases)}, bytes={size})"
    )
    print(
        f"[gen_test_cases] epsilon report:  max={report['max_epsilon']:.6f}  "
        f"mean={report['mean_epsilon']:.6f}  "
        f"worst_idx={report['worst_case_index']}  "
        f"pred_match_rate={report['pred_match_rate']:.3f}"
    )


if __name__ == "__main__":
    main()
