# Project: 面向分布式推理的零知识证明框架

## Architecture
This is a ZKP-based verifiable distributed inference system with a **Prover-Worker architecture** (v2/).
Each Worker executes ONNX inference AND generates EZKL proof locally, distributing proof overhead.
The old system (distributed/, scripts/) and legacy v2 services (execution_worker.py, master_coordinator.py) are retained as baseline only.

## Core Principles
1. **All slices must eventually produce proof** — no permanent light nodes
2. **Worker never self-declares correctness** — independent Verifier only
3. **Commitment linking via proof public instances** — not external hash chains
4. **Circuit correctness ≠ float fidelity** — always separate these two concepts
5. **Worker proves locally** — Master never generates proofs; proof overhead distributed

## Tech Stack
- Python 3.13 (Miniconda), EZKL 23.0.5 (Halo2/PLONK/KZG), PyTorch 2.10, torchvision, ONNX, onnxruntime
- Windows environment: always set `PYTHONIOENCODING=utf-8` and `HOME` before EZKL
- Model: MNIST MLP (109K params, 784→128→ReLU→64→ReLU→10), 2-slice configuration

## Code Organization
- `v2/services/prover_worker.py` — Prover-Worker: inference + proof (active)
- `v2/services/distributed_coordinator.py` — Master coordinator (active)
- `v2/services/workers.json` — Worker IP/port configuration
- `v2/compile/` — Offline circuit compilation with MNIST MLP support
- `v2/verifier/` — Independent verification + chain linking
- `v2/experiments/` — E2E experiments (refactored_e2e.py, smoke_test.py)
- `models/mnist_model.py` — MNIST MLP model definition + slicing
- `models/configurable_model.py` — Legacy toy model (baseline only)
- `distributed/`, `scripts/` — Old v1 baseline (read-only reference)
- `docs/refactor/` — Design documents
- `survey/` — Literature and thesis materials

## EZKL Notes
- EZKL 23.0.5: `aggregate()` NOT available (confirmed via hasattr check)
- `swap_proof_commitments()` IS available — the real linking API
- `hashed` visibility causes cross-circuit linking failure (independent quantization scales)
- Use `public` visibility for current linking; `polycommit` for future upgrade
- PyTorch 2.10 ONNX export: must use `dynamo=False` for EZKL tract compatibility
