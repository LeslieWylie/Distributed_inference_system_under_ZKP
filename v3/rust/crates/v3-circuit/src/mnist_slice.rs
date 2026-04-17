//! MNIST MLP slice circuits (Phase 2).
//!
//! Two `FCircuit<F>` implementations matching the canonical 2-slice split:
//!
//! * [`MnistSlice1Circuit`] — `Linear(784→128) · ReLU · Linear(128→64) · ReLU`,
//!   producing a 64-vector that lives in the first 64 entries of the
//!   `STATE_DIM`-padded state.
//! * [`MnistSlice2Circuit`] — `Linear(64→10)` producing a 10-vector in the
//!   first 10 entries of the padded state.
//!
//! The trait's `state_len()` is always `STATE_DIM = 784`; unused suffix
//! entries are pinned to zero by constant-vs-variable equality constraints.
//! This keeps Phase 3 free to pipe one slice's output state directly into
//! the next slice's input state (they share the same state shape).
//!
//! Each circuit also exposes a native `step_native(z_i)` helper that does the
//! same fixed-point arithmetic as
//! `v3/python/models/gen_test_cases.py::_forward_slice_fixed_point` — the
//! 100-case consistency test asserts bit-for-bit equality.

use std::marker::PhantomData;

use ark_ff::PrimeField;
use ark_r1cs_std::alloc::AllocVar;
use ark_r1cs_std::eq::EqGadget;
use ark_r1cs_std::fields::fp::FpVar;
use ark_r1cs_std::fields::FieldVar;
use ark_relations::gr1cs::{ConstraintSystemRef, SynthesisError};

use folding_schemes::frontend::FCircuit;
use folding_schemes::Error as FSError;

use crate::constants::{ACTIVATION_BITS, QUANT_SCALE, SHIFT_REM_BITS, STATE_DIM};
use crate::gadgets::{i64_to_field, relu_gadget, shift_right_gadget};
use crate::model::{forward_slice_fixed_point, LayerEntry, SlicePayload};

/// FCircuit `Params` type — the quantized slice payload plus its scale.
#[derive(Debug, Clone)]
pub struct MnistSliceParams {
    pub slice: SlicePayload,
    pub scale: usize,
}

impl MnistSliceParams {
    pub fn new(slice: SlicePayload, scale: usize) -> Self {
        Self { slice, scale }
    }
}

/// Generic helper that `Slice1`/`Slice2` share.
fn build_slice_circuit(
    params: &MnistSliceParams,
    expected_input_dim: usize,
    expected_output_dim: usize,
) -> Result<(SlicePayload, usize), FSError> {
    if params.scale != QUANT_SCALE {
        return Err(FSError::Other(format!(
            "scale {} != compile-time QUANT_SCALE {}",
            params.scale, QUANT_SCALE
        )));
    }
    if params.slice.input_dim != expected_input_dim {
        return Err(FSError::Other(format!(
            "expected input_dim {}, got {}",
            expected_input_dim, params.slice.input_dim
        )));
    }
    if params.slice.output_dim != expected_output_dim {
        return Err(FSError::Other(format!(
            "expected output_dim {}, got {}",
            expected_output_dim, params.slice.output_dim
        )));
    }
    Ok((params.slice.clone(), params.scale))
}

/// Native-level slice application. Mirrors `generate_slice_constraints`.
/// Made `pub` so Phase 3's unified step circuit can chain slices.
pub fn step_native_vec<F: PrimeField>(
    slice: &SlicePayload,
    scale: usize,
    z_i: &[F],
    input_dim: usize,
    output_dim: usize,
) -> Vec<F> {
    debug_assert_eq!(z_i.len(), STATE_DIM);
    // Recover signed i64 values from the first `input_dim` entries.
    let mut input_i64: Vec<i64> = Vec::with_capacity(input_dim);
    for v in z_i.iter().take(input_dim) {
        input_i64.push(crate::gadgets::field_to_i128(*v) as i64);
    }
    let out = forward_slice_fixed_point(&input_i64, &slice.layers, scale);
    debug_assert_eq!(out.len(), output_dim);
    // Pad to STATE_DIM with zeros.
    let mut z_out = vec![F::zero(); STATE_DIM];
    for (i, v) in out.iter().enumerate() {
        z_out[i] = i64_to_field::<F>(*v);
    }
    z_out
}

