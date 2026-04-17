//! Phase 3 Groth16 decider wrapper (Sonobe `decider_eth`).
//!
//! # Why Groth16 and not Halo2
//!
//! The Phase 3 brief originally called for a Halo2-KZG decider. The
//! Sonobe commit that Phase 1 pinned (`63f2930d`) ships no Halo2 decider
//! — `folding-schemes/Cargo.toml` has zero halo2 dependencies, and the
//! only `halo2` mentions in the tree are unrelated doc comments.
//! Supervisor decision 2026-04-17 selected Sonobe's official
//! `folding_schemes::folding::nova::decider_eth::Decider<…, Groth16<Bn254>,…>`
//! instead. That module is a straight ark-snark generic — we instantiate
//! it with `ark_groth16::Groth16<Bn254>` and reuse the Nova params
//! produced by [`v3_folding::MnistIvcDriver`].
//!
//! The wrapper is a **thin type alias + two helpers**; no custom
//! cryptography, per brief §9(10). See `examples/full_flow.rs` in the
//! Sonobe checkout for the canonical usage pattern we follow.
//!
//! # Trusted-setup disclosure
//!
//! Groth16 requires a circuit-specific CRS. `D::preprocess` calls
//! `S::circuit_specific_setup(DeciderEthCircuit::dummy(...), &mut rng)`
//! once per (nova_params, state_len) pair. Different slice counts
//! (`--slices 2/4/8`) all use the **same** `MnistStepCircuit` shape and
//! the same `state_len = STATE_DIM = 784`, but Nova preprocessing
//! depends on the step circuit's R1CS which is **shape-identical**
//! across slice counts for our implementation (one step = `slice_1 ∘
//! slice_2`). See `ivc_demo.rs`'s CRS sharing disclosure for the empirical
//! side — we verify equal verifying-key bytes across the three slice
//! counts to make this concrete.

use std::time::Instant;

use ark_bn254::{Bn254, Fr, G1Projective as Projective};
use ark_groth16::Groth16;
use ark_grumpkin::Projective as Projective2;
use ark_serialize::{CanonicalDeserialize, CanonicalSerialize};

use folding_schemes::commitment::{kzg::KZG, pedersen::Pedersen};
use folding_schemes::folding::nova::decider_eth::{Decider as DeciderEth, Proof as DeciderProof};
use folding_schemes::folding::traits::CommittedInstanceOps;
use folding_schemes::frontend::FCircuit;
use folding_schemes::{Decider, Error as FSError};

use v3_folding::{MnistIvcDriver, MnistNova, MnistStepCircuit};

/// Concrete decider type alias: Nova + CycleFold over Bn254/Grumpkin,
/// with KZG on G1 and Pedersen on G2, wrapped by Groth16 over Bn254.
pub type MnistDecider = DeciderEth<
    Projective,
    Projective2,
    MnistStepCircuit<Fr>,
    KZG<'static, Bn254>,
    Pedersen<Projective2>,
    Groth16<Bn254>,
    MnistNova,
>;

/// Prover key for [`MnistDecider`] (Groth16 PK + KZG PK).
pub type MnistDeciderPP =
    <MnistDecider as Decider<Projective, Projective2, MnistStepCircuit<Fr>, MnistNova>>::ProverParam;

/// Verifier key for [`MnistDecider`] (pp_hash + Groth16 VK + KZG VK).
pub type MnistDeciderVP = <MnistDecider as Decider<
    Projective,
    Projective2,
    MnistStepCircuit<Fr>,
    MnistNova,
>>::VerifierParam;

/// Succinct decider proof (Groth16 proof + KZG openings + last-fold cmT/r).
pub type MnistDeciderProof = DeciderProof<Projective, KZG<'static, Bn254>, Groth16<Bn254>>;

/// Timings around decider setup / prove / verify.
#[derive(Debug, Clone)]
pub struct DeciderTimings {
    pub setup_ms: u128,
    pub prove_ms: u128,
    pub verify_ms: u128,
    pub proof_size_bytes: usize,
}

