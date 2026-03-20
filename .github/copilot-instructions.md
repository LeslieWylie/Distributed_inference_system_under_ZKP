# Project: 面向分布式推理的零知识证明框架

## Architecture
This is a ZKP-based verifiable distributed inference system with a **deferred certification architecture** (v2/).
The old system (distributed/, scripts/) is retained as baseline only.

## Core Principles
1. **All slices must eventually produce proof** — no permanent light nodes
2. **Worker never self-declares correctness** — independent Verifier only
3. **Commitment linking via proof public instances** — not external hash chains
4. **Circuit correctness ≠ float fidelity** — always separate these two concepts
5. **Statement-first, protocol-first, verifier-first**

## Tech Stack
- Python 3.13 (Miniconda), EZKL 23.0.5 (Halo2/PLONK/KZG), PyTorch, ONNX, onnxruntime
- Windows environment: always set `PYTHONIOENCODING=utf-8` and `HOME` before EZKL

## Code Organization
- `v2/` — New architecture (active development)
- `distributed/`, `scripts/` — Old baseline (read-only reference)
- `models/` — Shared model definitions
- `docs/refactor/` — Design documents
- `survey/` — Literature and thesis materials

## EZKL Notes
- EZKL 23.0.5: `aggregate()` NOT available (confirmed via hasattr check)
- `swap_proof_commitments()` IS available — the real linking API
- `hashed` visibility causes cross-circuit linking failure (independent quantization scales)
- Use `public` visibility for current linking; `polycommit` for future upgrade
