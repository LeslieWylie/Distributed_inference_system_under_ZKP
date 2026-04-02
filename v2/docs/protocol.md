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
| Client | Trusted | Final verification authority; verifies ProofBundle locally |
| Coordinator | **Untrusted** | Scheduling, proof collection, bundle assembly only |
| Prover-Worker | **Untrusted** | Executes slice inference AND generates proof locally |
| Verifier (client-side) | Trusted | Independent proof verification + chain linking |
| Artifact Registry | Trusted (static, immutable after setup) | Stores vk/pk/srs/model_digest per slice |

**Architecture change (v2 refactor)**: In the previous design, Workers only executed inference
and Master generated proofs centrally. The refactored architecture merges inference and proving
into each **Prover-Worker**, distributing proof generation overhead across all nodes.
This matches the task requirement: "将证明生成任务分摊至多个节点".

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

### Phase 1: Distributed Execution + Proving (sequential HTTP calls to Prover-Workers)
```
for i = 1 to n:
    // HTTP POST to Prover-Worker_i at /infer_and_prove
    (x_i, π_i) = ProverWorker_i.infer_and_prove(req_id, x_{i-1})
        // Worker internally: x_i = ONNXRuntime(M_i, x_{i-1})
        //                    π_i = ezkl.prove(x_{i-1}, compiled_i, pk_i, srs_i)
    pass x_i to slice i+1
return x_n as provisional_output
collect all {π_1, ..., π_n}
```

**Key security property**: Each Worker generates its own proof locally.
The proof's `pretty_public_inputs.rescaled_outputs` is cryptographically bound
to the actual ONNX computation result (PLONK soundness). A malicious Worker
that returns a tampered `x_i` but honest proof will be detected because
`rescaled_outputs(π_i) ≠ x_i` (terminal/adjacent binding failure).

### Phase 2: Independent Verification (~120ms)
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

Adjacent linking uses approximate equality with **dynamic epsilon budget plus an engineering floor**:
- Base budget: `BASE_EPSILON = 0.01`
- Per-edge threshold: `LINK_EPSILON = max(BASE_EPSILON / (n-1), 0.004)` where n = number of slices
- Accumulated chain budget: total accumulated diff must stay < `BASE_EPSILON`
- Terminal binding: `TERMINAL_EPSILON = max(BASE_EPSILON / n, 0.004)`

The floor (0.004) avoids false negatives when EZKL public-instance dequantization introduces
legitimate drift at fine-grained boundaries (empirically up to ~0.003 per edge in 8-slice
chains with propagated calibration). The accumulated-chain budget still prevents unbounded
sub-threshold distortion. This is an engineering tolerance, not a cryptographic exact-match
guarantee.

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

### Distributed mode (v2/services/) — Refactored Architecture
True multi-process architecture with HTTP communication:
- **Prover-Workers** (`prover_worker.py`): FastAPI services, one per slice.
  Each Worker executes ONNX inference AND generates EZKL proof locally.
  Endpoint: `POST /infer_and_prove` → returns `{output_tensor, proof_json}`.
  Bind address: `0.0.0.0` (supports cross-host deployment).
- **Distributed Coordinator** (`distributed_coordinator.py`): HTTP-based orchestration.
  Master does NOT prove — only dispatches requests and collects proofs.
  Delegates all proofs to independent Verifier.
- **Worker configuration**: `workers.json` defines per-slice `{host, port}`.
  Local development: `127.0.0.1`; server deployment: real IPs.
- Workers are untrusted; Master/Verifier is the trust anchor.

### Legacy mode (v2/services/execution_worker.py, master_coordinator.py)
Old architecture where Workers only executed inference and Master proved centrally.
Retained as baseline comparison. **Not recommended** — has a fundamental security gap:
Master proves using Worker-declared output, which a malicious Worker can forge.

## 10. Comparison with NanoZK

| Aspect | NanoZK | This System |
|---|---|---|
| Granularity | Transformer block | Arbitrary ONNX slice |
| Commitment | SHA-256 on activations | SHA-256 with domain separation |
| Linking | Hash equality | Rescaled public instance ≈-comparison |
| Parallelism | Layer-parallel proving | Subprocess-parallel proving |
| Soundness | Compositional (union bound) | Per-slice + linking + terminal |
| Scale target | LLM (GPT-2, 12 layers) | MNIST MLP (109K params, 2 slices) |
| Proof distribution | Single prover | Distributed Prover-Worker per slice |
