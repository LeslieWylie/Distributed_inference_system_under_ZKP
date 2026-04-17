//! v3-folding — Nova / folding-scheme glue crate for V3.
//!
//! Phase 1 only verifies the Sonobe toolchain via the `nova_hello`
//! example; Phase 3 will host the real multi-slice IVC driver.

/// Phase 1 placeholder; Phase 3 will replace this with real APIs.
pub fn dummy() {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dummy_is_a_noop() {
        dummy();
    }
}
