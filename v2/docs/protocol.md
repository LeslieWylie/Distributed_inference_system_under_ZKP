# Protocol: Deferred Certification for End-to-End Verifiable Distributed Inference

## 1. End-to-End Statement

For request `req_id`, model slices `M_1, ..., M_n` with digests `d_1, ..., d_n`,
and user input `x_0`:

> There exist intermediate states `x_1, ..., x_{n-1}` such that
> `x_i = M_i(x_{i-1})` for all `i`, and the certified output `y = x_n`.

The statement is verified through three binding mechanisms:

1. **Per-slice proof soundness**: `ezkl.verify(π_i, vk_i)` confirms `x_i = M_i(x_{i-1})`
   within the quantized circuit semantics.
2. **Adjacent linking**: `rescaled_outputs(π_i) ≈ rescaled_inputs(π_{i+1})` within ε = 0.01,
   where both values are cryptographically bound as public instances in their respective proofs.
3. **Terminal binding**: `rescaled_outputs(π_n) ≈ provisional_output` within ε = 0.01.

## 2. Roles

| Role | Trust | Responsibility |
|---|---|---|
| Client | Trusted | Submits input, receives provisional/certified output |
| Master/Scheduler | Trusted | Orchestration, state machine, triggers verification |
| Execution Worker | **Untrusted** | Executes slice inference only |
| Prover | **Untrusted** | Generates proof (cannot forge valid proof for wrong computation) |
| Verifier | Trusted (co-located with Master) | Independent proof verification + chain linking |
| Artifact Registry | Trusted (static, immutable after setup) | Stores vk/pk/srs/model_digest per slice |

## 3. Request State Machine

```
SUBMITTED → EXECUTING → EXECUTED_UNCERTIFIED → PROVING → VERIFYING → CERTIFIED
                                                                  ↘ INVALID
```

- **SUBMITTED**: Client request received
- **EXECUTING**: Slices being computed sequentially
- **EXECUTED_UNCERTIFIED**: All slices computed, provisional output available
- **PROVING**: Background proof generation in progress
- **VERIFYING**: Independent verification of all proofs + commitment chain
- **CERTIFIED**: All proofs valid, all links pass, terminal binding holds
- **INVALID**: Any proof failed, any link broken, or terminal mismatch

## 4. Online Protocol (per request)

### Phase 1: Execution (critical path, ~10ms for 4 slices)
```
for i = 1 to n:
    input_commit_i = Commit(req_id, i, d_i, x_{i-1})
    x_i = ONNXRuntime(M_i, x_{i-1})    // untrusted Worker
    output_commit_i = Commit(req_id, i, d_i, x_i)
    pass x_i to slice i+1
return x_n as provisional_output
status ← EXECUTED_UNCERTIFIED
```

### Phase 2: Proving (background, parallel, ~2-5s for 4 slices)
```
for i = 1 to n (parallel):
    witness_i = ezkl.gen_witness(x_{i-1}, compiled_i)
    π_i = ezkl.prove(witness_i, compiled_i, pk_i, srs_i)
status ← PROVING → completed
```

### Phase 3: Verification (independent, ~80ms)
```
for i = 1 to n:
    assert ezkl.verify(π_i, settings_i, vk_i, srs_i)       // Step 1: proof soundness
    assert model_digest(M_i) == registry.d_i                  // Step 1a: model binding
    extract rescaled_inputs_i, rescaled_outputs_i from π_i

for i = 1 to n-1:
    assert |rescaled_outputs_i - rescaled_inputs_{i+1}| < ε   // Step 2: adjacent linking

assert |rescaled_outputs_n - provisional_output| < ε          // Step 3: terminal binding

if all pass:
    status ← CERTIFIED; issue certificate
else:
    status ← INVALID; record failure details
```

## 5. Commitment Construction

```
Commit(req_id, slice_id, model_digest, tensor) = SHA-256(
    JSON.stringify({req_id, slice_id, model_digest, tensor}, sort_keys=True)
)
```

