# Threat Model

## 1. Adversary Capabilities

The system assumes the following adversary capabilities:

| Capability | Assumed? | Mitigation |
|---|:---:|---|
| Return forged output from any slice | ✓ | Proof soundness + terminal binding |
| Forge SHA-256 hashes | ✓ | Proof binds public instances, not external hashes |
| Produce valid proof but send fake output downstream | ✓ | Adjacent linking: rescaled_outputs[i] ≈ rescaled_inputs[i+1] |
| Collude with adjacent Worker | ✓ | Both slices must produce individually valid proofs; linking verified independently |
| Replay old request outputs | ✓ | req_id in commitment provides domain separation |
| Claim `verified=True` without running verifier | ✓ | Worker never self-declares correctness; Verifier is independent |
| Replace slice model or quantization settings | ✓ | model_digest re-computed and checked at verification time |
| Refuse to compute (availability attack) | ✓ | Out of scope — correctness guarantee, not availability |

## 2. Trust Assumptions

| Component | Trust Level | Justification |
|---|---|---|
| ZKP backend (EZKL/Halo2) | Computationally sound | Standard cryptographic assumption |
| SHA-256 | Collision-resistant | Standard |
| Master/Verifier | Trusted | Centralized trust anchor; future: on-chain or multi-verifier |
| Artifact Registry | Tamper-proof after setup | Static VK/PK/SRS/model_digest fixed at compile time |
| Execution Workers | **Untrusted** | May be malicious; correctness enforced by proof |
| Network | Authenticated channels | Master ↔ Worker communication integrity assumed |

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

| Attack | Detection Mechanism | Verified |
|---|---|:---:|
| tamper (output + 999.0) | Terminal binding / adjacent linking | ✓ 6/6 |
| skip (all zeros) | Terminal binding | ✓ |
| random (uniform noise) | Terminal binding | ✓ |
| replay (fixed value) | Terminal binding | ✓ |
| tamper at middle slice | Adjacent linking (edge mismatch) | ✓ |
| model substitution | model_digest re-verification | ✓ (by construction) |

## 6. Relationship to Prior Work

- **DSperse**: Targeted verification as engineering tradeoff; does not claim full e2e guarantee.
  This system goes further: all slices eventually proven.
- **NanoZK**: Layerwise proofs + commitment chains for LLM. This system applies the same
  principle to distributed multi-worker sliced inference with deferred certification.
- **Non-Composability Note (Zamir 2026)**: Layerwise approximate verification does not compose
  in general. This system proves exact quantized circuit statements, not approximate relations.
