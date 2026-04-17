//! Phase 2 correctness test suite.
//!
//! Loads `v3/artifacts/models/mnist_mlp_v3_slices.json` and
//! `v3/artifacts/models/mnist_mlp_v3_cases.json` (both produced by the
//! Python exporter), then runs the 100 stored MNIST samples through the
//! *native* slice pipeline and asserts bit-for-bit equality against the
//! Python fixed-point ground truth, plus `ε < 0.01` on the dequantized
//! logits against the PyTorch float output.
//!
//! The acceptance criterion from `03-phase2-mnist-r1cs.md` is "100 组 test
//! case 全通过" — exact match to the Python reference is how we get there.

use std::path::{Path, PathBuf};

use ark_bn254::Fr;
use ark_ff::Zero;
use serde::Deserialize;

use v3_circuit::gadgets::i64_to_field;
use v3_circuit::model::forward_slice_fixed_point;
use v3_circuit::{
    MnistSlice1Circuit, MnistSlice2Circuit, MnistSliceParams, SlicesDocument, STATE_DIM,
};
use folding_schemes::frontend::FCircuit;

#[derive(Debug, Clone, Deserialize)]
struct TestCase {
    input: Vec<i64>,
    slice_outputs: Vec<Vec<i64>>,
    float_output: Vec<f64>,
    pytorch_pred: usize,
    #[allow(dead_code)]
    label: i64,
}

#[derive(Debug, Clone, Deserialize)]
struct TestCases {
    #[allow(dead_code)]
    version: String,
    scale: usize,
    num_cases: usize,
    #[allow(dead_code)]
    seed: u64,
    model_id: String,
    cases: Vec<TestCase>,
}

fn artifacts_dir() -> PathBuf {
    // tests/ is at v3/rust/crates/v3-circuit/tests — walk up to repo root.
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    Path::new(manifest_dir)
        .ancestors()
        .find(|p| p.join("v3").is_dir() && p.join(".gitignore").exists())
        .map(|root| root.join("v3").join("artifacts").join("models"))
        .unwrap_or_else(|| PathBuf::from("v3/artifacts/models"))
}

fn load_fixtures() -> (SlicesDocument, TestCases) {
    let dir = artifacts_dir();
    let slices_path = dir.join("mnist_mlp_v3_slices.json");
    let cases_path = dir.join("mnist_mlp_v3_cases.json");
    assert!(
        slices_path.exists(),
        "missing {} — run `python -m v3.python.models.mnist_export` first",
        slices_path.display()
    );
    assert!(
        cases_path.exists(),
        "missing {} — run `python -m v3.python.models.gen_test_cases` first",
        cases_path.display()
    );
    let slices = SlicesDocument::load_from_path(&slices_path).expect("parse slices json");
    let cases_text = std::fs::read_to_string(&cases_path).expect("read cases json");
    let cases: TestCases = serde_json::from_str(&cases_text).expect("parse cases json");
    (slices, cases)
}

fn pad_to_state(input: &[i64]) -> Vec<Fr> {
    let mut v = vec![Fr::zero(); STATE_DIM];
    for (i, x) in input.iter().enumerate() {
        v[i] = i64_to_field::<Fr>(*x);
    }
    v
}

fn field_vec_head_to_i64(state: &[Fr], n: usize) -> Vec<i64> {
    state
        .iter()
        .take(n)
        .map(|f| v3_circuit::gadgets::field_to_i128(*f) as i64)
        .collect()
}

#[test]
fn slice_payloads_parse_and_shape_matches() {
    let (slices, _cases) = load_fixtures();
    assert_eq!(slices.model_id, "mnist_mlp_v3");
    assert_eq!(slices.num_slices, 2);
    assert_eq!(slices.slices[0].input_dim, 784);
    assert_eq!(slices.slices[0].output_dim, 64);
    assert_eq!(slices.slices[1].input_dim, 64);
    assert_eq!(slices.slices[1].output_dim, 10);
}

#[test]
fn pure_native_matches_python_for_all_cases() {
    let (slices, cases) = load_fixtures();
    assert_eq!(slices.model_id, cases.model_id);
    assert_eq!(slices.scale, cases.scale);
    assert_eq!(cases.num_cases, cases.cases.len());
    assert_eq!(cases.num_cases, 100, "expected 100 test cases");

    let slice0 = &slices.slices[0];
    let slice1 = &slices.slices[1];
    let scale = slices.scale;

    for (i, case) in cases.cases.iter().enumerate() {
        assert_eq!(case.input.len(), 784, "case {}: bad input len", i);
        let out1 = forward_slice_fixed_point(&case.input, &slice0.layers, scale);
        assert_eq!(out1.len(), 64);
        assert_eq!(
            out1, case.slice_outputs[0],
            "case {}: slice 1 native mismatch", i
        );
        let out2 = forward_slice_fixed_point(&out1, &slice1.layers, scale);
        assert_eq!(out2.len(), 10);
        assert_eq!(
            out2, case.slice_outputs[1],
            "case {}: slice 2 native mismatch", i
        );
    }
}