Domain separation prevents:
- Cross-request replay (req_id)
- Cross-slice splicing (slice_id)
- Model version confusion (model_digest)

## 6. Linking Semantics

In `public` visibility mode, EZKL binds `rescaled_inputs` and `rescaled_outputs`
as public instances in the proof. `ezkl.verify()` cryptographically confirms these
values are consistent with the proven computation.

Adjacent linking uses approximate equality with **dynamic epsilon budget**:
- Base budget: `BASE_EPSILON = 0.01`
- Per-edge threshold: `LINK_EPSILON = BASE_EPSILON / (n-1)` where n = number of slices
- Accumulated chain budget: total accumulated diff must stay < `BASE_EPSILON`
- Terminal binding: `TERMINAL_EPSILON = BASE_EPSILON / n`

This dynamic scheme prevents the **epsilon accumulation vulnerability**: an attacker
cannot inject sub-threshold perturbations across many edges to accumulate undetected
distortion. The total chain budget is bounded at `BASE_EPSILON` regardless of slice count.

**Note on commitment semantics**: The `compute_commitment()` function (SHA-256 with
domain separation) is used for audit logging and request tracking. The actual security
binding comes from EZKL proof public instances (rescaled values), not from external
SHA-256 commitments. Future upgrade to `polycommit` + `swap_proof_commitments()`
will achieve cryptographically exact linking (no epsilon needed).

**Relationship to NanoZK**: NanoZK uses exact SHA-256 commitment matching between
layers. Our system uses approximate rescaled-value comparison due to EZKL's independent
per-slice quantization calibration. This is an engineering limitation, not a fundamental
design choice. The polycommit upgrade path eliminates this gap.

## 7. Security Guarantee

**Theorem (informal)**: If EZKL proof system is sound and SHA-256 is collision-resistant,
then for any certified request, the output `y` satisfies `y ≈ M_n(M_{n-1}(...M_1(x_0)...))`
within quantization precision of the circuit semantics.

**What this does NOT guarantee**:
- Fidelity to the original floating-point model (separate empirical measurement)
- Privacy of intermediate activations (current implementation uses public visibility;
  privacy mode exploration documented in v1 experiments)
- Availability (malicious Worker can refuse to compute)
- Exact cryptographic commitment chain (current: public-instance linking;
  future: polycommit upgrade)

## 8. Linking Level Classification

| Level | Description | Status |
|---|---|---|
| **L1: Public-instance linking** | Compare `rescaled_outputs[i]` ≈ `rescaled_inputs[i+1]` with dynamic ε budget | **Current implementation** |
| **L2: Cryptographic commitment chain** | `C_out_i == C_in_{i+1}` via Poseidon/polycommit exact equality | **Future upgrade** (polycommit + swap_proof_commitments) |

Current system implements **Level 1**. Level 2 requires EZKL `polycommit` visibility
mode with explicit scale alignment across slices.

## 9. Architecture: Distributed Runtime

The system supports two runtime modes:

### Local mode (v2/execution/pipeline.py)
Single-process function-call orchestration. Used for fast prototyping and unit testing.

### Distributed mode (v2/services/)
True multi-process architecture with HTTP communication:
- **Execution Workers** (`execution_worker.py`): FastAPI services, one per slice
- **Master Coordinator** (`master_coordinator.py`): HTTP-based orchestration + verification
- Workers are untrusted; Master/Verifier is the trust anchor

## 10. Comparison with NanoZK

| Aspect | NanoZK | This System |
|---|---|---|
| Granularity | Transformer block | Arbitrary ONNX slice |
| Commitment | SHA-256 on activations | SHA-256 with domain separation |
| Linking | Hash equality | Rescaled public instance ≈-comparison |
| Parallelism | Layer-parallel proving | Subprocess-parallel proving |
| Soundness | Compositional (union bound) | Per-slice + linking + terminal |
| Scale target | LLM (GPT-2, 12 layers) | Small FC model (8 layers, 2/4/8 slices) |
