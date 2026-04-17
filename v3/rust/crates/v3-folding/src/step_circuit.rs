//! Unified MNIST step circuit used by the Phase 3 Nova IVC driver.
//!
//! # Why one packed circuit per step?
//!
//! Sonobe Nova + CycleFold requires the R1CS layout of the step circuit to
//! be **uniform across all folding steps** — `Nova::preprocess` snapshots
//! one R1CS at init time and every subsequent `prove_step` must fold an
//! instance that uses the same matrices. Branching on the native
//! `step_index` inside `generate_step_constraints` would produce different
//! R1CS per step and break folding (the constant coefficients baked into
//! the matrices would diverge).
//!
//! The pragmatic, correctness-first implementation of the brief's
//! "slice_1 → slice_2 → slice_1 → slice_2 …" stress test is therefore:
//!
//! **one Nova step = one full MNIST inference**, i.e. one back-to-back
//! execution of `slice_1` then `slice_2`. `--slices N` is interpreted as
//! `N` Nova folding steps, each step chaining slice_1 and slice_2 through
//! the same padded `STATE_DIM = 784` state vector.
//!
//! The scaling axis (2/4/8 steps) still stress-tests IVC depth, which is
//! what the Phase 3 hard metric (`verify_ms` growth < 2× across 2/4/8)
//! actually measures.
//!
//! # State layout
//!
//! * `z_i` — `STATE_DIM`-wide, same as slice 1's input/output.
//! * At the start of each step: `z_i[:784]` is either the padded input
//!   image (step 0) or the previous inference's 10-logit output padded
//!   with zeros (subsequent steps). `v3-circuit` already enforces
//!   `z_i[input_dim..].is_zero()` inside `generate_slice_constraints`, so
//!   this padding is part of the contract.
//! * Internally: after slice 1 we hold a 64-wide activation vector; we
//!   pad it back to `STATE_DIM` with zeros before feeding slice 2.
//! * At step end: `z_{i+1}[:10]` are the 10 logits; `z_{i+1}[10:]` is
//!   zero — ready to feed back in as the next step's "image".
//!
//! # What this circuit does NOT do
//!
//! * It does not implement per-slice dynamic branching (would break
//!   Nova's uniform-R1CS requirement; see above).
//! * It does not add Pedersen / hiding commitments — that's Phase 4.
//! * It does not take external inputs — weights are frozen into the
//!   circuit at `new()` time via [`MnistStepParams`].

use std::marker::PhantomData;

use ark_ff::PrimeField;
use ark_r1cs_std::fields::fp::FpVar;
use ark_relations::gr1cs::{ConstraintSystemRef, SynthesisError};

use folding_schemes::frontend::FCircuit;
use folding_schemes::Error as FSError;

use v3_circuit::{
    generate_slice_constraints, step_native_vec, LayerEntry, QUANT_SCALE, SlicePayload,
    STATE_DIM,
};

/// Params for [`MnistStepCircuit`]: the two quantized slice payloads plus
/// the shared quantization scale.
///
/// The caller is expected to have already sanity-checked dimensions via
/// `SlicesDocument` — we defensively re-check them in `new` so the
/// `FCircuit::new` return channel surfaces a descriptive error instead of
/// triggering an R1CS-level dimension mismatch deep inside folding.
#[derive(Debug, Clone)]
pub struct MnistStepParams {
    pub slice1: SlicePayload,
    pub slice2: SlicePayload,
    pub scale: usize,
}

impl MnistStepParams {
    pub fn new(slice1: SlicePayload, slice2: SlicePayload, scale: usize) -> Self {
        Self { slice1, slice2, scale }
    }

    fn validate(&self) -> Result<(), FSError> {
        if self.scale != QUANT_SCALE {
            return Err(FSError::Other(format!(
                "scale {} != compile-time QUANT_SCALE {}",
                self.scale, QUANT_SCALE
            )));
        }
        if self.slice1.input_dim != 784 || self.slice1.output_dim != 64 {
            return Err(FSError::Other(format!(
                "slice1 dims ({},{}) != (784,64)",
                self.slice1.input_dim, self.slice1.output_dim
            )));
        }
        if self.slice2.input_dim != 64 || self.slice2.output_dim != 10 {
            return Err(FSError::Other(format!(
                "slice2 dims ({},{}) != (64,10)",
                self.slice2.input_dim, self.slice2.output_dim
            )));
        }
        Ok(())
    }
}

