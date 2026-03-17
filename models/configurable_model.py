"""
可配置深度的全连接网络，支持 N 切片导出。

用于阶段 3 实验：测试不同切片粒度（2/4/8 切片）对证明开销的影响。

模型结构 (num_layers=8, hidden_dim=8):
  Input(8) -> [Linear(8,8) -> ReLU] * 7 -> Linear(8,4)

切片方式: 将 num_layers 层均匀分成 num_slices 组，每组导出一个 ONNX。
"""

import json
import os
import math

import torch
import torch.nn as nn
import numpy as np


class ConfigurableModel(nn.Module):
    """可配置层数的全连接网络。"""

    def __init__(self, input_dim: int = 8, hidden_dim: int = 8,
                 output_dim: int = 4, num_layers: int = 8):
        super().__init__()
        layers = []
        in_d = input_dim
        for i in range(num_layers - 1):
            layers.append(nn.Linear(in_d, hidden_dim))
            layers.append(nn.ReLU())
            in_d = hidden_dim
        layers.append(nn.Linear(in_d, output_dim))
        self.layers = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


class SliceModel(nn.Module):
    """一个切片子模型：包含原模型的连续几层。"""

    def __init__(self, layers: nn.Sequential):
        super().__init__()
        self.layers = layers

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


def split_and_export(
    num_slices: int = 2,
    num_layers: int = 8,
    input_dim: int = 8,
    hidden_dim: int = 8,
    output_dim: int = 4,
    output_dir: str = "models",
    seed: int = 42,
) -> dict:
    """
    创建模型，按 num_slices 均匀切分，导出 ONNX 和数据文件。

    返回:
        {
            "slices": [
                {"id": 1, "onnx": path, "data": path, "cal": path},
                ...
            ],
            "intermediates": [tensor, ...],  # 各切片的输出
            "input": tensor,
        }
    """
    os.makedirs(output_dir, exist_ok=True)
    torch.manual_seed(seed)

    model = ConfigurableModel(input_dim, hidden_dim, output_dim, num_layers)
    model.eval()

    # 确定每个切片包含的层
    all_layers = list(model.layers)
    total = len(all_layers)
    slice_sizes = [total // num_slices] * num_slices
    for i in range(total % num_slices):
        slice_sizes[i] += 1

    # 生成输入
    dummy_input = torch.randn(1, input_dim)

    slices_info = []
    intermediates = []
    current_input = dummy_input
    layer_idx = 0

    for sid in range(num_slices):
        # 提取这个切片的层
        end_idx = layer_idx + slice_sizes[sid]
        slice_layers = nn.Sequential(*all_layers[layer_idx:end_idx])
        slice_model = SliceModel(slice_layers)
        slice_model.eval()

        # 导出 ONNX
        onnx_path = os.path.join(output_dir, f"slice_{sid + 1}.onnx")
        in_dim = current_input.shape[1]
        torch.onnx.export(
            slice_model,
            current_input,
            onnx_path,
            input_names=["input"],
            output_names=["output"],
            opset_version=18,
        )

        # 前向推理
        with torch.no_grad():
            slice_output = slice_model(current_input)

        # 输入数据 JSON
        data_array = current_input.detach().numpy().reshape([-1]).tolist()
        data_path = os.path.join(output_dir, f"slice_{sid + 1}_input.json")
        with open(data_path, "w") as f:
            json.dump({"input_data": [data_array]}, f)

        # 校准数据 JSON
        cal_array = torch.randn(20, in_dim).numpy().reshape([-1]).tolist()
        cal_path = os.path.join(output_dir, f"slice_{sid + 1}_cal.json")
        with open(cal_path, "w") as f:
            json.dump({"input_data": [cal_array]}, f)

        slices_info.append({
            "id": sid + 1,
            "onnx": os.path.abspath(onnx_path),
            "data": os.path.abspath(data_path),
            "cal": os.path.abspath(cal_path),
        })
        intermediates.append(slice_output.detach().numpy())

        current_input = slice_output
        layer_idx = end_idx

    # 验证切片组合 == 完整模型
    with torch.no_grad():
        full_output = model(dummy_input)
    final_slice_output = torch.tensor(intermediates[-1])
    assert torch.allclose(final_slice_output, full_output, atol=1e-5), \
        "切片组合输出与完整模型不一致!"

    print(f"[Model] {num_slices} 切片导出完成 ({num_layers} 层)")
    for s in slices_info:
        print(f"  Slice {s['id']}: {s['onnx']}")

    return {
        "slices": slices_info,
        "intermediates": intermediates,
        "input": dummy_input.detach().numpy(),
        "num_slices": num_slices,
    }


if __name__ == "__main__":
    for n in [2, 4, 8]:
        split_and_export(num_slices=n, output_dir=f"models/exp_{n}slices")
