//! Fixed-point arithmetic gadgets over `ark_relations::gr1cs`.
//!
//! Three primitives, all intended to be safe against the standard malicious
//! prover tricks (field-wraparound, negative-remainder cheating):
//!
//! * [`i64_to_field`] — lifts a signed `i64` to `F` using the canonical
//!   negate-positive-representation trick.
//! * [`enforce_unsigned_in_range`] — asserts `0 ≤ v < 2^n_bits` via bit
//!   decomposition (`FpVar::to_bits_le_with_top_bits_zero`).
//! * [`relu_gadget`] — `y = ReLU(x)` via `x = pos − neg`, `pos · neg = 0`,
//!   `pos, neg ∈ [0, 2^ACTIVATION_BITS)`.
//! * [`shift_right_gadget`] — `q = ⌊y / 2^shift⌋` via `y = q·2^shift + r`,
//!   `r ∈ [0, 2^shift)`, `q = q_pos − q_neg`, both range-checked.
//!
//! The gadgets use `FpVar::to_bits_le_with_top_bits_zero` from
//! `ark-r1cs-std` for range-checks — this avoids hand-rolled bit
//! decomposition and keeps the constraint count predictable (`n_bits + 1`
//! constraints per range check).

use ark_ff::PrimeField;
use ark_r1cs_std::alloc::AllocVar;
use ark_r1cs_std::eq::EqGadget;
use ark_r1cs_std::fields::fp::FpVar;
use ark_r1cs_std::fields::FieldVar;
use ark_r1cs_std::GR1CSVar;
use ark_relations::gr1cs::{ConstraintSystemRef, SynthesisError};

use crate::constants::ACTIVATION_BITS;

/// Lift a signed `i64` to a prime-field element. Negative values map to the
/// additive inverse of `|v|`.
pub fn i64_to_field<F: PrimeField>(v: i64) -> F {
    if v >= 0 {
        F::from(v as u64)
    } else {
        -F::from((-(v as i128)) as u64)
    }
}

/// Lift a signed `i128` to a prime-field element (used for pre-shift
/// accumulators that don't quite fit in `i64`).
pub fn i128_to_field<F: PrimeField>(v: i128) -> F {
    if v >= 0 {
        let lo = v as u128;
        // Split into two u64 halves.
        let lower = lo as u64;
        let upper = (lo >> 64) as u64;
        let mut out = F::from(upper);
        // 2^64
        let two_64 = F::from(2u64).pow([64]);
        out *= two_64;
        out + F::from(lower)
    } else {
        -i128_to_field::<F>(-v)
    }
}

/// Convert a field element that we know lives in `[0, 2^127)` (signed) back
/// to `i128`. Only used for witness debugging.
pub fn field_to_i128<F: PrimeField>(v: F) -> i128 {
    // If v < p/2 treat as nonneg; else as nonpos.
    let half = F::MODULUS_MINUS_ONE_DIV_TWO;
    let bi = v.into_bigint();
    if bi <= half {
        // extract low 128 bits
        let limbs = bi.as_ref();
        let lo0 = limbs.first().copied().unwrap_or(0);
        let lo1 = limbs.get(1).copied().unwrap_or(0);
        let lo = (lo0 as u128) | ((lo1 as u128) << 64);
        lo as i128
    } else {
        let neg = (-v).into_bigint();
        let limbs = neg.as_ref();
        let lo0 = limbs.first().copied().unwrap_or(0);
        let lo1 = limbs.get(1).copied().unwrap_or(0);
        let lo = (lo0 as u128) | ((lo1 as u128) << 64);
        -(lo as i128)
    }
}

/// Enforce that the (value-known-only-to-prover) `FpVar` lies in
/// `[0, 2^n_bits)`. Uses bit decomposition with explicit top-bit zeroing.
pub fn enforce_unsigned_in_range<F: PrimeField>(
    v: &FpVar<F>,
    n_bits: usize,
) -> Result<(), SynthesisError> {
    // The helper decomposes `v` into `n_bits` bits and enforces that the
    // rest of the field element is zero. That is exactly a range-check to
    // `[0, 2^n_bits)`.
    let _ = v.to_bits_le_with_top_bits_zero(n_bits)?;
    Ok(())
}

