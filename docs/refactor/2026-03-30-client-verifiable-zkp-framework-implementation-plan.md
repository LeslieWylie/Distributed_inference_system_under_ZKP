# Client-Verifiable ZKP Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor v2 into a client-verifiable distributed ZKP framework where Workers and Coordinator are untrusted, the Coordinator returns a proof bundle as the primary artifact, and the client independently verifies correctness using local registry artifacts with fail-closed bundle validation.

**Architecture:** Keep Prover-Workers as the proving edge, downgrade the Coordinator into an untrusted bundle assembler, add a client-facing bundle verification path, define one canonical registry manifest for compile/runtime/client use, and converge experiments and docs around `ProofBundle + Client Verification` as the single v2 mainline.

**Tech Stack:** Python 3.13, EZKL 23.0.5, FastAPI, ONNX Runtime, dataclasses, JSON-based bundle artifacts.

---

## File Structure

### Files to Create

- `v2/common/registry_manifest.py` — Canonical registry manifest builder/digest helper shared by compile, Coordinator, and client verifier.

### Files to Modify

- `v2/common/types.py` — Consolidate `ProofBundle`, `ProofBundleSlice`, `ClientVerificationResult`, and downgrade `Certificate` semantics.
- `v2/compile/build_circuits.py` — Persist canonical registry metadata needed by clients.
- `v2/services/distributed_coordinator.py` — Return `ProofBundle` as the primary artifact and downgrade server certificate to advisory.
- `v2/verifier/verify_chain.py` — Extract reusable verification internals so bundle verification is not tied to a trusted server workflow.
- `v2/verifier/bundle_verifier.py` — Harden bundle verification with canonical metadata checks and fail-closed malformed-bundle handling.
- `v2/experiments/refactored_e2e.py` — Switch to bundle generation plus client-side verification.
- `v2/experiments/resource_metrics.py` — Report bundle generation and client verification metrics on the new mainline.
- `v2/experiments/smoke_test.py` — Remove stale `certificate`-based expectations or explicitly downgrade it.
- `README.md` — Rewrite the main workflow around client verification.
- `v2/docs/protocol.md` — Rewrite trust boundaries and protocol flow.
- `v2/docs/threat_model.md` — Rewrite the threat model for the strong-engineering trust model.

### Files to Downgrade or Mark as Baseline

- `v2/execution/pipeline.py`
- `v2/execution/deferred_pipeline.py`
- `v2/services/master_coordinator.py`
- `v2/experiments/distributed_e2e.py`
- `v2/experiments/e2e_certified.py`
- `v2/experiments/scalability.py`

### Validation Targets

- `v2/experiments/refactored_e2e.py`
- `v2/experiments/resource_metrics.py`

---

## Task 1: Consolidate Bundle Data Model And Registry Digest

**Files:**
- Modify: `v2/common/types.py`
- Create: `v2/common/registry_manifest.py`
- Modify: `v2/compile/build_circuits.py`

- [ ] **Step 1: Consolidate shared types with bundle-first structures**

Ensure `v2/common/types.py` exposes the client-verification-facing dataclasses with stable field names and semantics.

```python
@dataclass
class ProofBundleSlice:
    slice_id: int
    model_digest: str
    proof_json: dict
    worker_claimed_output: list[float] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProofBundle:
    bundle_version: str
    req_id: str
    created_at: str
    model_id: str
    registry_digest: str
    slice_count: int
    initial_input: list[float]
    claimed_final_output: list[float]
    slices: list[ProofBundleSlice] = field(default_factory=list)
    server_side_advisory: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClientVerificationResult:
    req_id: str
    status: str
    all_single_proofs_verified: bool
    all_links_verified: bool
    final_output_commit: str | None = None
    failure_reasons: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 2: Downgrade `Certificate` comments and semantics**

Update `Certificate` comments in `v2/common/types.py` so future code treats it as advisory only.

```python
@dataclass
class Certificate:
    """Server-side advisory result only. Not a trust root for clients."""
    req_id: str
    status: str
    slice_count: int
    final_output_commit: str
    all_single_proofs_verified: bool
    all_links_verified: bool
    timestamp: str = ""
    model_digests: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)