/// Unified two-slice MNIST step circuit.
///
/// One Nova step = `slice1` (`Linear(784→128)·ReLU·Linear(128→64)·ReLU`)
/// followed by `slice2` (`Linear(64→10)`). `state_len` is `STATE_DIM` so
/// the same `Vec<FpVar>` shape threads through every folding step.
#[derive(Debug, Clone)]
pub struct MnistStepCircuit<F: PrimeField> {
    slice1: SlicePayload,
    slice2: SlicePayload,
    scale: usize,
    _f: PhantomData<F>,
}

impl<F: PrimeField> MnistStepCircuit<F> {
    pub const SLICE1_INPUT_DIM: usize = 784;
    pub const SLICE1_OUTPUT_DIM: usize = 64;
    pub const SLICE2_INPUT_DIM: usize = 64;
    pub const SLICE2_OUTPUT_DIM: usize = 10;

    /// Native-level packed step: applies slice 1 then slice 2 on the
    /// first 784 entries of `z_i` and returns a `STATE_DIM`-padded
    /// 10-logit vector. Mirrors the R1CS exactly and is used for Nova
    /// witness generation + the demo's native sanity print.
    pub fn step_native(&self, z_i: &[F]) -> Vec<F> {
        let mid = step_native_vec::<F>(
            &self.slice1,
            self.scale,
            z_i,
            Self::SLICE1_INPUT_DIM,
            Self::SLICE1_OUTPUT_DIM,
        );
        step_native_vec::<F>(
            &self.slice2,
            self.scale,
            &mid,
            Self::SLICE2_INPUT_DIM,
            Self::SLICE2_OUTPUT_DIM,
        )
    }

    /// Count the number of `Linear` layers in both slices. Used in the
    /// CRS-sharing disclosure printed by `ivc_demo` — two circuits with
    /// the same layer structure must share an R1CS shape, hence CRS.
    pub fn layer_signature(&self) -> (usize, usize) {
        fn count_linear(layers: &[LayerEntry]) -> usize {
            layers
                .iter()
                .filter(|l| matches!(l, LayerEntry::Linear { .. }))
                .count()
        }
        (count_linear(&self.slice1.layers), count_linear(&self.slice2.layers))
    }
}

impl<F: PrimeField> FCircuit<F> for MnistStepCircuit<F> {
    type Params = MnistStepParams;
    type ExternalInputs = ();
    type ExternalInputsVar = ();

    fn new(params: Self::Params) -> Result<Self, FSError> {
        params.validate()?;
        Ok(Self {
            slice1: params.slice1,
            slice2: params.slice2,
            scale: params.scale,
            _f: PhantomData,
        })
    }

    fn state_len(&self) -> usize {
        STATE_DIM
    }

    fn generate_step_constraints(
        &self,
        cs: ConstraintSystemRef<F>,
        _i: usize,
        z_i: Vec<FpVar<F>>,
        _external_inputs: Self::ExternalInputsVar,
    ) -> Result<Vec<FpVar<F>>, SynthesisError> {
        // Slice 1: 784 -> 64 (padded to STATE_DIM).
        let mid = generate_slice_constraints(
            cs.clone(),
            &self.slice1,
            self.scale,
            z_i,
            Self::SLICE1_INPUT_DIM,
            Self::SLICE1_OUTPUT_DIM,
        )?;
        // Slice 2: 64 -> 10 (padded to STATE_DIM). `mid` already has zero
        // padding past index 64 (enforced inside slice1's
        // generate_slice_constraints output — padding is built from
        // FpVar::<F>::zero() which is a ConstraintSystem constant), so
        // slice2's "tail = 0" check on z_i[input_dim..] passes trivially.
        generate_slice_constraints(
            cs,
            &self.slice2,
            self.scale,
            mid,
            Self::SLICE2_INPUT_DIM,
            Self::SLICE2_OUTPUT_DIM,
        )
    }
}
