# Threat Model

## 1. Adversary Capabilities

The system assumes the following adversary capabilities:

| Capability | Assumed? | Mitigation |
|---|:---:|---|
| Return forged output from any slice | ✓ | Proof soundness + terminal binding (proof generated at Worker, not Master) |
| Forge SHA-256 hashes | ✓ | Proof binds public instances, not external hashes |
| Produce valid proof but send fake output downstream | ✓ | Adjacent linking: rescaled_outputs[π_i] ≈ rescaled_inputs[π_{i+1}]. Both values extracted from Worker-generated proofs by independent Verifier |
| Collude with adjacent Worker | ✓ | Both slices must produce individually valid proofs; linking verified independently |
| Replay old request outputs | ✓ | req_id in commitment provides domain separation |
| Claim `verified=True` without running verifier | ✓ | Worker generates proof only; verification is independent |
| Replace slice model or quantization settings | ✓ | model_digest re-computed and checked at verification time |
| Return tampered output with honest proof | ✓ | Terminal binding: Verifier extracts rescaled_outputs from proof, compares to Worker-declared output. Mismatch → INVALID |
| Refuse to compute (availability attack) | ✓ | Out of scope — correctness guarantee, not availability |

## 2. Trust Assumptions

| Component | Trust Level | Justification |
|---|---|---|
| ZKP backend (EZKL/Halo2) | Computationally sound | Standard cryptographic assumption |
| SHA-256 | Collision-resistant | Standard |
| Client-side Verifier | Trusted | Local verification program; the only final trust authority |
| Coordinator | **Untrusted** | Untrusted bundle assembler; its advisory is non-authoritative |
| Artifact Registry | Tamper-proof after setup | Static VK/PK/SRS/model_digest fixed at compile time |
| Prover-Workers | **Untrusted** | May be malicious; correctness enforced by proof soundness + linking |
| Network | **Untrusted** | Transport integrity not assumed; client verifies proof evidence directly |

**Minimal trust root**: client local verification program + registry artifacts + cryptographic assumptions.

## 3. What the System Proves

For each certified request:

> Every slice `i` was computed correctly according to the registered model `M_i`,
> adjacent slice outputs flow into the next slice's inputs without substitution,
> and the user's final output matches the last slice's proven computation —
> all within the quantized circuit semantics.

## 4. What the System Does NOT Prove

1. **Float-model fidelity**: The certified output may differ from the original
   floating-point model due to EZKL quantization. Fidelity is measured separately (F1/F2/F3).
2. **Privacy**: Public visibility mode exposes intermediate activations.
3. **Availability**: A malicious Worker can refuse service.
4. **Master integrity**: If Master colludes with Workers, the guarantee breaks.
   Mitigation: on-chain verification or independent auditor (future work).
5. **Sub-epsilon attacks**: Perturbations smaller than `BASE_EPSILON / (n-1)` per edge
   are within the quantization noise floor and not distinguishable from legitimate
   calibration variance. The total accumulated distortion is bounded at `BASE_EPSILON = 0.01`.
6. **Exact commitment chain**: Current linking uses rescaled float comparison,
   not cryptographically exact commitment matching (unlike NanoZK's SHA-256 chain).
   Upgrade path: `polycommit` + `swap_proof_commitments()`.

## 5. Attack Detection Matrix (Empirically Verified)

| Attack | Detection Mechanism | Architecture | Verified |
|---|---|---|:---:|
| tamper (output + 999.0) | Terminal binding: proof rescaled_outputs ≠ declared output | Prover-Worker | ✓ |
| skip (all zeros) | Terminal binding | Prover-Worker | ✓ |
| random (uniform noise) | Terminal binding | Prover-Worker | ✓ |
| replay (fixed value) | Terminal binding | Prover-Worker | ✓ |
| tamper at middle slice | Adjacent linking (edge mismatch between π_i and π_{i+1}) | Prover-Worker | ✓ |
| model substitution | model_digest re-verification | Both | ✓ (by construction) |

**Model**: MNIST MLP (109,386 parameters, 784→128→ReLU→64→ReLU→10), 2-slice configuration.

**Key difference from legacy architecture**: In the legacy design, Master proved using
Worker-declared output. A malicious Worker could return tampered output and Master would
generate a valid proof for the *tampered* computation. In the refactored Prover-Worker
architecture, Worker generates its own proof. The proof's public instances (rescaled I/O)
are bound to the *actual* ONNX computation, not to the Worker's declared output.
Verifier extracts I/O from proof, not from network messages.

## 6. Relationship to Prior Work

- **DSperse**: Targeted verification as engineering tradeoff; does not claim full e2e guarantee.
  This system goes further: all slices eventually proven.
- **NanoZK**: Layerwise proofs + commitment chains for LLM. This system applies the same
  principle to distributed multi-worker sliced inference with deferred certification.
- **Non-Composability Note (Zamir 2026)**: Layerwise approximate verification does not compose
  in general. This system proves exact quantized circuit statements, not approximate relations.