#[test]
fn fcircuit_step_native_matches_python_for_all_cases() {
    let (slices, cases) = load_fixtures();
    let scale = slices.scale;
    let slice0_params = MnistSliceParams::new(slices.slices[0].clone(), scale);
    let slice1_params = MnistSliceParams::new(slices.slices[1].clone(), scale);

    let s1 = MnistSlice1Circuit::<Fr>::new(slice0_params).unwrap();
    let s2 = MnistSlice2Circuit::<Fr>::new(slice1_params).unwrap();
    assert_eq!(s1.state_len(), STATE_DIM);
    assert_eq!(s2.state_len(), STATE_DIM);

    for (i, case) in cases.cases.iter().enumerate() {
        let z0 = pad_to_state(&case.input);
        let z1 = s1.step_native(&z0);
        let got1 = field_vec_head_to_i64(&z1, 64);
        assert_eq!(
            got1, case.slice_outputs[0],
            "case {}: slice 1 FCircuit step_native mismatch", i
        );
        let z2 = s2.step_native(&z1);
        let got2 = field_vec_head_to_i64(&z2, 10);
        assert_eq!(
            got2, case.slice_outputs[1],
            "case {}: slice 2 FCircuit step_native mismatch", i
        );
    }
}

#[test]
fn dequantized_logits_epsilon_below_budget() {
    let (slices, cases) = load_fixtures();
    let scale = slices.scale;
    let denom = (1u64 << scale) as f64;

    let mut max_eps = 0.0f64;
    let mut total_eps = 0.0f64;
    let mut pred_match = 0usize;
    for case in &cases.cases {
        let logits_int = &case.slice_outputs[1];
        let logits_dequant: Vec<f64> =
            logits_int.iter().map(|v| (*v as f64) / denom).collect();
        let mut local_max = 0.0f64;
        for (a, b) in logits_dequant.iter().zip(case.float_output.iter()) {
            local_max = local_max.max((a - b).abs());
        }
        total_eps += local_max;
        if local_max > max_eps {
            max_eps = local_max;
        }
        let circuit_pred = logits_dequant
            .iter()
            .enumerate()
            .max_by(|x, y| x.1.partial_cmp(y.1).unwrap())
            .map(|(i, _)| i)
            .unwrap();
        if circuit_pred == case.pytorch_pred {
            pred_match += 1;
        }
    }
    let mean_eps = total_eps / (cases.num_cases as f64);
    println!(
        "epsilon report: max={:.6} mean={:.6} pred_match={}/{}",
        max_eps, mean_eps, pred_match, cases.num_cases
    );
    assert!(
        max_eps < 0.01,
        "max epsilon {:.6} exceeds 0.01 budget",
        max_eps
    );
    assert_eq!(
        pred_match, cases.num_cases,
        "quantization changed a prediction (pred_match={}/{})",
        pred_match, cases.num_cases
    );
}

#[test]
fn slice1_constraint_system_is_satisfied_on_case_zero() {
    use ark_relations::gr1cs::ConstraintSystem;
    use folding_schemes::frontend::utils::WrapperCircuit;
    use ark_relations::gr1cs::ConstraintSynthesizer;

    let (slices, cases) = load_fixtures();
    let params = MnistSliceParams::new(slices.slices[0].clone(), slices.scale);
    let fc = MnistSlice1Circuit::<Fr>::new(params).unwrap();

    let case = &cases.cases[0];
    let z0 = pad_to_state(&case.input);
    let z1 = fc.step_native(&z0);

    let cs = ConstraintSystem::<Fr>::new_ref();
    let wrapper = WrapperCircuit::<Fr, MnistSlice1Circuit<Fr>> {
        FC: fc,
        z_i: Some(z0),
        z_i1: Some(z1),
    };
    wrapper.generate_constraints(cs.clone()).unwrap();
    cs.finalize();
    let num_constraints = cs.num_constraints();
    println!(
        "slice1 WrapperCircuit: {} constraints ({} witnesses, satisfied={})",
        num_constraints,
        cs.num_witness_variables(),
        cs.is_satisfied().unwrap_or(false)
    );
    assert!(cs.is_satisfied().unwrap(), "slice 1 constraint system unsatisfied");
}
