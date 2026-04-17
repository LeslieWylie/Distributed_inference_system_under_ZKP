//! Quantized MNIST MLP loader.
//!
//! Parses the JSON emitted by `v3/python/models/mnist_export.py`. The schema
//! is the one in `docs/refactor/v3/99-interfaces.md §1`. Numbers stay as
//! `i64` in memory — the circuit lifts them to the scalar field only at
//! constraint-generation time.

use std::fs::File;
use std::io::BufReader;
use std::path::Path;

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};

/// A linear layer or an elementwise ReLU, matching the interface contract.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum LayerEntry {
    /// Fully connected layer. ``weight`` is ``(out_dim, in_dim)`` at scale
    /// ``s``; ``bias`` is ``out_dim`` at scale ``2s`` (pre-shift).
    Linear {
        weight: Vec<Vec<i64>>,
        bias: Vec<i64>,
    },
    /// Elementwise `max(x, 0)` on the current activations.
    Relu,
}

impl LayerEntry {
    /// For a linear layer, returns `(output_dim, input_dim)`.
    pub fn linear_dims(&self) -> Option<(usize, usize)> {
        match self {
            LayerEntry::Linear { weight, .. } => {
                let out = weight.len();
                let inp = weight.first().map(|r| r.len()).unwrap_or(0);
                Some((out, inp))
            }
            LayerEntry::Relu => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SlicePayload {
    pub index: usize,
    pub input_dim: usize,
    pub output_dim: usize,
    pub layers: Vec<LayerEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SlicesDocument {
    #[serde(default)]
    pub version: String,
    pub model_id: String,
    pub scale: usize,
    pub num_slices: usize,
    pub slices: Vec<SlicePayload>,
}

impl SlicesDocument {
    /// Parse a slices document from disk.
    pub fn load_from_path<P: AsRef<Path>>(path: P) -> Result<Self> {
        let file = File::open(&path)
            .with_context(|| format!("opening slices json at {}", path.as_ref().display()))?;
        let reader = BufReader::new(file);
        let doc: SlicesDocument = serde_json::from_reader(reader)
            .with_context(|| format!("parsing slices json at {}", path.as_ref().display()))?;
        doc.sanity_check()?;
        Ok(doc)
    }

    /// Parse a slices document from a JSON string (useful for embedding in
    /// tests / `include_str!`).
    pub fn load_from_str(text: &str) -> Result<Self> {
        let doc: SlicesDocument = serde_json::from_str(text)?;
        doc.sanity_check()?;
        Ok(doc)
    }

    fn sanity_check(&self) -> Result<()> {
        if self.num_slices != self.slices.len() {
            return Err(anyhow!(
                "num_slices ({}) disagrees with slices.len() ({})",
                self.num_slices,
                self.slices.len()
            ));
        }
        for (i, slice) in self.slices.iter().enumerate() {
            if slice.index != i {
                return Err(anyhow!("slice[{}].index = {}", i, slice.index));
            }
            // Layer dims must chain: input_dim -> linear(out,in)... -> output_dim.
            let mut cur = slice.input_dim;
            for (li, layer) in slice.layers.iter().enumerate() {
                match layer {
                    LayerEntry::Linear { weight, bias } => {
                        let (out, inp) = (weight.len(), weight.first().map(|r| r.len()).unwrap_or(0));
                        if inp != cur {
                            return Err(anyhow!(
                                "slice {} layer {}: linear expected in={}, got in={}",
                                i, li, cur, inp
                            ));
                        }
                        if bias.len() != out {
                            return Err(anyhow!(
                                "slice {} layer {}: bias len {} != out {}",
                                i, li, bias.len(), out
                            ));
                        }
                        cur = out;
                    }
                    LayerEntry::Relu => { /* shape-preserving */ }
                }
            }
            if cur != slice.output_dim {
                return Err(anyhow!(
                    "slice {} final dim {} != output_dim {}",
                    i, cur, slice.output_dim
                ));
            }
        }
        Ok(())
    }
}

/// Run a slice in fixed-point i64 arithmetic. Mirrors
/// `v3/python/models/gen_test_cases.py::_forward_slice_fixed_point`
/// bit-for-bit (arithmetic/floor-division right-shift on signed i64).
pub fn forward_slice_fixed_point(input: &[i64], layers: &[LayerEntry], scale: usize) -> Vec<i64> {
    let mut cur = input.to_vec();
    for layer in layers {
        match layer {
            LayerEntry::Linear { weight, bias } => {
                let out = weight.len();
                let mut next = vec![0i64; out];
                for i in 0..out {
                    let row = &weight[i];
                    // y_2s = sum_j w[i][j] * x[j] + bias[i]
                    let mut acc: i128 = bias[i] as i128;
                    for j in 0..row.len() {
                        acc += (row[j] as i128) * (cur[j] as i128);
                    }
                    // Floor-division by 2^scale (arithmetic shift right for signed).
                    let shifted = acc >> scale;
                    next[i] = shifted as i64;
                }
                cur = next;
            }
            LayerEntry::Relu => {
                for v in cur.iter_mut() {
                    if *v < 0 {
                        *v = 0;
                    }
                }
            }
        }
    }
    cur
}
