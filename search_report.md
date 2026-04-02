# **面向分布式零知识机器学习（ZKML）的精确状态链接与隐私保护架构深度研究报告**

## **引言与分布式 ZKML 系统模型解析**

在现代密码学与人工智能交叉的前沿领域，零知识机器学习（Zero-Knowledge Machine Learning, ZKML）正致力于解决计算外包场景下的信任与隐私危机。然而，随着神经网络模型参数量与深度的指数级增长，生成零知识证明（ZKP）所需的计算与内存资源达到了单机硬件的物理极限。将神经网络（例如具有 109K 参数的多层感知机 MNIST MLP，或更复杂的大型语言模型）进行层级切片（Model Slicing），并将其计算图分配至 ![][image1] 个不可信的分布式工作节点（Worker Nodes）进行并行或流水线推理，已成为突破内存墙与算力瓶颈的必然架构演进方向 1。

在使用基于 Halo2/PLONK/KZG 证明系统的 EZKL（版本 23.0.5）作为底层 ZK 编译管线时，分布式架构引入了一个致命的验证难题：**跨分片状态链接的精确性与隐私性**。验证方不仅需要依靠 PLONK 协议验证单一分片内部计算的正确性，还必须从密码学意义上确认相邻切片间的状态一致性（即切片 ![][image2] 的输出激活张量必须严格等于切片 ![][image3] 的输入激活张量）1。

在当前系统的早期实现中，采用的 public 可见性模式面临两个根本性缺陷。首先是**链接精度缺失**。由于 EZKL 针对每个子电路独立执行量化校准，浮点张量被映射到有限域 ![][image4] 时会产生微小的缩放（Scale）漂移。即便通过“三阶段编译管线”将相邻切片的 Scale 强行对齐，并将差异压缩至 1 ULP（Unit in the Last Place，约 1/512），这种浮点近似比较依然不具备密码学意义上的严格匹配属性。攻击者完全可以在不可信的计算节点上注入小于 1 ULP 的对抗性扰动（Adversarial Perturbation），而近似验证机制对此毫无察觉。其次是**数据隐私彻底泄露**。public 模式将网络的所有中间层激活值直接暴露在 Proof 的公开实例（Public Instances）中，任何持有该 Proof JSON 文件的实体均可读取明文数据，这完全违背了零知识证明保护数据隐私的核心初衷 1。

本研究报告将围绕上述架构瓶颈，深入剖析四条核心技术路径的可行性与实现细节：Scale 严格对齐后的哈希（Hashed）可见性模式、基于非盲化 KZG 多项式承诺（Polycommit）的确定性 G1 点比较、面向增量可验证计算（IVC）与折叠方案（Folding Schemes）的分布式优化，以及最新前沿学术成果 NanoZK 的外部承诺链机制。此外，报告还将前瞻性地探讨基于可信执行环境（TEE）、同态加密（FHE）与多方安全计算（MPC）的混合隐私保护架构，以期为新一代分布式 ZKML 框架的设计提供详尽的理论依据与工程指导。

## ---

**第一部分：代数对齐与内部哈希（Hashed Mode）的精确链接理论**

EZKL 框架原生提供的 hashed 可见性模式，其设计初衷即为解决大维度张量公开暴露的隐私问题。该模式利用零知识友好的密码学哈希函数（主要为 Poseidon）在算术电路内部对输入或输出张量进行哈希压缩，并将最终的单一哈希值作为公开实例暴露给验证方 4。

### **1\. 量化代数域与 Scale 对齐的密码学必然性**

在 ZKML 的底层计算中，神经网络的浮点数（Floating-point）运算必须被映射到大素数阶的有限域 ![][image4] 上。原始浮点张量 ![][image5] 转换为有限域元素 ![][image6] 的过程依赖于一个特定的缩放因子 ![][image7]（Scale）：

![][image8]  
在未对齐的分布式 ZKML 切片中，由于每个子网络的数据分布区间不同，EZKL 的校准机制会为切片 ![][image2] 和切片 ![][image3] 生成不同的缩放因子 ![][image9] 和 ![][image10]。即使共享的中间激活张量 ![][image5] 在实数域上完全相等，经过不同的 Scale 缩放和舍入操作后，其在有限域上的向量表示 ![][image11] 与 ![][image12] 也会产生分歧。

密码学哈希函数（如 Poseidon）具有极其严格的雪崩效应（Avalanche Effect）与抗碰撞性（Collision Resistance）。有限域输入向量的任何一个微小比特位差异，都会导致最终计算出的哈希值呈现出完全随机且截然不同的结果 5。这正是此前 hashed 模式测试失败的根本原因。

然而，在系统成功引入“三阶段编译管线”并实现相邻切片的 Scale 强对齐后，数学前提发生了根本性转变。在此前提下，同一个浮点值被两个相邻电路的量化器处理时，使用了完全相同的缩放因子与舍入逻辑，因此在电路底层产生的是**逐比特完全一致的有限域元素序列**。

由于 Poseidon 哈希算法是一个纯粹的确定性代数函数，当输入向量严格相等时：

![][image13]  
因此，**在 Scale 严格对齐的前提下，hashed 模式生成的两个相邻证明中，其公开实例 processed\_outputs\[i\] 和 processed\_inputs\[i+1\] 必须且必定严格相等。**

### **2\. 隐私保护与精确链接的双重实现**

验证方在获取相邻两个 Worker 节点的证明后，只需提取各自 Proof JSON 中的公开实例数组，执行一次简单的字符串或大整数哈希值全等判定（Equality Check）。这一机制完美达成了两大核心诉求：

1. **密码学级别的精确链接**：通过抗碰撞的哈希值比对，彻底摒弃了 1 ULP 的近似容差验证。攻击者无法在不改变输出哈希的前提下篡改任何一个中间激活值，从而堵死了微小数据偏移注入的攻击向量。  
2. **中间状态的绝对隐私**：哈希函数的单向性（One-wayness）确保了持有证明的第三方无法逆向推导出原始的高维激活张量。高达数万维的中间特征被压缩为一个单一的 ![][image4] 元素（通常为 256 位或更高），实现了信息论意义上的明文隐藏 5。

### **3\. Poseidon 哈希在电路内的约束膨胀分析**

尽管 hashed 模式在理论上无懈可击，但其在工程实践中引入了沉重的计算负担。Poseidon 是一种基于海绵结构（Sponge Construction）的代数哈希函数，专门为零知识证明系统（如 PLONK 和 Halo2）设计，以最小化乘法复杂性 6。

在 Halo2 的算术化网格中，Poseidon 的内部状态由若干个有限域元素构成。每一次状态吸收（Absorb）与挤出（Squeeze）都需要经过多轮（Rounds）的非线性变换（S-Box，通常为 ![][image14]）和线性混合层（MDS 矩阵乘法） 6。 对于一个长度为 ![][image15] 的张量（例如包含数万个激活值的多维矩阵），电路必须对其依次进行吸收。这意味着需要在 PLONK 网格中分配大量额外的建议列（Advice Columns）或行来容纳哈希的内部状态轨迹。

根据经验数据与理论推算，每吸收一个元素，Poseidon 在底层电路中可能需要消耗数十到上百行的约束。对于一个具有 ![][image16] 参数的 MLP 模型的中间层输出，引入全张量哈希将极大地增加电路规模。 如果当前的基础推理电路规模处于 logrows \= 16 到 18（即 ![][image17] 到 ![][image18] 行约束）的区间，引入内部 Poseidon 哈希可能导致总约束行数越过 ![][image19] 的阈值，甚至逼近 ![][image20]。logrows 的每一次递增都意味着底层多项式阶数的翻倍，这将直接导致工作节点执行 FFT（快速傅里叶变换）与 MSM（多标量乘法）时的内存占用和计算时间呈指数级上升 7。 因此，尽管 hashed 模式从逻辑上解决了问题，但在算力受限的分布式节点上，其带来的开销膨胀可能抵消模型切片带来的并行加速红利。

| 评估维度 | 传统 public 模式 | 优化后 hashed 模式 |
| :---- | :---- | :---- |
| **链接精度** | 1 ULP 近似匹配 | 绝对密码学精确匹配 |
| **数据隐私性** | 完全暴露（明文 JSON） | 强隐藏（暴露单向哈希值） |
| **电路内开销** | 极低（仅需验证范围与量化） | 极高（需计算数万次非线性 S-Box） |
| **logrows 影响** | 保持基准不变 | 预估增加 1\~2 个量级 |
| **抗扰动攻击** | 弱（极小扰动不可检测） | 强（哈希雪崩效应检测） |

## ---

**第二部分：确定性 KZG 多项式承诺（Polycommit）比较的突破**

鉴于在电路内部执行哈希带来的高昂开销，一种能够将一致性校验移出庞大零知识电路外部的方案显得尤为迫切。EZKL 提供的 polycommit（在命令行中通常配置为 kzgcommit 模式）正是为了解决此类高维数据压缩问题而生。该模式下，中间激活张量不作为公开实例输出，而是直接被编码并折叠进底层的 KZG 多项式承诺（KZG Commitment）中 9。

### **1\. KZG 承诺的盲化机制与 EZKL 的确定性设计**

在深入探讨 Polycommit 方案前，必须理解多项式承诺的隐藏性原理。标准的 Kate-Zaverucha-Goldberg (KZG) 承诺方案允许证明者对多项式 ![][image21] 生成一个紧凑的承诺 ![][image22]，其中 ![][image23] 是不可见的结构化参考字符串（SRS）中的秘密陷门 10。 为了实现零知识属性（Zero-Knowledge Property），即保证承诺不泄露原多项式的任何信息，标准实现中必须引入盲化因子（Blinding Factor）。通常，证明者会引入一个随机的盲化多项式 ![][image24]，并生成：

![][image25]  
由于 ![][image24] 包含密码学安全的真随机数，即使每次输入相同的张量（即相同的核心多项式 ![][image21]），最终生成的承诺 ![][image26] 也会因盲化因子的不同而在椭圆曲线上呈现为完全不同的 ![][image27] 仿射点（Affine Point） 12。如果 EZKL 采用这种盲化机制，那么直接比较两个独立证明中的 KZG 承诺将毫无意义。

然而，**EZKL 在其底层的 Halo2 API 适配中，实施了一项极为精妙的工程改动以支持确定性承诺：引入“非盲化建议列（Unblinded Advice Columns）”** 9。 在将 I/O 设置为 polycommit / kzgcommit 时，EZKL 编译器会将承载输入输出张量的多项式列的盲化因子强制设定为一个恒定常数（例如 1 或 0），从而彻底移除了随机性 9。 这一设计的直接结果是：**多项式承诺过程退化为一个完全确定性的映射函数**。只要相邻切片对同一张量采用了严格对齐的 Scale 并生成了一模一样的有限域向量前像（Pre-image），该向量在相同的 SRS 下必然被映射到椭圆曲线 BN254 上的同一个且绝对唯一的 ![][image27] 仿射点。

### **2\. G1 点的提取与直接内存级比较**

在解决了确定性前提后，随之而来的工程挑战是如何在不依赖本地 Witness 文件的情况下提取并比较这些承诺点。 用户在早期测试中调用 swap\_proof\_commitments() API 失败，原因是该高级 API 的逻辑是在验证方本地读取 Witness JSON 重新计算 KZG 承诺并执行替换。而在分布式 HTTP 架构下，工作节点保留了 Witness，验证方仅收到 Proof 9。

破解这一困境的关键在于深度解析 Halo2 生成的证明抄本（Proof Transcript）的二进制或序列化结构。根据 Halo2 的网格工程（Grid Engineering）规范，为了避免后续复杂的索引操作，编译器被设计为**将所有对建议列（Advice Columns）的 KZG 承诺置于生成的 Proof 字节流的最前端** 8。

* **格式结构**：在采用 BN254 曲线的系统中，一个未压缩的 ![][image27] 仿射点由两个 ![][image28] 坐标 ![][image29] 组成，序列化后通常固定占用 64 字节（每个坐标 32 字节）。这些字节被直接写入最终的 JSON 文件的 proof 字段（通常以十六进制 Hex 或 Base64 编码的形式存在） 9。  
* **验证机制**：验证方在接收到切片 ![][image2] 的证明 ![][image30] 和切片 ![][image3] 的证明 ![][image31] 后，完全无需调用重度依赖 Witness 的 API。由于我们确切知道输出和输入张量对应于未盲化的 polycommit 列，其承诺必然位于证明字节数组的头部 8。验证方只需编写一个轻量级的字节截取脚本，从 ![][image30] 提取前 ![][image1] 个字节（输出张量的 KZG 承诺），从 ![][image31] 同样提取相应的 ![][image1] 个字节（输入张量的 KZG 承诺），执行一次简单的 memcpy 或字符串判定即可。

### **3\. Polycommit 模式的压倒性优势**

相比于在电路内部执行全张量的 Poseidon 哈希，基于未盲化 KZG 承诺直接进行字节验证代表了当前 EZKL 技术栈下的最优化解路径：

1. **零内部约束成本（Zero Circuit Overhead）**：将高维张量映射到 ![][image27] 点的计算（即多标量乘法 MSM）完全发生在外层协议生成阶段，而非算术电路的内部约束中。这意味着 logrows 维持基准水平，极大缓解了分布式工作节点的内存与证明生成时间压力 8。  
2. **强隐私保护（Cryptographic Hiding）**：尽管剥离了盲化因子使得承诺对字典攻击（Dictionary Attack）具有一定的理论脆弱性，但由于高维神经网络中间层激活值的熵（Entropy）极大，攻击者在计算上绝对无法穷举并反演出一个哪怕仅包含几百个浮点数的多维张量 9。因此，在实际应用中，未盲化 KZG 承诺提供了极为可靠的隐私隐藏能力。  
3. **架构解耦（Architectural Decoupling）**：无需在各个不可信 Worker 节点之间传输或同步 Witness 文件，彻底适应基于 FastAPI 的独立进程通信拓扑。仅依靠极小的常量级字节比对，即可建立起坚不可摧的计算链路。

| 技术指标 | Hashed (内部哈希模式) | Polycommit (未盲化 KZG 承诺比较) |
| :---- | :---- | :---- |
| **链接一致性** | 完美精确 | 完美精确 |
| **隐私保护级别** | 绝对安全 (单向散列) | 计算上安全 (防穷举) |
| **对 logrows 的影响** | 显著增加，甚至导致 OOM | 无任何额外影响，保持基准性能 |
| **Witness 文件依赖** | 否 | 否 (通过直接解析 Proof 头字节) |
| **验证端计算复杂度** | 常量级 (哈希值比对) | 常量级 (64字节序列化比对) |

## ---

**第三部分：增量可验证计算（IVC）与折叠方案（Folding Scheme）的分布式重构**

