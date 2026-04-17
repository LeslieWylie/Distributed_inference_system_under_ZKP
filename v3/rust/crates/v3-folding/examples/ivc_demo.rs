//! `ivc_demo` — Phase 3 end-to-end demo.
//!
//! Runs the Phase 2 MNIST MLP through `num_slices / 2` folding steps of
//! Nova (each step = `slice_1 ∘ slice_2`), produces a succinct Groth16
//! decider proof via Sonobe `decider_eth`, verifies it, and emits:
//!
//! * A machine-parseable trailer (`---- ivc_demo summary ----`) that the
//!   Python bridge captures.
//! * An IVC-proof envelope JSON under
//!   `v3/artifacts/proofs/<ts>_slices<N>.json` (schema aligned with
//!   `docs/refactor/v3/99-interfaces.md §3`).
//! * An appended row in `v3/metrics/ivc_benchmarks.json`.
//!
//! CLI:
//!   cargo run --release -p v3-folding --example ivc_demo -- --slices <N>
//!
//! Where `<N>` is one of 2 / 4 / 8 (per Phase 3 brief §3(3); other
//! even values >= 2 also work and are useful for ad-hoc sweeps).

#![allow(non_snake_case)]

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use ark_bn254::Fr;
use ark_ec::CurveGroup;
use ark_ff::Zero;
use ark_serialize::CanonicalSerialize;
use base64::Engine as _;
use serde_json::{json, Value as JsonValue};
use sha2::{Digest, Sha256};

use folding_schemes::folding::traits::CommittedInstanceOps;
use folding_schemes::frontend::FCircuit;
use folding_schemes::Error as FSError;

use v3_circuit::{gadgets::{field_to_i128, i64_to_field}, SlicesDocument, STATE_DIM};
use v3_decider::{prove_and_verify, DeciderRunOutput};
use v3_folding::{MnistIvcDriver, MnistStepCircuit, MnistStepParams};

// -----------------------------------------------------------------------
// Layout helpers
// -----------------------------------------------------------------------

fn repo_root() -> PathBuf {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    Path::new(manifest_dir)
        .ancestors()
        .find(|p| p.join("v3").is_dir() && p.join(".gitignore").exists())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."))
}

// -----------------------------------------------------------------------
// CLI argument parsing (no clap dep to keep the crate light).
// -----------------------------------------------------------------------

struct Args {
    slices: usize,
    case_index: usize,
    output_envelope_dir: PathBuf,
    metrics_file: PathBuf,
}

fn parse_args() -> Args {
    let mut slices: usize = 2;
    let mut case_index: usize = 0;
    let root = repo_root();
    let mut envelope_dir = root.join("v3").join("artifacts").join("proofs");
    let mut metrics_file = root.join("v3").join("metrics").join("ivc_benchmarks.json");

    let argv: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < argv.len() {
        match argv[i].as_str() {
            "--slices" => {
                slices = argv
                    .get(i + 1)
                    .and_then(|s| s.parse::<usize>().ok())
                    .expect("--slices needs an integer");
                i += 2;
            }
            "--case-index" => {
                case_index = argv
                    .get(i + 1)
                    .and_then(|s| s.parse::<usize>().ok())
                    .expect("--case-index needs an integer");
                i += 2;
            }
            "--envelope-dir" => {
                envelope_dir = PathBuf::from(&argv[i + 1]);
                i += 2;
            }
            "--metrics-file" => {
                metrics_file = PathBuf::from(&argv[i + 1]);
                i += 2;
            }
            other => panic!("unknown arg: {other}"),
        }
    }

    if slices < 2 || !slices.is_multiple_of(2) {
        panic!("--slices must be even and >= 2 (got {slices})");
    }
    Args {
        slices,
        case_index,
        output_envelope_dir: envelope_dir,
        metrics_file,
    }
}

// -----------------------------------------------------------------------
// Main
// -----------------------------------------------------------------------

