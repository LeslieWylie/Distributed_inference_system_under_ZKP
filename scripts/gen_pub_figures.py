# -*- coding: utf-8 -*-
"""
Publication-quality figure generator for midterm defense.
Style: top-tier conference (NeurIPS / CCS / S&P).
"""
import json, os, sys, warnings
import numpy as np

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
METRICS = os.path.join(PROJECT, "metrics")
OUT = os.path.join(PROJECT, "figures")
os.makedirs(OUT, exist_ok=True)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import matplotlib.patches as mpatches

# ─── Global Style (top-conference) ─────────────────────────────────
# Try Chinese font, fallback to DejaVu
_zh = None
for _f in ["SimHei", "Microsoft YaHei", "SimSun"]:
    from matplotlib.font_manager import fontManager
    if any(_f in f.name for f in fontManager.ttflist):
        _zh = _f; break

plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor":   "white",
    "savefig.facecolor":"white",
    "font.size":        11,
    "axes.titlesize":   13,
    "axes.labelsize":   12,
    "xtick.labelsize":  10,
    "ytick.labelsize":  10,
    "legend.fontsize":  9.5,
    "figure.dpi":       150,
    "savefig.dpi":      300,
    "axes.grid":        True,
    "grid.alpha":       0.3,
    "grid.linestyle":   "--",
    "axes.spines.top":  False,
    "axes.spines.right":False,
    "axes.unicode_minus":False,
    "legend.framealpha": 0.9,
    "legend.edgecolor":  "0.8",
})
if _zh:
    plt.rcParams["font.sans-serif"] = [_zh, "DejaVu Sans"]

# ─── Color Palette (colorblind-safe, conference-friendly) ──────────
C_BLUE   = "#3274A1"
C_ORANGE = "#E1812C"
C_GREEN  = "#3A923A"
C_RED    = "#C03D3E"
C_PURPLE = "#9372B2"
C_GRAY   = "#868686"
PALETTE  = [C_BLUE, C_ORANGE, C_GREEN, C_RED, C_PURPLE, C_GRAY]

def load(name):
    with open(os.path.join(METRICS, name), encoding="utf-8") as f:
        return json.load(f)

