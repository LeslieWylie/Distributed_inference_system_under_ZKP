"""
v2/compile/build_circuits.py — 编译阶段：切片导出 + EZKL 电路编译 + 工件注册。

离线编译流程:
  1. 切分完整模型为 N 个 ONNX 子模型
  2. 为每片生成 EZKL settings (input/output 均 hashed)
  3. 校准 + 编译电路
  4. 生成 PK/VK/SRS
  5. 提取量化 scale 元数据
  6. 计算 model_digest
  7. 写入 slice_registry.json
"""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

# Windows 编码 & HOME 修复
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
USER_HOME = str(Path.home())
os.environ.setdefault("HOME", USER_HOME)
os.environ.setdefault("EZKL_REPO_PATH", os.path.join(USER_HOME, ".ezkl"))
os.makedirs(os.environ["EZKL_REPO_PATH"], exist_ok=True)

import ezkl

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from v2.common.commitments import compute_file_digest
from v2.common.types import SliceArtifact
from v2.common.registry_manifest import (
    build_client_registry_manifest,
    compute_registry_digest,
)


def ensure_min_logrows(settings_path: str, min_logrows: int) -> int:
    """Ensure EZKL settings keep at least the requested logrows."""
    with open(settings_path, "r", encoding="utf-8") as handle:
        settings = json.load(handle)

    run_args = settings.setdefault("run_args", {})
    current = int(run_args.get("logrows", 0) or 0)
    if current < min_logrows:
        run_args["logrows"] = min_logrows
        with open(settings_path, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)
        return min_logrows
    return current


def _read_logrows(settings_path: str) -> int:
    with open(settings_path, "r", encoding="utf-8") as handle:
        settings = json.load(handle)
    return int(settings.get("run_args", {}).get("logrows", 0) or 0)


def _ensure_srs(paths: dict):
    with open(paths["settings"], "r", encoding="utf-8") as handle:
        settings = json.load(handle)
    logrows = settings.get("run_args", {}).get("logrows", 0)
    shared_srs = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(paths["settings"])))),
        "shared_srs",
        f"kzg_{logrows}.srs",
    )

    if os.path.exists(paths["srs"]):
        return

    if os.path.exists(shared_srs):
        import shutil
        shutil.copy2(shared_srs, paths["srs"])
        print(f"  [SRS] Reused shared SRS (logrows={logrows})")
        return

    async def _fetch():
        return await ezkl.get_srs(settings_path=paths["settings"], srs_path=paths["srs"])

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import nest_asyncio

        nest_asyncio.apply()
        asyncio.run(_fetch())
    else:
        asyncio.run(_fetch())

    os.makedirs(os.path.dirname(shared_srs), exist_ok=True)
    import shutil

    shutil.copy2(paths["srs"], shared_srs)


def export_slices(
    num_slices: int = 4,
    num_layers: int = 8,
    input_dim: int = 8,
    hidden_dim: int = 8,
    output_dim: int = 4,
    output_dir: str | None = None,
    seed: int = 42,
    model_type: str = "mnist",
) -> list[dict]:
    """导出 ONNX + 校准数据。支持 mnist / configurable 两种模型。"""
    if output_dir is None:
        output_dir = os.path.join(PROJECT_ROOT, "v2", "artifacts", "models")

    if model_type == "mnist":
        from models.mnist_model import split_and_export as mnist_export
        result = mnist_export(
            num_slices=num_slices,
            output_dir=output_dir,
            seed=seed,
            train=True,
            train_epochs=3,
        )
    elif model_type == "mnist_cnn":
        from models.mnist_cnn import split_and_export as cnn_export
        result = cnn_export(
            output_dir=output_dir,
            seed=seed,
            train=True,
            train_epochs=3,
        )
    else:
        from models.configurable_model import split_and_export
        result = split_and_export(
            num_slices=num_slices,
            num_layers=num_layers,
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            output_dim=output_dim,
            output_dir=output_dir,
            seed=seed,
        )
    return result["slices"]


