"""
定义一个简单的两层全连接网络，并提供切片导出功能。

全模型结构:
  Input(5) -> Linear(5,4) -> ReLU -> Linear(4,3)

切片方式:
  Slice 1: Input(5) -> Linear(5,4) -> ReLU   => Output(4)
  Slice 2: Input(4) -> Linear(4,3)            => Output(3)
"""

import json
import os

import torch
import torch.nn as nn


class FullModel(nn.Module):
    """完整的两层全连接网络。"""

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(5, 4)
        self.relu = nn.ReLU()
        self.fc2 = nn.Linear(4, 3)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.relu(self.fc1(x))
        x = self.fc2(x)
        return x


class Slice1(nn.Module):
    """切片 1: Linear(5,4) + ReLU"""

    def __init__(self, fc1: nn.Linear):
        super().__init__()
        self.fc1 = fc1
        self.relu = nn.ReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.relu(self.fc1(x))


class Slice2(nn.Module):
    """切片 2: Linear(4,3)"""

    def __init__(self, fc2: nn.Linear):
        super().__init__()
        self.fc2 = fc2

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc2(x)


def export_slices(output_dir: str = "models") -> dict:
    """
    创建完整模型，切分为两个子模型，导出 ONNX 与输入数据 JSON。

    返回:
        包含路径与中间数据的字典。
    """
    os.makedirs(output_dir, exist_ok=True)

    # 固定随机种子以保证可复现
    torch.manual_seed(42)

    full_model = FullModel()
    full_model.eval()

    # 构建切片，共享权重
    slice1 = Slice1(full_model.fc1)
    slice2 = Slice2(full_model.fc2)
    slice1.eval()
    slice2.eval()

    # 生成随机输入
    dummy_input = torch.randn(1, 5)

    # --- Slice 1 导出 ---
    onnx_path_1 = os.path.join(output_dir, "slice_1.onnx")
    torch.onnx.export(
        slice1,
        dummy_input,
        onnx_path_1,
        input_names=["input"],
        output_names=["output"],
        opset_version=18,
    )

    # Slice 1 前向推理，获取中间结果
    with torch.no_grad():
        mid_output = slice1(dummy_input)

    # Slice 1 的输入数据 JSON — 严格遵循 EZKL 官方格式: {"input_data": [flat_list]}
    data_array_1 = dummy_input.detach().numpy().reshape([-1]).tolist()
    data_1 = {"input_data": [data_array_1]}
    data_path_1 = os.path.join(output_dir, "slice_1_input.json")
    with open(data_path_1, "w") as f:
        json.dump(data_1, f)

    # Slice 1 校准数据 (多个随机样本，用于 calibrate_settings)
    cal_array_1 = torch.randn(20, 5).numpy().reshape([-1]).tolist()
    cal_data_1 = {"input_data": [cal_array_1]}
    cal_path_1 = os.path.join(output_dir, "slice_1_cal.json")
    with open(cal_path_1, "w") as f:
        json.dump(cal_data_1, f)

    # --- Slice 2 导出 ---
    onnx_path_2 = os.path.join(output_dir, "slice_2.onnx")
    torch.onnx.export(
        slice2,
        mid_output,
        onnx_path_2,
        input_names=["input"],
        output_names=["output"],
        opset_version=18,
    )

    # Slice 2 前向推理，获取最终输出
    with torch.no_grad():
        final_output = slice2(mid_output)

    # Slice 2 的输入数据 JSON
    data_array_2 = mid_output.detach().numpy().reshape([-1]).tolist()
    data_2 = {"input_data": [data_array_2]}
    data_path_2 = os.path.join(output_dir, "slice_2_input.json")
    with open(data_path_2, "w") as f:
        json.dump(data_2, f)

    # Slice 2 校准数据
    cal_array_2 = torch.randn(20, 4).numpy().reshape([-1]).tolist()
    cal_data_2 = {"input_data": [cal_array_2]}
    cal_path_2 = os.path.join(output_dir, "slice_2_cal.json")
    with open(cal_path_2, "w") as f:
        json.dump(cal_data_2, f)

    # 完整模型端到端验证
    with torch.no_grad():
        full_output = full_model(dummy_input)

    print(f"[Model] Slice 1 ONNX  -> {onnx_path_1}")
    print(f"[Model] Slice 2 ONNX  -> {onnx_path_2}")
    print(f"[Model] 中间结果 shape: {mid_output.shape}")
    print(f"[Model] 最终输出 shape: {final_output.shape}")

    # 验证切片组合结果与完整模型一致
    assert torch.allclose(final_output, full_output, atol=1e-6), \
        "切片组合输出与完整模型输出不一致!"
    print("[Model] 切片组合验证通过 ✓")

    return {
        "onnx_1": onnx_path_1,
        "onnx_2": onnx_path_2,
        "data_1": data_path_1,
        "data_2": data_path_2,
        "cal_1": cal_path_1,
        "cal_2": cal_path_2,
        "input": dummy_input.detach().numpy(),
        "mid_output": mid_output.detach().numpy(),
        "final_output": final_output.detach().numpy(),
    }


if __name__ == "__main__":
    export_slices()