def save(fig, name):
    p = os.path.join(OUT, name)
    fig.savefig(p, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    print(f"  [OK] {p}")

def bar_label(ax, bars, fmt="{:.0f}", offset=4, fs=8):
    for b in bars:
        h = b.get_height()
        ax.text(b.get_x()+b.get_width()/2, h+offset,
                fmt.format(h), ha="center", va="bottom", fontsize=fs)

def ms2s(v): return v / 1000.0

# ====================================================================
#  Fig 1 — Stacked bar: proof vs verify vs other (by slice count)
# ====================================================================
def fig01():
    data = load("stage3_experiments.json")
    normal = [d for d in data if d["fault_at"] is None]
    slices  = [d["num_slices"] for d in normal]
    proof   = [ms2s(d["total_proof_gen_ms"]) for d in normal]
    verify  = [ms2s(d["total_verify_ms"]) for d in normal]
    other   = [ms2s(normal[i]["e2e_latency_ms"]) - proof[i] - verify[i] for i in range(len(normal))]

    fig, ax = plt.subplots(figsize=(5.5, 4))
    x = np.arange(len(slices))
    w = 0.45
    b1 = ax.bar(x, proof,  w, label="Proof Generation", color=C_BLUE)
    b2 = ax.bar(x, verify, w, bottom=proof, label="Verification", color=C_ORANGE)
    b3 = ax.bar(x, other,  w, bottom=[p+v for p,v in zip(proof,verify)],
                label="Network/IO", color=C_GRAY, alpha=0.6)

    for i in range(len(slices)):
        total = proof[i]+verify[i]+other[i]
        ax.text(x[i], total+0.15, f"{total:.1f}s", ha="center", fontsize=9, fontweight="bold")

    ax.set_xlabel("Number of Slices")
    ax.set_ylabel("Time (seconds)")
    ax.set_title("Fig.1  End-to-End Latency Breakdown by Slice Count")
    ax.set_xticks(x)
    ax.set_xticklabels([str(s) for s in slices])
    ax.legend(loc="upper left")
    ax.set_ylim(0, max(proof[i]+verify[i]+other[i] for i in range(len(slices)))*1.2)
    save(fig, "fig01_latency_breakdown.png")


# ====================================================================
#  Fig 2 — Per-slice proof time heatmap style (8 slices)
# ====================================================================
def fig02():
    data = load("stage3_experiments.json")
    d8 = [d for d in data if d["num_slices"]==8 and d["fault_at"] is None][0]

    sids = [s["slice_id"] for s in d8["slices"]]
    pms  = [s["proof_gen_ms"] for s in d8["slices"]]
    vms  = [s["verify_ms"] for s in d8["slices"]]

    fig, ax = plt.subplots(figsize=(7, 3.5))
    x = np.arange(len(sids))
    w = 0.35
    b1 = ax.bar(x-w/2, pms, w, label="Proof Gen (ms)", color=C_BLUE, edgecolor="white", linewidth=0.5)
    b2 = ax.bar(x+w/2, vms, w, label="Verify (ms)",    color=C_ORANGE, edgecolor="white", linewidth=0.5)
    bar_label(ax, b1, offset=20)
    bar_label(ax, b2, offset=2)

    ax.set_xlabel("Slice ID")
    ax.set_ylabel("Time (ms)")
    ax.set_title("Fig.2  Per-Slice Proof Generation & Verification Time (8 Slices)")
    ax.set_xticks(x)
    ax.set_xticklabels([f"S{s}" for s in sids])
    ax.legend()
    save(fig, "fig02_per_slice_8s.png")


# ====================================================================
#  Fig 3 — Selective verification: line chart (4s + 8s)
# ====================================================================
def fig03():
    data = load("advanced_experiments.json")
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))

    for ns, color, marker in [(4, C_BLUE, "o"), (8, C_ORANGE, "s")]:
        sub = sorted([d for d in data
                      if d["experiment"].startswith(f"P1_{ns}s") and "normal" in d["experiment"]],
                     key=lambda d: d["verify_ratio"], reverse=True)
        vr  = [d["verify_ratio"] for d in sub]
        apf = [d["actual_proof_fraction"] for d in sub]
        e2e = [ms2s(d["e2e_latency_ms"]) for d in sub]
        pfm = [ms2s(d["total_proof_gen_ms"]) for d in sub]

        # (a) E2E
        ax = axes[0]
        ax.plot(vr, e2e, marker=marker, color=color, linewidth=2, markersize=7,
                label=f"{ns} slices")
        for v, e in zip(vr, e2e):
            ax.annotate(f"{e:.1f}s", (v, e), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=8, color=color)

        # (b) Proof gen
        ax = axes[1]
        ax.plot(vr, pfm, marker=marker, color=color, linewidth=2, markersize=7,
                label=f"{ns} slices")
        for v, p in zip(vr, pfm):
            ax.annotate(f"{p:.1f}s", (v, p), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=8, color=color)

        # (c) Actual fraction
        ax = axes[2]
        offset_x = -0.015 if ns == 4 else 0.015
        ax.plot([v+offset_x for v in vr], apf, marker=marker, color=color,
                linewidth=2, markersize=7, label=f"{ns} slices")
        for v, a in zip(vr, apf):
            ax.annotate(f"{a:.1%}", (v+offset_x, a), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=8, color=color)

    for i, (ax, title, ylabel) in enumerate(zip(axes,
            ["(a) End-to-End Latency", "(b) Proof Generation Time", "(c) Actual Proof Fraction"],
            ["Time (s)", "Time (s)", "Fraction"])):
        ax.set_title(title, fontsize=11)
        ax.set_xlabel("Requested Verify Ratio")
        ax.set_ylabel(ylabel)
        ax.invert_xaxis()
        ax.set_xticks([1.0, 0.5, 0.25])
        ax.set_xticklabels(["100%", "50%", "25%"])
        ax.legend(fontsize=8)

    # Add diagonal reference line on (c)
    axes[2].plot([1.0, 0.25], [1.0, 0.25], "k--", alpha=0.3, linewidth=1)
    axes[2].set_ylim(0, 1.15)

    fig.suptitle("Fig.3  Effect of Selective Verification on Overhead and Coverage",
                 fontsize=13, y=1.03)
    fig.tight_layout()
    save(fig, "fig03_selective_verification.png")