def build_circuit_for_slice(
    onnx_path: str,
    cal_path: str,
    artifacts_dir: str,
    min_logrows: int | None = None,
    visibility_mode: str = "public",
) -> dict:
    """
    为单个切片执行 EZKL 编译流程:
      gen_settings → calibrate → compile → get_srs → setup

    visibility_mode:
      - "public":      输入输出均暴露为明文 rescaled 值 (默认, 向后兼容)
      - "polycommit":  输入输出以多项式承诺形式暴露, 支持精确链接
      - "hashed":      输入输出以 Poseidon 哈希暴露, 提供隐私保护
    """
    os.makedirs(artifacts_dir, exist_ok=True)
    artifacts_dir = os.path.abspath(artifacts_dir)

    paths = {
        "settings": os.path.join(artifacts_dir, "settings.json"),
        "compiled": os.path.join(artifacts_dir, "network.compiled"),
        "pk": os.path.join(artifacts_dir, "pk.key"),
        "vk": os.path.join(artifacts_dir, "vk.key"),
        "srs": os.path.join(artifacts_dir, "kzg.srs"),
    }

    py_run_args = ezkl.PyRunArgs()
    py_run_args.input_visibility = visibility_mode
    py_run_args.output_visibility = visibility_mode
    py_run_args.param_visibility = "fixed"
    assert ezkl.gen_settings(onnx_path, paths["settings"], py_run_args=py_run_args)

    # calibrate
    assert ezkl.calibrate_settings(cal_path, onnx_path, paths["settings"], "resources")

    if min_logrows is not None:
        ensure_min_logrows(paths["settings"], min_logrows)

    max_setup_attempts = 3
    for attempt in range(max_setup_attempts):
        assert ezkl.compile_circuit(onnx_path, paths["compiled"], paths["settings"])

        if os.path.exists(paths["srs"]):
            os.remove(paths["srs"])
        _ensure_srs(paths)

        try:
            assert ezkl.setup(paths["compiled"], paths["vk"], paths["pk"], srs_path=paths["srs"])
            break
        except RuntimeError as error:
            if "too small" not in str(error).lower() or attempt == max_setup_attempts - 1:
                raise
            next_logrows = _read_logrows(paths["settings"]) + 1
            ensure_min_logrows(paths["settings"], next_logrows)
            for stale_path in (paths["compiled"], paths["pk"], paths["vk"]):
                if os.path.exists(stale_path):
                    os.remove(stale_path)
            print(f"  [Compile] setup retry with logrows={next_logrows}")

    return paths


def extract_scale_metadata(settings_path: str) -> dict:
    """从 settings.json 提取量化 scale 信息。"""
    with open(settings_path, "r") as f:
        settings = json.load(f)
    run_args = settings.get("run_args", {})
    return {
        "input_scale": run_args.get("input_scale"),
        "output_scale": run_args.get("output_scale"),
        "param_scale": run_args.get("param_scale"),
    }


def align_interface_scales(settings_paths: list[str]) -> bool:
    """
    对齐相邻切片接口的量化 scale。

    对每对相邻切片 (i, i+1)：
      output_scale[i] = input_scale[i+1] = max(output_scale[i], input_scale[i+1])

    对齐后同一浮点张量在两个电路中产生完全相同的 field element，
    使链接比较从近似容差 (ε=0.004) 变为精确匹配 (ε=0)。

    代价：提高 scale 会增加电路规模 (更多 bits)，编译阶段的 logrows
    自动重试机制会处理这一点。

    返回 True 表示修改了 scale，False 表示原本就对齐。
    """
    settings_data = []
    for p in settings_paths:
        with open(p, "r", encoding="utf-8") as f:
            settings_data.append(json.load(f))

    modified = False
    for i in range(len(settings_data) - 1):
        curr_ra = settings_data[i].get("run_args", {})
        next_ra = settings_data[i + 1].get("run_args", {})

        out_s = int(curr_ra.get("output_scale", 7))
        in_s = int(next_ra.get("input_scale", 7))

        if out_s != in_s:
            aligned = max(out_s, in_s)
            curr_ra["output_scale"] = aligned
            next_ra["input_scale"] = aligned
            settings_data[i]["run_args"] = curr_ra
            settings_data[i + 1]["run_args"] = next_ra
            modified = True
            print(f"  [Scale Align] slice {i+1}→{i+2}: "
                  f"out_scale={out_s}, in_scale={in_s} → aligned={aligned}")

    if modified:
        for p, d in zip(settings_paths, settings_data):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)

    return modified


