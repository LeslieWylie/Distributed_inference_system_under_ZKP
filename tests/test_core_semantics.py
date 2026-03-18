"""
最小回归测试：验证系统核心安全语义是否在代码层面闭合。

这些测试不需要启动 Worker 或运行 EZKL，只检查代码结构和逻辑路径。
用法：
    python -m pytest tests/test_core_semantics.py -v
    或直接：
    python tests/test_core_semantics.py
"""

import importlib
import inspect
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestWorkerReProveReturnsProof(unittest.TestCase):
    """验证 /re_prove 响应中包含 proof 本体，使 Master 交叉验证可以工作。"""

    def test_re_prove_returns_proof_field(self):
        """检查 /re_prove 的 return 语句中是否包含 'proof' 键。"""
        source = inspect.getsource(
            importlib.import_module("distributed.worker")
        )
        # 找到 /re_prove 函数体中的 return 语句
        in_re_prove = False
        found_proof_in_return = False
        for line in source.split("\n"):
            stripped = line.strip()
            if "def re_prove" in stripped:
                in_re_prove = True
            if in_re_prove and "return {" in stripped:
                # 开始扫描 return 字典
                pass
            if in_re_prove and '"proof"' in stripped and "result" in stripped:
                found_proof_in_return = True
                break
        self.assertTrue(
            found_proof_in_return,
            "/re_prove 的返回值中缺少 'proof' 字段 — Master 交叉验证将无法工作"
        )


class TestWorkerReProveNoFallback(unittest.TestCase):
    """验证 /re_prove 不再 fallback 到 req.input_data。"""

    def test_no_input_data_fallback(self):
        source = inspect.getsource(
            importlib.import_module("distributed.worker")
        )
        # 在 re_prove 函数体中不应出现 "elif req.input_data" 这类 fallback
        in_re_prove = False
        has_fallback = False
        for line in source.split("\n"):
            if "def re_prove" in line:
                in_re_prove = True
            if in_re_prove and "def " in line and "re_prove" not in line:
                break  # 离开 re_prove 函数
            if in_re_prove and "req.input_data" in line and "elif" in line:
                has_fallback = True
        self.assertFalse(
            has_fallback,
            "/re_prove 仍然有 fallback 到 req.input_data 的逻辑"
        )


class TestMasterUsesProofForLinking(unittest.TestCase):
    """验证 Master 的 L2 linking 从 proof.json 的 pretty_public_inputs 提取，
    而非从 witness 文件路径提取。"""

    def test_linking_not_from_witness(self):
        source = inspect.getsource(
            importlib.import_module("distributed.master")
        )
        self.assertNotIn(
            "load_proof_instances_from_witness(witness_path)",
            source,
            "Master 仍然从 witness 文件提取 linking 数据"
        )

    def test_linking_from_proof_ppi(self):
        source = inspect.getsource(
            importlib.import_module("distributed.master")
        )
        self.assertIn(
            "pretty_public_inputs",
            source,
            "Master 没有从 proof 的 pretty_public_inputs 提取 linking 数据"
        )


class TestMasterChallengeOutputCrossCheck(unittest.TestCase):
    """验证 Master 随机挑战中存在真正的输出交叉验证逻辑。"""

    def test_cross_check_compares_outputs(self):
        source = inspect.getsource(
            importlib.import_module("distributed.master")
        )
        # 应该存在 "challenge_output_mismatch" 这类正式失败条件
        self.assertIn(
            "challenge_output_mismatch",
            source,
            "Master 随机挑战中缺少 output mismatch 的正式失败条件"
        )

    def test_cross_check_not_self_compare(self):
        """确保不存在之前的自比较 bug。"""
        source = inspect.getsource(
            importlib.import_module("distributed.master")
        )
        # 旧 bug：output_cross_ok = (original_hash_in == target_data.get("hash_in", ""))
        self.assertNotIn(
            'output_cross_ok = (original_hash_in == (target_data.get("hash_in"',
            source,
            "Master 交叉验证仍然是自比较（旧 bug 未修）"
        )


class TestMasterRequestIdUnique(unittest.TestCase):
    """验证 Master 发送的 request_id 不是固定的 req-{sid} 格式。"""

    def test_request_id_has_timestamp(self):
        source = inspect.getsource(
            importlib.import_module("distributed.master")
        )
        # 应该包含时间戳或 uuid 来保证唯一性
        has_unique_id = (
            "time.time()" in source and "req-" in source
        ) or "uuid" in source
        self.assertTrue(
            has_unique_id,
            "Master request_id 仍然是固定格式，不保证全局唯一"
        )


class TestMasterActualProofFraction(unittest.TestCase):
    """验证 Master summary 中包含 actual_proof_fraction。"""

    def test_actual_proof_fraction_in_summary(self):
        source = inspect.getsource(
            importlib.import_module("distributed.master")
        )
        self.assertIn(
            "actual_proof_fraction",
            source,
            "Master summary 中缺少 actual_proof_fraction 字段"
        )


class TestMasterCacheFindingsInL2(unittest.TestCase):
    """验证 from_cache=False 和 cache_consistent=False 被纳入正式判定。"""

    def test_cache_miss_finding(self):
        source = inspect.getsource(
            importlib.import_module("distributed.master")
        )
        self.assertIn(
            "challenge_cache_miss",
            source,
            "cache miss 未纳入 l2_findings"
        )

    def test_cache_inconsistent_finding(self):
        source = inspect.getsource(
            importlib.import_module("distributed.master")
        )
        self.assertIn(
            "challenge_cache_inconsistent",
            source,
            "cache inconsistent 未纳入 l2_findings"
        )


class TestEdgeCoverSemantics(unittest.TestCase):
    """验证 edge_cover 选择策略的基本正确性。"""

    def test_edge_cover_includes_endpoints(self):
        from distributed.master import _select_verified_slices
        result = _select_verified_slices(8, 0.25, "edge_cover")
        self.assertIn(1, result, "edge_cover 未包含首节点")
        self.assertIn(8, result, "edge_cover 未包含尾节点")

    def test_edge_cover_actual_fraction_higher_than_ratio(self):
        from distributed.master import _select_verified_slices
        result = _select_verified_slices(8, 0.25, "edge_cover")
        actual = len(result) / 8
        self.assertGreaterEqual(
            actual, 0.25,
            f"actual_proof_fraction ({actual}) 低于 verify_ratio (0.25)"
        )

    def test_full_verification(self):
        from distributed.master import _select_verified_slices
        result = _select_verified_slices(4, 1.0, "edge_cover")
        self.assertEqual(result, {1, 2, 3, 4})


class TestWorkerArtifactsIsolation(unittest.TestCase):
    """验证 Worker artifacts 目录按 visibility_mode 隔离。"""

    def test_main_uses_visibility_in_path(self):
        source = inspect.getsource(
            importlib.import_module("distributed.worker")
        )
        self.assertIn(
            "visibility_mode",
            source,
            "Worker main() 中 artifacts 路径未包含 visibility_mode"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