fn main() -> Result<(), FSError> {
    let args = parse_args();
    // Each Nova step = slice1 ∘ slice2 (one full MNIST inference).
    // `--slices N` means N Nova folding steps = N MNIST inferences in
    // the same IVC chain = 2N slice-level operations total. See
    // `step_circuit.rs` for why per-step branching is incompatible with
    // Nova's uniform-R1CS requirement.
    let num_steps = args.slices;

    // -------------------------------------------------------------------
    // Load model + input
    // -------------------------------------------------------------------
    let root = repo_root();
    let art_models = root.join("v3").join("artifacts").join("models");
    let slices_path = art_models.join("mnist_mlp_v3_slices.json");
    let cases_path = art_models.join("mnist_mlp_v3_cases.json");

    let slices_doc = SlicesDocument::load_from_path(&slices_path)
        .map_err(|e| FSError::Other(format!("load slices: {e:?}")))?;
    if slices_doc.slices.len() < 2 {
        return Err(FSError::Other(format!(
            "expected >= 2 slice payloads, got {}",
            slices_doc.slices.len()
        )));
    }

    let cases_text = std::fs::read_to_string(&cases_path)?;
    let cases_json: JsonValue =
        serde_json::from_str(&cases_text).map_err(|e| FSError::Other(format!("{e:?}")))?;

    let case = &cases_json["cases"][args.case_index];
    let input_vec = case["input"]
        .as_array()
        .ok_or_else(|| FSError::Other(format!("cases[{}].input missing", args.case_index)))?;
    let pytorch_pred = case["pytorch_pred"].as_i64().unwrap_or(-1);

    // z_0: padded quantized image (784 ints, pad STATE_DIM - 784 zeros).
    let mut z_0 = vec![Fr::zero(); STATE_DIM];
    for (j, v) in input_vec.iter().enumerate() {
        let x = v
            .as_i64()
            .ok_or_else(|| FSError::Other("input element not i64".into()))?;
        z_0[j] = i64_to_field::<Fr>(x);
    }
    // image hash (sha256 over the canonical little-endian i64 vector).
    let image_hash = {
        let mut h = Sha256::new();
        for v in input_vec.iter() {
            let x = v.as_i64().unwrap_or(0);
            h.update(x.to_le_bytes());
        }
        let out = h.finalize();
        format!("0x{}", hex_encode(&out))
    };

    // Model commit: sha256 over the slices document's canonical JSON
    // (sort_keys=false — we rely on the file's exact byte shape, which
    // is what Phase 2 wrote). This is a simple non-cryptographic
    // placeholder; Phase 4 will replace with a real Pedersen / KZG
    // commitment to the weight vector.
    let model_commit = {
        let bytes = std::fs::read(&slices_path)?;
        let mut h = Sha256::new();
        h.update(&bytes);
        format!("0x{}", hex_encode(&h.finalize()))
    };

    // -------------------------------------------------------------------
    // Build unified step circuit + driver
    // -------------------------------------------------------------------
    let params = MnistStepParams::new(
        slices_doc.slices[0].clone(),
        slices_doc.slices[1].clone(),
        slices_doc.scale,
    );
    let circuit = MnistStepCircuit::<Fr>::new(params)?;
    let (n_lin1, n_lin2) = circuit.layer_signature();
    println!(
        "[ivc_demo] circuit layer signature: slice1_linears={}, slice2_linears={}",
        n_lin1, n_lin2
    );

    println!(
        "[ivc_demo] building driver (num_steps={}, state_len={})",
        num_steps,
        circuit.state_len()
    );
    let driver = MnistIvcDriver::setup(circuit.clone(), num_steps)?;
    println!("[ivc_demo] setup_ms: {}", driver.setup_ms());

    // -------------------------------------------------------------------
    // Fold
    // -------------------------------------------------------------------
    let (nova, prove_timings) = driver.prove(z_0.clone())?;
    for (i, ms) in prove_timings.per_step_ms.iter().enumerate() {
        println!("[ivc_demo] prove_step {}: {} ms", i, ms);
    }
    println!(
        "[ivc_demo] ivc_raw_proof_bytes={} ivc_verify_ms={}",
        prove_timings.raw_ivc_proof_size_bytes, prove_timings.ivc_verify_ms
    );

    // Native output (for envelope metadata + sanity vs PyTorch).
    let z_i_native = circuit.step_native(&z_0);
    // After num_steps: the final state is the Nova-folded z_i. We can
    // compare against iterated native eval.
    let mut z_iter = z_0.clone();
    for _ in 0..num_steps {
        z_iter = circuit.step_native(&z_iter);
    }
    // Circuit-level final state for comparison.
    let z_final_nova: &[Fr] = &nova.z_i;
    // Validate parity: Nova's z_i matches iterated native (first 10 slots).
    for j in 0..MnistStepCircuit::<Fr>::SLICE2_OUTPUT_DIM {
        if z_iter[j] != z_final_nova[j] {
            return Err(FSError::Other(format!(
                "native vs nova z_final mismatch at logit {}: native={:?}, nova={:?}",
                j, z_iter[j], z_final_nova[j]
            )));
        }
    }
    let logits: Vec<i64> = z_final_nova
        .iter()
        .take(MnistStepCircuit::<Fr>::SLICE2_OUTPUT_DIM)
        .map(|v| field_to_i128(*v) as i64)
        .collect();
    let argmax = logits
        .iter()
        .enumerate()
        .max_by(|a, b| a.1.cmp(b.1))
        .map(|(i, _)| i)
        .unwrap_or(0);
    let _ = z_i_native;

    // -------------------------------------------------------------------
    // Decider (Groth16 via Sonobe decider_eth)
    // -------------------------------------------------------------------
    let i_fr = nova.i;
    let z_0_final = nova.z_0.clone();
    let z_i_final = nova.z_i.clone();
    let running_commits = nova.U_i.get_commitments();
    let incoming_commits = nova.u_i.get_commitments();

    let decider_out: DeciderRunOutput = prove_and_verify(&driver, nova)?;

    println!(
        "[ivc_demo] decider setup_ms={} prove_ms={} verify_ms={} proof_size_bytes={}",
        decider_out.timings.setup_ms,
        decider_out.timings.prove_ms,
        decider_out.timings.verify_ms,
        decider_out.timings.proof_size_bytes
    );

    // -------------------------------------------------------------------
    // Envelope JSON (99-interfaces.md §3)
    // -------------------------------------------------------------------
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let ts_str = format_ts(ts);
    std::fs::create_dir_all(&args.output_envelope_dir)?;

    let proof_b64 = base64::engine::general_purpose::STANDARD.encode(&decider_out.proof_bytes);

    // Serialize z_0, z_i, running/incoming commits for client-side verify.
    let z_0_bytes = serialize_field_vec(&z_0_final);
    let z_i_bytes = serialize_field_vec(&z_i_final);
    let running_bytes = serialize_point_vec(&running_commits);
    let incoming_bytes = serialize_point_vec(&incoming_commits);
    let i_fr_bytes = serialize_field(i_fr);
    let vp_bytes = serialize_decider_vp(&decider_out)?;

    let envelope = json!({
        "protocol_version": "v3-ivc-0.1",
        "model_commit": model_commit,
        "input_hash": image_hash,
        "claimed_output": logits,
        "num_slices": args.slices,
        "num_nova_steps": num_steps,
        "proof_bytes": proof_b64,
        "commitments": {
            "z_0": hex_encode(&z_0_bytes),
            "z_i_final": hex_encode(&z_i_bytes),
            "running": hex_encode(&running_bytes),
            "incoming": hex_encode(&incoming_bytes)
        },
        "public_inputs": [
            format!("0x{}", hex_encode(&i_fr_bytes)),
        ],
        "verifier_key": {
            "snark_vp_sha256": format!("0x{}", hex_encode(&sha256(&decider_out.snark_vp_bytes))),
            "vp_bytes_len": vp_bytes.len(),
            "vp_bytes_hex": hex_encode(&vp_bytes)
        },
        "metadata": {
            "prover_version": "v3-folding 0.1.0",
            "decider": "sonobe decider_eth + Groth16<Bn254>",
            "state_dim": STATE_DIM,
            "pytorch_pred": pytorch_pred,
            "circuit_pred": argmax,
            "prove_time_ms": {
                "setup": prove_timings.setup_ms,
                "init": prove_timings.init_ms,
                "per_step": prove_timings.per_step_ms,
                "fold_total": prove_timings.prove_total_ms,
                "ivc_verify": prove_timings.ivc_verify_ms,
                "decider_setup": decider_out.timings.setup_ms,
                "decider_prove": decider_out.timings.prove_ms,
                "total": prove_timings.setup_ms
                    + prove_timings.init_ms
                    + prove_timings.prove_total_ms
                    + decider_out.timings.setup_ms
                    + decider_out.timings.prove_ms
            },
            "verify_ms": decider_out.timings.verify_ms,
            "proof_size_bytes": decider_out.timings.proof_size_bytes,
            "raw_ivc_proof_size_bytes": prove_timings.raw_ivc_proof_size_bytes
        }
    });

    let envelope_path = args
        .output_envelope_dir
        .join(format!("{}_slices{}.json", ts_str, args.slices));
    std::fs::write(&envelope_path, serde_json::to_vec_pretty(&envelope).unwrap())?;
    println!("[ivc_demo] envelope_path: {}", envelope_path.display());

    // -------------------------------------------------------------------
    // Metrics JSON (append / merge)
    // -------------------------------------------------------------------
    let metrics_row = json!({
        "slices": args.slices,
        "num_nova_steps": num_steps,
        "setup_ms": prove_timings.setup_ms,
        "init_ms": prove_timings.init_ms,
        "per_step_prove_ms": prove_timings.per_step_ms,
        "total_prove_ms": prove_timings.prove_total_ms,
        "decider_setup_ms": decider_out.timings.setup_ms,
        "decider_prove_ms": decider_out.timings.prove_ms,
        "decider_verify_ms": decider_out.timings.verify_ms,
        "proof_size_bytes": decider_out.timings.proof_size_bytes,
        "raw_ivc_proof_size_bytes": prove_timings.raw_ivc_proof_size_bytes,
        "snark_vp_sha256": format!("0x{}", hex_encode(&sha256(&decider_out.snark_vp_bytes))),
        "snark_vp_bytes_len": decider_out.snark_vp_bytes.len(),
        "verify_ms": decider_out.timings.verify_ms,
        "envelope_path": envelope_path.display().to_string(),
        "ts": ts_str.clone(),
    });
    merge_metrics_row(&args.metrics_file, metrics_row)?;

    // -------------------------------------------------------------------
    // Machine-parseable trailer
    // -------------------------------------------------------------------
    println!("---- ivc_demo summary ----");
    println!("verify: true");
    println!("slices: {}", args.slices);
    println!("num_nova_steps: {}", num_steps);
    println!("state_len: {}", STATE_DIM);
    println!("setup_ms: {}", prove_timings.setup_ms);
    println!("init_ms: {}", prove_timings.init_ms);
    println!("prove_total_ms: {}", prove_timings.prove_total_ms);
    println!("decider_setup_ms: {}", decider_out.timings.setup_ms);
    println!("decider_prove_ms: {}", decider_out.timings.prove_ms);
    println!("verify_ms: {}", decider_out.timings.verify_ms);
    println!("proof_size_bytes: {}", decider_out.timings.proof_size_bytes);
    println!(
        "raw_ivc_proof_size_bytes: {}",
        prove_timings.raw_ivc_proof_size_bytes
    );
    println!(
        "per_step_ms: [{}]",
        prove_timings
            .per_step_ms
            .iter()
            .map(|v| v.to_string())
            .collect::<Vec<_>>()
            .join(",")
    );
    println!(
        "snark_vp_sha256: 0x{}",
        hex_encode(&sha256(&decider_out.snark_vp_bytes))
    );
    println!("envelope_path: {}", envelope_path.display());
    println!("pytorch_pred: {}", pytorch_pred);
    println!("circuit_pred: {}", argmax);

    Ok(())
}

