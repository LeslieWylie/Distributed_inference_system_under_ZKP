"""
models/mnist_cnn.py — MNIST CNN 模型，用于验证框架对卷积网络的支持。

架构: Conv2d(1→8, 3×3) → ReLU → Flatten → Linear(8*26*26, 10)
参数量: ~54K
切分: 2 片 (Conv+ReLU | Flatten+FC)

与 mnist_model.py (MLP) 一起，说明框架不限于全连接网络。
"""

import json
import os
import sys

import numpy as np
import torch
import torch.nn as nn

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODEL_CACHE_DIR = os.path.join(PROJECT_ROOT, "v2", "artifacts", "model_cache")
MODEL_CACHE_PATH = os.path.join(MODEL_CACHE_DIR, "mnist_cnn_state.pt")


class MnistCNN(nn.Module):
    """简单 CNN: Conv → ReLU → Flatten → FC。"""

    def __init__(self):
        super().__init__()
        self.conv = nn.Conv2d(1, 8, kernel_size=3, padding=0)  # 28→26
        self.relu = nn.ReLU()
        self.flatten = nn.Flatten()
        self.fc = nn.Linear(8 * 26 * 26, 10)

    def forward(self, x):
        # x: (batch, 1, 28, 28)
        x = self.conv(x)
        x = self.relu(x)
        x = self.flatten(x)
        x = self.fc(x)
        return x


class CNNSlice1(nn.Module):
    """切片 1: Conv → ReLU (输入 1×28×28, 输出 8×26×26)。"""
    def __init__(self, conv, relu):
        super().__init__()
        self.conv = conv
        self.relu = relu

    def forward(self, x):
        return self.relu(self.conv(x))


class CNNSlice2(nn.Module):
    """切片 2: Flatten → FC (输入 8×26×26, 输出 10)。"""
    def __init__(self, flatten, fc):
        super().__init__()
        self.flatten = flatten
        self.fc = fc

    def forward(self, x):
        return self.fc(self.flatten(x))


def _train_cnn(model, epochs=3):
    """在 MNIST 上训练 CNN。"""
    from torchvision import datasets, transforms

    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,)),
    ])

    data_dir = os.path.join(PROJECT_ROOT, "data")
    train_set = datasets.MNIST(data_dir, train=True, download=True, transform=transform)
    test_set = datasets.MNIST(data_dir, train=False, transform=transform)
    train_loader = torch.utils.data.DataLoader(train_set, batch_size=64, shuffle=True)
    test_loader = torch.utils.data.DataLoader(test_set, batch_size=256)

    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()

    model.train()
    for epoch in range(epochs):
        total_loss = 0
        for images, labels in train_loader:
            optimizer.zero_grad()
            loss = criterion(model(images), labels)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        print(f"  Epoch {epoch+1}/{epochs}: loss={total_loss/len(train_loader):.4f}")

    model.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for images, labels in test_loader:
            preds = model(images).argmax(dim=1)
            correct += (preds == labels).sum().item()
            total += labels.size(0)

    accuracy = correct / total
    print(f"  Test accuracy: {accuracy:.4f} ({correct}/{total})")
    return {"trained": True, "accuracy": accuracy, "epochs": epochs}


