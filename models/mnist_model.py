"""
MNIST MLP 模型定义，支持 N 切片导出。

用于替代玩具 ConfigurableModel，提供真实模型负载和标准数据集评估。

模型结构:
  Input(784) -> Linear(784,128) -> ReLU
              -> Linear(128,64) -> ReLU
              -> Linear(64,10)

参数量: ~110K (784*128 + 128 + 128*64 + 64 + 64*10 + 10 = 109,386)
"""

import json
import math
import os

import numpy as np
import torch
import torch.nn as nn


MODEL_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".mnist_cache")
MODEL_CACHE_PATH = os.path.join(MODEL_CACHE_DIR, "full_model_state.pt")


class MnistMLP(nn.Module):
    """三层 MLP 用于 MNIST 分类。"""

    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(784, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 10),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


class SliceModel(nn.Module):
    """切片子模型：包含原模型的连续几层。"""

    def __init__(self, layers: nn.Sequential):
        super().__init__()
        self.layers = layers

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


def _expand_layers_for_slices(all_layers: list[nn.Module], num_slices: int) -> list[nn.Module]:
    expanded = list(all_layers)
    while len(expanded) < num_slices:
        next_expanded: list[nn.Module] = []
        for index, layer in enumerate(expanded):
            next_expanded.append(layer)
            if len(next_expanded) < num_slices and index < len(expanded) - 1:
                next_expanded.append(nn.Identity())
        if len(next_expanded) == len(expanded):
            break
        expanded = next_expanded
    return expanded


def build_slice_models(model: MnistMLP, num_slices: int) -> list[SliceModel]:
    all_layers = _expand_layers_for_slices(list(model.layers), num_slices)
    total = len(all_layers)
    slice_sizes = [total // num_slices] * num_slices
    for index in range(total % num_slices):
        slice_sizes[index] += 1

    slice_models = []
    layer_index = 0
    for size in slice_sizes:
        end_index = layer_index + size
        slice_models.append(SliceModel(nn.Sequential(*all_layers[layer_index:end_index])))
        layer_index = end_index
    return slice_models


def build_slice_calibration_tensors(
    model: MnistMLP,
    num_slices: int,
    calibration_inputs: torch.Tensor,
) -> list[torch.Tensor]:
    slice_models = build_slice_models(model, num_slices)
    calibration_tensors: list[torch.Tensor] = []
    current_batch = calibration_inputs.detach().clone()

    with torch.no_grad():
        for slice_model in slice_models:
            calibration_tensors.append(current_batch.detach().cpu().clone())
            current_batch = slice_model(current_batch)

    return calibration_tensors


def load_representative_mnist_batch(num_samples: int, seed: int = 42) -> torch.Tensor:
    samples = sample_mnist_inputs(num_samples=num_samples, seed=seed)
    return torch.tensor([sample["input_tensor"] for sample in samples], dtype=torch.float32)


def save_model_state(
    model: MnistMLP,
    output_dir: str,
    metadata: dict | None = None,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    state_path = os.path.join(output_dir, "full_model_state.pt")
    payload = {
        "state_dict": model.state_dict(),
        "metadata": metadata or {},
        "architecture": "784 -> 128 -> ReLU -> 64 -> ReLU -> 10",
    }
    torch.save(payload, state_path)
    return state_path


def load_model_state(state_path: str, map_location: str = "cpu") -> MnistMLP:
    payload = torch.load(state_path, map_location=map_location)
    state_dict = payload.get("state_dict", payload)
    model = MnistMLP()
    model.load_state_dict(state_dict)
    model.eval()
    return model


def load_model_checkpoint(state_path: str, map_location: str = "cpu") -> dict:
    return torch.load(state_path, map_location=map_location)


def sample_mnist_inputs(num_samples: int, seed: int = 42) -> list[dict]:
    from torchvision import datasets, transforms

    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,)),
        transforms.Lambda(lambda x: x.view(-1)),
    ])
    dataset = datasets.MNIST(
        root=os.path.join(os.path.dirname(__file__), ".mnist_data"),
        train=False,
        download=True,
        transform=transform,
    )

    rng = np.random.default_rng(seed)
    indices = rng.choice(len(dataset), size=num_samples, replace=False)
    samples = []
    for index in indices.tolist():
        tensor, label = dataset[index]
        samples.append({
            "index": int(index),
            "label": int(label),
            "input_tensor": tensor.detach().numpy().astype(np.float32).tolist(),
        })
    return samples