// -----------------------------------------------------------------------
// Serialization helpers
// -----------------------------------------------------------------------

fn serialize_field(v: Fr) -> Vec<u8> {
    let mut out = Vec::new();
    v.serialize_compressed(&mut out).expect("serialize field");
    out
}

fn serialize_field_vec(vs: &[Fr]) -> Vec<u8> {
    let mut out = Vec::new();
    vs.serialize_compressed(&mut out).expect("serialize field vec");
    out
}

fn serialize_point_vec<C: CurveGroup>(ps: &[C]) -> Vec<u8> {
    let mut out = Vec::new();
    ps.serialize_compressed(&mut out).expect("serialize point vec");
    out
}

fn serialize_decider_vp(out: &DeciderRunOutput) -> Result<Vec<u8>, FSError> {
    let mut bytes = Vec::new();
    out.vp
        .serialize_compressed(&mut bytes)
        .map_err(|e| FSError::Other(format!("serialize vp: {e:?}")))?;
    Ok(bytes)
}

fn hex_encode(b: &[u8]) -> String {
    let mut s = String::with_capacity(b.len() * 2);
    for byte in b {
        s.push_str(&format!("{:02x}", byte));
    }
    s
}

fn sha256(b: &[u8]) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(b);
    let out = h.finalize();
    let mut arr = [0u8; 32];
    arr.copy_from_slice(&out);
    arr
}