随着计算图日趋庞大，生成 N 个独立的庞大 SNARK 证明并要求验证方执行 N 次配对验证的传统横向扩展模式面临着验证瓶颈。以 Nova、SuperNova 为代表的增量可验证计算（IVC）与折叠方案（Folding Schemes）引入了一种降维打击的新范式：将多个计算步骤（或模型切片）的证明“折叠”为一个尺寸恒定的累加器（Accumulator），从而将验证复杂度从 ![][image32] 降至 ![][image33] 15。

### **1\. Nova/SuperNova 对 ONNX 算子的兼容性突破**

最初的 Nova 论文关注于相同计算步骤（Uniform Computation）的纯代数折叠，而 SuperNova 则扩展支持了具有多种不同指令逻辑的非统一计算虚拟机（Non-uniform zkVM） 17。神经网络中复杂的 ONNX 算子（如卷积 Conv、非线性激活 ReLU、矩阵乘法 MatMul）本身并不是原生的密码学算术。

然而，近期的相关工程与学术成果（如 **ZKTorch** 与 **Jolt Atlas**）已经成功搭建了从 ONNX 到折叠后端的桥梁 19。 以 ZKTorch 为例，该编译器并不会要求 Nova 直接理解 ONNX，而是将 ONNX 计算图重写并编译为 60 余种基础的“密码学块”（Basic Blocks）。例如，将矩阵乘法降解为针对固定矩阵的线性时间验证协议（CQLin），将 ReLU 和 GeLU 降解为查表操作（Lookup Arguments） 19。随后，底层转译器生成适配 Nova 松弛 R1CS（Relaxed R1CS）约束系统的数据格式，从而实现对各类机器学习算子的高效支持。因此，从理论支撑和生态工具演进来看，Nova 系完全能够胜任包含复杂算子的深度学习推理任务。

### **2\. 串行延迟瓶颈与树状并行折叠（Parallel Folding）**

经典的 IVC 理论定义了一个极为严苛的串行依赖结构：为了证明第 ![][image2] 步，证明者不仅需要当前步骤的输入与执行轨迹，还必须\*\*消费（Consume）\*\*第 ![][image34] 步生成的折叠证明累加器 16。 在本科毕设的分布式架构中，如果 Worker 2 必须原地等待 Worker 1 完成其庞大且耗时的折叠证明后才能启动后续折叠，那么整个集群的证明时间将退化为串行求和：![][image35]。这将彻底抹杀分布式系统在吞吐量和延迟上的核心优势 21。

为了破解这一串行死锁，当前的折叠研究前沿（如 ZKTorch 所基于的并行 Mira 累加方案，以及 NeutronNova 等衍生协议）设计了\*\*多重折叠（Multi-folding）与树状并行折叠（Tree-based Parallel Folding）\*\*机制 19。

* **分布式并行生成**：在这一改良架构下，所有 Worker 节点同时接收数据或从 TEE/共享存储中读取状态，并行地为各自的切片生成局部的松弛 R1CS 实例（而不必等待上游证明完成）。  
* **对数级聚合**：随后，系统调度多个协调节点，以二叉树的拓扑结构，将相邻的局部证明对（例如 Worker 1 和 Worker 2 的输出）折叠为一个证明。通过这种分层归并，整体的折叠延迟从 ![][image32] 锐减至 ![][image36]，使得折叠方案在分布式 ZKML 集群中的部署成为现实 24。

### **3\. Halo2 与 Nova 的异构嵌套验证（SNARK-Wrapping）**

当前毕设高度依赖 EZKL 与 Halo2 体系，能否将 Nova 的折叠能力与 Halo2 的特性混用？答案是肯定的，这种技术在业界被称为 **SNARK-Wrapping** 或 Heterogeneous Proof Systems，但其组合方向往往与常规认知相反 15。

基于底层代数结构的限制，通常不是先用 Halo2 为每层生成证明再用 Nova 跨层折叠（因为 Nova 主要针对 R1CS 或特定的算术化系统进行折叠操作，直接折叠 PLONKish 的验证电路极其笨重）。相反，主流的工程范式是：

1. **前端高频折叠**：在内部使用 Nova（或支持 PLONKish 折叠的 Sangria）对网络的所有切片进行高效的并行折叠计算。这一阶段不产生传统的密码学验证证明，只更新一个极其轻量级的“累加器状态”。  
2. **后端单次终结**：当所有的神经网络计算步骤被折叠进单一的累加器实例后，系统在最后一层调用 Halo2 或 Groth16，编写一个特殊的电路来验证这个累加器的最终合法性。由于只执行一次 Halo2 证明生成，整体的开销被极致压缩 24。

虽然由于底层算术化（Plonkish vs R1CS）的深度耦合，要求在现有的 EZKL 基础上独立改写和整合 Nova 后端超出了一个标准毕业设计的短期工程极限，但这种**折叠 \+ 单次终态证明**的混用范式，毫无疑问地勾勒出了下一代大规模分布式可验证计算网络的演进蓝图 26。

## ---

**第四部分：NanoZK——基于层级解耦与外部承诺链的 ZKML 新范式**

发表于顶级会议（如 ICLR 2026）的学术研究 NanoZK 专门针对大型语言模型及 Transformer 架构提出了一种全新的层级零知识证明（Layerwise Zero-Knowledge Proofs）验证框架 27。其核心思想高度契合分布式推理诉求，并以极其精妙的设计回避了 EZKL 在跨层链接与规模膨胀上的痛点 29。

### **1\. 外部哈希链接：将一致性验证移出电路**

NanoZK 的架构明确抛弃了在零知识电路内部计算哈希以实施状态链接的沉重做法。

* **机制解构**：对于给定的网络层 ![][image37]，NanoZK 独立生成该层的有效性证明 ![][image38]。在证明生成阶段，系统在**电路外部**（即明文运行时环境中）利用标准的 SHA-256 函数分别计算该层输入张量 ![][image39] 与输出张量 ![][image40] 的哈希值，并将这两个哈希值（作为密码学承诺 ![][image41] 和 ![][image42]）直接硬编码进证明 ![][image38] 的公开实例中 29。  
* **外部链接**：当所有的单层证明提交给验证方（例如部署在区块链上的智能合约或中心化的 Verifier）时，验证方不仅验证每片 ![][image38] 的逻辑正确性，还会执行一个简单至极的明文等式校验：检查上游层输出的 SHA-256 哈希是否与下游层输入的 SHA-256 哈希完全相同（即 ![][image43]） 29。 这种\*\*外部承诺链（External Commitment Chain）\*\*设计通过将哈希算子剥离出零知识电路，将原本需要消耗数十万 logrows 的重度计算转换为了验证方执行的极其廉价的 ![][image33] 明文比较，从根本上实现了性能的飞跃。

### **2\. 彻底根除跨层 Scale 漂移：全量化查表机制**

如果像 EZKL 早期版本那样存在 Scale 不一致或累积误差，即便移到了电路外，SHA-256 哈希也依然会不匹配。NanoZK 是如何确保上下游的张量在比特位上能够严丝合缝地对齐的？ 关键在于其对非算术运算（如 Softmax, GELU, LayerNorm 等）处理方式的颠覆性创新 29。

* 现有的框架（包括某些 EZKL 设置和 ZKML 论文）通常使用多项式逼近（Polynomial Approximations，例如 5 阶或更高阶多项式拟合）来在有限域上模拟非线性激活函数。这不可避免地会在每一层引入浮点舍入与逼近误差。随着网络加深，这种微小的误差（Error Accumulation）像滚雪球般放大，导致层与层之间的输出/输入绝对无法在有限域序列上精确匹配 29。  
* **NanoZK 的零误差解法**：它完全摈弃了运行时多项式计算，采用基于 Plookup 样式的预计算查找表（Lookup Tables）。它强行将所有的输入截断并量化为 16 位定点数（16-bit fixed-point），并将整个非线性函数的映射结果预先写入离线查找表中。在零知识证明时，电路只需做一件事——证明当前节点的输入输出映射对“存在于这张合法的查找表中” 29。 这一机制将误差严格限制在了最初的数据量化截断边界上，而计算内部**没有任何累积漂移**。正因如此，NanoZK 能够自豪地宣告在 WikiText-2 等标准基准测试中实现了 **0.00% 的相对困惑度下降（Perplexity Degradation）** 28。输入输出在整个网络中被像齿轮般精确咬合，确保了外部哈希链的匹配永不失效。

### **3\. ZKP 后端与性能的代差级对比**

虽然 NanoZK 在论文公开资料中未全盘开源其底层密码学后端的每一行代码，但其宣称采用了高度定制化的零知识协议组合，并针对层级并行验证做出了架构级优化 27。

* **证明尺寸与验证速度**：在针对具有 ![][image44] 维度的深度模型进行测试时，NanoZK 为每一层生成的证明大小被固定在极小的常量级别（约 5.5KB，其中注意力机制部分 2.1KB，多层感知机部分 3.5KB），并且单层验证时间压缩至惊人的 24 毫秒 27。  
* **对比 EZKL**：该文献中的实验基准明确指出，在同等参数量级与相同的安全强度假设（健全性误差 ![][image45]）下，NanoZK 的证明尺寸比 EZKL 小了 70 倍，生成证明的速度（Prover Time）快了 5.7 倍至 52 倍不等。在处理极为庞大的网络时，EZKL 面临的灾难性内存压力（OOM）被 NanoZK 优美的切片隔离架构所消解 27。

| 架构特性 | EZKL (当前部署架构) | NanoZK (ICLR 2026 前沿成果) |
| :---- | :---- | :---- |
| **状态链接机制** | 内部 Poseidon 或 Polycommit | 外部 SHA-256 承诺链验证 |
| **非线性激活函数处理** | 近似多项式拟合或内部查表计算 | 严格的 16-bit Plookup 预计算查表 |
| **跨层规模（Scale）漂移** | 存在（需强制管线对齐，否则失效） | 零漂移（基于定点离散与精确查表） |
| **单层证明体积** | 随网络复杂度和张量维度膨胀 | 固定常数级别（\~5.5KB） |
| **分布式并行适配性** | 需要复杂的见证同步与张量重组 | 原生适配，层级完全解耦 |

## ---

**第五部分：突破密码学边界的替代隐私保护路线（展望）**

当我们在零知识电路的局限性中反复权衡开销（如内部哈希的爆炸性计算）与隐私（如多项式承诺的隐藏性程度）时，工业界与学术界已经在探索跨越纯数学逻辑边界的混合式解决方案。以下三种架构路线，虽然无需在当前的本科毕业设计工程中强行实现，但为论文的“未来工作”与“展望”章节提供了极具深度的论据支撑。

### **1\. TEE \+ ZKP 混合计算完整性架构**

针对大规模神经网络参数量极大、在纯零知识电路中执行矩阵乘法导致算力崩溃的客观事实，混合硬件架构成为一种务实的工业级选择 33。

* **机制原理**：将网络的核心推理计算和敏感的中间状态驻留在基于硬件加密的可信执行环境（Trusted Execution Environment, TEE，例如 Intel SGX, AMD TDX, 或 ARM TrustZone）中进行。TEE 的内存隔离机制（Enclave）保证了即使是拥有宿主机最高权限的云服务商或恶意 Worker 节点，也无法探取激活张量的明文数据。  
* **与 ZKP 的交汇**：当某一切片在 TEE 内执行完毕后，硬件不仅输出下一步的输入张量，还会生成带有硬件芯片私钥签名的远程认证（Remote Attestation）证明。随后，系统只需用一个极小型的 ZK 证明来包装和验证这个 TEE 签名的合法性（即证明“这段数据确实是由授权的隔离区在运行指定模型后产生”），从而彻底绕过了庞大矩阵运算的算术化瓶颈。诸如 Ritual 等前沿去中心化 AI 算力网络正在积极布局此类 TeeML 的融合架构 33。

### **2\. 同态加密（HE） \+ ZKP 的全密文域推理**

当数据隐私被提升至极端安全级别（例如医疗影像分析或金融信贷风控）时，同态加密（Homomorphic Encryption, HE）提供了一种能够在不解密状态下直接操作数据的密码学基元 36。

* **机制原理**：验证方或数据拥有者在本地对推理输入执行全同态加密，并发送给分布式 Worker。Worker 节点全程处理的是同态密文，无论是线性矩阵乘法还是通过同态多项式逼近实现的非线性算子。切片间的状态传输也是密文传输。  
* **与 ZKP 的交汇**：由于同态加密本身不具备“计算正确性”的可验证属性（恶意 Worker 节点可能随意向密文中添加噪声或替换算子），因此需要在同态密文运算之上套用一层 ZK 证明，用来验证该同态运算是严格按照预定模型逻辑执行的。虽然这被称为算力黑洞，但在 RiseFL 等最新的隐私保护联邦学习框架中，这种混合承诺方案已显示出解决极高敏感场景的潜力 36。

### **3\. 基于秘密分享（Secret Sharing）的多方安全计算（MPC）**

为了打破单一中心验证方对数据链条可能存在的窥视，多方安全计算（MPC）通过密码学协议分散了信任 37。

* **机制原理**：切片的中间层激活输出不再以完整矩阵的形式出现，而是通过 Shamir 秘密分享等算法被碎裂为若干个随机分片（Shares），并分别传输给不同的验证者或下游的计算节点。  
* **去中心化隐私**：在这一体系下，任何单个或未达到勾结阈值（Threshold）的工作节点，看到的数据与完全的白噪声无异。节点们通过交互式的多方安全计算协议共同完成下一步的乘法与加法推理，或者联合完成对零知识证明的聚合验证（Collaborative zk-SNARKs） 39。这从系统拓扑层面消灭了单一信任瓶颈，达成了数据可用不可见的终极愿景。

## ---

**结论与工程实现建议**

本报告从代数底层原理到分布式系统架构，全方位剖析了面向切片化分布式推理的 ZKML 框架在状态链接与隐私保护上的核心技术争议。基于前沿理论分析与 EZKL 机制的深入拆解，得出以下综合结论与项目实施建议：

