# v3-circuit — MNIST MLP → R1CS FCircuit

This crate turns one slice of the MNIST MLP (`784 → 128 → ReLU → 64 → ReLU → 10`,
~109K params, shared with `v2/` but exported independently) into a Sonobe
`FCircuit<F>` trait impl. Phase 2 delivers two `FCircuit` structs — `MnistSlice1Circuit`
(slice 0, `784 → 64`) and `MnistSlice2Circuit` (slice 1, `64 → 10`) — plus an
arithmetic reference that bit-for-bit matches the Python fixed-point forward. The
crate does **not** do folding, Pedersen commitments, or multi-slice chaining;
Phase 3 will take these circuits as its Nova step function.

The crate is pure Rust / arkworks; it has no dependency on EZKL, Halo2, or the
legacy `v2/` pipeline. Weights are imported from JSON produced by
`v3/python/models/mnist_export.py`, and correctness is checked against 100 test
cases produced by `v3/python/models/gen_test_cases.py`.

See also `docs/refactor/v3/03-phase2-mnist-r1cs.md` for the Phase-2 spec.

## Quantization

All arithmetic inside the circuit is performed over integers embedded into the
base field `F = Fr(bn254)`; floating-point is never used. We use a simple
fixed-point representation with a single global scale `s = QUANT_SCALE = 16`
(see `src/constants.rs`). Concretely, a real number `x ∈ ℝ` is represented as

```
x_int = round(x · 2^s) ∈ ℤ ,       |x_int| < 2^{31}
```

The Python exporter (`v3/python/models/mnist_export.py`) and the Rust forward
(`forward_slice_fixed_point` in `src/model.rs`) share the same integer convention
for a `Linear + ReLU` block:

- **Input / activations** live at scale `s`: `a_int = round(a_real · 2^s)`.
- **Weights** `W` live at scale `s`: `W_int = round(W_real · 2^s)` (stored as
  `Vec<Vec<i64>>` in the JSON).
- **Biases** `b` live at scale `2s` — i.e. already pre-shifted to match the scale
  of `W·a` before the shift-down (stored as `Vec<i64>` in the JSON). This is
  why the exporter emits `bias ≈ round(b_real · 2^{2s})`.

A linear step then produces

```
y_2s[i]  =  b[i]  +  Σ_j W[i,j] · a[j]           (all ints, at scale 2s)
y_s[i]   =  y_2s[i] >> s                         (signed arithmetic shift, back to scale s)
```

and is followed by an optional ReLU (see below). The signed right shift is
implemented in the circuit by the `shift_right_gadget` in `src/gadgets.rs`: it
witnesses a quotient `q` and a non-negative remainder `r ∈ [0, 2^s)`, range-checks
both (after splitting `q` into positive and negative parts), and enforces
`q · 2^s + r = y_2s`. This gives a sound signed floor-division without doing a
full bit-decomposition of the accumulator.

### Error report (F2 budget)

The implementation was validated against PyTorch fp32 on 100 MNIST test cases
(`v3/artifacts/models/mnist_mlp_v3_cases.json`). The dequantized slice-2 logits
are compared against the PyTorch fp32 logits using

```
ε = max_i | dequant(logits_int[i]) − logits_fp32[i] |       with dequant(x) = x / 2^s
```

Measured over the 100 cases:

| Metric | Value | Budget |
|---|---|---|
| `max ε` | `0.000703` | `< 0.01` ✅ |
| `mean ε` | `0.000379` | `< 0.01` ✅ |
| `worst_idx` | `76` | — |
| `argmax(logits_int) == argmax(logits_fp32)` | 100 / 100 | 100 / 100 ✅ |

The test `dequantized_logits_epsilon_below_budget` re-computes these numbers from
Rust using the same weights and the same fixed-point forward; the assertion is
that max ε is strictly below `F2_EPS_BUDGET = 0.01` and that predictions agree on
all 100 cases.

## ReLU

A naive `if x > 0 then x else 0` is expensive in R1CS because the branch requires
a full bit decomposition of the sign. Instead we use the standard *positive /
negative decomposition* trick (see `relu_gadget` in `src/gadgets.rs`):

Given the pre-activation `x : FpVar<F>`, the gadget allocates two witnesses
`pos, neg : FpVar<F>` satisfying

```
pos − neg       = x                              (linear constraint)
pos · neg       = 0                              (one multiplicative constraint)
pos ∈ [0, 2^N)                                   (range check via to_bits_le_with_top_bits_zero)
neg ∈ [0, 2^N)                                   (range check via to_bits_le_with_top_bits_zero)
```

with `N = ACTIVATION_BITS = 32`. From these, `ReLU(x) = pos` is returned. The
first two equations force the decomposition to be exact; the range checks make
both parts non-negative integers; the product constraint forces one of `pos, neg`
to be zero — giving the unique decomposition `pos = max(x, 0)`,
`neg = max(−x, 0)`. Total cost: `2 · N + 1` constraints plus the linear combine.

The same pattern underlies the signed shift gadget: the quotient `q` is written
as `q_pos − q_neg` with both parts range-checked to `ACTIVATION_BITS` bits, and
the remainder `r` is range-checked to `SHIFT_REM_BITS = s = 16` bits.

## Slice Boundary Convention

There are two slices in the 2-slice MLP configuration. The slice boundary — i.e.
what exactly is passed between `MnistSlice1Circuit` and `MnistSlice2Circuit` — is
fixed by the following convention (also honored by `mnist_export.py` and the
Phase-3 Nova IVC glue):

- **Slice 1** (`index = 0`, `MnistSlice1Circuit`) consumes the 784-dim MNIST
  input vector and applies `Linear(784 → 128) → ReLU → Linear(128 → 64) → ReLU`.
  Its output is the 64-dim **post-ReLU** activation vector.
- **Slice 2** (`index = 1`, `MnistSlice2Circuit`) consumes exactly that 64-dim
  vector (bit-identical — no rescaling, no re-quantization) and applies
  `Linear(64 → 10)`. Its output is the 10-dim raw logit vector (**no** ReLU,
  **no** softmax, **no** argmax; argmax is a client-side operation). Logits are
  emitted at scale `s` just like the activations.

Both circuits share the same IVC state width `STATE_DIM = 784` so that the
Sonobe `FCircuit<F>` trait is uniformly implementable and Phase 3 can run them
as two steps of the same Nova instance without re-encoding the state. Each slice
uses only the first `input_dim` entries of the 784-wide state vector; the
remaining tail cells are enforced to be `F::zero()` via linear constraints in
`generate_step_constraints`, and the output of each slice is zero-padded back up
to `STATE_DIM` before being returned as `z_{i+1}`. This matches the
`slice_outputs[]` layout emitted by `gen_test_cases.py` (each entry has length
equal to the slice’s natural output dimension, and the Rust test harness re-pads
to `STATE_DIM` when constructing `z_0`).

No external inputs are used: both circuits set `FCircuit::ExternalInputs = ()`
and `ExternalInputsVar = ()`. Weights and biases are baked into the circuit as
field constants at `FCircuit::new(params)` time, so Phase 3 does not need to
thread per-step witnesses into the Nova IVC — it only needs to advance `z_i`
one slice at a time.
