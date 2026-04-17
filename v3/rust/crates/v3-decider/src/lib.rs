//! v3-decider — succinct decider wrapper for Phase 3 (Groth16-BN254 via
//! Sonobe `decider_eth`). Phase 4 will extend with Pedersen-hiding hooks
//! but keep this module as the outer decider.

pub mod groth16_decider;

pub use groth16_decider::{
    prove_and_verify, verify_from_bytes, DeciderRunOutput, DeciderTimings, MnistDecider,
    MnistDeciderPP, MnistDeciderProof, MnistDeciderVP,
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn type_aliases_resolve() {
        // Compile-time smoke: the alias tree resolves end-to-end.
        let _ = core::mem::size_of::<MnistDeciderVP>();
    }
}