/// One-shot wrapper: given a driver and a fully-folded Nova instance,
/// run Groth16 decider setup + prove + verify. Returns the serialized
/// proof bytes plus timings. The `snark_vp_bytes` is the serialized
/// Groth16 verifying key, exposed so callers (e.g. the demo) can
/// empirically show the verifying key is the same across different
/// `num_steps` runs — that's the CRS-sharing evidence the supervisor
/// asked for.
pub struct DeciderRunOutput {
    pub proof_bytes: Vec<u8>,
    pub vp: MnistDeciderVP,
    pub snark_vp_bytes: Vec<u8>,
    pub timings: DeciderTimings,
}

pub fn prove_and_verify(
    driver: &MnistIvcDriver,
    nova: MnistNova,
) -> Result<DeciderRunOutput, FSError> {
    let mut rng = ark_std::rand::rngs::OsRng;
    let nova_tuple = driver.nova_params_tuple();
    let state_len = driver.circuit().state_len();

    let setup_start = Instant::now();
    let (pp, vp): (MnistDeciderPP, MnistDeciderVP) =
        MnistDecider::preprocess(&mut rng, (nova_tuple, state_len))?;
    let setup_ms = setup_start.elapsed().as_millis();

    // Serialize the Groth16 VK so we can compare across runs.
    let mut snark_vp_bytes = Vec::new();
    vp.snark_vp
        .serialize_compressed(&mut snark_vp_bytes)
        .map_err(|e| FSError::Other(format!("serialize snark_vp: {e:?}")))?;

    // The decider needs the running + incoming committed-instance
    // commitments, not the full instances (Sonobe's decider_eth API).
    let running = nova.U_i.get_commitments();
    let incoming = nova.u_i.get_commitments();
    let i_fr = nova.i;
    let z_0 = nova.z_0.clone();
    let z_i = nova.z_i.clone();

    let prove_start = Instant::now();
    let proof: MnistDeciderProof = MnistDecider::prove(rng, pp, nova)?;
    let prove_ms = prove_start.elapsed().as_millis();

    let mut proof_bytes = Vec::new();
    proof
        .serialize_compressed(&mut proof_bytes)
        .map_err(|e| FSError::Other(format!("serialize decider proof: {e:?}")))?;
    let proof_size_bytes = proof_bytes.len();

    let verify_start = Instant::now();
    let ok = MnistDecider::verify(vp.clone(), i_fr, z_0, z_i, &running, &incoming, &proof)?;
    let verify_ms = verify_start.elapsed().as_millis();
    if !ok {
        return Err(FSError::Other("decider verify returned false".into()));
    }

    Ok(DeciderRunOutput {
        proof_bytes,
        vp,
        snark_vp_bytes,
        timings: DeciderTimings {
            setup_ms,
            prove_ms,
            verify_ms,
            proof_size_bytes,
        },
    })
}

/// Deserialize-and-verify path — loads a serialized decider proof + VK
/// and re-runs verify against the claimed Nova public IO. Used by the
/// Python bridge's `rust_verify_ivc` entry point.
pub fn verify_from_bytes(
    vp_bytes: &[u8],
    proof_bytes: &[u8],
    i_fr: Fr,
    z_0: Vec<Fr>,
    z_i: Vec<Fr>,
    running: Vec<Projective>,
    incoming: Vec<Projective>,
) -> Result<(bool, u128), FSError> {
    let vp: MnistDeciderVP = MnistDeciderVP::deserialize_compressed(vp_bytes)
        .map_err(|e| FSError::Other(format!("deserialize vp: {e:?}")))?;
    let proof: MnistDeciderProof = MnistDeciderProof::deserialize_compressed(proof_bytes)
        .map_err(|e| FSError::Other(format!("deserialize proof: {e:?}")))?;
    let t0 = Instant::now();
    let ok = MnistDecider::verify(vp, i_fr, z_0, z_i, &running, &incoming, &proof)?;
    Ok((ok, t0.elapsed().as_millis()))
}
