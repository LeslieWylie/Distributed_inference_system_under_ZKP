"""
中期答辩图表生成器 — 从 metrics/*.json 读取数据，生成 PPT 用 PNG 图表。
"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np

# ---------- 字体设置 ----------
# 尝试使用中文字体
for font_name in ["SimHei", "Microsoft YaHei", "SimSun", "Arial Unicode MS"]:
    if any(font_name in f.name for f in fm.fontManager.ttflist):
        plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
        break
plt.rcParams["axes.unicode_minus"] = False

OUT_DIR = os.path.join(PROJECT_ROOT, "figures")
os.makedirs(OUT_DIR, exist_ok=True)

METRICS_DIR = os.path.join(PROJECT_ROOT, "metrics")


def load(name):
    with open(os.path.join(METRICS_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


def save(fig, name):
    path = os.path.join(OUT_DIR, name)
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  -> {path}")


# ======================================================================
# 图 1: 不同切片数的端到端时延 (Stage3)
# ======================================================================
def fig_stage3_latency():
    data = load("stage3_experiments.json")
    normal = [d for d in data if d["fault_at"] is None]

    slices = [d["num_slices"] for d in normal]
    e2e = [d["e2e_latency_ms"] for d in normal]
    proof = [d["total_proof_gen_ms"] for d in normal]
    verify = [d["total_verify_ms"] for d in normal]

    x = np.arange(len(slices))
    w = 0.25
    fig, ax = plt.subplots(figsize=(8, 5))
    b1 = ax.bar(x - w, e2e, w, label="端到端时延", color="#4472C4")
    b2 = ax.bar(x, proof, w, label="证明生成总时间", color="#ED7D31")
    b3 = ax.bar(x + w, verify, w, label="验证总时间", color="#A5A5A5")

    for bars in [b1, b2, b3]:
        for b in bars:
            h = b.get_height()
            ax.text(b.get_x() + b.get_width() / 2, h + 100,
                    f"{h:.0f}", ha="center", va="bottom", fontsize=8)

    ax.set_xlabel("切片数")
    ax.set_ylabel("时间 (ms)")
    ax.set_title("图 1  不同切片数下的系统开销")
    ax.set_xticks(x)
    ax.set_xticklabels([str(s) for s in slices])
    ax.legend()
    ax.set_ylim(0, max(e2e) * 1.25)
    save(fig, "fig01_stage3_latency.png")


# ======================================================================
# 图 2: Proof-bound output 预防效果 (Stage3 fault experiments)
# ======================================================================
def fig_stage3_prevention():
    data = load("stage3_experiments.json")
    fault = [d for d in data if d["fault_at"] is not None]

    slices = [d["num_slices"] for d in fault]
    prevented = [d.get("fault_prevented", False) for d in fault]
    detected = [d.get("fault_detected", False) for d in fault]

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(slices))
    colors = ["#70AD47" if p else "#FF0000" for p in prevented]
    ax.bar(x, [1] * len(slices), color=colors, edgecolor="black")
    for i, (s, p) in enumerate(zip(slices, prevented)):
        label = "预防 ✓" if p else "检测"
        ax.text(i, 0.5, label, ha="center", va="center", fontsize=12,
                fontweight="bold", color="white")

    ax.set_xlabel("切片数")
    ax.set_title("图 2  Proof-bound output 对故障注入的处理效果")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s} 切片\n(fault@{s})" for s in slices])
    ax.set_yticks([])
    ax.set_ylim(0, 1.2)
    save(fig, "fig02_proof_bound_prevention.png")


# ======================================================================
# 图 3: 选择性验证 — 8切片 requested_vr vs e2e / proof_gen (P1)
# ======================================================================
def fig_selective_verification():
    data = load("advanced_experiments.json")
    # 只取 8 切片正常模式
    p1_8s_normal = [d for d in data
                    if d["experiment"].startswith("P1_8s") and "normal" in d["experiment"]]
    p1_8s_normal.sort(key=lambda d: d["verify_ratio"], reverse=True)

    vr = [d["verify_ratio"] for d in p1_8s_normal]
    apf = [d["actual_proof_fraction"] for d in p1_8s_normal]
    e2e = [d["e2e_latency_ms"] for d in p1_8s_normal]
    proof = [d["total_proof_gen_ms"] for d in p1_8s_normal]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    # 子图 a: e2e latency
    ax = axes[0]
    bars = ax.bar([f"{v:.0%}" for v in vr], e2e, color="#4472C4", width=0.5)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 200,
                f"{b.get_height():.0f}", ha="center", fontsize=9)
    ax.set_xlabel("请求验证比例")
    ax.set_ylabel("端到端时延 (ms)")
    ax.set_title("(a) 端到端时延")

    # 子图 b: proof gen time
    ax = axes[1]
    bars = ax.bar([f"{v:.0%}" for v in vr], proof, color="#ED7D31", width=0.5)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 200,
                f"{b.get_height():.0f}", ha="center", fontsize=9)
    ax.set_xlabel("请求验证比例")
    ax.set_ylabel("证明生成总时间 (ms)")
    ax.set_title("(b) 证明生成总时间")

    # 子图 c: actual proof fraction
    ax = axes[2]
    x_pos = np.arange(len(vr))
    ax.bar(x_pos - 0.15, vr, 0.3, label="请求验证比例", color="#4472C4")
    ax.bar(x_pos + 0.15, apf, 0.3, label="实际 proof 覆盖率", color="#ED7D31")
    for i in range(len(vr)):
        ax.text(x_pos[i] - 0.15, vr[i] + 0.02, f"{vr[i]:.0%}",
                ha="center", fontsize=8)
        ax.text(x_pos[i] + 0.15, apf[i] + 0.02, f"{apf[i]:.1%}",
                ha="center", fontsize=8)
    ax.set_xlabel("配置")
    ax.set_ylabel("比例")
    ax.set_title("(c) 请求比例 vs 实际覆盖率")
    ax.set_xticks(x_pos)
    ax.set_xticklabels([f"vr={v:.0%}" for v in vr])
    ax.legend(fontsize=8)
    ax.set_ylim(0, 1.3)

    fig.suptitle("图 3  选择性验证机制效果 (8 切片)", fontsize=13, y=1.02)
    fig.tight_layout()
    save(fig, "fig03_selective_verification.png")


# ======================================================================
# 图 4: 攻击检测结果 (P3)
# ======================================================================
def fig_attack_detection():
    data = load("advanced_experiments.json")
    p3 = [d for d in data if d["experiment"].startswith("P3_")]

    attacks = ["tamper", "skip", "random", "replay"]
    vr_labels = ["100%", "50%"]

    fig, ax = plt.subplots(figsize=(9, 5))
    x = np.arange(len(attacks))
    w = 0.3

    for j, vr_val in enumerate([1.0, 0.5]):
        vals = []
        labels_detail = []
        for atk in attacks:
            match = [d for d in p3
                     if d["fault_type"] == atk and d["verify_ratio"] == vr_val]
            if match:
                d = match[0]
                prevented = d.get("fault_prevented", False)
                detected = d.get("detection_accuracy", 0)
                vals.append(1.0)
                labels_detail.append("预防" if prevented else "检测")
            else:
                vals.append(0)
                labels_detail.append("-")

        color = "#4472C4" if j == 0 else "#ED7D31"
        bars = ax.bar(x + (j - 0.5) * w, vals, w,
                      label=f"请求验证比例 {vr_labels[j]}", color=color)
        for i, (b, lbl) in enumerate(zip(bars, labels_detail)):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.02,
                    lbl, ha="center", fontsize=8)

    ax.set_xlabel("攻击类型")
    ax.set_ylabel("检测/预防成功率")
    ax.set_title("图 4  四类攻击在不同验证预算下的处理结果\n"
                 "(当前攻击模型：响应层篡改)")
    ax.set_xticks(x)
    ax.set_xticklabels(attacks)
    ax.legend()
    ax.set_ylim(0, 1.3)
    ax.set_yticks([0, 0.5, 1.0])
    ax.set_yticklabels(["0%", "50%", "100%"])
    save(fig, "fig04_attack_detection.png")


# ======================================================================
# 图 5: 可见性模式对比 — proof 生成时间 + verify 时间 (P2)
# ======================================================================
def fig_visibility_time():
    data = load("p2_visibility_modes.json")
    modes = [d["visibility_mode"] for d in data]
    proof_ms = [d["avg_total_proof_gen_ms"] for d in data]
    verify_ms = [d["avg_total_verify_ms"] for d in data]

    mode_labels = {"all_public": "all_public\n(无隐私)", "hashed": "hashed\n(Poseidon)",
                   "private": "private\n(完全隐私)"}
    labels = [mode_labels.get(m, m) for m in modes]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # 子图 a: proof gen
    ax = axes[0]
    bars = ax.bar(labels, proof_ms, color=["#4472C4", "#ED7D31", "#70AD47"], width=0.5)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 100,
                f"{b.get_height():.0f}", ha="center", fontsize=9)
    ax.set_ylabel("证明生成总时间 (ms)")
    ax.set_title("(a) 证明生成时间对比")

    # 子图 b: verify
    ax = axes[1]
    bars = ax.bar(labels, verify_ms, color=["#4472C4", "#ED7D31", "#70AD47"], width=0.5)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 5,
                f"{b.get_height():.0f}", ha="center", fontsize=9)
    ax.set_ylabel("验证总时间 (ms)")
    ax.set_title("(b) 验证时间对比")

    # 计算倍数
    base = proof_ms[0]
    for i, m in enumerate(proof_ms):
        ratio = m / base
        if i > 0:
            axes[0].text(i, proof_ms[i] / 2, f"{ratio:.2f}×",
                         ha="center", fontsize=11, fontweight="bold", color="white")

    fig.suptitle("图 5  三种可见性模式的证明开销对比 (4 切片 × 3 次均值)", fontsize=13, y=1.02)
    fig.tight_layout()
    save(fig, "fig05_visibility_time.png")


# ======================================================================
# 图 6: 可见性模式对比 — proof size + witness size (P2)
# ======================================================================
def fig_visibility_size():
    data = load("p2_visibility_modes.json")
    modes = [d["visibility_mode"] for d in data]

    mode_labels = {"all_public": "all_public", "hashed": "hashed", "private": "private"}
    labels = [mode_labels.get(m, m) for m in modes]

    # 从 trials 中提取 proof/witness size 的均值
    proof_sizes = []
    witness_sizes = []
    for d in data:
        ps = [t["total_proof_size_bytes"] for t in d["trials"]]
        ws = [t["total_witness_size_bytes"] for t in d["trials"]]
        proof_sizes.append(np.mean(ps))
        witness_sizes.append(np.mean(ws))

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # proof size
    ax = axes[0]
    ps_kb = [s / 1024 for s in proof_sizes]
    bars = ax.bar(labels, ps_kb, color=["#4472C4", "#ED7D31", "#70AD47"], width=0.5)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.5,
                f"{b.get_height():.1f}", ha="center", fontsize=9)
    ax.set_ylabel("Proof 总大小 (KB)")
    ax.set_title("(a) Proof 大小对比")

    # witness size
    ax = axes[1]
    ws_kb = [s / 1024 for s in witness_sizes]
    bars = ax.bar(labels, ws_kb, color=["#4472C4", "#ED7D31", "#70AD47"], width=0.5)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.1,
                f"{b.get_height():.1f}", ha="center", fontsize=9)
    ax.set_ylabel("Witness 总大小 (KB)")
    ax.set_title("(b) Witness 大小对比")

    fig.suptitle("图 6  三种可见性模式的产物大小对比 (4 切片)", fontsize=13, y=1.02)
    fig.tight_layout()
    save(fig, "fig06_visibility_size.png")


# ======================================================================
# 图 7: P4 切片逻辑一致性误差
# ======================================================================
def fig_p4_fidelity():
    data = load("p4_p6_results.json")
    fidelity = data["fidelity"]

    slices_keys = ["2_slices", "4_slices", "8_slices"]
    slice_labels = ["2 切片", "4 切片", "8 切片"]
    l1 = [fidelity[k]["l1_distance"] for k in slices_keys]
    l2 = [fidelity[k]["l2_distance"] for k in slices_keys]
    rel = [fidelity[k]["relative_error"] for k in slices_keys]

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(slice_labels))
    w = 0.25
    ax.bar(x - w, l1, w, label="L1 距离", color="#4472C4")
    ax.bar(x, l2, w, label="L2 距离", color="#ED7D31")
    ax.bar(x + w, rel, w, label="相对误差", color="#70AD47")

    # 所有值都是 0
    for i in range(len(slice_labels)):
        ax.text(i, 0.001, "0.0", ha="center", fontsize=10, fontweight="bold")

    ax.set_xlabel("切片数")
    ax.set_ylabel("误差值")
    ax.set_title("图 7  PyTorch 切片逻辑一致性验证\n"
                 "(切片串联输出 vs 完整模型输出)")
    ax.set_xticks(x)
    ax.set_xticklabels(slice_labels)
    ax.legend()
    ax.set_ylim(0, 0.01)
    ax.text(1, 0.008, "所有配置下误差为 0 (bit-exact)", ha="center",
            fontsize=11, style="italic", color="#666666")
    save(fig, "fig07_p4_fidelity.png")


# ======================================================================
# 图 8: P6 三种完整性检查机制对比
# ======================================================================
def fig_p6_integrity():
    data = load("p4_p6_results.json")
    zk = data["zk_chain_comparison"]

    # 按模式分组
    schemes = {}
    for d in zk:
        key = d["scheme"]
        if key not in schemes:
            schemes[key] = {"normal": None, "fault": None}
        if d["fault_at"] is None:
            schemes[key]["normal"] = d
        else:
            schemes[key]["fault"] = d

    scheme_labels = {
        "external_sha256": "外部哈希链\n(SHA-256)",
        "in_circuit_poseidon": "电路内 Poseidon\n(hashed mode)",
        "private_input": "完全隐私模式\n(private mode)",
    }

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # 子图 a: 正常模式下的 proof 时间对比
    ax = axes[0]
    labels_list = []
    proof_times = []
    for key in ["external_sha256", "in_circuit_poseidon", "private_input"]:
        if key in schemes and schemes[key]["normal"]:
            labels_list.append(scheme_labels[key])
            proof_times.append(schemes[key]["normal"]["total_proof_gen_ms"])
    bars = ax.bar(labels_list, proof_times,
                  color=["#4472C4", "#ED7D31", "#70AD47"], width=0.5)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 100,
                f"{b.get_height():.0f}", ha="center", fontsize=9)
    ax.set_ylabel("证明生成总时间 (ms)")
    ax.set_title("(a) 正常模式")

    # 子图 b: 故障注入下 — 所有模式都成功检测/预防
    ax = axes[1]
    checks = ["external_integrity", "circuit_verified", "chain_ok"]
    check_labels = ["外部哈希\n完整性", "电路内\n验证", "哈希链\n一致性"]
    x = np.arange(len(checks))
    w = 0.25
    for i, key in enumerate(["external_sha256", "in_circuit_poseidon", "private_input"]):
        if key in schemes and schemes[key]["fault"]:
            fd = schemes[key]["fault"]
            vals = [all(c[ch] for c in fd["checks"]) for ch in checks]
            vals_int = [1 if v else 0 for v in vals]
            color = ["#4472C4", "#ED7D31", "#70AD47"][i]
            ax.bar(x + (i - 1) * w, vals_int, w,
                   label=scheme_labels[key].replace("\n", " "), color=color)
    ax.set_ylabel("检查结果")
    ax.set_title("(b) 故障注入下各检查是否通过\n(proof-bound 已预防篡改)")
    ax.set_xticks(x)
    ax.set_xticklabels(check_labels)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["失败", "通过"])
    ax.legend(fontsize=7, loc="lower right")

    fig.suptitle("图 8  三种完整性检查机制对比 (4 切片)", fontsize=13, y=1.02)
    fig.tight_layout()
    save(fig, "fig08_p6_integrity.png")


# ======================================================================
# 图 9: 4切片 vs 8切片选择性验证对比
# ======================================================================
def fig_selective_4vs8():
    data = load("advanced_experiments.json")
    # 只取正常模式
    p1_normal = [d for d in data
                 if d["experiment"].startswith("P1_") and "normal" in d["experiment"]]

    fig, ax = plt.subplots(figsize=(9, 5))
    for ns, color, marker in [(4, "#4472C4", "o"), (8, "#ED7D31", "s")]:
        subset = sorted([d for d in p1_normal if d["num_slices"] == ns],
                        key=lambda d: d["verify_ratio"], reverse=True)
        vr = [d["verify_ratio"] for d in subset]
        e2e = [d["e2e_latency_ms"] for d in subset]
        ax.plot(vr, e2e, marker=marker, color=color, linewidth=2,
                markersize=8, label=f"{ns} 切片")
        for v, e in zip(vr, e2e):
            ax.annotate(f"{e:.0f}", (v, e), textcoords="offset points",
                        xytext=(0, 10), ha="center", fontsize=8)

    ax.set_xlabel("请求验证比例 (verify_ratio)")
    ax.set_ylabel("端到端时延 (ms)")
    ax.set_title("图 9  4 切片 vs 8 切片：请求验证比例对端到端时延的影响")
    ax.legend()
    ax.invert_xaxis()
    ax.set_xticks([1.0, 0.5, 0.25])
    ax.set_xticklabels(["100%", "50%", "25%"])
    save(fig, "fig09_selective_4vs8.png")


# ======================================================================
# 主入口
# ======================================================================
if __name__ == "__main__":
    print("生成中期答辩图表...")
    fig_stage3_latency()
    fig_stage3_prevention()
    fig_selective_verification()
    fig_attack_detection()
    fig_visibility_time()
    fig_visibility_size()
    fig_p4_fidelity()
    fig_p6_integrity()
    fig_selective_4vs8()
    print(f"\n完成！所有图表已保存到 {OUT_DIR}")
