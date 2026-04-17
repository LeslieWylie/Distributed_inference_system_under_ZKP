//! Phase 3 Nova IVC driver — single-party orchestrator.
//!
//! Wraps Sonobe's Nova + CycleFold with BN254/Grumpkin + KZG/Pedersen into
//! a driver that:
//!
//! * Sets up Nova `ProverParams`/`VerifierParams` once per (circuit, state_len)
//!   tuple.
//! * Initializes the folding scheme at a given `z_0` (padded
//!   quantized-image vector).
//! * Runs `num_steps` `prove_step` calls, recording per-step timings.
//! * Hands back the folded `Nova` instance plus a raw serialized IVC
//!   proof — the decider is applied separately by `v3-decider`.
//!
//! Phase 5 (distributed folding) will replace this single-process driver
//! with a coordinator-per-worker protocol. Phase 4 will wire hiding
//! commitments into the step circuit but keep this driver API stable.

use std::time::Instant;

use ark_bn254::{Bn254, Fr, G1Projective as Projective};
use ark_grumpkin::Projective as Projective2;
use ark_serialize::CanonicalSerialize;

use folding_schemes::commitment::{kzg::KZG, pedersen::Pedersen};
use folding_schemes::folding::nova::{Nova, PreprocessorParam};
#[allow(unused_imports)]
use folding_schemes::frontend::FCircuit;
use folding_schemes::transcript::poseidon::poseidon_canonical_config;
use folding_schemes::{Error as FSError, FoldingScheme};

use crate::step_circuit::MnistStepCircuit;

/// Type alias for the concrete Nova instance we fold over — `H = false`
/// (non-hiding); Phase 4 flips this when wiring Pedersen-hiding state.
pub type MnistNova = Nova<
    Projective,
    Projective2,
    MnistStepCircuit<Fr>,
    KZG<'static, Bn254>,
    Pedersen<Projective2>,
    false,
>;

/// Preprocessed Nova params for `MnistStepCircuit`. Returned by
/// [`MnistIvcDriver::setup`] so the caller can share a single preprocess
/// cost across multiple runs with the same number of steps / circuit
/// shape.
pub type MnistNovaParams = <MnistNova as FoldingScheme<
    Projective,
    Projective2,
    MnistStepCircuit<Fr>,
>>::PreprocessorParam;

/// Timing breakdown emitted by [`MnistIvcDriver::prove`].
#[derive(Debug, Clone)]
pub struct ProveTimings {
    pub setup_ms: u128,
    pub init_ms: u128,
    pub per_step_ms: Vec<u128>,
    pub prove_total_ms: u128,
    pub ivc_verify_ms: u128,
    pub raw_ivc_proof_size_bytes: usize,
}

/// Single-party Nova IVC driver. Holds a preprocessed Nova param set and
/// knows how to run `num_steps` folding steps of [`MnistStepCircuit`].
pub type NovaProverParam =
    <MnistNova as FoldingScheme<Projective, Projective2, MnistStepCircuit<Fr>>>::ProverParam;
pub type NovaVerifierParam =
    <MnistNova as FoldingScheme<Projective, Projective2, MnistStepCircuit<Fr>>>::VerifierParam;

pub struct MnistIvcDriver {
    circuit: MnistStepCircuit<Fr>,
    num_steps: usize,
    setup_ms: u128,
    nova_params: NovaProverParam,
    nova_vparams: NovaVerifierParam,
}

impl MnistIvcDriver {
    /// Preprocess Nova for the given step circuit. This is the slow
    /// setup phase (minutes on a laptop). The returned driver can run
    /// `prove` repeatedly on different inputs without redoing setup.
    pub fn setup(
        circuit: MnistStepCircuit<Fr>,
        num_steps: usize,
    ) -> Result<Self, FSError> {
        if num_steps < 2 {
            return Err(FSError::Other(format!(
                "MnistIvcDriver requires num_steps >= 2 (Sonobe decider rejects i <= 1); got {}",
                num_steps
            )));
        }

        let poseidon_config = poseidon_canonical_config::<Fr>();
        let mut rng = ark_std::rand::rngs::OsRng;

        let setup_start = Instant::now();
        let preprocess_params = PreprocessorParam::new(poseidon_config, circuit.clone());
        let (pp, vp) = MnistNova::preprocess(&mut rng, &preprocess_params)?;
        let setup_ms = setup_start.elapsed().as_millis();

        Ok(Self {
            circuit,
            num_steps,
            setup_ms,
            nova_params: pp,
            nova_vparams: vp,
        })
    }

    pub fn num_steps(&self) -> usize {
        self.num_steps
    }

    pub fn circuit(&self) -> &MnistStepCircuit<Fr> {
        &self.circuit
    }

    /// Expose the Nova params as the tuple `(pp, vp)` that Sonobe's
    /// decider `preprocess` expects (see
    /// `DeciderEth::preprocess(rng, ((pp, vp), state_len))`).
    pub fn nova_params_tuple(&self) -> (NovaProverParam, NovaVerifierParam) {
        (self.nova_params.clone(), self.nova_vparams.clone())
    }

    pub fn verifier_params(&self) -> NovaVerifierParam {
        self.nova_vparams.clone()
    }

    pub fn setup_ms(&self) -> u128 {
        self.setup_ms
    }

    /// Run `num_steps` folding steps starting from `z_0` and return the
    /// fully-folded Nova instance + timings. Also verifies the raw IVC
    /// proof at the Nova level — this is a cheap sanity check before
    /// the decider runs and is **not** the client-facing verify (which
    /// is the succinct Groth16 decider verify).
    pub fn prove(
        &self,
        z_0: Vec<Fr>,
    ) -> Result<(MnistNova, ProveTimings), FSError> {
        let rng = ark_std::rand::rngs::OsRng;

        let init_start = Instant::now();
        let full_params = (self.nova_params.clone(), self.nova_vparams.clone());
        let mut folding_scheme =
            MnistNova::init(&full_params, self.circuit.clone(), z_0.clone())?;
        let init_ms = init_start.elapsed().as_millis();

        let mut per_step_ms: Vec<u128> = Vec::with_capacity(self.num_steps);
        let prove_start = Instant::now();
        for _i in 0..self.num_steps {
            let step_start = Instant::now();
            folding_scheme.prove_step(rng, (), None)?;
            per_step_ms.push(step_start.elapsed().as_millis());
        }
        let prove_total_ms = prove_start.elapsed().as_millis();

        let ivc_proof = folding_scheme.ivc_proof();
        let mut raw_bytes: Vec<u8> = Vec::new();
        ivc_proof
            .serialize_compressed(&mut raw_bytes)
            .map_err(|e| FSError::Other(format!("serialize ivc_proof: {e:?}")))?;
        let raw_ivc_proof_size_bytes = raw_bytes.len();

        let verify_start = Instant::now();
        MnistNova::verify(self.nova_vparams.clone(), ivc_proof)?;
        let ivc_verify_ms = verify_start.elapsed().as_millis();

        let timings = ProveTimings {
            setup_ms: self.setup_ms,
            init_ms,
            per_step_ms,
            prove_total_ms,
            ivc_verify_ms,
            raw_ivc_proof_size_bytes,
        };

        Ok((folding_scheme, timings))
    }
}