1. **Hashed 模式在规模对齐后理论绝对成立，但工程代价高昂**：在三阶段编译管线消解了 Scale 漂移误差后，中间张量的底层有限域映射实现了严格等价。这意味着基于 Poseidon 算法的哈希输出必定绝对一致，从密码学层面彻底堵住了注入小于 1 ULP 扰动的漏洞，并实现了强效隐私隐藏。然而，基于 Sponge 结构的内部哈希不可避免地将在 Halo2 电路内引入庞大的附加非线性约束。对于高维张量而言，这将使得原本已处于极限边缘的 logrows 进一步跃升，引发分布式工作节点的内存溢出（OOM）及显著的证明延迟。因此，在算力受限的毕设环境中，不建议将其作为首选部署方案。  
2. **Polycommit 字节直比对是最优解路径**： EZKL 对 KZG polycommit 采用了恒定非盲化因子这一精妙的工程设计，使得原本随机的多项式承诺蜕变为一种对张量前像具有唯一确定性的单向哈希映射。鉴于此，系统可以完全剥离导致失败的 swap\_proof\_commitments() API。  
   **实施建议**：由于这些 64 字节的 ![][image27] 仿射点被确定性地序列化于 Proof JSON 字节流的头部，可以直接编写轻量级的后端脚本，在验证方收到两个 Proof JSON 后，截取对应的字节段进行严格的明文全等比较。这种做法以**零额外电路开销**达成了完美的密码学精确链接，并凭借高维数据的抗穷举特性提供了极高的数据隐私安全性。这是当前技术栈下侵入性最小、性价比最高且最易于在短期内落地的优雅架构。  
3. **架构的长期演化指向外部链接与并行折叠**：传统单一、串行、内聚式的 ZKML 证明正面临死胡同。如 NanoZK 所示的“电路外执行 SHA-256 承诺链比对搭配 16 位严格定点查表”技术，以及基于 Nova 变体的多重并行折叠树方案（Parallel Folding Trees），昭示了层级解耦、低延迟聚合验证的大势所趋。结合 TEE 或 MPC 的混合信任模型，这些前沿思想为论文后续的系统展望和演进规划提供了极为厚重的学术与工程论据支撑。

#### **引用的著作**