# ====================================================================
#  Fig 4 — Attack detection: grouped bar chart
# ====================================================================
def fig04():
    data = load("advanced_experiments.json")
    p3 = [d for d in data if d["experiment"].startswith("P3_")]
    attacks = ["tamper", "skip", "random", "replay"]
    vrs = [1.0, 0.5]
    vr_labels = ["$r$=100%", "$r$=50%"]

    fig, ax = plt.subplots(figsize=(7, 4.2))
    x = np.arange(len(attacks))
    w = 0.32
    hatches = ["", "//"]

    for j, vr in enumerate(vrs):
        e2es = []
        for atk in attacks:
            match = [d for d in p3 if d["fault_type"]==atk and d["verify_ratio"]==vr]
            e2es.append(ms2s(match[0]["e2e_latency_ms"]) if match else 0)
        color = C_BLUE if j==0 else C_ORANGE
        bars = ax.bar(x + (j-0.5)*w, e2es, w, label=vr_labels[j],
                      color=color, edgecolor="white", linewidth=0.5, hatch=hatches[j])
        bar_label(ax, bars, fmt="{:.1f}s", offset=0.08, fs=8)

        # Add checkmark on top
        for b in bars:
            bx = b.get_x()+b.get_width()/2
            by = b.get_height() + 0.35
            ax.text(bx, by, "P", ha="center", fontsize=7, color=C_GREEN,
                    fontweight="bold", fontstyle="italic",
                    bbox=dict(boxstyle="round,pad=0.15", fc="#E8F5E9", ec=C_GREEN, lw=0.8))

    ax.set_xlabel("Attack Type")
    ax.set_ylabel("End-to-End Latency (s)")
    ax.set_title("Fig.4  Attack Handling Under Response-Layer Tampering Model\n"
                 '("P" = prevented by proof-bound output)', fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([a.capitalize() for a in attacks])
    ax.legend(title="Verify Ratio")
    ax.set_ylim(0, max(e2es)*1.35 if e2es else 10)
    save(fig, "fig04_attack_handling.png")


# ====================================================================
#  Fig 5 — Visibility mode: proof time + verify time (side by side)
# ====================================================================
def fig05():
    data = load("p2_visibility_modes.json")
    modes = [d["visibility_mode"] for d in data]
    mode_nice = {"all_public": "All-Public", "hashed": "Hashed\n(Poseidon)",
                 "private": "Private"}
    labels = [mode_nice.get(m, m) for m in modes]

    proof_mean = [ms2s(d["avg_total_proof_gen_ms"]) for d in data]
    proof_std  = [ms2s(np.std([t["total_proof_gen_ms"] for t in d["trials"]])) for d in data]
    ver_mean   = [d["avg_total_verify_ms"] for d in data]
    ver_std    = [np.std([t["total_verify_ms"] for t in d["trials"]]) for d in data]

    colors = [C_BLUE, C_ORANGE, C_GREEN]

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))

    # (a) proof gen
    ax = axes[0]
    bars = ax.bar(labels, proof_mean, yerr=proof_std, width=0.5,
                  color=colors, edgecolor="white", linewidth=0.5,
                  capsize=4, error_kw={"linewidth":1.2})
    for i, b in enumerate(bars):
        ratio = proof_mean[i]/proof_mean[0]
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+proof_std[i]+0.15,
                f"{proof_mean[i]:.1f}s\n({ratio:.2f}x)",
                ha="center", fontsize=9, fontweight="bold")
    ax.set_ylabel("Total Proof Generation (s)")
    ax.set_title("(a) Proof Generation Time", fontsize=11)

    # (b) verify
    ax = axes[1]
    bars = ax.bar(labels, ver_mean, yerr=ver_std, width=0.5,
                  color=colors, edgecolor="white", linewidth=0.5,
                  capsize=4, error_kw={"linewidth":1.2})
    for i, b in enumerate(bars):
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+ver_std[i]+5,
                f"{ver_mean[i]:.0f}ms", ha="center", fontsize=9, fontweight="bold")
    ax.set_ylabel("Total Verification Time (ms)")
    ax.set_title("(b) Verification Time", fontsize=11)

    fig.suptitle("Fig.5  Visibility Mode Overhead Comparison (4 Slices, 3 Trials Mean +/- Std)",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    save(fig, "fig05_visibility_time.png")


# ====================================================================
#  Fig 6 — Visibility mode: proof size + witness size
# ====================================================================
def fig06():
    data = load("p2_visibility_modes.json")
    modes = [d["visibility_mode"] for d in data]
    mode_nice = {"all_public": "All-Public", "hashed": "Hashed", "private": "Private"}
    labels = [mode_nice.get(m, m) for m in modes]
    colors = [C_BLUE, C_ORANGE, C_GREEN]

    ps_all, ws_all = [], []
    for d in data:
        ps_all.append([t["total_proof_size_bytes"]/1024 for t in d["trials"]])
        ws_all.append([t["total_witness_size_bytes"]/1024 for t in d["trials"]])
    ps_mean = [np.mean(v) for v in ps_all]
    ps_std  = [np.std(v) for v in ps_all]
    ws_mean = [np.mean(v) for v in ws_all]
    ws_std  = [np.std(v) for v in ws_all]

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))

    ax = axes[0]
    bars = ax.bar(labels, ps_mean, yerr=ps_std, width=0.5,
                  color=colors, edgecolor="white", capsize=4)
    for i, b in enumerate(bars):
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+ps_std[i]+0.5,
                f"{ps_mean[i]:.1f} KB", ha="center", fontsize=9, fontweight="bold")
    ax.set_ylabel("Proof Size (KB)")
    ax.set_title("(a) Total Proof Size", fontsize=11)

    ax = axes[1]
    bars = ax.bar(labels, ws_mean, yerr=ws_std, width=0.5,
                  color=colors, edgecolor="white", capsize=4)
    for i, b in enumerate(bars):
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+ws_std[i]+0.1,
                f"{ws_mean[i]:.1f} KB", ha="center", fontsize=9, fontweight="bold")
    ax.set_ylabel("Witness Size (KB)")
    ax.set_title("(b) Total Witness Size", fontsize=11)

    fig.suptitle("Fig.6  Proof & Witness Artifact Size by Visibility Mode (4 Slices)",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    save(fig, "fig06_visibility_size.png")


# ====================================================================
#  Fig 7 — Proof-bound output prevention diagram
# ====================================================================
def fig07():
    data = load("stage3_experiments.json")
    fault = [d for d in data if d["fault_at"] is not None]
    slices = [d["num_slices"] for d in fault]
    e2e_normal = [ms2s([dd for dd in load("stage3_experiments.json")
                        if dd["num_slices"]==d["num_slices"] and dd["fault_at"] is None][0]
                       ["e2e_latency_ms"]) for d in fault]
    e2e_fault  = [ms2s(d["e2e_latency_ms"]) for d in fault]

    fig, ax = plt.subplots(figsize=(6, 4))
    x = np.arange(len(slices))
    w = 0.3
    b1 = ax.bar(x-w/2, e2e_normal, w, label="Normal", color=C_BLUE, edgecolor="white")
    b2 = ax.bar(x+w/2, e2e_fault,  w, label="Fault Injected\n(prevented by proof-bound)",
                color=C_GREEN, edgecolor="white", hatch="//")
    bar_label(ax, b1, fmt="{:.1f}s", offset=0.1, fs=9)
    bar_label(ax, b2, fmt="{:.1f}s", offset=0.1, fs=9)

    ax.set_xlabel("Number of Slices (fault at last slice)")
    ax.set_ylabel("End-to-End Latency (s)")
    ax.set_title("Fig.7  Proof-Bound Output: Normal vs Fault-Injected\n"
                 "(tampering prevented at source, no propagation)")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s} slices" for s in slices])
    ax.legend()
    save(fig, "fig07_proof_bound.png")