def _compile_and_setup_slice(onnx_path: str, circuit_dir: str,
                             min_logrows: int | None = None) -> dict:
    """Phase 3 helper: compile + srs + setup for one slice (settings already exists)."""
    circuit_dir = os.path.abspath(circuit_dir)
    paths = {
        "settings": os.path.join(circuit_dir, "settings.json"),
        "compiled": os.path.join(circuit_dir, "network.compiled"),
        "pk": os.path.join(circuit_dir, "pk.key"),
        "vk": os.path.join(circuit_dir, "vk.key"),
        "srs": os.path.join(circuit_dir, "kzg.srs"),
    }

    if min_logrows is not None:
        ensure_min_logrows(paths["settings"], min_logrows)

    max_attempts = 3
    for attempt in range(max_attempts):
        assert ezkl.compile_circuit(onnx_path, paths["compiled"], paths["settings"])

        if os.path.exists(paths["srs"]):
            os.remove(paths["srs"])
        _ensure_srs(paths)

        try:
            assert ezkl.setup(
                paths["compiled"], paths["vk"], paths["pk"],
                srs_path=paths["srs"],
            )
            break
        except RuntimeError as error:
            if "too small" not in str(error).lower() or attempt == max_attempts - 1:
                raise
            next_logrows = _read_logrows(paths["settings"]) + 1
            ensure_min_logrows(paths["settings"], next_logrows)
            for stale in (paths["compiled"], paths["pk"], paths["vk"]):
                if os.path.exists(stale):
                    os.remove(stale)
            print(f"  [Compile] setup retry with logrows={next_logrows}")

    return paths