def _train_mnist_model(model: MnistMLP, epochs: int = 5) -> dict:
    """
    使用 torchvision MNIST 数据集训练模型。
    如果 torchvision 不可用，使用随机权重（仍具有有意义的计算图）。

    返回训练指标字典。
    """
    try:
        from torchvision import datasets, transforms
        transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.1307,), (0.3081,)),
            transforms.Lambda(lambda x: x.view(-1)),  # 展平为 784
        ])
        train_dataset = datasets.MNIST(
            root=os.path.join(os.path.dirname(__file__), ".mnist_data"),
            train=True, download=True, transform=transform,
        )
        test_dataset = datasets.MNIST(
            root=os.path.join(os.path.dirname(__file__), ".mnist_data"),
            train=False, download=True, transform=transform,
        )
        train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=64, shuffle=True)
        test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=256, shuffle=False)

        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        criterion = nn.CrossEntropyLoss()

        model.train()
        for epoch in range(epochs):
            total_loss = 0
            for data, target in train_loader:
                optimizer.zero_grad()
                output = model(data)
                loss = criterion(output, target)
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
            avg_loss = total_loss / len(train_loader)
            print(f"  Epoch {epoch+1}/{epochs}: loss={avg_loss:.4f}")

        # 测试准确率
        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for data, target in test_loader:
                output = model(data)
                pred = output.argmax(dim=1)
                correct += (pred == target).sum().item()
                total += target.size(0)
        accuracy = correct / total
        print(f"  Test accuracy: {accuracy:.4f} ({correct}/{total})")
        return {"trained": True, "accuracy": accuracy, "epochs": epochs}

    except ImportError:
        print("  [Warning] torchvision not available, using random weights")
        return {"trained": False, "accuracy": None, "epochs": 0}


