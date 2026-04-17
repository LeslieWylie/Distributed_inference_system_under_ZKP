//! Global v3-circuit constants.
//!
//! These are the canonical numbers referenced from the interface contract
//! (`docs/refactor/v3/99-interfaces.md §Rust API`). Changing them is a
//! cross-Phase event — update the doc and notify Phase 3.

/// Padded state length carried through Sonobe's IVC step state.
///
/// Chosen as `max(784, 64, 10) = 784` so that every slice can be embedded
/// into a single fixed-length `Vec<FpVar<F>>` — shorter slices pad the tail
/// with zeros. Phase 3 uses this to keep `state_len()` consistent across
/// heterogeneous slices.
pub const STATE_DIM: usize = 784;

/// Quantization scale exponent. Values are represented as
/// `x_int = round(x_float * 2^QUANT_SCALE)`.
pub const QUANT_SCALE: usize = 16;

/// Number of low bits retained as the remainder of the `y_2s >> s` shift
/// gadget. Always equals `QUANT_SCALE`.
pub const SHIFT_REM_BITS: usize = QUANT_SCALE;

/// Upper bound on `|quotient|` / `|activation|` expressed as a bit-width.
///
/// 32 bits covers the observed activation magnitudes for the MNIST MLP at
/// `QUANT_SCALE = 16` with a comfortable safety margin (the worst-case
/// `|y_2s|` we ever accumulate is under `2^44` and the shifted quotient
/// under `2^28`). Used by the signed range-check helpers in `gadgets`.
pub const ACTIVATION_BITS: usize = 32;