/// R1CS-level slice application on a padded state vector. Made `pub` so
/// Phase 3's unified step circuit can compose slice1 then slice2 within a
/// single Nova step.
pub fn generate_slice_constraints<F: PrimeField>(
    cs: ConstraintSystemRef<F>,
    slice: &SlicePayload,
    scale: usize,
    z_i: Vec<FpVar<F>>,
    input_dim: usize,
    output_dim: usize,
) -> Result<Vec<FpVar<F>>, SynthesisError> {
    // Enforce that state padding beyond `input_dim` is zero. This keeps
    // Phase 3's state-chaining contract tight (a Worker cannot smuggle
    // information in the padded tail).
    let zero_var = FpVar::<F>::zero();
    for v in z_i.iter().skip(input_dim) {
        v.enforce_equal(&zero_var)?;
    }

    let mut cur: Vec<FpVar<F>> = z_i.into_iter().take(input_dim).collect();

    for layer in &slice.layers {
        match layer {
            LayerEntry::Linear { weight, bias } => {
                let out = weight.len();
                let mut next = Vec::with_capacity(out);
                for i in 0..out {
                    // y_2s = sum_j W[i][j] * cur[j] + bias[i], accumulated as
                    // a linear combination over constants × variables.
                    let bias_var = FpVar::new_constant(cs.clone(), i64_to_field::<F>(bias[i]))?;
                    let mut acc = bias_var;
                    for j in 0..weight[i].len() {
                        let w_const =
                            FpVar::new_constant(cs.clone(), i64_to_field::<F>(weight[i][j]))?;
                        acc += &w_const * &cur[j];
                    }
                    let quot = shift_right_gadget(cs.clone(), &acc, scale, ACTIVATION_BITS)?;
                    let _ = SHIFT_REM_BITS; // keep constant referenced
                    next.push(quot);
                }
                cur = next;
            }
            LayerEntry::Relu => {
                let mut next = Vec::with_capacity(cur.len());
                for v in cur.iter() {
                    next.push(relu_gadget(cs.clone(), v)?);
                }
                cur = next;
            }
        }
    }

    debug_assert_eq!(cur.len(), output_dim);

    // Pad back to STATE_DIM with zeros.
    let mut out_state = Vec::with_capacity(STATE_DIM);
    out_state.extend(cur);
    while out_state.len() < STATE_DIM {
        out_state.push(FpVar::<F>::zero());
    }
    Ok(out_state)
}

// =============================================================================
// Slice 1: 784 -> 128 -> ReLU -> 64 -> ReLU
// =============================================================================

#[derive(Debug, Clone)]
pub struct MnistSlice1Circuit<F: PrimeField> {
    slice: SlicePayload,
    scale: usize,
    _f: PhantomData<F>,
}

impl<F: PrimeField> MnistSlice1Circuit<F> {
    pub const INPUT_DIM: usize = 784;
    pub const OUTPUT_DIM: usize = 64;

    /// Native step function (used for witness generation + the 100-case
    /// consistency test).
    pub fn step_native(&self, z_i: &[F]) -> Vec<F> {
        step_native_vec::<F>(
            &self.slice,
            self.scale,
            z_i,
            Self::INPUT_DIM,
            Self::OUTPUT_DIM,
        )
    }
}

impl<F: PrimeField> FCircuit<F> for MnistSlice1Circuit<F> {
    type Params = MnistSliceParams;
    type ExternalInputs = ();
    type ExternalInputsVar = ();

    fn new(params: Self::Params) -> Result<Self, FSError> {
        let (slice, scale) = build_slice_circuit(
            &params,
            Self::INPUT_DIM,
            Self::OUTPUT_DIM,
        )?;
        Ok(Self {
            slice,
            scale,
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
        generate_slice_constraints(
            cs,
            &self.slice,
            self.scale,
            z_i,
            Self::INPUT_DIM,
            Self::OUTPUT_DIM,
        )
    }
}

// =============================================================================
// Slice 2: 64 -> 10
// =============================================================================

#[derive(Debug, Clone)]
pub struct MnistSlice2Circuit<F: PrimeField> {
    slice: SlicePayload,
    scale: usize,
    _f: PhantomData<F>,
}

impl<F: PrimeField> MnistSlice2Circuit<F> {
    pub const INPUT_DIM: usize = 64;
    pub const OUTPUT_DIM: usize = 10;

    pub fn step_native(&self, z_i: &[F]) -> Vec<F> {
        step_native_vec::<F>(
            &self.slice,
            self.scale,
            z_i,
            Self::INPUT_DIM,
            Self::OUTPUT_DIM,
        )
    }
}

impl<F: PrimeField> FCircuit<F> for MnistSlice2Circuit<F> {
    type Params = MnistSliceParams;
    type ExternalInputs = ();
    type ExternalInputsVar = ();

    fn new(params: Self::Params) -> Result<Self, FSError> {
        let (slice, scale) = build_slice_circuit(
            &params,
            Self::INPUT_DIM,
            Self::OUTPUT_DIM,
        )?;
        Ok(Self {
            slice,
            scale,
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
        generate_slice_constraints(
            cs,
            &self.slice,
            self.scale,
            z_i,
            Self::INPUT_DIM,
            Self::OUTPUT_DIM,
        )
    }
}
