# 项目计划 — 面向分布式推理的零知识证明框架

> 最后更新: 2026-03-30

---

## 已完成阶段

### Phase 1: 单机验证原型 ✅
- [x] PyTorch 模型定义 + ONNX 导出 + EZKL 编译
- [x] 单机 2 切片 prove + verify 端到端
- [x] Windows 环境兼容 (HOME, PYTHONIOENCODING)

### Phase 2: 分布式推理原型 (v1) ✅
- [x] Master-Worker FastAPI 通信
- [x] 2/4/8 切片配置
- [x] 三层校验 + edge-cover 选择性验证
- [x] 故障注入 + 攻击检测

### Phase 3: Deferred Certification 架构 (v2) ✅
- [x] 执行-证明解耦 (provisional output 先返回)
- [x] 子进程并行 proving
- [x] 独立 Verifier + 链式链接 + 证书签发
- [x] 6 种攻击全部检出

### Phase 4: Prover-Worker 重构 ✅ (2026-03-30)
- [x] MNIST MLP 真实模型 (109K params, 97.24% 准确率)
- [x] Worker 本地 prove (证明分摊)
- [x] 全链路 proof 绑定 (terminal binding)
- [x] 跨主机支持 (0.0.0.0 + workers.json)
- [x] 5/5 E2E 测试通过
- [x] torchvision 安装 + MNIST 数据集训练
- [x] 文档全面更新 (protocol.md, threat_model.md, README)

---

## 当前可执行的实验

| 实验 | 命令 | 状态 |
|------|------|------|
| E2E 正确性 | `refactored_e2e.py --slices 2` | ✅ 5/5 PASS |
| 冒烟测试 | `smoke_test.py` | ✅ 3/3 PASS |
| 本地 Phase A | `e2e_certified.py` | ✅ 可运行 |
| 本地 Phase B | `deferred_certified.py` | ✅ 可运行 |
| 保真度 F1/F2/F3 | `fidelity.py` | ⚠ 需用新模型重跑 |
| 可扩展性 2/4/8 | `scalability.py` | ⚠ 需用新模型重跑 |

---

## 后续工作

### 论文写作 (最优先)
- [ ] 框架设计章节 (Prover-Worker 架构描述)
- [ ] 实验评估章节 (G2/G3/G4 数据表 + 分析)
- [ ] 安全分析章节 (威胁模型 + 证明)
- [ ] 相关工作章节

### 实验补充
- [ ] 多切片 (4/8 片) 可扩展性实验
- [ ] 保真度实验 (float vs quantized)
- [ ] 资源占用实验 (CPU/RAM per Worker)

### 工程优化 (如有时间)
- [ ] Worker 并行化 (多 Worker 同时 prove)
- [ ] polycommit 链接升级 (精确匹配)
- [ ] 服务器部署验证 (跨物理机)