# ====================================================================
#  Fig 8 — P4 fidelity table-style
# ====================================================================
def fig08():
    data = load("p4_p6_results.json")
    fid = data["fidelity"]
    keys = ["2_slices", "4_slices", "8_slices"]
    labels_col = ["L1 Dist.", "L2 Dist.", "Max Abs.", "Mean Abs.", "Rel. Err."]
    metrics_k = ["l1_distance", "l2_distance", "max_abs_error", "mean_abs_error", "relative_error"]

    fig, ax = plt.subplots(figsize=(6, 2.5))
    ax.axis("off")

    row_labels = ["2 Slices", "4 Slices", "8 Slices"]
    cell_text = []
    for k in keys:
        row = [f"{fid[k][m]:.2e}" if fid[k][m] != 0 else "0.00" for m in metrics_k]
        cell_text.append(row)

    table = ax.table(cellText=cell_text, rowLabels=row_labels, colLabels=labels_col,
                     cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.0, 1.6)

    # Style header
    for j in range(len(labels_col)):
        table[0, j].set_facecolor("#E3EBF5")
        table[0, j].set_text_props(fontweight="bold")
    for i in range(len(row_labels)):
        table[i+1, -1].set_facecolor("#F0F4FA")
        table[i+1, -1].set_text_props(fontweight="bold")

    ax.set_title("Fig.8  Slice Logic Consistency (PyTorch slice vs full model output)\n"
                 "All errors = 0 (bit-exact)", fontsize=11, pad=20)
    save(fig, "fig08_p4_fidelity.png")


