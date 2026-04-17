//! v3-circuit — MNIST slice FCircuits for Sonobe Nova folding.
//!
//! This crate lands in Phase 2 per `docs/refactor/v3/03-phase2-mnist-r1cs.md`.
//! It provides:
//!
//! * Quantized-weight model loading (`model` module) from the JSON schema in
//!   `docs/refactor/v3/99-interfaces.md §1`.
//! * Fixed-point arithmetic gadgets (`gadgets` module): signed ReLU,
//!   arithmetic-shift-right with range-checked remainder, and clamped
//!   bit-decomposition range checks.
//! * Two `FCircuit<F>` implementations (`mnist_slice` module) matching the
//!   canonical 2-slice MNIST MLP split (`784→128→ReLU→128→64→ReLU` and
//!   `64→10`).
//!
//! Phase 2 deliberately does **not** implement folding, IVC, or any hiding
//! commitments — those belong to Phase 3 and Phase 4 respectively.

pub mod constants;
pub mod gadgets;
pub mod model;
pub mod mnist_slice;

pub use constants::{ACTIVATION_BITS, QUANT_SCALE, SHIFT_REM_BITS, STATE_DIM};
pub use mnist_slice::{MnistSlice1Circuit, MnistSlice2Circuit, MnistSliceParams};
pub use model::{LayerEntry, SlicePayload, SlicesDocument};

#[cfg(test)]
mod tests {
    #[test]
    fn state_dim_is_784() {
        assert_eq!(super::STATE_DIM, 784);
    }
}