def split_and_export(
    num_slices: int = 2,
    output_dir: str = "models",
    seed: int = 42,
    train: bool = True,
    train_epochs: int = 3,
) -> dict:
    """
    创建 MNIST MLP 模型，按 num_slices 均匀切分，导出 ONNX。

    返回:
        {
            "slices": [
                {"id": 1, "onnx": path, "data": path, "cal": path},
                ...
            ],
            "intermediates": [numpy_array, ...],
            "input": numpy_array,
            "model_info": {...},
        }
    """
    os.makedirs(output_dir, exist_ok=True)
    torch.manual_seed(seed)

    model = MnistMLP()

    train_info = {"trained": False}
    if train and os.path.exists(MODEL_CACHE_PATH):
        print("[Model] Loading cached MNIST MLP state...")
        payload = load_model_checkpoint(MODEL_CACHE_PATH)
        model.load_state_dict(payload["state_dict"])
        train_info = payload.get("metadata", train_info)
    elif train:
        print("[Model] Training MNIST MLP...")
        train_info = _train_mnist_model(model, epochs=train_epochs)
        save_model_state(model, MODEL_CACHE_DIR, metadata=train_info)
    model.eval()

    slice_models = build_slice_models(model, num_slices)

    calibration_source = "mnist_test_set"
    calibration_sample_count = 20  # 保守: 足够校准但避免极端激活导致 EZKL 溢出
    try:
        calibration_batch = load_representative_mnist_batch(calibration_sample_count, seed=seed)
    except Exception as exc:
        print(f"  [Warning] failed to load representative MNIST calibration data, falling back to random inputs: {exc}")
        calibration_source = "random_fallback"
        calibration_batch = torch.randn(calibration_sample_count, 784)

    dummy_input = calibration_batch[:1].clone()
    calibration_tensors = build_slice_calibration_tensors(model, num_slices, calibration_batch)

    slices_info = []
    intermediates = []
    current_input = dummy_input

    for sid, slice_model in enumerate(slice_models, start=1):
        slice_model.eval()
        calibration_input = calibration_tensors[sid - 1]

        # 导出 ONNX (使用 legacy exporter 确保 EZKL/tract 兼容)
        onnx_path = os.path.join(output_dir, f"slice_{sid}.onnx")
        slice_model.eval()
        torch.onnx.export(
            slice_model,
            (current_input,),
            onnx_path,
            input_names=["input"],
            output_names=["output"],
            opset_version=18,
            do_constant_folding=True,
            dynamo=False,
        )

        # 前向推理
        with torch.no_grad():
            slice_output = slice_model(current_input)

        # 输入数据 JSON (EZKL 格式)
        in_dim = current_input.shape[1]
        data_array = current_input.detach().numpy().reshape([-1]).tolist()
        data_path = os.path.join(output_dir, f"slice_{sid}_input.json")
        with open(data_path, "w") as f:
            json.dump({"input_data": [data_array]}, f)

        # 校准数据 JSON（20 个随机样本）
        cal_array = calibration_input.detach().numpy().reshape([-1]).tolist()
        cal_path = os.path.join(output_dir, f"slice_{sid}_cal.json")
        with open(cal_path, "w") as f:
            json.dump({"input_data": [cal_array]}, f)

        slices_info.append({
            "id": sid,
            "onnx": os.path.abspath(onnx_path),
            "data": os.path.abspath(data_path),
            "cal": os.path.abspath(cal_path),
            "in_dim": in_dim,
            "out_dim": slice_output.shape[1],
        })
        intermediates.append(slice_output.detach().numpy())

        current_input = slice_output

    # 保真度验证
    with torch.no_grad():
        full_output = model(dummy_input)
    final_slice_output = torch.tensor(intermediates[-1])

    diff = final_slice_output - full_output
    fidelity = {
        "l1_distance": float(torch.abs(diff).sum()),
        "l2_distance": float(torch.norm(diff, p=2)),
        "max_abs_error": float(torch.abs(diff).max()),
        "relative_error": float(
            torch.norm(diff, p=2) / (torch.norm(full_output, p=2) + 1e-10)
        ),
    }
    assert torch.allclose(final_slice_output, full_output, atol=1e-5), \
        "切片组合输出与完整模型输出不一致!"

    total_params = sum(p.numel() for p in model.parameters())
    model_info = {
        "name": "MnistMLP",
        "total_params": total_params,
        "architecture": "784 -> 128 -> ReLU -> 64 -> ReLU -> 10",
        "num_slices": num_slices,
        "input_dim": 784,
        "output_dim": 10,
        "fidelity": fidelity,
        "calibration_source": calibration_source,
        "calibration_samples": int(calibration_batch.shape[0]),
        **train_info,
    }

    state_path = save_model_state(model, output_dir, metadata=model_info)
    model_info["state_path"] = os.path.abspath(state_path)

    print(f"[Model] MNIST MLP: {total_params:,} params, {num_slices} slices")
    print(f"[Fidelity] L1={fidelity['l1_distance']:.2e}  "
          f"L2={fidelity['l2_distance']:.2e}  "
          f"MaxErr={fidelity['max_abs_error']:.2e}")

    # 保存模型信息
    info_path = os.path.join(output_dir, "model_info.json")
    with open(info_path, "w") as f:
        json.dump(model_info, f, indent=2)

    return {
        "slices": slices_info,
        "intermediates": intermediates,
        "input": dummy_input.detach().numpy(),
        "model_info": model_info,
        "state_path": os.path.abspath(state_path),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slices", type=int, default=2)
    parser.add_argument("--no-train", action="store_true")
    parser.add_argument("--output-dir", type=str, default="models/mnist")
    args = parser.parse_args()

    result = split_and_export(
        num_slices=args.slices,
        output_dir=args.output_dir,
        train=not args.no_train,
    )
    print(f"\nExported {len(result['slices'])} slices to {args.output_dir}")
    for s in result["slices"]:
        print(f"  Slice {s['id']}: in={s['in_dim']}, out={s['out_dim']}, {s['onnx']}")
