# Client-Verifiable ZKP Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor v2 into a client-verifiable distributed ZKP framework where Workers and Coordinator are untrusted, the Coordinator returns a proof bundle as the primary artifact, and the client independently verifies correctness using local registry artifacts.

**Architecture:** Keep Prover-Workers as the proving edge, downgrade the Coordinator into an untrusted bundle assembler, add a client-facing bundle verification path, and converge experiments and docs around `ProofBundle + Client Verification` as the single v2 mainline.

**Tech Stack:** Python 3.13, EZKL 23.0.5, FastAPI, ONNX Runtime, dataclasses, JSON-based bundle artifacts.

---

## File Structure

### Files to Create

- `v2/verifier/bundle_verifier.py` — Client-oriented verification entrypoint that consumes `ProofBundle + Registry` and returns the final trusted verdict.

### Files to Modify

- `v2/common/types.py` — Add `ProofBundle`, `ProofBundleSlice`, `ClientVerificationResult`, and downgrade `Certificate` semantics.
- `v2/compile/build_circuits.py` — Add stable registry digest generation and expose registry metadata needed by clients.
- `v2/services/distributed_coordinator.py` — Return `ProofBundle` as the primary artifact and downgrade server certificate to advisory.
- `v2/verifier/verify_chain.py` — Extract reusable verification internals so bundle verification is not tied to a trusted server workflow.
- `v2/experiments/refactored_e2e.py` — Switch to bundle generation plus client-side verification.
- `v2/experiments/resource_metrics.py` — Report bundle generation and client verification metrics on the new mainline.
- `README.md` — Rewrite the main workflow around client verification.
- `v2/docs/protocol.md` — Rewrite trust boundaries and protocol flow.
- `v2/docs/threat_model.md` — Rewrite the threat model for the strong-engineering trust model.

### Files to Downgrade or Mark as Baseline

- `v2/execution/pipeline.py`
- `v2/execution/deferred_pipeline.py`
- `v2/services/master_coordinator.py`
- `v2/experiments/distributed_e2e.py`

### Validation Targets

- `v2/experiments/refactored_e2e.py`
- `v2/experiments/resource_metrics.py`

---

## Task 1: Add Bundle Data Model And Registry Digest

**Files:**
- Modify: `v2/common/types.py`
- Modify: `v2/compile/build_circuits.py`

- [ ] **Step 1: Extend shared types with bundle-first structures**

Add client-verification-facing dataclasses to `v2/common/types.py`.

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

- [ ] **Step 3: Add stable registry digest helper**

In `v2/compile/build_circuits.py`, add a helper to compute a deterministic digest over registry JSON content.

```python
def compute_registry_digest(registry_data: list[dict]) -> str:
    payload = json.dumps(registry_data, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

Also add the necessary import:

```python
import hashlib
```

- [ ] **Step 4: Persist registry metadata including digest**

Update `build_registry()` so it writes both the slice registry and a metadata file usable by clients.

```python
registry_digest = compute_registry_digest(registry_data)
metadata_path = os.path.join(registry_dir, "registry", "registry_metadata.json")

with open(metadata_path, "w") as f:
    json.dump({
        "registry_digest": registry_digest,
        "slice_count": len(registry_data),
        "model_type": model_type,
    }, f, indent=2)
```

- [ ] **Step 5: Validate no syntax regressions in touched files**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -m py_compile v2/common/types.py v2/compile/build_circuits.py
```

Expected: no output and exit code `0`.

---

## Task 2: Introduce Client Bundle Verifier

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

- [ ] **Step 2: Create the bundle verification module**

Add `v2/verifier/bundle_verifier.py` with a client-facing API.

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

- [ ] **Step 3: Ensure bundle verification writes temp proof files when needed**

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

- [ ] **Step 4: Validate the new verifier module imports cleanly**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -m py_compile v2/verifier/verify_chain.py v2/verifier/bundle_verifier.py
```

Expected: no output and exit code `0`.

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
from v2.compile.build_circuits import compute_registry_digest
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

Construct and return a bundle object even if server-side advisory is retained.

```python
registry_data = [
    {
        "slice_id": a.slice_id,
        "model_digest": a.model_digest,
        "vk_path": a.vk_path,
        "settings_path": a.settings_path,
        "srs_path": a.srs_path,
    }
    for a in artifacts
]

bundle = ProofBundle(
    bundle_version="1.0",
    req_id=req_id,
    created_at=datetime.now(timezone.utc).isoformat(),
    model_id="mnist_mlp",
    registry_digest=compute_registry_digest(registry_data),
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

---

## Task 4: Switch Main E2E Experiment To Client Verification

**Files:**
- Modify: `v2/experiments/refactored_e2e.py`

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

- [ ] **Step 5: Run the main E2E experiment once**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -u -m v2.experiments.refactored_e2e --slices 2
```

Expected: normal case produces client `certified`; tamper/skip/random/replay cases produce client `invalid`.

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

- [ ] **Step 4: Update metrics output schema**

Include the new fields.

```python
results = {
    "num_slices": num_slices,
    "resource_profile": resource_profile,
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

- [ ] **Step 5: Run the metrics experiment**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -u v2/experiments/resource_metrics.py
```

Expected: JSON is written successfully and includes `bundle_generation_ms`, `client_verification_ms`, `client_verdict`, and `server_side_advisory`.

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

- [ ] **Step 4: Mark legacy paths as baseline/reference**

In README and protocol docs, explicitly downgrade:

```markdown
The following paths are retained as baseline/reference only and are not the recommended v2 trusted mainline:
- `v2/execution/pipeline.py`
- `v2/execution/deferred_pipeline.py`
- `v2/services/master_coordinator.py`
- `v2/experiments/distributed_e2e.py`
```

- [ ] **Step 5: Perform a consistency read-through**

Check that the following phrases are true everywhere:

- Coordinator is untrusted
- Client verification is final
- server certificate is advisory only
- Proof Bundle is the primary artifact

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

- [ ] **Step 3: Re-run the metrics path**

Run:

```powershell
& "C:\Users\v-yaolewu\AppData\Local\miniconda3\python.exe" -u v2/experiments/resource_metrics.py
```

Expected:

- metrics file is written successfully
- bundle-related fields exist
- client verdict is present and authoritative

- [ ] **Step 4: Confirm Definition of Done against the PRD**

Checklist:

```markdown
- Coordinator returns `ProofBundle`
- Client can independently verify from `ProofBundle + Registry`
- server advisory is not the trust root
- main experiments use client verification as final verdict
- docs match code semantics
- old paths are downgraded, not presented as mainline
```

- [ ] **Step 5: Commit**

```bash
git add v2/common/types.py v2/compile/build_circuits.py v2/verifier/verify_chain.py v2/verifier/bundle_verifier.py v2/services/distributed_coordinator.py v2/experiments/refactored_e2e.py v2/experiments/resource_metrics.py README.md v2/docs/protocol.md v2/docs/threat_model.md docs/refactor/2026-03-30-client-verifiable-zkp-framework-prd.md docs/refactor/2026-03-30-client-verifiable-zkp-framework-implementation-plan.md
git commit -m "refactor: add client-verifiable proof bundle workflow"
```
