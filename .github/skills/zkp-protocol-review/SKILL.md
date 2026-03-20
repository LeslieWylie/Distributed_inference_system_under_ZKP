---
name: zkp-protocol-review
description: "Review and validate the deferred certification protocol against security requirements. Use when: writing thesis security sections, preparing defense, reviewing threat model, checking end-to-end statement, validating commitment linking, auditing protocol correctness, comparing with NanoZK/DSperse/zkGPT, writing related work, ensuring claims match implementation, checking non-composability implications."
---

# ZKP Protocol Review & Thesis Security Validation

## When to Use
- Writing or reviewing thesis security analysis sections
- Preparing for defense Q&A on protocol correctness
- Validating that code implementation matches claimed security properties
- Comparing with related work (NanoZK, DSperse, zkGPT, VeriLLM)
- Checking whether a claim is safe to make in the thesis

## End-to-End Statement (must hold for CERTIFIED status)

For request `req_id`, slices `M_1,...,M_n`, input `x_0`:
1. Each proof `π_i` passes `ezkl.verify()` with registry's `vk_i` ✓
2. `model_digest(M_i)` matches registry ✓
3. `|rescaled_outputs(π_i) - rescaled_inputs(π_{i+1})| < ε` for all adjacent pairs ✓
4. `|rescaled_outputs(π_n) - provisional_output| < ε` ✓

**ε = 0.01** (covers quantization precision ~1/2^13, far below any attack magnitude)

## Claims You CAN Safely Make

| Claim | Justification |
|---|---|
| All slices eventually produce proof | Code: `pipeline.py` proves every slice |
| Independent verifier | `verify_single.py` uses registry vk, not Worker self-report |
| Adjacent commitment linking | `verify_chain.py` Step 2: rescaled value comparison |
| Terminal binding | `verify_chain.py` Step 3: proof output ≈ provisional output |
| model_digest integrity | `verify_chain.py` Step 1a: re-computes ONNX file hash |
| Provisional ≠ certified | State machine in `types.py`, dual-output in `deferred_pipeline.py` |
| 6/6 attack detection | Experimental evidence in `e2e_certified_results.json` |

## Claims You MUST NOT Make

| Claim | Why Not |
|---|---|
| "Layerwise tolerance implies e2e correctness" | Refuted by Zamir 2026 Non-Composability Note |
| "Hash chain provides adversarial security" | Colluding adjacent nodes can forge consistent chain |
| "Worker `verified=True` means correct" | Worker is untrusted; only verifier result counts |
| "Selective verification is end-to-end safe" | DSperse itself acknowledges this is a tradeoff |
| "EZKL aggregation is available in 23.0.5" | `hasattr(ezkl, 'aggregate')` returns False |
| "Hashed mode provides commitment chain" | Independent calibration breaks cross-circuit Poseidon |

## Threat Model Quick Reference

| Adversary | Mitigated By |
|---|---|
| Forged output | Proof soundness + terminal binding |
| Model substitution | model_digest re-verification |
| Replay | req_id domain separation in commitment |
| Colluding Workers | Both must produce valid individual proofs |
| Worker self-declaring verified | Worker role is prover only, not verifier |

See full details: [v2/docs/threat_model.md](../../v2/docs/threat_model.md)

## Literature Alignment Checklist

- [ ] **NanoZK**: Our commitment linking is analogous to their SHA-256 commitment chain
- [ ] **DSperse**: We cite selective verification as baseline, not as our main contribution  
- [ ] **Non-Composability Note**: We prove exact quantized circuit statements, not approximate
- [ ] **Artemis**: We mention polycommit + CP-SNARK as future upgrade direction
- [ ] **zkGPT/zkLLM**: We position as "layerwise distributed" vs their "monolithic" approach

## Key References
- Protocol: [v2/docs/protocol.md](../../v2/docs/protocol.md)
- Threat Model: [v2/docs/threat_model.md](../../v2/docs/threat_model.md)
- Refactoring Guide: [docs/refactor/claude_refactor_guide_merged.md](../../docs/refactor/claude_refactor_guide_merged.md)
- Deep Research: [survey/reference/DEEP_RESEARCH_GQ1_GQ5_2026-03-20.md](../../survey/reference/DEEP_RESEARCH_GQ1_GQ5_2026-03-20.md)
