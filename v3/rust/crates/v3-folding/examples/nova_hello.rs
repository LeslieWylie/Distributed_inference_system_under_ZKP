//! `nova_hello` — Phase 1 smoke test for the V3 Rust/Sonobe toolchain.
//!
//! Step function: `x_{i+1} = x_i^3 + x_i + 5` over the BN254 scalar field,
//! folded via Sonobe's Nova implementation for 10 steps, then the IVC proof
//! is verified.  Prove/verify time and a serialized-proof size are printed
//! so that Phase 3 has a baseline to compare against.
//!
//! This example is intentionally tiny: **no MNIST, no slicing, no linking**.
//! It exists to prove the workspace compiles and Sonobe's API surface is
//! usable.  See `docs/refactor/v3/02-phase1-rust-sonobe.md`.

#![allow(non_snake_case)]

use std::marker::PhantomData;
use std::time::Instant;

use ark_bn254::{Bn254, Fr, G1Projective as Projective};
use ark_ff::PrimeField;
use ark_grumpkin::Projective as Projective2;
use ark_r1cs_std::alloc::AllocVar;
use ark_r1cs_std::fields::fp::FpVar;
use ark_relations::gr1cs::{ConstraintSystemRef, SynthesisError};
use ark_serialize::CanonicalSerialize;

use folding_schemes::commitment::{kzg::KZG, pedersen::Pedersen};
use folding_schemes::folding::nova::{Nova, PreprocessorParam};
use folding_schemes::frontend::FCircuit;
use folding_schemes::transcript::poseidon::poseidon_canonical_config;
use folding_schemes::{Error, FoldingScheme};

/// Toy FCircuit implementing `z_{i+1} = z_i^3 + z_i + 5` on a 1-element state.
#[derive(Clone, Copy, Debug)]
pub struct CubicFCircuit<F: PrimeField> {
    _f: PhantomData<F>,
}

impl<F: PrimeField> FCircuit<F> for CubicFCircuit<F> {
    type Params = ();
    type ExternalInputs = ();
    type ExternalInputsVar = ();

    fn new(_params: Self::Params) -> Result<Self, Error> {
        Ok(Self { _f: PhantomData })
    }

    fn state_len(&self) -> usize {
        1
    }

    fn generate_step_constraints(
        &self,
        cs: ConstraintSystemRef<F>,
        _i: usize,
        z_i: Vec<FpVar<F>>,
        _external_inputs: Self::ExternalInputsVar,
    ) -> Result<Vec<FpVar<F>>, SynthesisError> {
        let five = FpVar::<F>::new_constant(cs.clone(), F::from(5u32))?;
        let x = z_i[0].clone();
        let x2 = &x * &x;
        let x3 = &x2 * &x;
        let out = x3 + &x + five;
        Ok(vec![out])
    }
}

fn main() -> Result<(), Error> {
    let num_steps: usize = 10;
    let initial_state = vec![Fr::from(1u32)];

    let f_circuit = CubicFCircuit::<Fr>::new(())?;
    let poseidon_config = poseidon_canonical_config::<Fr>();
    let mut rng = rand::rngs::OsRng;

    // Nova<G1, G2, FCircuit, CS1, CS2, H(=false => non-hiding)>
    type N = Nova<
        Projective,
        Projective2,
        CubicFCircuit<Fr>,
        KZG<'static, Bn254>,
        Pedersen<Projective2>,
        false,
    >;

    println!("[nova_hello] preprocessing Nova params ...");
    let setup_start = Instant::now();
    let preprocess_params = PreprocessorParam::new(poseidon_config, f_circuit);
    let nova_params = N::preprocess(&mut rng, &preprocess_params)?;
    let setup_ms = setup_start.elapsed().as_millis();
    println!("[nova_hello] setup_ms: {}", setup_ms);

    println!("[nova_hello] initializing folding scheme ...");
    let mut folding_scheme = N::init(&nova_params, f_circuit, initial_state.clone())?;

    let mut per_step_ms: Vec<u128> = Vec::with_capacity(num_steps);
    let prove_start = Instant::now();
    for i in 0..num_steps {
        let step_start = Instant::now();
        folding_scheme.prove_step(rng, (), None)?;
        let step_ms = step_start.elapsed().as_millis();
        per_step_ms.push(step_ms);
        println!("[nova_hello] prove_step {}: {} ms", i, step_ms);
    }
    let prove_total_ms = prove_start.elapsed().as_millis();

    let ivc_proof = folding_scheme.ivc_proof();

    // Approximate proof size via canonical serialization.
    let mut proof_bytes: Vec<u8> = Vec::new();
    ivc_proof
        .serialize_compressed(&mut proof_bytes)
        .expect("serialize ivc_proof");
    let proof_size_bytes = proof_bytes.len();

    let verify_start = Instant::now();
    N::verify(nova_params.1.clone(), ivc_proof)?;
    let verify_ms = verify_start.elapsed().as_millis();

    // ------------------------------------------------------------------
    // Output block — machine-parseable by the Python bridge
    // (key: value per line).  Keep stable; Phase 3 will graduate to JSON.
    // ------------------------------------------------------------------
    println!("---- nova_hello summary ----");
    println!("verify: true");
    println!("num_steps: {}", num_steps);
    println!("state_len: 1");
    println!("setup_ms: {}", setup_ms);
    println!("prove_total_ms: {}", prove_total_ms);
    println!("verify_ms: {}", verify_ms);
    println!("proof_size_bytes: {}", proof_size_bytes);
    println!(
        "per_step_ms: [{}]",
        per_step_ms
            .iter()
            .map(|v| v.to_string())
            .collect::<Vec<_>>()
            .join(",")
    );

    Ok(())
}
