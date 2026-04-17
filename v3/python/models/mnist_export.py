"""Quantize the v2 MNIST MLP and export slice weights for v3-circuit.

Output JSON schema follows ``docs/refactor/v3/99-interfaces.md §1``::

    {
      "version": "v3-models-0.1",
      "model_id": "mnist_mlp_v3",
      "scale": 16,
      "num_slices": 2,
      "slices": [
        {
          "index": 0,
          "input_dim": 784,
          "output_dim": 64,
          "layers": [
            {"type": "linear", "weight": [[int,...],...], "bias": [int,...]},
            {"type": "relu"},
            ...
          ]
        },
        ...
      ]
    }

Phase 2 convention (documented in ``v3/rust/crates/v3-circuit/README.md``):

* ``weight`` is quantized at scale ``s``:  ``w_int = round(w_float * 2^s)``.
* ``bias``   is quantized at scale ``2s``: ``b_int = round(b_float * 2^(2s))``.
  Biases live at ``2s`` because the linear accumulator is computed at ``2s``
  before the shift-right-by-``s`` truncation. This lets the circuit add bias
  with one constraint instead of having to re-scale.

The script is deterministic: it only reads the cached ``full_model_state.pt``
trained by v2's ``models/mnist_model.py`` (which is read-only for Phase 2).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import torch

# Add repo root to sys.path so we can import the read-only v2 model module.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from models.mnist_model import (  # type: ignore  # noqa: E402
    MODEL_CACHE_PATH,
    MnistMLP,
    load_model_checkpoint,
)


VERSION = "v3-models-0.1"
MODEL_ID = "mnist_mlp_v3"
DEFAULT_SCALE = 16
DEFAULT_OUT_DIR = _REPO_ROOT / "v3" / "artifacts" / "models"


def quantize_int(x: np.ndarray, scale: int) -> np.ndarray:
    """Round to nearest integer at fixed-point scale ``2**scale``."""
    return np.round(x.astype(np.float64) * (1 << scale)).astype(np.int64)


def _linear_entry(layer: torch.nn.Linear, scale: int) -> dict:
    w = layer.weight.detach().cpu().numpy()  # (out, in)
    b = (
        layer.bias.detach().cpu().numpy()
        if layer.bias is not None
        else np.zeros((w.shape[0],), dtype=np.float64)
    )
    w_int = quantize_int(w, scale)
    b_int_2s = quantize_int(b, 2 * scale)
    return {
        "type": "linear",
        "weight": w_int.tolist(),  # list[list[int]]
        "bias": b_int_2s.tolist(),  # list[int]
    }


def _build_slice_entry(
    index: int,
    input_dim: int,
    output_dim: int,
    pytorch_layers: Iterable[torch.nn.Module],
    scale: int,
) -> dict:
    layers_json: list[dict] = []
    for layer in pytorch_layers:
        if isinstance(layer, torch.nn.Linear):
            layers_json.append(_linear_entry(layer, scale))
        elif isinstance(layer, torch.nn.ReLU):
            layers_json.append({"type": "relu"})
        else:
            raise ValueError(f"unsupported layer type {type(layer).__name__}")
    return {
        "index": index,
        "input_dim": input_dim,
        "output_dim": output_dim,
        "layers": layers_json,
    }


def load_trained_model(state_path: str | None = None) -> MnistMLP:
    path = state_path or MODEL_CACHE_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"trained MNIST MLP state not found at {path}. "
            "Run `python -m v2.experiments.refactored_e2e --slices 2` (or "
            "`python -m models.mnist_model --slices 2`) once to populate the "
            "cache."
        )
    checkpoint = load_model_checkpoint(path)
    state_dict = checkpoint.get("state_dict", checkpoint)
    model = MnistMLP()
    model.load_state_dict(state_dict)
    model.eval()
    return model


def export_slices(model: MnistMLP, scale: int) -> dict:
    """Produce the full export JSON dict for the given trained MnistMLP."""
    all_layers = list(model.layers)
    # Canonical 2-slice split, matching 99-interfaces.md example:
    #   slice 0: Linear(784->128), ReLU, Linear(128->64), ReLU   -> indices [0..4)
    #   slice 1: Linear(64->10)                                  -> indices [4..5)
    slice_specs = [
        (0, 784, 64, all_layers[0:4]),
        (1, 64, 10, all_layers[4:5]),
    ]
    slices_json = [
        _build_slice_entry(idx, in_dim, out_dim, layers, scale)
        for idx, in_dim, out_dim, layers in slice_specs
    ]
    return {
        "version": VERSION,
        "model_id": MODEL_ID,
        "scale": scale,
        "num_slices": len(slices_json),
        "slices": slices_json,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Quantize MNIST MLP slices for v3.")
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE)
    parser.add_argument(
        "--state-path",
        type=str,
        default=MODEL_CACHE_PATH,
        help="path to the trained full_model_state.pt",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(DEFAULT_OUT_DIR),
        help="directory to write mnist_mlp_v3_slices.json",
    )
    args = parser.parse_args()

    model = load_trained_model(args.state_path)
    payload = export_slices(model, args.scale)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{MODEL_ID}_slices.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    size = out_path.stat().st_size
    print(
        f"[mnist_export] wrote {out_path} "
        f"(scale={args.scale}, num_slices={payload['num_slices']}, bytes={size})"
    )


if __name__ == "__main__":
    main()
