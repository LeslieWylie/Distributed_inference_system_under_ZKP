//! `mnist_single_slice` — Phase 2 smoke test: prove+verify a single MNIST
//! slice step through Sonobe Nova.
//!
//! This runs **one** Nova fold step on `MnistSlice2Circuit` (the compact
//! `64→10` final layer) using a real MNIST test case. The goal is not
//! performance — it is to prove end-to-end that:
//!
//! 1. `MnistSlice2Circuit::generate_step_constraints` produces a valid
//!    R1CS that Sonobe's Nova accepts.
//! 2. `step_native` and the R1CS agree on the output state (otherwise the
//!    augmented F-circuit witness generation would fail).
//! 3. `N::verify` returns Ok on the resulting IVC proof.
//!
//! We deliberately run a *single* step — multi-slice folding is Phase 3.
//! Slice 2 is chosen instead of Slice 1 to keep this example fast; Slice 1
//! is exercised directly by `v3-circuit`'s consistency test.

#![allow(non_snake_case)]

use std::path::{Path, PathBuf};
use std::time::Instant;

use ark_bn254::{Bn254, Fr, G1Projective as Projective};
use ark_ff::Zero;
use ark_grumpkin::Projective as Projective2;
use ark_serialize::CanonicalSerialize;

use folding_schemes::commitment::{kzg::KZG, pedersen::Pedersen};
use folding_schemes::folding::nova::{Nova, PreprocessorParam};
use folding_schemes::frontend::FCircuit;
use folding_schemes::transcript::poseidon::poseidon_canonical_config;
use folding_schemes::{Error, FoldingScheme};

use v3_circuit::gadgets::{i64_to_field, field_to_i128};
use v3_circuit::{MnistSlice2Circuit, MnistSliceParams, SlicesDocument, STATE_DIM};

fn repo_artifacts_dir() -> PathBuf {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    Path::new(manifest_dir)
        .ancestors()
        .find(|p| p.join("v3").is_dir() && p.join(".gitignore").exists())
        .map(|root| root.join("v3").join("artifacts").join("models"))
        .unwrap_or_else(|| PathBuf::from("v3/artifacts/models"))
}

fn main() -> Result<(), Error> {
    let art = repo_artifacts_dir();
    let slices_path = art.join("mnist_mlp_v3_slices.json");
    let cases_path = art.join("mnist_mlp_v3_cases.json");

    let slices = SlicesDocument::load_from_path(&slices_path)
        .map_err(|e| Error::Other(format!("load slices: {e:?}")))?;
    let cases_text = std::fs::read_to_string(&cases_path)?;
    let cases_json: serde_json::Value = serde_json::from_str(&cases_text)
        .map_err(|e| Error::Other(format!("parse cases: {e:?}")))?;

    // Pull slice2's input from case 0 (= slice1's output).
    let case0 = &cases_json["cases"][0];
    let slice1_out_vec = case0["slice_outputs"][0]
        .as_array()
        .ok_or_else(|| Error::Other("case0.slice_outputs[0] missing".into()))?;
    let pytorch_pred = case0["pytorch_pred"].as_i64().unwrap_or(-1);

    // Build z_0 (padded to STATE_DIM) from the 64-int slice-1 output.
    let mut z_0 = vec![Fr::zero(); STATE_DIM];
    for (i, v) in slice1_out_vec.iter().enumerate() {
        let x = v.as_i64().expect("slice_output element is i64");
        z_0[i] = i64_to_field::<Fr>(x);
    }

    let params = MnistSliceParams::new(slices.slices[1].clone(), slices.scale);
    let f_circuit = MnistSlice2Circuit::<Fr>::new(params)?;
    assert_eq!(f_circuit.state_len(), STATE_DIM);

    let poseidon_config = poseidon_canonical_config::<Fr>();
    let mut rng = rand::rngs::OsRng;

    type N = Nova<
        Projective,
        Projective2,
        MnistSlice2Circuit<Fr>,
        KZG<'static, Bn254>,
        Pedersen<Projective2>,
        false, // non-hiding; Phase 4 will flip this when linking commitments.
    >;

    println!("[mnist_single_slice] preprocessing Nova params (this is slow) ...");
    let setup_start = Instant::now();
    let preprocess_params = PreprocessorParam::new(poseidon_config, f_circuit.clone());
    let nova_params = N::preprocess(&mut rng, &preprocess_params)?;
    let setup_ms = setup_start.elapsed().as_millis();
    println!("[mnist_single_slice] setup_ms: {}", setup_ms);

    let init_start = Instant::now();
    let mut folding_scheme = N::init(&nova_params, f_circuit.clone(), z_0.clone())?;
    let init_ms = init_start.elapsed().as_millis();
    println!("[mnist_single_slice] init_ms: {}", init_ms);

    let prove_start = Instant::now();
    folding_scheme.prove_step(rng, (), None)?;
    let prove_ms = prove_start.elapsed().as_millis();
    println!("[mnist_single_slice] prove_step_ms: {}", prove_ms);

    let ivc_proof = folding_scheme.ivc_proof();
    let mut proof_bytes: Vec<u8> = Vec::new();
    ivc_proof
        .serialize_compressed(&mut proof_bytes)
        .map_err(|e| Error::Other(format!("serialize ivc_proof: {e:?}")))?;
    let proof_size_bytes = proof_bytes.len();

    let verify_start = Instant::now();
    N::verify(nova_params.1.clone(), ivc_proof)?;
    let verify_ms = verify_start.elapsed().as_millis();

    // Recompute native output to display the argmax for sanity.
    let z_1_native = f_circuit.step_native(&z_0);
    let logits: Vec<i64> = z_1_native
        .iter()
        .take(MnistSlice2Circuit::<Fr>::OUTPUT_DIM)
        .map(|v| field_to_i128(*v) as i64)
        .collect();
    let argmax = logits
        .iter()
        .enumerate()
        .max_by(|a, b| a.1.cmp(b.1))
        .map(|(i, _)| i)
        .unwrap();

    println!("---- mnist_single_slice summary ----");
    println!("verify: true");
    println!("slice_index: 1");
    println!("state_len: {}", STATE_DIM);
    println!("setup_ms: {}", setup_ms);
    println!("init_ms: {}", init_ms);
    println!("prove_step_ms: {}", prove_ms);
    println!("verify_ms: {}", verify_ms);
    println!("proof_size_bytes: {}", proof_size_bytes);
    println!("pytorch_pred: {}", pytorch_pred);
    println!("circuit_pred: {}", argmax);
    println!(
        "logits: [{}]",
        logits
            .iter()
            .map(|v| v.to_string())
            .collect::<Vec<_>>()
            .join(",")
    );

    Ok(())
}
