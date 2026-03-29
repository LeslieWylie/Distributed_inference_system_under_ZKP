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


def compute_registry_digest(registry_data: list[dict]) -> str:
    """Compute a deterministic SHA-256 digest over registry JSON content."""
    payload = json.dumps(registry_data, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
) -> dict:
    """
    为单个切片执行 EZKL 编译流程:
      gen_settings → calibrate → compile → get_srs → setup

    visibility: input=hashed, output=hashed (双端承诺链)
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

    # gen_settings: input/output 使用 public 模式
    # 原因：hashed 模式下 Poseidon 哈希受独立量化 scale 影响，
    #       导致相邻切片的 processed_outputs != processed_inputs。
    #       public 模式下 proof 仍然密码学绑定 rescaled 值，
    #       verifier 通过比对 rescaled_outputs[i] ≈ rescaled_inputs[i+1] 做 linking。
    py_run_args = ezkl.PyRunArgs()
    py_run_args.input_visibility = "public"
    py_run_args.output_visibility = "public"
    py_run_args.param_visibility = "fixed"
    assert ezkl.gen_settings(onnx_path, paths["settings"], py_run_args=py_run_args)

    # calibrate
    assert ezkl.calibrate_settings(cal_path, onnx_path, paths["settings"], "resources")

    # compile
    assert ezkl.compile_circuit(onnx_path, paths["compiled"], paths["settings"])

    # get_srs — 如果同 logrows 的 SRS 已在其他目录下载过, 复制过来避免重复下载
    if not os.path.exists(paths["srs"]):
        with open(paths["settings"]) as _f:
            _settings = json.load(_f)
        _logrows = _settings.get("run_args", {}).get("logrows", 0)
        _shared_srs = os.path.join(
            os.path.dirname(os.path.dirname(artifacts_dir)),
            "shared_srs", f"kzg_{_logrows}.srs"
        )
        if os.path.exists(_shared_srs):
            import shutil
            shutil.copy2(_shared_srs, paths["srs"])
            print(f"  [SRS] Reused shared SRS (logrows={_logrows})")
        else:
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
            # 保存到共享目录供后续复用
            os.makedirs(os.path.dirname(_shared_srs), exist_ok=True)
            import shutil
            shutil.copy2(paths["srs"], _shared_srs)

    # setup
    assert ezkl.setup(paths["compiled"], paths["vk"], paths["pk"], srs_path=paths["srs"])

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


def build_registry(
    num_slices: int = 4,
    num_layers: int = 8,
    registry_dir: str | None = None,
    model_type: str = "mnist",
) -> list[SliceArtifact]:
    """
    完整离线编译流程：导出模型 → 编译每片电路 → 写入 registry。

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

    artifacts = []
    for s in slices_info:
        sid = s["id"]
        circuit_dir = os.path.join(registry_dir, "circuits", f"slice_{sid}")
        print(f"[Compile] Building circuit for slice {sid}...")

        paths = build_circuit_for_slice(s["onnx"], s["cal"], circuit_dir)
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

    # Persist registry metadata (digest + slice count) for client verification
    registry_digest = compute_registry_digest(registry_data)
    metadata_path = os.path.join(registry_dir, "registry", "registry_metadata.json")
    with open(metadata_path, "w") as f:
        json.dump({
            "registry_digest": registry_digest,
            "slice_count": len(registry_data),
            "model_type": model_type,
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