def split_and_export(
    output_dir: str = "models",
    seed: int = 42,
    train: bool = True,
    train_epochs: int = 3,
) -> dict:
    """
    训练 MNIST CNN, 切分为 2 片, 导出 ONNX + 校准数据。

    Slice 1: Conv + ReLU  (input: 1×28×28 = 784, output: 8×26×26 = 5408)
    Slice 2: Flatten + FC (input: 5408, output: 10)
    """
    os.makedirs(output_dir, exist_ok=True)
    torch.manual_seed(seed)

    model = MnistCNN()

    # 训练
    train_info = {"trained": False}
    if train and os.path.exists(MODEL_CACHE_PATH):
        print("[CNN] Loading cached state...")
        state = torch.load(MODEL_CACHE_PATH, weights_only=True)
        model.load_state_dict(state["state_dict"])
        train_info = state.get("metadata", train_info)
    elif train:
        print("[CNN] Training MNIST CNN...")
        train_info = _train_cnn(model, epochs=train_epochs)
        os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
        torch.save({"state_dict": model.state_dict(), "metadata": train_info},
                    MODEL_CACHE_PATH)
    model.eval()

    # 切片
    slice1 = CNNSlice1(model.conv, model.relu)
    slice2 = CNNSlice2(model.flatten, model.fc)
    slice1.eval()
    slice2.eval()

    # 校准数据来自真实 MNIST
    try:
        from torchvision import datasets, transforms
        transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.1307,), (0.3081,)),
        ])
        data_dir = os.path.join(PROJECT_ROOT, "data")
        test_set = datasets.MNIST(data_dir, train=False, download=True, transform=transform)
        cal_images = torch.stack([test_set[i][0] for i in range(20)])
    except Exception:
        cal_images = torch.randn(20, 1, 28, 28)

    # 导出 Slice 1
    dummy1 = cal_images[:1]  # (1, 1, 28, 28)
    onnx1 = os.path.join(output_dir, "slice_1.onnx")
    torch.onnx.export(slice1, dummy1, onnx1,
                       input_names=["input"], output_names=["output"],
                       opset_version=18, do_constant_folding=True, dynamo=False)

    with torch.no_grad():
        slice1_out = slice1(dummy1)  # (1, 8, 26, 26)

    # Slice 1 输入数据
    data1 = dummy1.reshape(-1).tolist()
    with open(os.path.join(output_dir, "slice_1_input.json"), "w") as f:
        json.dump({"input_data": [data1]}, f)

    # Slice 1 校准数据 (20个真实MNIST样本)
    cal1 = cal_images.reshape(-1).tolist()
    with open(os.path.join(output_dir, "slice_1_cal.json"), "w") as f:
        json.dump({"input_data": [cal1]}, f)

    # 导出 Slice 2
    with torch.no_grad():
        cal_slice1_out = slice1(cal_images)  # (20, 8, 26, 26)

    dummy2 = slice1_out  # (1, 8, 26, 26)
    onnx2 = os.path.join(output_dir, "slice_2.onnx")
    torch.onnx.export(slice2, dummy2, onnx2,
                       input_names=["input"], output_names=["output"],
                       opset_version=18, do_constant_folding=True, dynamo=False)

    with torch.no_grad():
        slice2_out = slice2(dummy2)  # (1, 10)

    # Slice 2 输入数据
    data2 = dummy2.reshape(-1).tolist()
    with open(os.path.join(output_dir, "slice_2_input.json"), "w") as f:
        json.dump({"input_data": [data2]}, f)

    # Slice 2 校准数据 (真实上游激活)
    cal2 = cal_slice1_out.reshape(-1).tolist()
    with open(os.path.join(output_dir, "slice_2_cal.json"), "w") as f:
        json.dump({"input_data": [cal2]}, f)

    # 保真度检查
    with torch.no_grad():
        full_out = model(dummy1)
    diff = (full_out - slice2_out).abs()
    fidelity = {
        "l1_distance": float(diff.sum()),
        "max_abs_error": float(diff.max()),
    }
    assert torch.allclose(full_out, slice2_out, atol=1e-5), "CNN slice fidelity check failed"

    total_params = sum(p.numel() for p in model.parameters())
    model_info = {
        "name": "MnistCNN",
        "total_params": total_params,
        "architecture": "Conv2d(1→8, 3×3) → ReLU → Flatten → Linear(5408→10)",
        "num_slices": 2,
        "input_shape": [1, 1, 28, 28],
        "fidelity": fidelity,
        **train_info,
    }

    with open(os.path.join(output_dir, "model_info.json"), "w") as f:
        json.dump(model_info, f, indent=2)

    print(f"[CNN] {total_params:,} params, 2 slices")
    print(f"[CNN] Slice 1: {list(dummy1.shape)} → {list(slice1_out.shape)}")
    print(f"[CNN] Slice 2: {list(dummy2.shape)} → {list(slice2_out.shape)}")

    slices_info = [
        {
            "id": 1,
            "onnx": os.path.abspath(onnx1),
            "data": os.path.abspath(os.path.join(output_dir, "slice_1_input.json")),
            "cal": os.path.abspath(os.path.join(output_dir, "slice_1_cal.json")),
            "in_dim": 784,  # 1*28*28
            "out_dim": 5408,  # 8*26*26
        },
        {
            "id": 2,
            "onnx": os.path.abspath(onnx2),
            "data": os.path.abspath(os.path.join(output_dir, "slice_2_input.json")),
            "cal": os.path.abspath(os.path.join(output_dir, "slice_2_cal.json")),
            "in_dim": 5408,
            "out_dim": 10,
        },
    ]

    return {
        "slices": slices_info,
        "model_info": model_info,
        "input": dummy1.numpy(),
    }


if __name__ == "__main__":
    result = split_and_export(output_dir="models/cnn_export")
    print(f"\nExported {len(result['slices'])} slices")
    for s in result["slices"]:
        print(f"  Slice {s['id']}: {s['onnx']}")