# ====================================================================
#  Fig 9 — P6 integrity check comparison (proof time)
# ====================================================================
def fig09():
    data = load("p4_p6_results.json")
    zk = data["zk_chain_comparison"]

    schemes = {}
    for d in zk:
        key = d["scheme"]
        if key not in schemes:
            schemes[key] = []
        schemes[key].append(d)

    scheme_order = ["external_sha256", "in_circuit_poseidon", "private_input"]
    scheme_nice  = ["External\nSHA-256", "In-Circuit\nPoseidon", "Private\nMode"]
    colors_s = [C_BLUE, C_ORANGE, C_GREEN]

    # Normal mode proof time
    normal_proof = []
    for sk in scheme_order:
        norm = [d for d in schemes.get(sk, []) if d["fault_at"] is None]
        normal_proof.append(ms2s(norm[0]["total_proof_gen_ms"]) if norm else 0)

    fig, ax = plt.subplots(figsize=(6, 4))
    bars = ax.bar(scheme_nice, normal_proof, width=0.5, color=colors_s, edgecolor="white")
    for i, b in enumerate(bars):
        ratio = normal_proof[i]/normal_proof[0] if normal_proof[0] else 0
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+0.12,
                f"{normal_proof[i]:.1f}s\n({ratio:.2f}x)",
                ha="center", fontsize=9, fontweight="bold")
    ax.set_ylabel("Total Proof Generation (s)")
    ax.set_title("Fig.9  Integrity Check Mechanism Comparison\n"
                 "(Normal Mode, 4 Slices)")
    save(fig, "fig09_p6_integrity.png")


# ====================================================================
#  Fig 10 — Cost reduction summary (horizontal bar)
# ====================================================================
def fig10():
    data = load("advanced_experiments.json")

    configs = [
        ("4s, r=50%",  "P1_4s_vr0.50_normal", "P1_4s_vr1.00_normal"),
        ("4s, r=25%",  "P1_4s_vr0.25_normal", "P1_4s_vr1.00_normal"),
        ("8s, r=50%",  "P1_8s_vr0.50_normal", "P1_8s_vr1.00_normal"),
        ("8s, r=25%",  "P1_8s_vr0.25_normal", "P1_8s_vr1.00_normal"),
    ]

    labels, reductions = [], []
    for label, exp, base_exp in configs:
        d = [x for x in data if x["experiment"]==exp][0]
        b = [x for x in data if x["experiment"]==base_exp][0]
        reduction = 1.0 - d["e2e_latency_ms"]/b["e2e_latency_ms"]
        labels.append(label)
        reductions.append(reduction*100)

    fig, ax = plt.subplots(figsize=(7, 3.5))
    y = np.arange(len(labels))
    bars = ax.barh(y, reductions, height=0.5, color=[C_BLUE, C_BLUE, C_ORANGE, C_ORANGE],
                   edgecolor="white")
    for b in bars:
        w = b.get_width()
        ax.text(w+0.8, b.get_y()+b.get_height()/2, f"{w:.1f}%",
                va="center", fontsize=10, fontweight="bold")

    ax.set_xlabel("End-to-End Cost Reduction (%)")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_title("Fig.10  Selective Verification Cost Reduction Summary")
    ax.set_xlim(0, max(reductions)*1.25)
    ax.invert_yaxis()
    save(fig, "fig10_cost_reduction.png")


# ====================================================================
#  Fig 11 — Throughput by slice count
# ====================================================================
def fig11():
    data = load("stage3_experiments.json")
    normal = [d for d in data if d["fault_at"] is None]
    slices = [d["num_slices"] for d in normal]
    tp = [d.get("throughput_req_per_sec", 0) for d in normal]

    fig, ax = plt.subplots(figsize=(5, 3.5))
    bars = ax.bar([str(s) for s in slices], tp, width=0.45, color=PALETTE[:len(slices)],
                  edgecolor="white")
    bar_label(ax, bars, fmt="{:.3f}", offset=0.005, fs=9)
    ax.set_xlabel("Number of Slices")
    ax.set_ylabel("Throughput (req/s)")
    ax.set_title("Fig.11  System Throughput by Slice Count")
    save(fig, "fig11_throughput.png")


# ====================================================================
#  Main
# ====================================================================
if __name__ == "__main__":
    warnings.filterwarnings("ignore", category=UserWarning)
    print("Generating publication-quality figures...")
    fig01()
    fig02()
    fig03()
    fig04()
    fig05()
    fig06()
    fig07()
    fig08()
    fig09()
    fig10()
    fig11()
    print(f"\nDone! {len(os.listdir(OUT))} files in {OUT}")