fn format_ts(epoch_secs: u64) -> String {
    // UTC YYYYMMDD_HHMMSS — avoid chrono dep.
    let days_since_epoch = (epoch_secs / 86400) as i64;
    let secs_of_day = (epoch_secs % 86400) as u32;
    let (y, mo, d) = days_to_ymd(days_since_epoch);
    let h = secs_of_day / 3600;
    let m = (secs_of_day / 60) % 60;
    let s = secs_of_day % 60;
    format!("{:04}{:02}{:02}_{:02}{:02}{:02}", y, mo, d, h, m, s)
}

// Civil-from-days algorithm (Howard Hinnant, public-domain).
fn days_to_ymd(mut z: i64) -> (i32, u32, u32) {
    z += 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365; // [0, 399]
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = doy - (153 * mp + 2) / 5 + 1; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 }; // [1, 12]
    let yfinal = y + if m <= 2 { 1 } else { 0 };
    (yfinal as i32, m as u32, d as u32)
}

// -----------------------------------------------------------------------
// Metrics merge
// -----------------------------------------------------------------------

fn merge_metrics_row(path: &Path, row: JsonValue) -> Result<(), FSError> {
    use std::collections::BTreeMap;

    std::fs::create_dir_all(path.parent().unwrap_or_else(|| Path::new(".")))?;
    let mut doc: JsonValue = if path.exists() {
        match std::fs::read_to_string(path) {
            Ok(s) if !s.trim().is_empty() => serde_json::from_str(&s).unwrap_or_else(|_| {
                json!({"schema_version": "v3-metrics-0.1", "rows": []})
            }),
            _ => json!({"schema_version": "v3-metrics-0.1", "rows": []}),
        }
    } else {
        json!({"schema_version": "v3-metrics-0.1", "rows": []})
    };

    // Upsert by (slices) key — keep latest run per slice count.
    let slices_key = row["slices"].as_u64().unwrap_or(0);
    let rows = doc["rows"].as_array_mut().expect("rows is array");
    let mut map: BTreeMap<u64, JsonValue> = BTreeMap::new();
    for r in rows.drain(..) {
        let k = r["slices"].as_u64().unwrap_or(0);
        map.insert(k, r);
    }
    map.insert(slices_key, row);
    for (_k, v) in map {
        rows.push(v);
    }
    std::fs::write(path, serde_json::to_vec_pretty(&doc).unwrap())?;
    Ok(())
}