1. ray-project/distributed-zkml \- GitHub, 访问时间为 三月 31, 2026， [https://github.com/ray-project/distributed-zkml](https://github.com/ray-project/distributed-zkml)  
2. DSperse: A Framework for Targeted Verification in Zero-Knowledge Machine Learning, 访问时间为 三月 31, 2026， [https://arxiv.org/html/2508.06972v3](https://arxiv.org/html/2508.06972v3)  
3. Model Complexity Reduction for ZKML Healthcare Applications: Privacy Protection and Inference Optimization for ZKML Applications—A Reference Implementation With Synthetic ICHOM Dataset \- PMC, 访问时间为 三月 31, 2026， [https://pmc.ncbi.nlm.nih.gov/articles/PMC11624495/](https://pmc.ncbi.nlm.nih.gov/articles/PMC11624495/)  
4. How to extract the poseidon hash used by EZKL · zkonduit ezkl · Discussion \#910 \- GitHub, 访问时间为 三月 31, 2026， [https://github.com/zkonduit/ezkl/discussions/910](https://github.com/zkonduit/ezkl/discussions/910)  
5. A New Hash Function for Zero-Knowledge Proof Systems by Grassi et al.. It presents a new cryptographic hash function, Poseidon, optimized for use in practical computational integrity proof systems like SNARKs, STARKs, and Bulletproofs. The paper describes the design, implementation, and security analysis of Poseidon, highlighting its efficiency in zero-knowledge (ZK) proof systems, particularly in scenarios requiring the proving of preimage knowledge under a hash function. The authors focus on the hash function's modular framework, efficiency in large prime fields, and its comparative advantage over existing functions like SHA-256 and Pedersen Hash in terms of computational cost. Additionally, the paper details the cryptanalysis of Poseidon, emphasizing its resilience to various types of attacks, and demonstrates its practical applications in \- Poseidon Journal, 访问时间为 三月 31, 2026， [https://autoparallel.github.io/poseidon/index.html](https://autoparallel.github.io/poseidon/index.html)  
6. Poseidon \- ICICLE Docs, 访问时间为 三月 31, 2026， [https://dev.ingonyama.com/1.10.1/icicle/primitives/poseidon](https://dev.ingonyama.com/1.10.1/icicle/primitives/poseidon)  
7. Poseidon: A New Hash Function for Zero-Knowledge Proof Systems \- USENIX, 访问时间为 三月 31, 2026， [https://www.usenix.org/conference/usenixsecurity21/presentation/grassi](https://www.usenix.org/conference/usenixsecurity21/presentation/grassi)  
8. Splitting and Parallelizing Proofs \- EZKL Blog, 访问时间为 三月 31, 2026， [https://blog.ezkl.xyz/post/splitting/](https://blog.ezkl.xyz/post/splitting/)  
9. Removing Additional Commitment Cost \- EZKL Blog, 访问时间为 三月 31, 2026， [https://blog.ezkl.xyz/post/commits/](https://blog.ezkl.xyz/post/commits/)  
10. KZG polynomial commitments \- Dankrad Feist, 访问时间为 三月 31, 2026， [https://dankradfeist.de/ethereum/2020/06/16/kate-polynomial-commitments.html](https://dankradfeist.de/ethereum/2020/06/16/kate-polynomial-commitments.html)  
11. ️ Polynomial commitment schemes \- Math & Engineering, 访问时间为 三月 31, 2026， [https://xn--2-umb.com/22/polynomial-commitment/](https://xn--2-umb.com/22/polynomial-commitment/)  
12. Increase blinding level for KZG · Issue \#130 · ZK-Garage/plonk \- GitHub, 访问时间为 三月 31, 2026， [https://github.com/ZK-Garage/plonk/issues/130](https://github.com/ZK-Garage/plonk/issues/130)  
13. proof\_splitting.ipynb \- Colab \- Google, 访问时间为 三月 31, 2026， [https://colab.research.google.com/github/zkonduit/ezkl/blob/main/examples/notebooks/proof\_splitting.ipynb](https://colab.research.google.com/github/zkonduit/ezkl/blob/main/examples/notebooks/proof_splitting.ipynb)  
14. kzg-ezkl \- Colab \- Google, 访问时间为 三月 31, 2026， [https://colab.research.google.com/github/zkonduit/ezkl/blob/main/examples/notebooks/kzg\_vis.ipynb](https://colab.research.google.com/github/zkonduit/ezkl/blob/main/examples/notebooks/kzg_vis.ipynb)  
15. Intro to Nova & ZK folding schemes: The nuts and bolts of folding | Smart contract audits from Veridise, 访问时间为 三月 31, 2026， [https://veridise.com/blog/learn-blockchain/intro-to-nova-zk-folding-schemes-the-nuts-and-bolts-of-folding/](https://veridise.com/blog/learn-blockchain/intro-to-nova-zk-folding-schemes-the-nuts-and-bolts-of-folding/)  
16. Nova and Folding (1/2). This is part 3 of a blog series in… | by Veridise \- Medium, 访问时间为 三月 31, 2026， [https://medium.com/veridise/intro-to-nova-zk-folding-schemes-folding-and-nova-6cd61ffcb454](https://medium.com/veridise/intro-to-nova-zk-folding-schemes-folding-and-nova-6cd61ffcb454)  
17. zkStudyClub: Supernova (Srinath Setty \- MS Research) \- YouTube, 访问时间为 三月 31, 2026， [https://www.youtube.com/watch?v=ilrvqajkrYY](https://www.youtube.com/watch?v=ilrvqajkrYY)  
18. Sin7y Tech Review (34): Is SuperNova's Folding Scheme the Endgame for ZK? \- HackMD, 访问时间为 三月 31, 2026， [https://hackmd.io/@sin7y/HJLWGCdBn](https://hackmd.io/@sin7y/HJLWGCdBn)  
19. ZKTorch: Open-Sourcing the First Universal ZKML Compiler for Real-World AI \- Medium, 访问时间为 三月 31, 2026， [https://medium.com/@danieldkang/zktorch-open-sourcing-the-first-universal-zkml-compiler-for-real-world-ai-18446b6a1e86](https://medium.com/@danieldkang/zktorch-open-sourcing-the-first-universal-zkml-compiler-for-real-world-ai-18446b6a1e86)  
20. MicroNova: Folding-Based Arguments with Efficient (On-Chain) Verification \- ResearchGate, 访问时间为 三月 31, 2026， [https://www.researchgate.net/publication/392745631\_MicroNova\_Folding-Based\_Arguments\_with\_Efficient\_On-Chain\_Verification](https://www.researchgate.net/publication/392745631_MicroNova_Folding-Based_Arguments_with_Efficient_On-Chain_Verification)  
21. Performal: Formal Verification of Latency Properties for Distributed Systems \- DSpace@MIT, 访问时间为 三月 31, 2026， [https://dspace.mit.edu/bitstream/handle/1721.1/151092/3591235.pdf?sequence=1\&isAllowed=y](https://dspace.mit.edu/bitstream/handle/1721.1/151092/3591235.pdf?sequence=1&isAllowed=y)  
22. Adaptive Parallel Scheduling Scheme for Smart Contract \- MDPI, 访问时间为 三月 31, 2026， [https://www.mdpi.com/2227-7390/12/9/1347](https://www.mdpi.com/2227-7390/12/9/1347)  
23. SuperNova: Revolutionizing Cryptographic Proof Systems for Program Executions, 访问时间为 三月 31, 2026， [https://zkplabs.network/blog/supernova-revolutionizing-cryptographic-proof-systems-for-program-executions](https://zkplabs.network/blog/supernova-revolutionizing-cryptographic-proof-systems-for-program-executions)  
24. The Definitive Guide to ZKML (2025). \- ICME, 访问时间为 三月 31, 2026， [https://blog.icme.io/the-definitive-guide-to-zkml-2025/](https://blog.icme.io/the-definitive-guide-to-zkml-2025/)  
25. Parallel proving and next generation decentralized proving networks \- ICME, 访问时间为 三月 31, 2026， [https://blog.icme.io/parallel-proving-and-next-generation-decentralized-prover-marketplaces/](https://blog.icme.io/parallel-proving-and-next-generation-decentralized-prover-marketplaces/)  
26. Trustless Agents — with zkML \- ICME, 访问时间为 三月 31, 2026， [https://blog.icme.io/trustless-agents-with-zkml/](https://blog.icme.io/trustless-agents-with-zkml/)  
27. \[2603.18046\] NANOZK: Layerwise Zero-Knowledge Proofs for Verifiable Large Language Model Inference \- arXiv, 访问时间为 三月 31, 2026， [https://arxiv.org/abs/2603.18046](https://arxiv.org/abs/2603.18046)  
28. Pointer Sentinel Mixture Models | Request PDF \- ResearchGate, 访问时间为 三月 31, 2026， [https://www.researchgate.net/publication/319770231\_Pointer\_Sentinel\_Mixture\_Models](https://www.researchgate.net/publication/319770231_Pointer_Sentinel_Mixture_Models)  
29. NANOZK: Layerwise Zero-Knowledge Proofs for Verifiable Large Language Model Inference \- arXiv, 访问时间为 三月 31, 2026， [https://arxiv.org/html/2603.18046v1](https://arxiv.org/html/2603.18046v1)  
30. NANOZK: Layerwise Zero-Knowledge Proofs for Verifiable Large Language Model Inference | 每日论文, 访问时间为 三月 31, 2026， [https://paper.dou.ac/p/2603.18046v1](https://paper.dou.ac/p/2603.18046v1)  
31. Sub-linear Size Pairing-based Non-interactive Zero-Knowledge Arguments. \- ResearchGate, 访问时间为 三月 31, 2026， [https://www.researchgate.net/publication/220336190\_Sub-linear\_Size\_Pairing-based\_Non-interactive\_Zero-Knowledge\_Arguments](https://www.researchgate.net/publication/220336190_Sub-linear_Size_Pairing-based_Non-interactive_Zero-Knowledge_Arguments)  
32. NANOZK: Layerwise Zero-Knowledge Proofs for Verifiable Large Language Model Inference \- ResearchGate, 访问时间为 三月 31, 2026， [https://www.researchgate.net/publication/402859538\_NANOZK\_Layerwise\_Zero-Knowledge\_Proofs\_for\_Verifiable\_Large\_Language\_Model\_Inference](https://www.researchgate.net/publication/402859538_NANOZK_Layerwise_Zero-Knowledge_Proofs_for_Verifiable_Large_Language_Model_Inference)  
33. State of Verifiable Inference & Future Directions \- Equilibrium Labs, 访问时间为 三月 31, 2026， [https://equilibrium.co/writing/state-of-verifiable-inference](https://equilibrium.co/writing/state-of-verifiable-inference)  
34. Decentralized AI (2024–2025): Verifiable Compute, TEEs, and Federated Optimization, 访问时间为 三月 31, 2026， [https://lightcapai.medium.com/decentralized-ai-systems-cryptographic-infrastructures-verifiable-computation-and-federated-6355d3dea7f9](https://lightcapai.medium.com/decentralized-ai-systems-cryptographic-infrastructures-verifiable-computation-and-federated-6355d3dea7f9)  
35. Nexus: A New Standard for Decentralized Autonomy \- Talus Network, 访问时间为 三月 31, 2026， [https://talus.network/whitepaper](https://talus.network/whitepaper)  
36. Secure and Verifiable Data Collaboration with Low-Cost Zero-Knowledge Proofs \- VLDB Endowment, 访问时间为 三月 31, 2026， [https://www.vldb.org/pvldb/vol17/p2321-xiao.pdf](https://www.vldb.org/pvldb/vol17/p2321-xiao.pdf)  
37. Privacy-Preserving Machine Learning Techniques: Cryptographic Approaches, Challenges, and Future Directions \- MDPI, 访问时间为 三月 31, 2026， [https://www.mdpi.com/2076-3417/16/1/277](https://www.mdpi.com/2076-3417/16/1/277)  
38. A Survey of Zero-Knowledge Proof Based Verifiable Machine Learning \- arXiv, 访问时间为 三月 31, 2026， [https://arxiv.org/pdf/2502.18535](https://arxiv.org/pdf/2502.18535)  
39. Scalable Collaborative zk-SNARK and Its Application to Fully Distributed Proof Delegation | USENIX, 访问时间为 三月 31, 2026， [https://www.usenix.org/system/files/usenixsecurity25-liu-xuanming.pdf](https://www.usenix.org/system/files/usenixsecurity25-liu-xuanming.pdf)

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABIAAAAZCAYAAAA8CX6UAAABHElEQVR4Xu2SMUtCURiGv8AG0QixXRqbgpwKNxtsaQuEfkAQ/g3XKGlqKAhCUEpaGvoB0dgfaAn/RATZ83butXOP59os+MAD3vN+9/N8n5otWXxO8Q0/8B3PsOzlVbzDK88DL5+yh0f4ghP8xpaXr2ITr5PsGGtenmEbR+YaqnicjX9Rg154GKKiLq7jq7mbhaiJ6uZyjofJ5465RmqasoaP5m6ei4oecCt53jQ3mr8nZU9Y8c5mUJEaqaFYwUvsYyE50221bGW5aDfh7EVz42nMEt5jPVMRYWjx2dVIi9/BZ9zIxrNoibHZtSf9FS7wxv4ZS9xavEh70q3kSZBFySvaxU/8wkaQTdk3d+30G/WCXgxp48D+fr0lC88Prak0EMaMmPcAAAAASUVORK5CYII=>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAcAAAAZCAYAAAD9jjQ4AAAAiUlEQVR4XmNgGOpAEIgZ0QVBgBWI/wOxJ7oEDHCgCxAEzEAsjC4IAipAfBqIXwOxPLIEDxCvAGIzIDYF4iJkSZDLQIKcQLwDiBWRJWFAB4jfM+DwYwMQ/0MXBAF+ID4BxNeBWBmIA5ElbYD4NxBPAuJSII5AlpRhgOjaD8QmyBIwAAo2SXTBQQUAlhoPwfFnHOEAAAAASUVORK5CYII=>

[image3]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACgAAAAZCAYAAABD2GxlAAABG0lEQVR4Xu2Vv2oCQRCHJ5iAYsCgIAhp7H0ESSVICquUgo2NhZ2Fffo8QgpL8QXs7CTgW4h1grWF+Q3DgTe33u6drIjsB18zc39+3M7uEQUCgfviAVbhk25cCX53XRdPmcAj/NINz5ThEO7hVPVi8Jer6KIjPV1w5AUW4Izk46QGvIS8ASOsAXn+irqYAa8BX+GKZAa6queKt4AluIAfcAfnJDORFW8B3+EbyRIfYDveTvAMGwYHhhrL17twNmBEE/6QfRd/wq3BX0ONHcltVqwB+5TSdMDbEjM8h0vYIlkSPjT58MyC14Ac7I/kmOEZ/IaPsSvseA1Yg2u4gWOSDZOVvAE5EAfTJp7HvznXHWci8cBbo6MLgcA98A+CFjwJ139HGwAAAABJRU5ErkJggg==>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABQAAAAZCAYAAAAxFw7TAAABCklEQVR4XmNgGAUjAzQB8SMi8F0gtoXqIQi4gXgeELMA8Uwg1oGKswJxHRAfAOJsIPaFihMFJgMxDxAvBGJJJHGQoSDQykAlA42htBUUEw2wGQjiwwwkGaAbyAjEyQxUMHArEK8E4jtAPIOBCgYie1mBgcoGggDVDRRCYpMEcBmIDpiBuBKI+YA4GIgnAbEJigooINZAFwaImktArAfEwkC8C1lBLRC/AuJ/DJA8+wuIv0DZoGSDDdwCYn0oWwmInyPJkQVOAbEglO0HxM+Q5MgCSxkgCR+Uz0FptgxVmjQACueDQFwMxNOBuIoBUYCQBTQZIGlTAIg50ORIBvFAfAGI5wMxF5ocbQAAOks1CZg00pUAAAAASUVORK5CYII=>

[image5]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACwAAAAZCAYAAABKM8wfAAACM0lEQVR4Xu2WTYhOURjH/zLkq3wtZGNsfZUipZGFDRvUkHyUjayUBTOkLKYkUywoCylkIVMUJVlYslAWWEiRBbESylbh/+s5p/e+Z+Z9a5gxd3H/9eve85xzb//3uc95zis1atSo0Xh0wNw0VyscMzPNLHO+mDsVj02d1pjd5q75bX6lGJphdpoPaY4fl+emXD3mtsIY96jXPDe78qK6aYfC8Goz3zw2J8y06qI6aYl5o6jhK4p6nd62ooY6q6hjDFPDtddG89NsKifqqA3mraKOKQ1KpJOOmxvmk6ItfjPr2laMT5TenDLYTXSEpwrTmMD03rYVLS1UdI/tCpPrzcd0/7daYQ6VwU6iIzxUq31dVhgmxuFRaql5qZZBrv9qmOQMlsGxxMZig1Xb1zbFxvuuaHFVzTb7FV/hpKJnj2V42IyYw4pn0GZzydwzR83cFN9jvphbikOMbHfUBY3uCGT8mSLLQ5U4otY47T6bg4ofWRreahaluX3mmsL0e0XLJE7LfJDii80dc0bx9eapELWHmUzZFX4U8/C1Ms9LOa55D6oaznNZeczXWKnI4n1Flt+lecTGZc2kqJthOguZzMprySzxvhRnLftgWRpnw5TJQIpNmLoZ5nNfTFfEHnhtVikM8vlRv9rfgWEOLcphKMUmRMsV3YMyoY7XmheKTcqVMVmiR5PVJ2YLD1qvzHVzxJxL40dpDuOMTyv+Ff530Q7JZs404p7YqE2VxDMLymCjydYf5J92Z5wu86MAAAAASUVORK5CYII=>

[image6]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACQAAAAZCAYAAABZ5IzrAAAB3klEQVR4Xu2WPShGURjHH/mIDGSRKKOFkK8UJkL5GCjMisVkYLC8KQMWiYgok8JgI0yyGZiQMrAqNjH4+P97zuk997qv1+C9Ge6/fnXPec577v+e5znnvCKRIkUKV81gD6w5LIMyE68AG06MzyMmlhIVg34wBd7BJ+gDuU58HryAVTAISk0s5eLLaKjc6aO5c6cdqgrBNYiZdis4kxBXJEiLoqZ6wC2o94bDV6No2h7lH5ihckQNcedl+GKhKw/sgBtRUzFP1Ktq0CW6kt2+GMXduQKenL4skOm0fxQHcltzEtYRDbGWWOhBmgHpoE7iR4RfNeDBaQ+ZvqSimZioIT6zjt7AB+iID/Noy98RINcQ08/5kxpKAxPgWDRlFOvoUHSVtuV7LTWAU9ALCkxfLdgELaJzUtYQx8yBVzAp+ruEGgAHEp/Yyh6Sz+I9KKl8sCuaTq4oD88j0Q+i0TYzzhrimEpwBdoloAw6RV/mMu7E9wPiJ+KtFZuyInAvasjed8Mm5qaM4y5NX0pkDfFr78CoE7NKZIgX9p/LGmK9LIgeF0wNDY6ZmN8QU9Zk+v9M2WBa9PbnXxHWE1O5LlqLPDZKQBW4EN2pfObGWBKtvVkJSTRrd1gi8QM4LtKv9QXR8F97nHqINAAAAABJRU5ErkJggg==>

[image7]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA0AAAAaCAYAAABsONZfAAABDElEQVR4Xu3TMS8EQRjG8VeOy4lziEtUGrlGLSIhGgndXeNUCqVGJzqfQlQKnaiuo9CI5HQSnWgVKgkaFJLj/q+ZsPvsFj7APskv2Xnfmc3s7qxZkX9lHFMYiuNy4jqVAazgHi94xCemcYKZv6khvmAHz2jFsaeGL3QxGmu/WcYr1rVBnnCgRc8pbjChDXKFTS163qMFbZA2hrXoucM3euhgA2OpGTnZtbBIXSQnaUoWXoI/ly7M3ZqmgkWcW1g0m26HVLUQM4lbzGmjjmstxvjr9+1mtjePNy3GrFr4DJlsW9h3Q+p+swccSv3nfB1j38LBvMQRzvCBPcs52YNYig3/DZrYwhpGEvOKaPpTHjFImqMTUQAAAABJRU5ErkJggg==>

[image8]: <data:image/png;base64,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>

[image9]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABIAAAAZCAYAAAA8CX6UAAABR0lEQVR4Xu2TPS8EURSGj/iO76yERDQ6lYSIhGYjIRRUJBKFSqeQiEaU/AFRKXSiEg3JVrIJFZVC/AEtFQqJj+e4d3fuHDMbo9rCkzzFeefsnZ17zxX5p2roxJ6gbsD6oK5IDU7iPT7hA+5hPx7hQNSaji6yho8472tFF/jAS2zzWUWO8Qa7TN6KRVw2eSr6GUVxPwzR+hSHTJ7Ki3fM5I24gM0mT+UOP/EdT3ARO2Idv2RD3ELW3rApK004jufiTnEw/ljWxfX8wG5wiZy4fRsx+ZKpv6nDQxt69AVJ/yiRbryyoUdnqiDRibXjFq6WOwJG8dmGnimvovdsF4fFTXkMvQb6Wdv4hhd4gGf4iptRaxm9e/s21P2ZEPc2velzuILT2BL0ldCeW5zBWvMsE7N4jX2SYdKTyIs7mB2T/wkdicRhrB6+AA9MMaOuG4cnAAAAAElFTkSuQmCC>

[image10]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACMAAAAZCAYAAAC7OJeSAAABo0lEQVR4Xu2WPShFYRjH/0IphMEghjspZVAspAzKx8CAMljIYpCBgUxmRTLKYjAQmZQMEmUxGC0GyqSQgUH5+D+ec5z3Ps65cm/nDLq/+tU9z/uee/73/ToXyJPnH1NCK53rAlruXCdCHd2mr/SBXtEOOkg3nX6x00Lv6AothY5Gt1d7p9NB13hZoM+02dSFPnpNa009Nk7pLXSaLBJmhxbZhri4oR90FDo9Lg20zdRiZR8aRrygM7QwrUeC9NM3BIHEJzqMnyOVKPLwFHRBS6h1p03W1Bl0u/+VAbpBy2yDS1TjJDSMfIFPDZ2j1U7Npx46wmH00l16jOjnfbFqCx6yiyRMVLtFjoUJW3SYxS9h5IiXbR3GCPSw6/GuZfqWoSMQRs5hZNve26LHCT1EcHM7HaNbCN9pOYeRKZAF+kLP6Rp0jcgrIBV0++aSNnqf5RCcgt4j7kEXt3+9BF1jPhnDyK9rpcXQd1EXdGqGEB5EOKAV0P6WnEfmL0gACdvpaUk0jPzHWaTz0NG0ZArTRB+hB+sRHU9vzo4qRJ/ImcLkyYpP1YVM3BfoAQcAAAAASUVORK5CYII=>

[image11]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAXCAYAAABqBU3hAAABs0lEQVR4Xu2VTSgFURiGP6HIXyISUrJRSvKXouxQsrChWCk7C7EQCyl2ykJWfhYWKCyUn5VyiygWsrJWtjaKjcL7ds50z3wz07UYd3Wferpz3m/u3G9mzjlXJEN8lMA6HVpYW4U1uhAXWXBRh2Aa5tnjVrgFc5Pl+GiCezoEo84xm9yE/U4WG0twSochDMF9HZIZ+KPcduoF8NKpvcNmp3YKu+2YFMMFOOlkpBHeqcwHL84f4kU1FfAadqq8Ht7CKjtugGOwCz57J1kK4YnKfLCBB1iqcr6/ebhsj134JB4l2YDHOtxQWcoGPuGLBC/WAq/EPAUNz03YT49K+CRmwmU7eRE8c8YB3uCHmCXjkQ+PxEygMMrhjfi/MwDvYbWY73uwSb6uSHj3X+KfUONwR6LXbw48gMNO1iumqRUnI+1iJmwk52LmwZwd833PJsuRlMFjHSp4DveKWl1w2RXTACcQ6RGzjf6FDjFPIwzeCHdF9ymFwjtnA+yUK+HCX07JoA4sbXBCgisoANcvG0iI2d3W3GI64EzmKviGI6qWFvh3+QoPJXrW/ytcs31i1naGDGnhF+YrRBiODH8uAAAAAElFTkSuQmCC>

[image12]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADEAAAAYCAYAAABTPxXiAAACMElEQVR4Xu2VQUgVYRDHJzIwrDwUhRhY4UUKKpMkVOhmHuzgJaGu3jqEYkGQeNCrkEEl1KEg1Ao6RDfBB4UHPXXq1EHIY8e6COn//2aXNzt9u2vwHrixP/ix7MzbtzO7384nUvJ/0uIDdeIAPBodGwb/fMoHwT246IM5HIJ98ANsM/Em+BgOmFhduQDf+CAYhcM+GHHcB8Al+BQuwE1JNkGuwGV42MXrwjS864M5+AItbDzUxBH4CV5z8SrjcMf5wuS51ldc/qLJfYT90Tk5Bh/CMdHlEcIXaElrgsyL1huE65rFLcGDLkeYfwS/i772GN5oHXZF5yx6FnbDz5Ly1CRcYExWE2zAPuC/YBN84qEpc1K0qF4XPwfXpHbDTnhbtPhv8GwU94QKjMlqgrlXPmj5LeGLL8NV0UY8/G0lOsacgl/hDdHRGMLfw5LVxIjkNPET/hKdAjGcBO/gTROznIBfJHnNkOgSa4f3TdwSKjAmq4kHoss1FV64LcmP9A58KekfKOc39wI+oZjroo3NwB4Tt4QKPAOfwy3Rpb0BJ02e3+VrSX+gVbjmebGd7VxG5815CO4HT1yM47DZxSyhJvLgMn0fHVPhDGYTfGWEnU/U0qlw4+Kf/wuhCZgHV4V9M0H4wbAJzmLCLb61ls7kqujSahQd8JnsoR6ORjZREd2F52yyKHCqsIk/oiPydDJdDDgmOWLZxC2XKwx88j/gW0kfqfsebmyDohtYSUlJQdkF6Mla9gtYNlkAAAAASUVORK5CYII=>

[image13]: <data:image/png;base64,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>

[image14]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAF8AAAAZCAYAAABXTfKEAAAEQUlEQVR4Xu2YXahUVRTHl1jgR6R1RY0USepCqPgRKUTai4E+FKEPSk/XJ6MuPnjxg56K8CF6iT4MAvt4iEDzQa5KFOjUQ5o+SKIIYlBhiIYJQoGK2fq5zprZs+6ZYe6cmTtzL/ODP/fctc/ZZ++11157nRHp0aNHj65nr+pT1T7VMVV/dXOPdvKZ6rbqsLTO8TOiYYIwSTU5GovwTjQUZJ5qZTROEHD+x6oHY0Oz4Pw5qj6xzotAxH8fjR1gieqs6g/VrtBWFFI0askC0NEzqrfFUtD06uaGYeFYyOHY0AEeVW1UfSetd/6Tql/F+i8EDnsku35IVVJ9WW5tHPr5SHVSuivfvyStdz4sVl1XbY4No4Hc/G92XcT5pK0Lqj2xocO0y/lTVd+qjqimhLYyODTmcmzO06ovsuvHxbZTM6u5VnVXtT42ZFAheJXAYB/L/jq0zVbNTGwR2niO+2pVHORhAsHnOBrn0+csqc7lvKtWbifQrqgWxgY4qrqs+lv1k2qR6ivVDdWQ2IKgQTGHH1K9K7VfVg8meFO1NNg/UN3LtEL1tdgheE31n9j5skP1u9hYWUCqpQjp7IzY+VQSuz8WB3PFSuafxe4/LjauRp3P+XBebGyrVAOqH8Xm9bqMfN8GsTkQeFUQ7c9l154SmNhO1SWxCMfuvCD2TLOQqnAIkZlCtC4Xex/OYAGABd6f2XZLJZLXiZ0d6URJjYw7tXHYpQfeAtU5scPQ4fqiNO58gvApMeeziJ9k9gEx372Y/e88r7qj2poamciW5H8qmX9U36geUK1RPZy0F8XPCpSmNMfbGUcKTnkz2Fi8klT6IaqIrry8ym5i4qTL38QOwEijkT9f7B3bpdKvQ+rCVpLq+blf2d01oSMepuN2UMT50THR+bQz9jyws+PcCey8SN47akFgEqAUIM8mdgLE35Xi7432MmzVz8W2B9ukHYyF82O+hVY7n8OWnI+4BnYDFU3cDeDvjbv3/uD99KazNMdT4cT8VQSPmHiOOEWc/7LYxPMW1R1CtUHVUSTtgOdwihJfbO+bYuCJzOa486v6pzzi8CFfeoc4ByfBh9JcOVkP8h4DzKtUijjf50JeT+HD8C+xCo55UUXlpSeiMr6jFp6e0/sHM9tQYnMoq2njTCjDwPmF8gfVe6pXxKICGz8Z8+ndajwK4tYkLTBAF6mBwXJvan9VzOmpLc2lJ8RKPu75U2w3x1TUL1ap8BzzPCD2c7n3V+WkHDxDHBTzHc/j9LzS29P5aan8QlCGlJN+sPiHTb2PmCJ4dKc7rNX4R1afjHS84/f4PBlXvftTPEPgbJ7JS6GOp/O3gr1jvCb2ARVz43iB3RF3bi34Hrkq9htPV0C0nJIuioZRwG5ttCLkd51h1fvS2I4aM1aL5c3xBB+d5PdbYpXONqmfOjepfpH84qLjEA1vROMEYZpqWTT26AL+B+i77WmciCPKAAAAAElFTkSuQmCC>

[image15]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABUAAAAZCAYAAADe1WXtAAABT0lEQVR4Xu2SvUpDQRCFj6BgIzaSIBoQX0AkSBqttBFJExR8A1u1Ee0F2yTEVqxEtLGwsZBga22nICLYC9oI6jlOlsyQ3OAD3A++Ipm5u/OzQE5OYpOe0yf6Qq9iODBEt2F5sklLIaNDha7TO/pDv2I4ME8fYXn3dJmOhAzHLL2hu7AP+rFBd2ibftKFEO3DCj2ha8g+tEXn6Bt9oBMx3MsB3aJl+kGHY/hvlkt0FXbpJXpzAqP0gi7SafpKx0KGzVKHHMIO1ZgGMgWbZxHWklqbdHEtog67/Bq2SBUwEN26536fovtRgR7Tcdjcv/GP1sUZ7IOEWqzB5rgPm6XQxWrdF5DJLWyWiVS5lnaEblWqUJX6AjLRkjSvRBVWveY44/5/hi3RF5CJ2vWoQlWkJ+bRgrQoX0BAj/gdNqOk3qDQC0hzTK/B58lGJ56TQ34B+h1C45PzeZQAAAAASUVORK5CYII=>

[image16]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAC8AAAAZCAYAAAChBHccAAACbklEQVR4Xu2WTahNURTHl1Dk+6NeQr0nFElKBmQkRj4GGChzZsqEkmRiYGCCAVIyMGIiKcrgFRMZy4R6SglJKQMp/H/23u+uu945+95nYuD86t+9Z+11zvnvs9de55h1dHR0/CtmSGulmXHAsSlrfhxwzJbWSYvjQOCkdFu6EbQlj6+QLrn4NWlzHutjo3RfmrB0UoQJnZDuWrrhZ+miJaMFJr9P+iI9lb5Kl6V5LqdA7g7psPRLeikdlfZaL3+pdFb6KX0MY3/g5iPSVumb9Nammmf8lfQwxDGGURiT3mWtmsxIOd+l7S7mIRdzu0N8m/Rc2hXijdTMH7D0dDDiOZ3jwFPj/wtpyWRGL4ffJjA9Ia10MVZlXBp1sSo18xes2cD+HIdictz690OJs2pzXLxwxvrHKIsr+Xdoauap8Zp5zA4yzx5Y4OKAYYyX645aWjk25rRoM19uMMh8KRs23nKXw5MlHq8L1Dt7ZKel2v5kKZf9NS3azLN8T2yweTgk/ZBOWapbjtl0TZOCMjE6Co2Dc67m2CKXN5A28zBM2QA3p1V+sNTarkvncg4PwNexX1HOK9CVat2pkZr5tg17MMf9zYHjZfl/qfnYqdZI7y2tlGeu9Ei6Kc0KY63UzJd65oIe3yqBt/MeSwaASdyx5idJi6S/vwlxOGLp/cGbfChq5scsbax7IV5eQEBvp1MwmeM5Vs57YL0JFcpqxmvCiKWx8yE+BYximOQo3/Ko11uWPhGOSa+lx5Ze4QU2KPXOHuFbhK6xwY1DvEcsqbIifvyZtNDl/BWUAUtKGa3Px5HVlr5XyKl94HV0dPwv/Ab5rqx3qHj5IAAAAABJRU5ErkJggg==>

[image17]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADkAAAAXCAYAAACxvufDAAADbUlEQVR4Xu2WWahOURTHlwyZSiJDkTESQoYi3iRlzBClpDygFFFECkkyPVCISBQezElelIsX8SKRMhRFQlKKogzr962zzlln33u5X5K6ff/6931nnXX2GvZaa2+RGmqooYYa/j3aKecpD2acVn4tU5Tzw3Nb5QTl4iBrClYrhypbZ88tlEuUQ1whw0DlVuWR7Hd0+XUOvufdLjHdVeXXZbxQnlGOUI5S3hRbwLFe+TPhN+WcoNMUvJT661xTdgo62L0rltQBys3K78oeQcdxWflQOUvZR7lbymvlGKdcJ0VQS8WMd841ykFi8JyYA9UiDfK1FLvqGK88JoWzrcTsHc3+O3h/PfsFg5VvlRNzjQwLxYylaJM8EyT8W9Qpe6bCBL2UJ6RwnuSfUl4VaxPQT/lK2TV7dsQk5AIyRJAYXiZF/bcMesCD7CKm68aqRZ0U3/PLen9Cb+VT5cwgWyTmN99PF+vJGcoOQacCsvBITPm82CChx56J9UkEAV5QXlKeVX5S7hQbWNXgjvKk8r5YSdJPY0saBUj0ILG+o//jjNgv5vdpsYDZGPTeBZ0KyKT3CNly0BNfpdwrBImDjkliOky0asCO0PPuMDY+is2FiOHKB2Il+Vm5ofy6Us74HQcf5Y2PDKAcMciG5ATr6JbRwVC6JxZoNWBn0lbAPpO9Xj9loEfZ+eiPB5n2JPItUdBRrEfSIF3ugwYjnJ0rXCGDG2oq2L3DYoFGsFPQz0L6NZ24lDS2mBvAp30K5HViMeTw2o7wXk0XfJ9rWIMzvtNvfwcqBP29iRzZG2V/sZK7JfV3lgSg54mfrPwhSTBi8cQpXMFUMeUIL9dh2TONzdm4I9coEoHcwQ5hZHaQRVDij5UjEznOXxEbYm6bBMZJSe/hJ8GB7mJrxbMcUF0rE1llYQzEZl0g5rwPB8r1ttj0daCD0Th4OKxx+EOQpUA/TmR2Lg4edu+QFAkGlO5xsW9jGS9Xzg3PfZXPpZEbD0GQvTViC31RbippWPaZjGuVF8WSwBUqOky2GfVpZUTsUT5Rbs+I3fQIwUluLvvEWuaG2JrpGUjA+Er1bJSiAhoFNc8FnVKLx0kEi6LDoUu5NIZtqSAB62OHtcYk7xxRB3uNXRqiXkOXmH8CbiUHUmFzAgc4pRX7u9mBUmmfCv8nfgHBJ70GsvB2AgAAAABJRU5ErkJggg==>

[image18]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEMAAAAXCAYAAABQ1fKSAAADZUlEQVR4Xu2XS6hNURzGP6GIPMtb1yuilPIqUZSJkMclA2byGKibFDE38IgwIAM3A+WRV96PwSkDyuBGXpGBYoCBEYU8vs9/rXPWWWftc/eWMrj7V1/n7LXW3mftb63/f/0PUFJSUlJS8i/pRu2ljrvP2VT3uhE1pqM2to3qV9+NPtQ6WP96anB9d2EGUVupAXFHgjnUnbgxYhx1l5oad4ie1H5qLWzyHdQv6lY4yDGMekIto1qofagfN416Ru2mDlPfnFbDDM+L5rQCZqjuf0MNrxvRyBDY3DQ2i97UFeozbFEb2EydCa77w5yTIZpU3K5PMYl6T31315rMfWqeuxat1A/Yj2u35UWrdok6CHt+Z2b0oI7C5tzMjG2wMUkztKX9i+u7RwN1w1J3PZZ6Sz2tjjA0CUksgj1H0nfPSddWCdry4ufRzAzt0GuwENG4LDPOUwth80iaISbCckDITOoL7GahENILybglsPEyKjRQu2gDtRG2HYVC4xTsXk24KHnMUCitgfVnmaF57ITltwqamJFiC+wFtPWF4l/XV2HG6EGXqQ/UDDcmxVDqOexehWNR8phxDLYQzcxQ6Cq8+6KgGaOpV7DE5fFb/WfQpoc/oF4GbTE7YPddh02kKJ2ZMQEWJiLLDJ1m+n1RyAw57DP4yqDdmxHnDN+eYhb1ibqBWtItSjMzFIrnguuUGQqPA7DEKXKbsZ16BHPb08t9+hXWy4f49njVtVp6nq9VZLLqhaJkmaHwe0ydhi2epNz01ekQNYW6CTtK/Zh26iNsh1+ElQINyMHbsDoibJvrviuR6gGVaq/hc4k3TYyi7qG+rlBCPhFc5yXLDO0KnVirAil5aydKqoOU7+ZHY1QMvoAd11rIkUjQisatPAJWrQmfCB/Wuv8Qh4mecYFaHLTJlDZYISZ0AimzS+FplCLLjBSpMInpNEyUG5Qj9FKxBgbjdBr4AkuMoV7DjlshI5Qf4md46RQSYT2yybVlodpBR/w7anzUFzMZdrpJWWi3dMDeY0HUV3UqnrhXuP0V93tgobELtgIywoeW6o74fi+FmK9ZWmBls9raXVuMVjl+hlRBY35K/W5q5dUWj/NF5V+zHBZ/+rGsP3N5UD46Ejd2RZRkVbSppunSKGmehSXtEpgh4dH73/kN5UTukVfPGJkAAAAASUVORK5CYII=>

[image19]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABgAAAAXCAYAAAARIY8tAAABcElEQVR4Xu2UTytFQRjGH6GIQikpsrLwZ6fIzoKyuSuRva2FSEpkLVYKJZSUyMbSQroLO19A8h3kA4jnue8997xnTOJcG3V/9VvMvDPzzpl55wA1/pguukWXXF8dnaXX9IKeuNiPaaKrdIc+0TUXG6ebsERijDan4d/RSovIJlim20gTdNPONJxST/foEV2hvdlwiViCSfpOD2g7nUearEILPYUFN+gbbNIisoNjCRphcz5gcw5drIImaOcJo/QVNmHK9ccSaHP7dJe+wBIpaWbAXTng0SLqK7q+MIHaN3QkGUAaaMG1SwzCKsGzDkug5AlhAi12RqeTAbDdD7t2FJXZLSyB7kEs0HvYsen4rsr9/fQBVk26w2NELjlE566FtPu2IBZDux6iE7Cv/JY++kwfaU8QqxpV0jns4oXeRkcarg59psrNl9gAvXTt3OhS9K/xi6tvDtn3kZsZ2KWqakL9o8pFUtvhwolfHk2N/8UnqQxIofiBcRgAAAAASUVORK5CYII=>

[image20]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABgAAAAXCAYAAAARIY8tAAABaElEQVR4Xu2UzytEURTHv5IiSn6UDclGsVVWyoaFBYtR2NtaiKRE/gA7ZSMpSbGzIQsxKxsLW/kXLOQPEN/vnHd75915Gr2ZjZpvfZq557w53/vuOXeAphqsAbJP1l2shSyTW3JNzlzuz2onW+SAvJJtl1sgT6QvWa/BTAupi5SRGsj4hlwgLTpHRpLvGbWSQ3JMNslQNl1RbBDW/lgmyJRbV9RJTskK2SWf5AvVrxsb5L3BfEJG+oF2HjRJPmAmsy4eG0jqwQtsANrICSID7f6efPsgrIhiZRfLM1DRXqRvUCL9ado0Tvai2A7MQOZBeQYb5J2MwsyuXO5XdZA7mIH6IK2SB9ix6fhCoRnyCLsLOh6Nck3p3FVIu++OcnnSM9OwPtTUMHkjz2QwytUtTdI5rPGS7kZPmq5PatBR8hk0Ri7durA0Yvqv8cUVW0L2fhTWIqypmpoYP5KFFGY7LhyouvZN/S/9AOsqSAMYXsimAAAAAElFTkSuQmCC>

[image21]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADAAAAAZCAYAAAB3oa15AAACoUlEQVR4Xu2XQYhNURjHP6FGaDJEQhrZEEWyILMahTQlFIWVBUkWNKYoKVmwUaaMNDUskLKZhcxgITZkSzbUI5GFrCgJ8//3nTNz7vfuOe+85s5CvV/9er3vu+/ce797znfuE2nRokWVTIfz4DSbmARVj5ekH+6Tak/YC8/BmTZRxmF4A96D7+FH9917Ga4eP7oIL/q6lJ9oFbwpxbGuwGUuz5u+FeSuwYUuNweOwlPue5LNcC98Bv+JnpTf6TH42sVnuONDuuBKG3QsgLvgWfgHPoLbZOJm18Jh+A6egN1BjmwQLej6IBaF1foGP8GlJsdqPIV9Jr4JfjaxGPtFx+5033mhA6JPL8V2+BWusQnLDtEqP4BtJrdItBInTfwqfG5iMZaIjn9cdNqdho9he3hQCfxdTeqLV8dF0RPw07IF/hZ9pB52iVeiN5EDL5rjj8BD8CHsKBxRDovJotIo/iCegE8ihI96yOXCLuOn3JEg1ggWgeO8gctNLgWLyhkQhXOe8/MLXBHE54t2hr/wUhAnW128x8RTvJD4U07BqfvDBkPOiA48aBMJ/KDhtErBuX4Hfhe9cS7OXFgkXl8UP30O2EQCLqrcG+A09HvFXdFz8bOsLZfR8AZqkn8xnt2S9xvfcXy7ZOX5BLh+uI5yaHgDXFxcWNx4cuGgvBCuhRR7RJ+wb5dsyW9loqXm4IsVhYPdlubeZTbCn6KDx9gJX0p9xzkvek4u6kb7AOF0ZYMpwF30l+hAoXaziuFb730TXww/SHFM33VmwycmR1OteK7oZpm73zQFp0DNBiuGrxB8lWima2XD9xq+uU4lLFLuVGsarpkLcJZNVAQ3Ul78QZuoEr6/pxbyZDgq+ro9VQUaZ53ov6fcjSkHLtrY/4z/nzG7wI4koc3tAwAAAABJRU5ErkJggg==>

[image22]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAGMAAAAZCAYAAAAlgpAyAAAD6ElEQVR4Xu2ZS6hNURjHvxuKkGceIdezPEJ5lTwmFOWRx0AZGNwBAxkoiYHkMWAgZCQGJkIoA0LS8RgopYiSR10SSRJlgDz+/7v2ctf+ztprn7P3cc69t/2rf7ez1r777P391/q+tdYRKSgoKCgo6Jr0hIZDg6Am1ZcX3ruPbgwQup7tfM7+uiMvfHHe2P3iHlBv53O9WAGdgLaLCUatmAFdh0brjgC89jY0S3eAZdAN6LTuyAJH3VzoCvQHegd9h+5AzdBJaIO9uI7QjCSOijHqJvQGehV9tjoETf53dZxH0BLdWAFzoOe6MYLPmtuMYdAl6Dd0GeoWtfPvaugh9BWaHrXXk5AZq6Ad0E8xz74bWhdpK/RazMDirHbh5yOSLe3xf/ZB/XSH1MCMj9BnaJ7ucJgtZmY0gpAZhGYw4Beg7qqPabYk8WfvBV2T8murgYbcl3JDcpnB2sAX2SnhUcL60YgURUJmMKA0ge+wTfWRkdBb6IDTNlXM4MvLD2ixastlBkfVE2iI7lDQjEm6sU6EzBgBtYpJU/PjXW0sFZO+Fjhtm8Sk3DSYyoZK8urovcRNJpnNGCNm1GzRHR5oBqd3IwiZQQNoBAs3A+fCYJ4XM2vclMRg8fok+J4HxRjGdNYq5emI3BWz2HFXeJnN4Ajhg87UHRnhKLXFs1LpwuojZEZSvRgFnREzKxhYi60hlA9bnBnogVEbFy2MlYbf+RQa7LRlMsM+FF8kVCs6AiEzWsUEXOfuJDjDucJKChgH5jfoC7QLGidmb+WLEe/xSeLp22cGZ86aqN27YXTNSIPp7KxurCMhM5iimLvH6o4E0sxgOuIqiXGxehm7oh3eg8a5mcVnxjHooph4e81wVyFpsKbs0Y11JGQGn1/n7RBpZhCmnf1iTLCG+O7Pe7DmcsVm8ZlBmE5LkmAGYZD5RaHC3AxdFZOD0+DKxR1Rlahv23+GSTKDA4r3YDqplAHQAzE7dh8cxdOczxPEFHu3LlgYdBpLgy2ZzWCAX0h82efCKctRt1Z31JkkM7hgqKZeEI5wvhMLtIbvS3Pd/Yq93rdBpKEliQc4sxkWOx2fQaci3YMmuhc1ENcMFlMGQc+wao5qVkK/dGPEB+iWmHMtzqDH0KLYFe2wXuiNcG4zeP7EFQGXmhsleSQ2ilo/j90o+mAsuAGu5Cic9YKLG5fcZnR0am1Gk5hDQt9GrlJYZ49L+ZK3MCMD46H1urEKePQ+RTeK34xzYs7CmBqZAlvi3Z2L/2EG4e8ZaWdyPjijSroxwmdGl4KrJf5wxI1nLX9p5FEMT6uX644AC6G94j/G2SzmOQ/rjoJOwl9UNNRQNC/bbgAAAABJRU5ErkJggg==>

[image23]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAkAAAAbCAYAAACuj6WAAAAAo0lEQVR4XmNgGAVDCIgDMSu6IAxIAPE6ID4CxE+B2BVVGqJzFRC3Q9n9QLwViDmQFVkC8U8gvgLEokAsjK4ABGSA+DoQ/4fiKiBmQVEBBWpAvIABoghkKsh0ONAD4i4GiGkgUMkAUegLU8ANxHuggp5QMRsgfgjEmsiKdgDxRCgbBBYA8XQGNDeBTHgJxBuBeCEQT2JAaEABoLABhbQkusSQBQBuUhnSeBhkEAAAAABJRU5ErkJggg==>

[image24]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAC8AAAAZCAYAAAChBHccAAACtklEQVR4Xu2XT6jNQRTHj/yJkPwpyUIs/AlZiFLsKBIJRbFTssCCsLB5b2Flg4hEspCSBQtRlFsslJWFSBRSFhZKURb+fD9m5r35neZ3/7x3bzb3U9/enXPunTkzc+bMPLM+ffqMhjHSdGmsd4yQ8dI0b+wFDHRaOmZhEt2AwO9K272jxCbpuvRa+hh1OdOgtHDo21WOWxhokneIddJtq/Z1QVoU/cud76q0P/rmSS+kHbFdyxJpp3RD+iN9iG20T3oc7aWtfCct9cbIXAt9nJR+SU8sBDM58/+WfkiXpN0Wgk7slV45W5EpUsNCkHuqrn+csBDo7My2UVqTtZtBYF+lZZmNibQK7KD0yRs9C6TP0jdphfORy+zKcwsHM9nOZ+1WMGkWZiC210tPh7z1MFkm3ZStFjrPA0zMtzD7o5ltlvQya7cD/ZMGjPVGWl11FyEWYhrnHTmnLHR+xdmpJhwmfHxOrJS+Z+12+Gmhny/WXuCQdp3FKpLn+2FpjrQtfn5rYbU2py9Htlj4fic8sPAbKlDTlXSwsBSVInUpw2nHfsuqqw4c4E5Wnkp11kI5znO/HRiLM1KkLmVSalA62Y2cToJn4pRC/p6z4dzPK1czGIudLtKwcolkttTh0QRPwAMWggdKK7lPv5TadmCsVd6YqCuRaUd8fQcm2irnOWzcwA9t+ILjJk65fzPaWkHwZEGRUr5PlO5FX8PCoebQcCtC2pU6CHyXdF+a4XxcWPTbsn5HSOfKzrPKrDad5GI1UiVYayE10CGrPr5ShWKSObyTfJ9Hom+CdKfgf2TDTwYPi/XeG9tlpoXDwhvFP3nZTm7mXsIOs8hdhwcZKdAr0hOEM9J16PyZlV+b3YDF4Xm+wTu6BcEf8MYuQIm9Jl20zm7jjqEK8U/GVO8YIYulM9a7Hf1//AXMZJoB4b3caQAAAABJRU5ErkJggg==>

[image25]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAmwAAAArCAYAAADFV9TYAAABZElEQVR4Xu3boUoEURSA4StiEMRiMWo1WHwMg4iafACLwSYmX0QfwiJsu8HHUESwGQSDUT3DDMt4cZdVl5kJ3wc/7Jw72w8zuykBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPOzFq20rpdanwEA6Ml69BHdRItN+9EoemvdBwBAD16i13LYeI+uyiEAAN2pXn9+RhflQeMpOi6HAAB0YzN6jk7Lg5bDaLkcAgDQjZNUP13bKQ9mtJfqhW5au+O7AQD4tZzqhW2hmAMAMBA51QvbJNUr09Vy+A+X0Vk5BABgsuq3a9XC9tNv1Dai23JYqP5ZWn1/Wo/ju+sF8bx1DQDADA6ih2i7NduKjtL8X5XmZGEDAPiT+1Q/Dbtuuvt+PDc5WdgAAAYtJwsbAMCg5WRhAwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAoCdfM5EtOV1qAy0AAAAASUVORK5CYII=>

[image26]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA8AAAAZCAYAAADuWXTMAAAA+0lEQVR4XmNgGAVUB8JALAnEPEhirEDMjcRHAYxAbA7EW4H4PxA/A+KfQHwIiBWAeA4QR8MUIwMJIF4HxP+AeCMQM0PFQXQgEJ8D4k9ArA8Vh4PXQPweiK3QJZCAKQPEZhQA8hvIiZUMEGfjAiD/Yzi5HIivALEYugQaAGnWRBZQBOInQJyDLIgDgDRzIgukM0CcbIwsSAwAxd8BBohmfH7FCpA1EwIg761AFmAB4jUMxGkGhUkDNkGQZpSAQAMKQLwNiGXRxMECt4HYFl0CCvgZIEk1GF0CGdxhgLjgBhDPheIjQKyGrAgXAKVfUAIIAeJ4IPZFlR4FFAEAyQ8mDcVus5UAAAAASUVORK5CYII=>

[image27]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABcAAAAaCAYAAABctMd+AAABYUlEQVR4Xu2UzytEURiGXxlFlAUpIbOwtFCyUlaabJRsKCW7+Q8oCztbWQlZsGGjrGwmC9nOPyDZWJiFBY1SUn68n+9cc+/XnXPvHVmZp56aznvnO+ee850LNPl3tNM+2hoaawn9zkwHXabX9JM+0Ce6AJ1k5efJjEzQW/pGN2iPGx+kJXpM391YJuahRcs0H42+GYW+wbMN0iAruqHDNnB00gvo5KmRA9qmJzRnMssRLdpBH8HrztjAIIc5R3tt4GMN2hUDNvgtsg2n0OJJW5KZLnoJLR6H5P3GoD2FNjpJz1wWIan4FvQSSS4+0kOXjdEdukfvEFNc2IT+sd61XoLm59DPgWUWnuLj9IWO2MAhLSrF123g8BaXFa/SK+g1DyPt90o/6LTJArzFBZmgCl2h3MB96IWp0CF6gPqtmlhckJOfoovQfS7Q7sgT8aQq3ih/UjxPd+k9atsZ101NanwBKKRCVIz0FMUAAAAASUVORK5CYII=>

[image28]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABMAAAAaCAYAAABVX2cEAAABL0lEQVR4Xu3TsUsDMRTH8SdVECyoKIi4FRfBTYqLgru4uOrezblLF53E0kGXgoOKk6B/gIscCi466ujQIggO+g9U6PeZHM0dd/XOOEl/8IHmkr42LzmRQf5XdtHO4BSj5iv9M4dLTOMEU/b5BFZxi3MU7fO+0UVnmMVebE4zgkA8iw1hzH6+Es9iK2g640xJKjaOazE9zBW32A0u8IYK6ij0lv4ct1gDazjGPY56y7IlaZuabfHcplushH1nnClpxdKiV2YZh1jEgjuZp5he4ANUMYxPLLkL4gegi9KifXzGjB0/iXkNv1PDOzp4xRc+8Ij5cJGN/mggZnth9IB027mj/7yFLTvWV03v4q8yiQds2LEeQqRfebMppr87uBOnXz4J++edsphCL1iPTv1RuifrOynll1WHAAAAAElFTkSuQmCC>

[image29]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAC0AAAAZCAYAAACl8achAAAClklEQVR4Xu2WTahNURTHl6KIfIQkJDIRJfmKfA0oBgyYKBmTj5Fi+koGZsLATAZIlAFClDcUZURKySMlyYAiRfj/7t77nn3XPfee8666T3m/+nfba+1z7tp7rb32MRtllP+DcdIUb+wzxFCbudJdabV39JkbVnPjmHRfOuAdI8AxC7FUBn5CuilN8I4RYLr0yELwY5yvCQ4mVa6sj8ySXkgDzt6ECSe9cYRhI89Jz6UZztdgi7TdGx1To4AXksLxhbsWw31ur/RDWu8dcFxa5o2RxdJj6Z30RVoiPYzjT9KuYmolPPde+ibtyOwrpdfSpswGqyzM3e/sDS5Ks71RLJTuSYvieKn0XdotbZU+Sw+irwoOOM/RVlnwJSsO2Rnpl4WM5/D/LBJ/C5OkwfjrOWhFScBOK2p/srTR6ncbyi/VKQFui3YyScZuW3vZsJFvoq+FbkF7WHFV7XdjjjQkPZGmRRt1+9vKG0EKetDZawed5pGyXiFTBJinu1NpQAqaUmphrHTdQtvzUBqcdtKa0jgx86+TVmTjKthNgs6zxa5Tt2WbkYLmzLXBajkgno8WAiXglMYEtcxi18YxFxMvvyzNTJMc+L9a60I71TPQ0ehYdLc2eElZW3kq3bKwG0ekw9K1OD5lrYdwvvRM+ildyOw5LOyOha5zVXplnUsDCLZTFhr1yq5RKjl8IlI2eb2Tsm71z0Vw1hsz0gXDe9CQhQPqYefJwBVrj6vJW2mBN/YA2SjLGn/sLxUuJj7UUr/O4U74YEVrLIVbb8Abhwk7SMbKFs+ucib2xDHl9NLKP9JYxGmr8dW5wUKNcU33AqV0XjrqHRFKigM/T9ps4Quu0226xkIsy72jDFa4TzrkHX3mby6wf48/6mdzZIbA1LQAAAAASUVORK5CYII=>

[image30]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABEAAAAZCAYAAADXPsWXAAAA6UlEQVR4XmNgGAWjYMQCRiAOB+JHQPwOiP+j4V9AXA5XjQWADMgB4m9A/IwBouEJlP0XSt8AYleYBmxAE4jloewGBoSNnkC8iAFiCTrQB2I1dEEYOATELlA2yLCFSHLIAGQxJ7ogDFwFYmkoG2QALkNwApCztwIxB5QPMgCZDwLMQBwMxK1IYihAkQE1BkCGgFwmgiQGisEgIO5GEoMDHiA+AMT8SGLpDJDojUASAwFQmF1EE4MDATQ+KxALMaDGDog9BYqRvUkSEAfiS0BsA8Q6aHJEA2EgPgjEzQzY0w/RAORN5LAbRAAA/A0ktrVLaMEAAAAASUVORK5CYII=>

[image31]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACIAAAAaCAYAAADSbo4CAAABY0lEQVR4Xu2UzytFQRTHj/yIUk8oqVd+JKWUhbKypEg2LPgPbHg72b6SUrKzkGzYWFpZ+7F4GytK2RKW7CxeeXxPc8Y7c7znqetaaD716c6cmXvvtzszlygSiUQi/5w6uADv4TN8Nxbh2ufslOAQy/AVPpF76YO03+R6Cyf9DWkxBk/hAByHh7AB9sMj2Fqemi5DsEfaeSovwTS5UPzFLCNw0BZrwM/phXtwJhz6ygWckDYHOlBjGg7fYotCtYCb4gucNWMBnPgENkufQ+g+Uw/n4YaqWUZtQdEN76hGkD4KTwYHuYGdqsYnaw5uqZolcZA8uc3q2YclOCX9JrgCs7DgJ1UgURA+GWcwo2pL5P4hi6rG8B66kjY/eJvcBvQem36O3Cn0878NwrSZfiNsp/DUcHtH1HtHk+iL/JQueE1uCYfNmOdPgnTAc7hOlf8vTLUgq/CS3HI/wt1w+PfhJY0k5gNeyT6r//tQOQAAAABJRU5ErkJggg==>

[image32]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADAAAAAZCAYAAAB3oa15AAACxklEQVR4Xu2WTahNURTHl1CEEJEiYaSUgSiFiY8YkJgIgzdTYmSgZHBLiplESkoGIqQkkoGEgTIwIRMGpGSAKEo+/7+39+6ds+6++z7vnTvR/dW/zlv7vH3XWmettbdZnz59mmaqNN4bR8F0aaw3/gtzpFk2vE22S6eliX5hFLDnOWmSXyiBA/ulN1EfpW/SAStn95E03xvFIumyDe23pr48yE7pbEXHpGnSOOlMFM9dWS69lG5IC6JtjDQg/bKweY4Z0m5vjMyUtkqHpJ/SHWv/SgROcJ+lw9JaG0rWPOmZtCX+3REcvGntmyeWSV8t1HkV/n7sbDkIfof0R2rVlwa5LS32xsgS6b200S8kyADO4WQn6IfX0lJnXyd9cjYPzchXXWghgBfS7NobIQDeyzFZui+dd/ZBKJET0qn43IkUwCZnPyo9dzYPmb1qwRGcJwi+RhWatcRJ6/A7fB4ySCZL4MQHqwcwQboVVWKbBQegZSEA3wu7Ks85WP/ijXDQwoY0WwkC/G31MuN/yMqFii0HzhMEpIR9l1ZGG1+mVL5A4vCzRqqttgVHalTeq5YZ/UBWSEInqHvqv1rfNCPJSL2Ac6XyhTREagw3ADJFxvwGadNSADjnx2/6cqkX6KNujDgA5vEVC+/Q7FXmSm+tHADO5eq7ZWHPBxbGdzeyAQD1WQqA45wzgsOEa0WVNJlSg3q4Aly3cEB6Ui/w29np4mAPbgRtrLfgIKepZ4WFH3ll+UNmivRQuugXItT/XcsPCK4GlywEcM2t5dgs/fDGBGVCUz2V9kbxPBDXSuyR3jlbGq84l4Sz/j7DcLgnrXJ2Dw1Okorjms02WKhXGiuX8RyUAudDL2FSMbH2+YUmSKXgs9skjF3KOF0wG2e1hS/RC9IUPGLdz4oRw8bcZXrxFRgyT6x9AjYOPXTcwtnQFJwh6brx//EXEuCOaYoXuPYAAAAASUVORK5CYII=>

[image33]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACgAAAAZCAYAAABD2GxlAAACMUlEQVR4Xu2WTyhtURTGl1CUkj8lpcSUkl6o95CBAQNMFcYUI4NnqmQuqTd8vYFEJEmJiRkxllIGpCQzxcTf77MctnX2Pee47yrJr77uPXudvc+31957nSPyzTdfj1yo0DbGwD5pUwaVQwU24KECWocabSCGZXnnpLKgVmgbOoFOoVtoF/rl3OfCB2xCQzbgwExxbMtv0b6JTDILK6LGepz2YmgJunPaXCagVSjfBkA29FN0gr6VKIF2RI36JvDCBXQIVdqAwyQ0bdo4KB9gM5Anuj342wU9iN8g4VY6gMZN+wscnAP024CBD9qDipw2Dk7jUcQZ5CRnoH2o1MSeGBN/Fix80LFoZgLaoU7n2kecQdIH3UDNNlAlehBGbMDDqIQNcnJ1zrWPJAYboGto0AbonJ1rbcDAZfgr4SX+J28N+0hisBo6E7PH2WFLtHPkCRJdynuo22kL+kc9mCQxyElyddbcRtdgFMEm5n3uPv0Ig1tuI0sAHccZ/AFdSbgOfoTBWRtgiWBnmvXBQTdE72ExdcmBFkVLTRTvMcg9/YYgO75DwtfTH9G9x1/fi52bmm+gKJIYZCW4FK0KIfg64hHnDJhRnux5aEG0DEXBCYZKg7xOnMasQlkSNcZTzNPshUvcJmqu9/l/EpgVLjOXO12CszAn/zdOSvhxEZfpKLi9zqEOG8gU/FIZt40JYQmbktRfQxmhBTqCamwgAU2ifettINMwEwPQsA3EEPeh8Xl5BIGgbppQv6/XAAAAAElFTkSuQmCC>

[image34]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACgAAAAZCAYAAABD2GxlAAAA/0lEQVR4Xu2VsQ4BQRRFnyAhJIREItHofYJoRaFSKrU6ha/wCQql+AGdTiT+QvSiVnBvhmR3ZGemsENkTnKaebOZuzNvZ0UCgcB/kYE1mNcLnuDaDX0wygze4UIvpEwJTuAVzrVaDO5cRR9MmSrMwpWozTEG/CbWgOy/gj7oEWPAFtyJ6oG+VvNFYsAi3MARPMO1qJ7wTWLAAeyJOuIb7MbLb3Be09H6c74LiQFftOFB7F8xFz05ypMpq8esWAOOxVD0gDEg+3ALO6LemJcmL0+fGAMy2EXUNcMeXMJcbEb6GAOyr/bwCKfi3tifgIEYTHcYnUT4m3Nt6EAg8Cs8ACpMN1RPexALAAAAAElFTkSuQmCC>

[image35]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAIIAAAAaCAYAAAB7NoTTAAAFBElEQVR4Xu2aW6hUVRzGv+hCWZGlaFFR+VAUUQ+FEVoZRRe6GBUYCiGJWBaB0QWt4GhED750kYoeioiILqSSvlSQSghdyBStKHowopeIIEgo6PL/8V/LWXvNnpm9z4xnNuN88HFmr7X3nLX2+tb/tkYa41BjlvG08PfI0HZsaIvkeowRx23GncZ/jTeEtvONLxn3G1caZ4b2MUYYLPKLxq+MbxmPCu3XGu+JN40x+rjbON94jvFn44TcFTxlPLl12xhNQu6767LM108YTzceYdxg/DZc007bGA3EAuN/xt3GV7rwdeNPxr/D/ZEsdL64j6vlDi4x/iF3C4sO3tEgHG1cpfYJd+Ip/tjIgffAgv5unJv1dcJJxqXywI/dPrvQK92bfEYQxAlr5KIYFC6SxyH5OpWR+67zx9pxoXzyb6v1wAfySPcF453GR0P/AeMcf2wk8blcDB/LF7kqjpO/r7uyduKDFGQO2zTY+ABLxJh3qbV+WCza9hqXGZeEftqe9seKYALvGa9I2jBvr8kfynGLyn3hqIC5I3rm/o/xjmJ3V8wwbpGnhzcbvzN+ZjwvvUm+KIPEm3JrluJP42/ysUScYPxQ7pragDXgixBEBGr9QuVCuD1vGEHwUt+Rz/+XrK8XpmtqNwrCjfWJFIydNUwtz4nG91UUx0EsVdGPAfxXVFSOh/KGEcVZxu/lL5Sd1FRghahapkAcjPv5rJ34ZbNqFLAwXVFRhzNulbsHXEWeDTQZWAHWry8XlMYHuaKaBHJxgtiqvEbtfrQXeBerjX+pGEM1HVj0PD6ojdQtdPqieHjSC7zI4/PGEpBWLZSbYnL0puFX+cYgGq8rpqlG3MjdNvHFxi+N5+YdKVaoPNBIQRWtCvBFa/LGLkAETRQCmQMugjrBGVlf08A736fuboEN/rCKCUIBVdwCuXVVIVwuL2BURVOFQN2EHVS1yDRMRIveyZpXQlRTp0ADv0xRabnxRrUUhYAQzhvy3RNdBztoh9xHXxbarjQ+Z9xofFBF11FHCKRMjLMqGQfp02SwVfXqCcNEtOhl1px1YR4UlE7N+groFR+QH18v9zHpDy3uk4uCa1zBWrk4OGF7V25ByK/Bj/IUNAZiVDAj6ghhqkBM0E/WwG8RrsobK4D/y2apan1BatHLwPkGdaD18kOvAsgtWZx8B0ViJVIglnRwXFPOTK8RExXIx9S+sBfIC1ib5BP9IelrmhDYPViDOhhkQWm/qgkhX7OUWIgUVBU5VMvPRGojCoEvhFgHouq0nxM2XEcqhEfkVgTRzQtt3Pu18Zhw3SQhEA98Ki8sVQXxExYOa8ruxA12jcx7oKoQqoIxbQicpj4Fy+IRPN2kVpyQmnd8N7/GYdERAucYpIcTchWy8DPCvZgpJrsgXDdFCJcav1H94BAXQmma+XLY9IA8pooulAOp/DQwPRXM3fGghcD73yMf22L58cKkQbmVQeNnYmmTwONZ4xPGT9SaENaCF/qkPF1BkZilV433G58J17zwdfKTTcjnYQELwJjqBocsNilmNMX8PVMlvrgGBi0ENuB2eexGwM969AV+j5AXVhBIDAhTcB/3x3/KXwbU1Pr9R6oXHDIPimEc8eZBNp/72XWDFgJgPeocrx+WuFoe+E6W6cksaTG/aaC0DSeDQyGEMaYYBGLEC6TTufXshbONL8ujfiq8BNp9BXZjDBfEDVVdzFDxPylpGni80B3zAAAAAElFTkSuQmCC>

[image36]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEwAAAAZCAYAAACb1MhvAAAEC0lEQVR4Xu2YS6iNURTHl1CERIQoFwOJopTyTPJMKAw8Y4YYGRAD3ZKJKKSU6GYgeZNITG4orwnlUUohGRCiKG/rZ3/bXWedfb/v3OugW+dX/8751t7f9+299tpr7XNEatSoUaOGpaOquze2If547H1U/VRdfUOCAaqLqjG+oQ1xVjXfG4top5qkuq56pnqu+qq6pRpv+llYmcuq1cbGvS9U71Sjjf1vs0p1R8L70enS5l8sUe032q2qUw1U3VUt+N2zAKIEL/OiecbeU3VS9c3YLFtV51SdjW2t6q3qg/xbh41TLVRdUP3I5BmmuidhbFtUs1VdsrZlqocSnJfLK9Ujye+4TbXH2YjIG1K+/9nGjfLvHQaDVZckpAd2yKLS5l/g0AnemLFOwn2DfEOEybISeDePOarbqh7GRp7DkZ7/6bCpqiMSitBeCbnVRj/g0F7OFhkhYXewvZNslHSUeHDYUwmFIMLgZpnrSJHDaOc5TKo5Oknowyf9yK3TpHzyHhYwTnas6lP2aTmm6uBsEQKCwDjhG4CwI/wIwyLWS7nDcPZIcx1pzmHkyVMStv9B1WvVSlV706dv1ue9hHz6UXUzs32RnJWX4Nzj0rTdcC67h0gjfUTynkG/w6r7vgGWSnggYZgHD2mQ8i15SEodGEk5jPyIo6hMMbLIM29UG6RpQlRk3tM7u66TUFSGSLh3VGZPEfOX3W7Mz+YkxtZc/ooQpSxmCXFSPNB6PwVb77tqrrHF+1PntJTDGACr5nMHUcoYYkHhOwthwUZKKMJux0i9hPtj2mEORfNlTMy3BOuwPHg4IU0/m+da6jDux+b7R4c1Smgj55A/Yo7Bllu1DHY7Rtg9JHEcMFPSRcoTx1QC+/18qsHBhJm4P4e1xmFXVd2y64h32E4JTlshIedtUq2JnQu4IqFyW3A8VZN3cJygahbBmMidZeBtHoTzUjABcgJ9yDMWBkIk+AFCymF890UD/JZkEaertqvOZJ+VgmNS1S9WS96TTOYOxsR4y4jRQ9h6SMz7JIQyn6kjAJMkCjwph5G47TVEpxO9HBnggYTk3RqYaAqqJZGFw6iARRyQsLhJKOmEHx2IOCrnUQlnlaK8weR9kgUGZtWY2XEuz+V91yT83lwspceKHVJ+P/os6R/HnOR939TZkJ3Ab+TUudDSX/VECnIdW3KyBGcxAL5XApFkE3SlcB9b0zoKKDAcPYY6+wwJZ7JUla02nAg4A/rDbtVgIkWRWClEwWZvzGAx/ZauNvFEkPo5VTU4aNZ7Yysh8kj6vvKSPxsk/JOQKjLVYriEAIj59K8wUfVYwsuqAU5hETgzsW0RlfqlaorpV23iolDgWppiWgyhvFzC/2BtlV1S/AdEjUr5CbNn8Zz/eVVlAAAAAElFTkSuQmCC>

[image37]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAYCAYAAADH2bwQAAAAw0lEQVR4XtXRPQtBURzH8b8weFiUlDIZZVMmo8HC4E3YbB4mWU2KxWZUUt6A8gLYzUpMXoL4nvu/Tw67/OrTvbffv86554j8aaKoo4241TnpY4WF6OBHDshhgKbVSRpj932OWlBpGqggi7X79BMTXTsjOjhFJDxQxA0JLFEOlyYz3LEV3eRbUtjhhKrVOcnjjIddeCnggoldmJidmtN7ypeDMemJ/t5VggFzH0lvoOR+bNARvaARhu6gnxaO2KNrl7/MC7JIGpwWFYglAAAAAElFTkSuQmCC>

[image38]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABIAAAAZCAYAAAA8CX6UAAAA/UlEQVR4XmNgGAWjYBQQARiBOByIHwHxOyD+j4Z/AXE5XDUOADIkB4i/AfEzBoimJ1D2Xyh9A4hdYRpwAU0gloeyGxgQNnsC8SIGiEXoQBiIpwFxBroEDBwCYhcoG2TgQiQ5ZNDAAPEFyDKs4CoQS0PZIEOwGaQIxCeAWAnKxgAgL2wFYg4oH2QIMh8GoqHiOgzYvQ02HTlmQAaBXCiCJMYCxGsYUMMSBfAA8QEg5kcSS2eARH0EkhgoYp4CcQIDDteAgAAanxWIhRhQNYDS2nkGVFeSDMyBuAmI1wOxIAPEApBPyAKgMOoAYi8gbmXA4z1iAMjL4gyYsTnIAAA6MykiN5A/ZQAAAABJRU5ErkJggg==>

[image39]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACMAAAAZCAYAAAC7OJeSAAABb0lEQVR4Xu2VPShGYRSAj0gG8pufKDerhIxMYpAoJmWWlFJKBqNsJllYzErJZvwykclgVIhJmZgMPKf3Xt/rlfvn697lPvX0dc957+187znvvSIFBQWVpxcf8Q2PsfpnOlta8Aw/cd3J5cIt3mGHm8iDdzzBGjeRB3aL6rHdymVKFX7gEl7gE77ilL0oK9rE7Mwlen6sGa/930xZxk0n1oclMS2zWfTdxi4nF8UonkrIfdqiIxxz4rO448SUc+zBDWxwcmEM4QE+SEgxQTvsI60F7uOEFQvQAnXXVtxEDGYkopgRMcfaPtLdeC+mUL1xXsxbWa8ncQ37g8UJiCxG+6/Da7NgxValPE/ayjkxp053LymhxehwlvDGiQ/iC17huBXfw07rWvHw8A91ruq+V0YUozTJ7xOj6EPsBzWKeQf9h8hi4lCLW2L+raK7M1BOx6YixQQM4zTuivnSJ8HDZzGzqKfXbWEqWiXd8Bak4gvuQzwFwZvVzQAAAABJRU5ErkJggg==>

[image40]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABIAAAAZCAYAAAA8CX6UAAABK0lEQVR4Xu2TvytHYRSHjzIQ9RUiWWRWJoMyGiVZ/QFSBhnU12Dyf0gGCxsyKouy2Ix+7JJFofB8nHuv954M35dSylNPt855u/c9P67ZP3+bcbzFZ1wMuSz68RwfcSLksrnDPWyPiVzecDUGv8MLTmEb9mFPPd0aXXiDS3hl3vgnPEgPtcIovuK2+UvFvHm5WazjbIhpDb56kUpv4iZ2p4kOPDS/VYkOb5lPMjJpPt0183MVw3htnyWlseMkJrQau7iAcyH3MSlNLEVlqqxlnMGhIq5bX5j/CQNFrEK7k/ai/Oo9jpmXMVjkdJPT4lmjgWd4EuIr+IBH5mdE2csdq7ehQounQxHFtZglI3iJ00ksm17cwH3znop0ytloCNoh9agz5LL40X/4e7wDtN4vXO2K4zEAAAAASUVORK5CYII=>

[image41]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAaCAYAAADWm14/AAABSklEQVR4Xu2UvytHURTAj1DEhPzIIiYTKxlNiuxGGZWSUOK7mGxGMcggRSw2SfkHJGITi0lKGQzic9z7/Xr38n3f++LLcj/16b13znmv886774pEIpFI5CuV2Iz1fuIvWMUnvMN73HbT5aUb97ABK3Aa35yKMqIjv7BHpQ1v8bVQYajGCS/2K+TEfVudQD/2eLF5HJLs66MFl3DKTyhNeInPfsKjD3exXUwzoczgCl7jrJf7QB/8IubhxejEMzETybmpIHRiJ1KkAV18D7jpxfUtR+35GJ7a43ChIpzUBqpwHa8SsVqcw317rb/nOS6Lqc9KagNKKx7hBh7gDU6KaaQGD3EL6/I3wDiupdj7WVq6gTz6+zWKu8g6xExnMBHLSnAD37EoZoMasNddiVwoP2pAGRGzB+giXPBypdBPdSxmU3vEHTcd+WfeAYCWOCZaDxmKAAAAAElFTkSuQmCC>

[image42]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA8AAAAZCAYAAADuWXTMAAAA6UlEQVR4XmNgGAWjgN6AEYiFoRjEJhrkAvEzKH4CxHdRpXEDCSC+AsQqUL4JED8FYm64ChwgB4j/M6A6Ux6IfZH4ILlKIPYCYh6YoCAQnwbifzABHMASiNcAsTQDkiX6QPwJiJ/DBLAAFiBeDsTRQGyFLGEMxF+B+ACyIBpQAuILDBCLGtAlQLYeQBaEApC/QbaCbDwMpZHDAQz4gXgFEC8E4pVA/BCI5wKxDBBzAPFWIF7MQCDkJYFYHIhZkcQUgPg6ELsgiREN6oB4LRDbQPnKSHJEAT8GSByD/FyDJkcQwNK7ALrEwAAAoSsgH8QpFdkAAAAASUVORK5CYII=>

[image43]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAK4AAAAZCAYAAACowxUjAAAGo0lEQVR4Xu2aZ4gkVRDHS8w5JxTOU1HEjKenmA7MiGJExfOD+MGMoBgwwGEAA4roGVEOP4gBQUQFBcFFREU/GDg9UcSAARUV5RSz1m+ra/dN9euZ3t2evd69/sGf6e7XPdNdr6pevdcj0tHR0dHR0dHR0dExDFZXbRgPTpGmv2+20zZ7raLaWLVqbGgTL6j2iwenyDOqk+LBjhI4yEWqu2PDCob7ulx1j1hiax1E+nnxYAPMUb2rOjk2dPSAfd5SbREbWgAO+2ChVjkvUXWDau3Y0BALVcvEnLijzI6qT1RHxIYW4ffYmgSE0y5WvREbGoZh8EvV3NiwkrOb6gfV6bGhhfi9toItxbLhTbGhYXjon1TnxoaVHAL6G9X2saGFMCIzD1orNqSQCTctxPawOFz1j+qY2NAwzEyp4Z6KDTOQ9cQCfqr1Hg7wvOol1bqhra2Q4CqD7GLV14UYXqkthsWVql9Ue8YGsYD5UPWF6jvVfxm9LBZcg+C7HlW9HxtmEIeo3lR9L2YTPlfrOWNibK36XPVQbCjYSvWY2G/9KWXbL1edNXb29MAKEcmuBDe7VKwYhnmqr2R4EfmImPEwYgqOdoVY4BBAZGUPpL/FjImIwLqZh3MH1Ug4wgLVKROQT/q2ybQN0mGjVw6GZ/xddaqYbViFeU7yAV+XfVS/iiWPCM/0kepHMTsvLz7Zx4nZZgTbyy+YJg6STLlHvUMkpaUBD3Bcsj8ZcPq9pVxyMOSNFGI7BaNuVmxzX3cV27uoXi+2I/eqjpTqZTU66N94cAbg9TnlgYNNj032t1W9pjo0OTYI+pX+jv27iWpfGe8vfhe7A/1wabEdOVNsdSgmoUEQlAdKvevwC/eFUbwGHEbH8kAjUnbOfo7rkFnokOOLfWphyoMcODgRWVUv47h01EyDkSImlAidfpVq89jQhyrHjbDiQP8QLNTDufNZrWHixMuC9UMbpdw54ZhDxibhPCD1HZdRegyGHGpNZphNs0RClBTUcdwDVB/LeEGO842MtY6D4TjnwmI7B9f+Fg+2HHeWYQRcHceldKLOBa+Jc+cvEguw88Nx4DoycT/4zrqOe3U8QL0zkh7MsIHqErG3GP7CgLqYmz6h2GeI59UtGWK+2ATrDtVRqjWLcwCjMMtn8pcOgymLpXfWi/NhvAhZnSGV9qrMRBvP2I91VC9KeSLST/5K+ehM2yC9MnplNR7cVffNszJpw747FccYdn0fZ7hOtUPRlsLo9JdUD/2ATX1e4I4ba2JGa0ZFXmDsGtqgacft+X2yFdl2JD1YMEfMyeaJ3eAeYga7U8xJbxUzwjvF+Tixz1Q3Un2qOljyS2tkYiZc1Gg5qO3SNV5uGmOneADQxr1WwT3lnL7N+LPlHJfa8ESxEuls1RNif0bBDqepfhYrH0gwuSUv6lacsieDJdBXJA4fhd1xuZ90NYO+JzlxL7F/oUnHpQwsZXzqycfFagiMwE0+LOZUzORYSvIJE/h+6jgEwAdikQE8SK5McDzTl2aKYvfDG7X0H0uUDn8k+0AHsPKRM5rDjP8zGf6LjmHAc10gtopAf7wnNkEl0wLPv0wsO4Kvz7pNq/qA40uk3K8OtS2jgr9V87LhW9XOfpLYdzNyMPI6XOP/L2AZknv2feSTPaeO4/r9kuGz8AUM3ekyE86cRhrGIRr5MhybiPOJ0VKx6ynS+REmVpzHfnQuHwr57hyx9uV6Zr0pZJe3w7EIa3/U8Dj+TAWb0jfRJmRcJkYEOFmV5EF/sDIABC19g/3j3wPpGxIB7RH6H4dOr6HfGUUdfhOnzSUep6mMy71MeB2ebErkuOMx9PuwzMSOMgEHvqU4j7LijKKN6KL2oTaOjgssX7EuOBnmq65XPS323ftLuWM5TpDRuV6XzxZ88kaNz5owIkgJZM+iOCfOy7p4fH7+DUaiWRSO12EN1TViGZTfh93Hm8doynGZQ5DtJwRfyvBzmepa1bNJG46C49wmNpS9WmzjyBiGOoshOh3yU6h9eSM0WRgFbhb7DdY2Y3AQNAQGk4fZBhnwSbF5BrUqWZIkw5DqdligulEsmeRYKGafubGhJqzRM9/B9nE0hH6Ou53qfrFSj+VYltNy/0Ug4PA5fmfCYAicLGY08DagPf1xHiY6U4QMnpuR1oUOyz0wx+nE+2Rqr0fbDEM5JZnbGDtEW8T9FGyE8+MUg/qpCsoH7/9IP8etC+Ug/6uumsSvUHjvzTpsk9AZVZm+oxcmV2S/NsHy5O0y/a+WOzqGx/87B0QCnfrb6wAAAABJRU5ErkJggg==>

[image44]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEEAAAAZCAYAAABuKkPfAAACjklEQVR4Xu2XzatNURjGH6HIR/nIR1ehlHyUJAY+0r0MlJggZMDMgBG59y8wkIEyUSgkE4kMTK+be0cUIxlIPlKKJMrAvYXnOe9ee797nXPP2Xdw9+nW+tWvc/Za++yz1/uu9a69gUQikUgkJsIsept+pW/p0lJvfUyja+j0uCNjLt1GN2bfx2MJ3U1XYPxrNTGT7qH/6AM6o9w96Wjw6+lj+oEuL/Uas+kP+omOwu71YekMYxV9Ra9nn59pnz+hHbqRMboz7phklABldQv9TT+iOQjKvAYzx7Vptr6hZ13bMfrUHQslVImtxAJYFnqi9rpoF4QDsMzrM6Ck3aNPYMtZDMACE3MnbvAoC4th60Y34S9YN+2CsIF+o5ujdg1uCEV9OAcLlmpBQDPmtTvOWUbvwy78ElYMr8Ii2S3aBUHEydGxknYLNitECJZqxgW6kN6FjbWJd/QmrNiIo/Qv3Zuf0RotlcMTUMVWs60KnYIQozqhQrkrat8Pmw1SY7qCYpw5yray7gk3EEe7TqoGQVnvhw3wfNSnzA/S+fQ4/YkiIGG2NNa/1kec8ROwE7tJ1SAcgp13Cm5gsF3gGn3m2rTstY1qbGtDY/gjXziEloYi202qBEEPSu9hUz6wAxaAdfQ7LKEeLUc9M+Q7S/gj/7SlrfEFbHvUhY64vph9KKZXFZWVeY1fdqZKEEbo9qjtBmxGhN/7bTSwFa59E/1V9DVQYfkDq7SqF6fL3bXRKQgr6UFYn/di1r8a9kDVaofrhSU4RwfDsEfU57DAaLp8gW0rfp3VgQYSzyA5hGLGhoelVvr6pl3gMn1Ez8Deh5T0S+6cHL1gyPByoYEvKrqnPArayeyzWy+DiUQiMTX4DxK6k0g8GYIhAAAAAElFTkSuQmCC>

[image45]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAE4AAAAZCAYAAACfIRhSAAACmElEQVR4Xu2YTagOURjH/8KNfCUfUeQj14Yoko2yYKGQkqJYsWBzU0qUm+5eFkRKSXaUjw0lWdxiQRYsWCokkiiKQuH/7zmne+ZxjXdmmPuq86tfzTln7p2ZZ855zjMvkMlkuoNxviNTZC29Qu+F48gbeomeTcwEFtCH4XgnfUuXhPYr+iL4kr4O/RlY4BQUsZl+p+vpGDopnkS20r6k3fVM9h0JY2kvXeEHajCKnqKP6czQlmIePQK7XtczjR6FzQKPHmgTfUev03P0Ip2SnlSRa/QmneP6FazjdKnrb425+PWmPAqIAiY10wYxfOD0gE/o9KRvL2yZbQht7YgHUUzs0RN0fjgvZRX9TAeSvpPB1llN7wYXu7EyJuL3gftEb6BYLui8H6j+kBvpo3C8Eva/L4T2VPqAHgrtVhhNt9BbdBmG8kWnlAVOAYoPF4kPPQj72045QL+G4zX0C90X2gthO2lHgdMb0FJ4D7vBy8XhP6KcsIM+pVfdWBXqBu45ne3GypgBq9V0z1oVZzC0CSynH2HBLWUW/UD3wPKRbqBK9bwfVvMoWWubb0JbgRNaHetguTVFq0SzbrzrLzAAu6EqS0oXOgarsPvdWFPaDFxtYhLUDaVqmfYk53k0MzVDD9MJbqwp/0XgdCFdsM4nhcoGzTbNOj/Vm1A1cLGUuINixf9PiYGTddGMU/BO4++88bLAfaO3UZzlsRw5j2rpphHxJuM3W12URHfDPo71AE02iLLA6VcMpRalmIgKYAVOu2Or6C1th011LYNnsG1a23VdtFvdh23zKoQ7mQm6ts+1UvlLeUyoXFB6UH7VZ5l+1dA35qIwPiLoA7dqGVKGgqUCWJX+LjfWFH3GbYMFTi8pk8lkMpmR5Sd80I+jGVWaMQAAAABJRU5ErkJggg==>