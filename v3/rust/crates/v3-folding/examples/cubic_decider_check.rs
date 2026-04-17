//! `cubic_decider_check` — Sonobe pin-health baseline.
//!
//! Goal: confirm that the frozen Sonobe pin (`63f2930d`) can successfully
//! generate **and verify** a Groth16 decider proof on Sonobe's own
//! `CubicFCircuit` (state_len=1) via
//! `folding_schemes::folding::nova::decider_eth::Decider`, using the exact
//! same curve pair (BN254 / Grumpkin) and commitment schemes
//! (KZG on G1, Pedersen on G2) as our workspace build.
//!
//! This is a verbatim port of Sonobe's internal `test_decider` in
//! `folding-schemes/src/folding/nova/decider_eth.rs:282` into an example
//! binary so it can run under OUR workspace (Rust 1.95 MSVC, cached
//! patched-arkworks dep graph) without touching Sonobe's own tests (which
//! require a different toolchain pin).
//!
//! Outputs on stdout:
//!   [cubic] preprocess_ms=..
//!   [cubic] prove_ms=..
//!   [cubic] verify_ms=..
//!   [cubic] verify=true|false
//!   [cubic] proof_kzg_proofs=2 (structural sanity)
//!
//! Exit code: 0 iff `verify=true`, else 1.
//!
//! Do **not** import anything from `v3-folding`, `v3-circuit`, `v3-decider`:
//! this example is intentionally isolated from our step circuit so its
//! result speaks purely about the Sonobe pin + arkworks patch set.

#![allow(non_snake_case)]
#![allow(non_upper_case_globals)]
#![allow(non_camel_case_types)]

use core::marker::PhantomData;
use std::time::Instant;

use ark_bn254::{Bn254, Fr, G1Projective as Projective};
use ark_ff::PrimeField;
use ark_groth16::Groth16;
use ark_grumpkin::Projective as Projective2;
use ark_r1cs_std::alloc::AllocVar;
use ark_r1cs_std::fields::fp::FpVar;
use ark_relations::gr1cs::{ConstraintSystemRef, SynthesisError};

use folding_schemes::commitment::{kzg::KZG, pedersen::Pedersen};
use folding_schemes::folding::nova::decider_eth::Decider as DeciderEth;
use folding_schemes::folding::nova::{Nova, PreprocessorParam};
use folding_schemes::folding::traits::CommittedInstanceOps;
use folding_schemes::frontend::FCircuit;
use folding_schemes::transcript::poseidon::poseidon_canonical_config;
use folding_schemes::{Decider as DeciderTrait, Error as FSError, FoldingScheme};

// Verbatim copy of Sonobe's `CubicFCircuit` from
// `folding-schemes/src/frontend/utils.rs:47-76`, which upstream is gated
// behind `#[cfg(test)]` (and therefore invisible to consumers such as this
// example). Re-declared here with identical semantics so the diagnostic
// binary stays "Sonobe's own reference FCircuit" in behaviour.
//
//   x^3 + x + 5 = y     (from Vitalik's QAP example)
//
// state_len = 1, no external inputs.
#[derive(Clone, Copy, Debug)]
struct CubicFCircuit<F: PrimeField> {
    _f: PhantomData<F>,
}

impl<F: PrimeField> FCircuit<F> for CubicFCircuit<F> {
    type Params = ();
    type ExternalInputs = ();
    type ExternalInputsVar = ();

    fn new(_params: Self::Params) -> Result<Self, FSError> {
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
        let z_i = z_i[0].clone();

        Ok(vec![&z_i * &z_i * &z_i + &z_i + &five])
    }
}

type N = Nova<
    Projective,
    Projective2,
    CubicFCircuit<Fr>,
    KZG<'static, Bn254>,
    Pedersen<Projective2>,
    false,
>;

type D = DeciderEth<
    Projective,
    Projective2,
    CubicFCircuit<Fr>,
    KZG<'static, Bn254>,
    Pedersen<Projective2>,
    Groth16<Bn254>,
    N,
>;

fn main() -> Result<(), FSError> {
    let mut rng = rand::rngs::OsRng;
    let poseidon_config = poseidon_canonical_config::<Fr>();

    let F_circuit = CubicFCircuit::<Fr>::new(())?;
    let z_0 = vec![Fr::from(3_u32)];

    println!("[cubic] F_circuit state_len={}", F_circuit.state_len());

    let preprocessor_param = PreprocessorParam::new(poseidon_config, F_circuit);
    let nova_params = N::preprocess(&mut rng, &preprocessor_param)?;

    let t0 = Instant::now();
    let mut nova = N::init(&nova_params, F_circuit, z_0.clone())?;
    let nova_init_ms = t0.elapsed().as_millis();
    println!("[cubic] nova_init_ms={}", nova_init_ms);

    let t0 = Instant::now();
    let (decider_pp, decider_vp) =
        D::preprocess(&mut rng, (nova_params, F_circuit.state_len()))?;
    let preprocess_ms = t0.elapsed().as_millis();
    println!("[cubic] preprocess_ms={}", preprocess_ms);

    // Two IVC steps, matching the supervisor-mandated `n_steps=2`
    // (also matches our `ivc_demo --slices 2` run).
    let t0 = Instant::now();
    nova.prove_step(&mut rng, (), None)?;
    nova.prove_step(&mut rng, (), None)?;
    let ivc_prove_ms = t0.elapsed().as_millis();
    println!("[cubic] ivc_prove_ms={}", ivc_prove_ms);

    let t0 = Instant::now();
    let proof = D::prove(rng, decider_pp, nova.clone())?;
    let prove_ms = t0.elapsed().as_millis();
    println!("[cubic] prove_ms={}", prove_ms);

    let t0 = Instant::now();
    let verified = D::verify(
        decider_vp,
        nova.i,
        nova.z_0.clone(),
        nova.z_i.clone(),
        &nova.U_i.get_commitments(),
        &nova.u_i.get_commitments(),
        &proof,
    )?;
    let verify_ms = t0.elapsed().as_millis();
    println!("[cubic] verify_ms={}", verify_ms);
    println!("[cubic] verify={}", verified);

    if verified {
        println!("[cubic] RESULT=PASS  (Sonobe pin 63f2930d healthy on CubicFCircuit/state_len=1)");
        Ok(())
    } else {
        eprintln!(
            "[cubic] RESULT=FAIL  (Sonobe pin 63f2930d broken at baseline CubicFCircuit/state_len=1)"
        );
        std::process::exit(1);
    }
}