/// Compute `max(x, 0)` while enforcing:
/// * `x = pos − neg`,
/// * `pos · neg = 0`,
/// * `pos, neg ∈ [0, 2^ACTIVATION_BITS)`.
///
/// Returns `pos` as the ReLU output.
pub fn relu_gadget<F: PrimeField>(
    cs: ConstraintSystemRef<F>,
    x: &FpVar<F>,
) -> Result<FpVar<F>, SynthesisError> {
    // Witness values — derive from `x.value()` if available.
    let (pos_val, neg_val) = match x.value() {
        Ok(v) => {
            let as_i = field_to_i128(v);
            if as_i >= 0 {
                (i128_to_field::<F>(as_i), F::zero())
            } else {
                (F::zero(), i128_to_field::<F>(-as_i))
            }
        }
        Err(_) => (F::zero(), F::zero()),
    };
    let pos = FpVar::new_witness(cs.clone(), || Ok(pos_val))?;
    let neg = FpVar::new_witness(cs.clone(), || Ok(neg_val))?;

    // pos - neg = x
    (&pos - &neg).enforce_equal(x)?;
    // pos * neg = 0  →  at least one is zero.
    (&pos * &neg).enforce_equal(&FpVar::<F>::zero())?;
    // range-check both non-negatives.
    enforce_unsigned_in_range(&pos, ACTIVATION_BITS)?;
    enforce_unsigned_in_range(&neg, ACTIVATION_BITS)?;

    Ok(pos)
}

/// Arithmetic shift right: returns `q = ⌊y / 2^shift⌋` (Python's `>>` on
/// signed ints, i.e. floor-division) and enforces
///
/// ```text
///   y = q * 2^shift + r,      0 <= r < 2^shift,
///   q = q_pos - q_neg,        q_pos, q_neg in [0, 2^quot_bits).
/// ```
///
/// The remainder must be non-negative, which uniquely determines `q` given
/// `y` — so a malicious prover cannot pick a different `(q, r)` pair.
pub fn shift_right_gadget<F: PrimeField>(
    cs: ConstraintSystemRef<F>,
    y: &FpVar<F>,
    shift: usize,
    quot_bits: usize,
) -> Result<FpVar<F>, SynthesisError> {
    let (q_pos_val, q_neg_val, r_val) = match y.value() {
        Ok(v) => {
            let y_i = field_to_i128(v);
            let divisor: i128 = 1i128 << shift;
            let q = y_i.div_euclid(divisor); // floor division (remainder is non-negative)
            let r = y_i.rem_euclid(divisor); // 0 <= r < 2^shift
            let q_pos = q.max(0);
            let q_neg = (-q).max(0);
            (
                i128_to_field::<F>(q_pos),
                i128_to_field::<F>(q_neg),
                F::from(r as u64),
            )
        }
        Err(_) => (F::zero(), F::zero(), F::zero()),
    };

    let q_pos = FpVar::new_witness(cs.clone(), || Ok(q_pos_val))?;
    let q_neg = FpVar::new_witness(cs.clone(), || Ok(q_neg_val))?;
    let r = FpVar::new_witness(cs.clone(), || Ok(r_val))?;

    // Range-checks.
    enforce_unsigned_in_range(&q_pos, quot_bits)?;
    enforce_unsigned_in_range(&q_neg, quot_bits)?;
    enforce_unsigned_in_range(&r, shift)?;

    let q = &q_pos - &q_neg;
    // y == q * 2^shift + r
    let two_pow = FpVar::<F>::new_constant(cs.clone(), F::from(2u64).pow([shift as u64]))?;
    let reconstructed = &q * &two_pow + &r;
    reconstructed.enforce_equal(y)?;
    Ok(q)
}