```

- [ ] **Step 3: Add canonical registry manifest helper**

Create `v2/common/registry_manifest.py` and define the single manifest structure used by compile-time metadata, Coordinator bundle generation, and client verification. The manifest MUST exclude host-local or transient absolute paths.

```python
def build_client_registry_manifest(artifacts: list[SliceArtifact]) -> list[dict]:
    return [
        {
            "slice_id": a.slice_id,
            "model_digest": a.model_digest,
            "input_scale": a.input_scale,
            "output_scale": a.output_scale,
            "param_scale": a.param_scale,
        }
        for a in sorted(artifacts, key=lambda item: item.slice_id)
    ]


def compute_registry_digest(manifest: list[dict]) -> str:
    payload = json.dumps(manifest, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

Also add the necessary imports.

```python
import hashlib
import json
```

- [ ] **Step 4: Persist registry metadata including canonical digest**

Update `build_registry()` so it writes both the slice registry and a metadata file usable by clients. The digest MUST be computed from the canonical manifest helper introduced above, not from a second ad-hoc dictionary shape.

```python
manifest = build_client_registry_manifest(artifacts)
registry_digest = compute_registry_digest(manifest)
metadata_path = os.path.join(registry_dir, "registry", "registry_metadata.json")

with open(metadata_path, "w") as f:
    json.dump({
        "registry_digest": registry_digest,
        "slice_count": len(registry_data),
        "model_type": model_type,
        "manifest": manifest,
    }, f, indent=2)
```

- [ ] **Step 5: Validate no syntax regressions in touched files**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -m py_compile v2/common/types.py v2/common/registry_manifest.py v2/compile/build_circuits.py
```

Expected: no output and exit code `0`.

---

## Task 2: Introduce Client Bundle Verifier And Fail-Closed Validation

**Files:**
- Create: `v2/verifier/bundle_verifier.py`
- Modify: `v2/verifier/verify_chain.py`

- [ ] **Step 1: Extract a reusable advisory builder from chain verification**

Keep `verify_chain()` intact enough for compatibility, but ensure its output can be reused by client-facing code.

```python
def build_client_verification_result(
    chain_result: ChainVerifyResult,
    verify_ms_total: float,
) -> ClientVerificationResult:
    failures = list(chain_result.proof_failures) + list(chain_result.link_failures)
    return ClientVerificationResult(
        req_id=chain_result.req_id,
        status=chain_result.status.value,
        all_single_proofs_verified=chain_result.all_single_proofs_verified,
        all_links_verified=chain_result.all_links_verified,
        final_output_commit=chain_result.final_output_commit,
        failure_reasons=failures,
        metrics={"verification_ms": round(verify_ms_total, 2)},
    )
```

- [ ] **Step 2: Harden the bundle verification module**

Refine `v2/verifier/bundle_verifier.py` so the existing client-facing API is the authoritative verification entrypoint.

```python
from __future__ import annotations

import json
import time

from v2.common.types import (
    ProofBundle,
    ProofJob,
    ProofJobStatus,
    SliceArtifact,
    ClientVerificationResult,
)
from v2.verifier.verify_chain import verify_chain, build_client_verification_result


def verify_bundle(bundle: ProofBundle, artifacts: list[SliceArtifact]) -> ClientVerificationResult:
    t0 = time.perf_counter()
    artifact_map = {a.slice_id: a for a in artifacts}
    proof_jobs: list[ProofJob] = []

    for slice_item in bundle.slices:
        artifact = artifact_map[slice_item.slice_id]
        proof_jobs.append(ProofJob(
            job_id=f"bundle-{bundle.req_id}-{slice_item.slice_id}",
            req_id=bundle.req_id,
            slice_id=slice_item.slice_id,
            input_commit="bundle-input-unused",
            output_commit="bundle-output-unused",
            artifact=artifact,
            proof_data=slice_item.proof_json,
            status=ProofJobStatus.DONE,
        ))

    chain_result = verify_chain(
        req_id=bundle.req_id,
        proof_jobs=proof_jobs,
        artifacts=artifacts,
        initial_input=bundle.initial_input,
        provisional_output=bundle.claimed_final_output,
    )
    verify_ms = (time.perf_counter() - t0) * 1000
    return build_client_verification_result(chain_result, verify_ms)
```

- [ ] **Step 3: Add explicit bundle metadata validation before proof verification**

Extend `bundle_verifier.py` with a pre-validation step that fail-closes before calling `verify_chain()`. The validator MUST check:

- supported `bundle_version`
- `slice_count == len(bundle.slices)`
- `slice_id` strictly increasing
- no duplicate slices
- no missing registry slice
- `bundle.registry_digest == local registry metadata digest`
- `slice_item.model_digest == artifact.model_digest`

On any mismatch, return `ClientVerificationResult(status="invalid", ...)` instead of raising.

- [ ] **Step 4: Ensure bundle verification writes temp proof files when needed**

Because `verify_proof()` currently consumes a proof path, update `bundle_verifier.py` to materialize temporary proof files before creating `ProofJob` items.

```python
import tempfile
from pathlib import Path


def _write_temp_proof(req_id: str, slice_id: int, proof_json: dict) -> str:
    temp_dir = Path(tempfile.gettempdir()) / "zkp_bundle_verify" / req_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    proof_path = temp_dir / f"slice_{slice_id}_proof.json"
    proof_path.write_text(json.dumps(proof_json), encoding="utf-8")
    return str(proof_path)
```

Use the returned path in each `ProofJob`.

- [ ] **Step 5: Remove assertion-driven crash paths from client-facing verification**

`verify_chain()` currently contains hard assertions on proof count and slice alignment. Replace those assertions with explicit failure accumulation, or catch `AssertionError` and convert it into a structured `invalid` result in `bundle_verifier.py`. The client verifier MUST fail closed, not crash, on malformed bundle input.

- [ ] **Step 6: Validate the new verifier module imports cleanly**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -m py_compile v2/verifier/verify_chain.py v2/verifier/bundle_verifier.py
```

Expected: no output and exit code `0`.

- [ ] **Step 7: Add malformed-bundle verification checks**

Run or script at least the following negative cases against `verify_bundle()`:

- duplicate `slice_id`
- missing last slice
- reversed slice order
- forged `registry_digest`

Expected: each case returns `status == "invalid"` without an uncaught exception.

---

## Task 3: Refactor Coordinator To Emit Proof Bundle

**Files:**
- Modify: `v2/services/distributed_coordinator.py`

- [ ] **Step 1: Import bundle structures and registry metadata helpers**

Add imports in `v2/services/distributed_coordinator.py`.

```python
from datetime import datetime, timezone

from v2.common.types import (
    SliceArtifact, ExecutionRecord, ProofJob, ProofJobStatus,
    ProofBundle, ProofBundleSlice,
)
from v2.common.registry_manifest import build_client_registry_manifest, compute_registry_digest
```

- [ ] **Step 2: Build bundle slices from collected worker proofs**

After saving received proofs, construct bundle slices.

```python
bundle_slices = []
for metric, artifact, proof_data in zip(per_slice_metrics, artifacts, proof_data_list):
    bundle_slices.append(ProofBundleSlice(
        slice_id=artifact.slice_id,
        model_digest=artifact.model_digest,
        proof_json=proof_data or {},
        worker_claimed_output=execution_records[artifact.slice_id - 1].output_tensor,
        metrics=metric,
    ))
```

- [ ] **Step 3: Replace primary response artifact with `ProofBundle`**

Construct and return a bundle object even if server-side advisory is retained. The bundle digest MUST be computed from the same canonical manifest persisted at compile time.

```python
manifest = build_client_registry_manifest(artifacts)

bundle = ProofBundle(
    bundle_version="1.0",
    req_id=req_id,
    created_at=datetime.now(timezone.utc).isoformat(),
    model_id="mnist_mlp",
    registry_digest=compute_registry_digest(manifest),
    slice_count=num_slices,
    initial_input=list(initial_input),
    claimed_final_output=list(provisional_output),
    slices=bundle_slices,
    server_side_advisory={
        "status": certificate.status,
        "all_single_proofs_verified": certificate.all_single_proofs_verified,
        "all_links_verified": certificate.all_links_verified,
        "note": "non-authoritative",
    },
)
```

- [ ] **Step 4: Change the function return shape to expose bundle-first semantics**

Replace the old `certificate`-first return with:

```python
return {
    "req_id": req_id,
    "proof_bundle": bundle,
    "server_side_advisory": bundle.server_side_advisory,
    "metrics": {
        "total_ms": round(total_ms, 2),
        "execution_ms": round(execution_ms, 2),
        "total_exec_ms": round(total_exec_ms, 2),
        "total_prove_ms": round(total_prove_ms, 2),
        "verification_ms": round(verify_ms, 2),
        "num_slices": num_slices,
        "per_slice": per_slice_metrics,
        "architecture": "prover_worker",
    },
}
```

- [ ] **Step 5: Validate coordinator syntax**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -m py_compile v2/services/distributed_coordinator.py
```

Expected: no output and exit code `0`.

- [ ] **Step 6: Keep bundle slices canonicalized**

Before constructing `ProofBundle`, sort `bundle_slices` by `slice_id` and assert locally in Coordinator code that the produced order matches `artifacts`. This assertion is allowed inside Coordinator because it guards local assembly logic; client-side verification must still reject malformed bundles independently.

---

## Task 4: Switch Main E2E Experiment To Client Verification

**Files:**
- Modify: `v2/experiments/refactored_e2e.py`
- Modify: `v2/experiments/smoke_test.py`

- [ ] **Step 1: Import the client verifier**

Add imports:

```python
from v2.verifier.bundle_verifier import verify_bundle
```

- [ ] **Step 2: Verify bundle locally after coordinator returns**

Replace direct use of service-side certificate as the final truth.

```python
r = run_distributed_pipeline(
    initial_input, artifacts, worker_urls,
    fault_at=test["fault_at"],
    fault_type=test["fault_type"],
)
bundle = r["proof_bundle"]
client_result = verify_bundle(bundle, artifacts)
status = client_result.status
passed = (status == test["expected"])
```

- [ ] **Step 3: Update stored results schema**

Persist both advisory and final client verdict, but make the client verdict authoritative.

```python
results.append({
    "name": test["name"],
    "expected": test["expected"],
    "actual": status,
    "passed": passed,
    "architecture": "prover_worker_client_verify",
    "model": "mnist_mlp",
    "num_slices": num_slices,
    "server_side_advisory": r.get("server_side_advisory", {}),
    "client_verification": {
        "status": client_result.status,
        "all_single_proofs_verified": client_result.all_single_proofs_verified,
        "all_links_verified": client_result.all_links_verified,
        "failure_reasons": client_result.failure_reasons,
        "metrics": client_result.metrics,
    },
    "metrics": r["metrics"],
})
```

- [ ] **Step 4: Update console summaries to print client verdict explicitly**

Use wording like:

```python
print(f"  [{mark}] {test['name']}: client={status} advisory={r.get('server_side_advisory', {}).get('status', 'unknown')}")
```

- [ ] **Step 5: Update `smoke_test.py` to stop reading `result["certificate"]`**

Replace all `r["certificate"]` lookups in `v2/experiments/smoke_test.py` with:

```python
bundle = r["proof_bundle"]
client_result = verify_bundle(bundle, artifacts)
status = client_result.status
```

If `smoke_test.py` is not maintained, mark it clearly as legacy and exclude it from the mainline validation set.

- [ ] **Step 6: Run the main E2E experiment once**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -u -m v2.experiments.refactored_e2e --slices 2
```

Expected: normal case produces client `certified`; tamper/skip/random/replay cases produce client `invalid`.

- [ ] **Step 7: Run `smoke_test.py` after migration**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -u v2/experiments/smoke_test.py
```

Expected: no `KeyError: 'certificate'`; smoke results use client verdict or are clearly marked legacy.

---

## Task 5: Converge Metrics On Bundle + Client Verification

**Files:**
- Modify: `v2/experiments/resource_metrics.py`

- [ ] **Step 1: Stop treating legacy pipeline outputs as the main source of truth**

Switch imports away from execution pipelines toward the distributed coordinator mainline and client verifier.

```python
from v2.services.distributed_coordinator import run_distributed_pipeline
from v2.verifier.bundle_verifier import verify_bundle
```

- [ ] **Step 2: Measure bundle generation and client verification separately**

Wrap client verification with timing.

```python
bundle_start = time.perf_counter()
r_normal = run_distributed_pipeline(initial_input, artifacts, worker_urls)
bundle_ms = (time.perf_counter() - bundle_start) * 1000

client_verify_start = time.perf_counter()
client_result = verify_bundle(r_normal["proof_bundle"], artifacts)
client_verify_ms = (time.perf_counter() - client_verify_start) * 1000
```

- [ ] **Step 3: Rewrite detection accuracy around client verdict**

Update the counting logic.

```python
if client_result.status == "certified":
    tn += 1
else:
    fp += 1
```

And for attack cases:

```python
if attack_client_result.status == "invalid":
    tp += 1
else:
    fn += 1
```

- [ ] **Step 4: Update metrics output schema without overclaiming system scope**

Include the new fields.

```python
results = {
    "num_slices": num_slices,
    "coordinator_local_resource_profile": resource_profile,
    "resource_profile_scope": "coordinator_local",
    "bundle_generation_ms": round(bundle_ms, 2),
    "client_verification_ms": round(client_verify_ms, 2),
    "throughput": {
        "num_requests": num_requests,
        "total_ms": round(throughput_ms, 2),
        "requests_per_sec": round(throughput_rps, 4),
    },
    "detection_accuracy": detection,
    "client_verdict": client_result.status,
    "server_side_advisory": r_normal.get("server_side_advisory", {}),
    "per_slice_proof_ms": [s["prove_ms"] for s in r_normal["metrics"]["per_slice"]],
    "per_slice_exec_ms": [s["exec_ms"] for s in r_normal["metrics"]["per_slice"]],
    "total_proof_gen_ms": r_normal["metrics"]["total_prove_ms"],
}
```

Do not claim this field is aggregated system CPU/RSS unless worker metrics are explicitly collected and summed.

- [ ] **Step 5: Run the metrics experiment**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -u v2/experiments/resource_metrics.py
```

Expected: JSON is written successfully and includes `bundle_generation_ms`, `client_verification_ms`, `client_verdict`, `server_side_advisory`, and an explicit `resource_profile_scope`.

---

## Task 6: Rewrite Main Documentation And Downgrade Legacy Paths

**Files:**
- Modify: `README.md`
- Modify: `v2/docs/protocol.md`
- Modify: `v2/docs/threat_model.md`

- [ ] **Step 1: Rewrite README main workflow**

Replace the current trusted-service framing with the new mainline summary.

```markdown
## Mainline Workflow

1. Build registry artifacts.
2. Start Prover-Workers.
3. Run the untrusted Coordinator to collect slice proofs and assemble a Proof Bundle.
4. Perform client-side verification against local registry artifacts.
5. Accept the result only if local verification returns `certified`.

Server-side certificates are advisory only and are not trust roots.
```

- [ ] **Step 2: Rewrite the protocol trust table**

In `v2/docs/protocol.md`, update the role table to state:

```markdown
| Role | Trust | Responsibility |
|---|---|---|
| Client | Trusted | Final verification authority |
| Coordinator | Untrusted | Scheduling and bundle assembly only |
| Prover-Worker | Untrusted | Slice execution and proof generation |
| Artifact Registry | Trusted | Static verification materials |
```
```

- [ ] **Step 3: Rewrite threat model assumptions**

In `v2/docs/threat_model.md`, replace old trust anchor wording with:

```markdown
Minimal trust root:
- client local verification program
- registry artifacts (`vk/settings/model_digest/srs`)
- underlying cryptographic assumptions

Not trusted:
- Coordinator
- Prover-Workers
- network transport
```

Also remove any residual wording that implies “Master integrity” is a correctness prerequisite in the new client-verifiable mainline.

- [ ] **Step 4: Mark legacy paths as baseline/reference**

In README and protocol docs, explicitly downgrade:

```markdown
The following paths are retained as baseline/reference only and are not the recommended v2 trusted mainline:
- `v2/execution/pipeline.py`
- `v2/execution/deferred_pipeline.py`
- `v2/services/master_coordinator.py`
- `v2/experiments/distributed_e2e.py`
- `v2/experiments/e2e_certified.py`
- `v2/experiments/scalability.py`
```

- [ ] **Step 5: Perform a consistency read-through**

Check that the following phrases are true everywhere:

- Coordinator is untrusted
- Client verification is final
- server certificate is advisory only
- Proof Bundle is the primary artifact

Also ensure docs do not present single-machine `127.0.0.1` experiments as evidence of cross-host deployment.

No command required. Manually review the modified docs before finishing.

---

## Task 7: Final Verification Pass

**Files:**
- Review only

- [ ] **Step 1: Run syntax verification on all modified Python files**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -m py_compile \
  v2/common/types.py \
  v2/compile/build_circuits.py \
  v2/verifier/verify_chain.py \
  v2/verifier/bundle_verifier.py \
  v2/services/distributed_coordinator.py \
  v2/experiments/refactored_e2e.py \
  v2/experiments/resource_metrics.py
```

Expected: no output and exit code `0`.

- [ ] **Step 2: Re-run the main E2E path**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -u -m v2.experiments.refactored_e2e --slices 2
```

Expected:

- `normal` is client `certified`
- tamper cases are client `invalid`
- result JSON stores both advisory and client verification sections

- [ ] **Step 3: Re-run `smoke_test.py` or explicitly retire it**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -u v2/experiments/smoke_test.py
```

Expected:

- no `result["certificate"]` dependency remains in the maintained version
- smoke output uses client verdict or prints a clear legacy warning and exits cleanly

- [ ] **Step 4: Re-run the metrics path**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -u v2/experiments/resource_metrics.py
```

Expected:

- metrics file is written successfully
- bundle-related fields exist
- client verdict is present and authoritative

- [ ] **Step 5: Confirm Definition of Done against the PRD**

Checklist:

```markdown
- Coordinator returns `ProofBundle`
- Client can independently verify from `ProofBundle + Registry`
- `registry_digest` is computed from one canonical manifest definition
- malformed bundles return `invalid` instead of uncaught exceptions
- server advisory is not the trust root
- main experiments use client verification as final verdict
- docs match code semantics
- old paths are downgraded, not presented as mainline
```

- [ ] **Step 6: Commit only if explicitly requested**

Do not include an automatic commit step in the default execution path. If a commit is required, create it only after the verification steps above pass and the user explicitly requests a commit.
