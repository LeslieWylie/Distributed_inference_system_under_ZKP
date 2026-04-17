//! v3-folding — Nova / folding-scheme glue crate for V3.
//!
//! Phase 3 lands the real multi-slice IVC driver and the unified MNIST step
//! circuit. See [`ivc_driver`] and [`step_circuit`].

pub mod ivc_driver;
pub mod step_circuit;

pub use ivc_driver::{MnistIvcDriver, MnistNova, NovaProverParam, NovaVerifierParam, ProveTimings};
pub use step_circuit::{MnistStepCircuit, MnistStepParams};

#[cfg(test)]
mod tests {
    // Smoke test: module path exports compile. Heavy end-to-end tests
    // live in `examples/ivc_demo.rs` (release-only) because Nova
    // preprocessing is minutes-slow in debug.
    #[test]
    fn exports_wired() {
        let _ = core::mem::size_of::<super::MnistStepParams>();
    }
}