def build_registry(
    num_slices: int = 4,
    num_layers: int = 8,
    registry_dir: str | None = None,
    model_type: str = "mnist",
    visibility_mode: str = "public",
) -> list[SliceArtifact]:
    """
    完整离线编译流程（三阶段）。

    Phase 1: 导出模型切片 + 各片独立校准 (gen_settings + calibrate)
    Phase 2: 对齐相邻切片接口 scale (align_interface_scales)
    Phase 3: 各片编译 + 密钥生成 (compile + srs + setup)

    visibility_mode:
      "public"     — 所有 I/O 暴露为明文 rescaled 值
      "polycommit" — 中间接口隐藏于 KZG 承诺，首端输入/末端输出保持 public

    返回 SliceArtifact 列表。
    """
    if registry_dir is None:
        registry_dir = os.path.join(PROJECT_ROOT, "v2", "artifacts")

    models_dir = os.path.join(registry_dir, "models")
    slices_info = export_slices(
        num_slices=num_slices,
        num_layers=num_layers,
        output_dir=models_dir,
        model_type=model_type,
    )

    # ═══════════════════════════════════════════════════════════
    # PHASE 1: 各片独立校准 — 确定量化参数
    # ═══════════════════════════════════════════════════════════
    print(f"[Compile] Phase 1: Calibrating {num_slices} slices "
          f"(visibility={visibility_mode})...")

    circuit_dirs = []
    settings_paths = []

    for s in slices_info:
        sid = s["id"]
        cdir = os.path.join(registry_dir, "circuits", f"slice_{sid}")
        os.makedirs(cdir, exist_ok=True)
        cdir = os.path.abspath(cdir)
        circuit_dirs.append(cdir)

        sp = os.path.join(cdir, "settings.json")

        # 确定本片可见性
        # polycommit 模式下的隐私方案:
        #   首片 input = public  (验证方需要做输入绑定)
        #   末片 output = public (验证方需要做终端绑定)
        #   其余接口 = polycommit (中间激活隐藏于 KZG 承诺)
        vis_in = visibility_mode
        vis_out = visibility_mode
        if visibility_mode == "polycommit" and num_slices > 1:
            if sid == 1:
                vis_in = "public"
            if sid == num_slices:
                vis_out = "public"

        py_run_args = ezkl.PyRunArgs()
        py_run_args.input_visibility = vis_in
        py_run_args.output_visibility = vis_out
        py_run_args.param_visibility = "fixed"

        assert ezkl.gen_settings(s["onnx"], sp, py_run_args=py_run_args)

        # 确保校准前 logrows 足够大，避免大激活值导致 forward pass 溢出
        if model_type == "mnist":
            ensure_min_logrows(sp, 16)

        assert ezkl.calibrate_settings(s["cal"], s["onnx"], sp, "resources")
        settings_paths.append(sp)
        print(f"  Slice {sid}: calibrated (in={vis_in}, out={vis_out})")

    # ═══════════════════════════════════════════════════════════
    # PHASE 2: 对齐接口 scale — 消除链接容差
    # ═══════════════════════════════════════════════════════════
    scales_aligned = False
    if num_slices > 1:
        print(f"[Compile] Phase 2: Aligning interface scales...")
        was_modified = align_interface_scales(settings_paths)
        scales_aligned = True
        if was_modified:
            print(f"[Compile] Scales modified and aligned")
        else:
            print(f"[Compile] Scales already aligned")

    # ═══════════════════════════════════════════════════════════
    # PHASE 3: 各片编译 + 密钥生成
    # ═══════════════════════════════════════════════════════════
    print(f"[Compile] Phase 3: Compiling {num_slices} circuits...")

    artifacts = []
    for s, cdir in zip(slices_info, circuit_dirs):
        sid = s["id"]
        print(f"  Compiling slice {sid}...")

        paths = _compile_and_setup_slice(
            s["onnx"], cdir,
            min_logrows=16 if model_type == "mnist" else None,
        )
        model_digest = compute_file_digest(s["onnx"])
        scales = extract_scale_metadata(paths["settings"])

        artifact = SliceArtifact(
            slice_id=sid,
            model_path=s["onnx"],
            compiled_path=paths["compiled"],
            settings_path=paths["settings"],
            pk_path=paths["pk"],
            vk_path=paths["vk"],
            srs_path=paths["srs"],
            model_digest=model_digest,
            input_scale=scales.get("input_scale"),
            output_scale=scales.get("output_scale"),
            param_scale=scales.get("param_scale"),
        )
        artifacts.append(artifact)

    # 写入 registry JSON
    registry_path = os.path.join(registry_dir, "registry", "slice_registry.json")
    os.makedirs(os.path.dirname(registry_path), exist_ok=True)

    registry_data = []
    for a in artifacts:
        registry_data.append({
            "slice_id": a.slice_id,
            "model_path": a.model_path,
            "compiled_path": a.compiled_path,
            "settings_path": a.settings_path,
            "pk_path": a.pk_path,
            "vk_path": a.vk_path,
            "srs_path": a.srs_path,
            "model_digest": a.model_digest,
            "input_scale": a.input_scale,
            "output_scale": a.output_scale,
            "param_scale": a.param_scale,
        })

    with open(registry_path, "w") as f:
        json.dump(registry_data, f, indent=2)
    print(f"[Compile] Registry written: {registry_path}")

    # Persist registry metadata (canonical digest for client verification)
    manifest = build_client_registry_manifest(artifacts)
    registry_digest = compute_registry_digest(manifest)
    metadata_path = os.path.join(registry_dir, "registry", "registry_metadata.json")
    with open(metadata_path, "w") as f:
        json.dump({
            "registry_digest": registry_digest,
            "slice_count": len(registry_data),
            "model_type": model_type,
            "visibility_mode": visibility_mode,
            "scales_aligned": scales_aligned,
            "manifest": manifest,
        }, f, indent=2)
    print(f"[Compile] Registry metadata written: {metadata_path}")

    return artifacts


def load_registry(registry_path: str) -> list[SliceArtifact]:
    """从 JSON 加载已注册的切片工件。"""
    with open(registry_path, "r") as f:
        data = json.load(f)
    return [
        SliceArtifact(
            slice_id=d["slice_id"],
            model_path=d["model_path"],
            compiled_path=d["compiled_path"],
            settings_path=d["settings_path"],
            pk_path=d["pk_path"],
            vk_path=d["vk_path"],
            srs_path=d["srs_path"],
            model_digest=d["model_digest"],
            input_scale=d.get("input_scale"),
            output_scale=d.get("output_scale"),
            param_scale=d.get("param_scale"),
        )
        for d in data
    ]
