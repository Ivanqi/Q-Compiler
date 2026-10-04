# flowgraph.py 详解
## 一、这个文件的作用是什么？
这是 qcc 后端寄存器分配的前置分析模块：把指令选择产出的"线性抽象指令列表"（frame.instructions）转成流图（CFG）+ 活性信息。上一系列讲过，寄存器分配器要回答"哪些虚拟寄存器不能共用同一个物理寄存器"

回答这个问题的全部输入都来自本文件：
- 基本块划分：把线性指令列表切成块（FlowGraph 的节点）；
- 块级 gen/kill：每个块"使用了哪些寄存器、覆盖（重定义）了哪些"；
- 活性分析（liveness analysis）：不动点迭代算出每个块、每条指令的 live_in/live_out；
- 活动区间（live ranges）：某 vreg 从哪条指令活到哪条指令——干涉图（interference graph）的建图依据。

它在 registerallocator.py:305-310 被调用：
```c
cfg = FlowGraph(self.frame.instructions)   # 建流图
cfg.calculate_liveness()                    # 活性分析

```

与标准教科书的区别在于：输入不是 IR 基本块，而是已选好的机器抽象指令——跳转目标信息不在指令序列结构里，而在每条跳转指令的 jumps 属性里（前几讲见过：RISC-V 模式的 Bop(..., jumps=[yes_label, jmp_ins]) 把跳转目标随指令携带）。

所以本文件的三趟扫描全围绕 ins.jumps 展开。

## 二、每个方法的作用
### FlowGraphNode（7–45 行）—— 一个基本块节点
一个节点可以装多条指令（9 行 docstring）。
####  __init__(g, ins)（11–20 行）：

建节点时自带 gen/kill/live_in/live_out 四个集合和指令列表，并立刻把创建它的那条指令（leader）装进去；

#### add_instruction(ins)（22–30 行）：
把指令并入本节点，同时维护节点的 gen/kill 传递函数：
- 24–25 行：单条指令的 gen = 使用的寄存器（used_registers）、kill = 定义的寄存器（defined_registers）；

- 29 行：self.gen = self.gen | (ins.gen - self.kill)——新指令使用的寄存器，若前面已被本节点 kill 过（即在本节点内更早被重定义），就不能算"块入口处活跃"——这是数据流方程 gen[n] = ∪ (gen[i] − kill[前序]) 的正确实现；

- 30 行：self.kill = self.kill | ins.kill——kill 单调累积；
注意 gen/kill 更新的先后顺序：先减去旧 kill 再并入新 kill，保证"同块内先定义后用"的寄存器不会漏进 gen；

#### __repr__/longrepr（32–45 行）：

调试用——longrepr 打印 gen/kill/live_in/live_out/前后继，排查活性分析问题时的利器。

### FlowGraph(DiGraph)（48–164 行）—— 流图
#### __init__(instrs)（51–92 行）—— 三趟扫描建图（58 行 TODO 注释承认这段"tricky"）：
第一趟：确定 leaders（60–71 行）。基本块入口 = ① 第一条指令；② 所有跳转目标（ins.jumps 里的每条目标指令都 get_node 建节点，67–70 行）；③ 跳转指令的下一条（node = None 使下一轮循环把下一条指令建成新节点）。注意注释：这一趟只建节点不连边——因为边要等所有节点就位后一次连成；

第二趟：连边（73–81 行）。再次扫描：遇到跳转指令，从它所在节点向每个 jumps 目标所在节点 add_edge；

第三趟：装指令（83–92 行）。最后一次扫描：是 leader 的指令归入自己的节点，其余指令 add_instruction 挂到当前节点（92 行断言保证"前面一定已有 leader"——线性列表的起始指令必是 leader，第一趟已保证）。

#### has_node/get_node（94–104 行）：

_map（指令 → 节点）查询与按需创建——一条指令只属于一个节点，jump 目标与列表位置指向同一对象时自然复用。

#### calculate_liveness（107–164 行）—— 活性分析（docstring 109–113 行先写明了方程）：
```python
in[n]  = gen[n] ∪ (out[n] − kill[n])
out[n] = ∪ { in[s] | s ∈ succ[n] }
```
- 114–116 行：所有节点 live_in/live_out 清零；

- 120 行：节点遍历序——TODO 注释说理想顺序是"反向深度优先"（能大幅加速收敛），当前就用插入序；

- 123–142 行：不动点迭代——反复按方程更新每个节点，直到某一轮没有任何变化（change 标志，139–141 行）。这是教科书级的标准实现；

- 144–160 行：把块级活性细化到指令级（寄存器分配需要指令粒度）——从块内最后一条指令开始：ins2.live_out = node.live_out（147–148 行），live_in = gen ∪ (live_out − kill)（149 行）；然后在块内倒着推（150–160 行）：上一条的 live_out = 下一条的 live_in；

- 157–158 行：记录活动区间：for vreg in ins2.live_in & ins1.live_out: self._live_ranges[vreg].append((ins1, ins2))——某 vreg 既"流经上一条"又"流入下一条"，说明它横跨这两条指令存活，记入 _live_ranges（按 vreg 分组的 (起点, 终点) 列表）。干涉图的边正是由这些区间推导的：两个 vreg 的活动区间有重叠 ⇒ 不能共用寄存器；

- 162–164 行：日志记录迭代次数（调试活性分析收敛速度）。

### 三、与前几讲怎么衔接
```python
instructionselector.py（BURG 匹配）→ frame.instructions（线性抽象指令，jumps 带跳转目标）
   ↓ registerallocator.py:305
FlowGraph(frame.instructions)      ← 本文件：三趟扫描建 CFG
   ↓ registerallocator.py:310
cfg.calculate_liveness()           ← 活性分析 + 活动区间
   ↓
干涉图构建 → 图着色 → 虚拟寄存器映射到物理寄存器

```

### 四、详细例子
#### 第 1 步：输入——指令选择后的线性抽象指令列表
沿用 RISC-V 例子（上一讲），假设选择器产出了这样的指令流（L_x 是 Label 指令，jumps 里装目标指令对象）：
```c
[0] mv   v1, a0                (jumps=[])
[1] blt  v1, v2, L_then        (jumps=[L_then, jmp_else])
[2] jmp_else: j L_end          (jumps=[L_end])
[3] L_then:  add v3, v1, v2    (jumps=[])
[4] j       L_end              (jumps=[L_end])
[5] L_end:  mv  a0, v3         (jumps=[])
[6] ret                        (jumps=[])

```

（语义：v1 < v2 走 L_then 算 v3 = v1+v2，否则跳过；最后 a0 = v3 返回。）

#### 第 2 步：三趟扫描建图
##### 第一趟（找 leaders）：
[0] 是首指令 → 建节点 N0；

[1] 的 jumps 里有 L_then、jmp_else → 为它们各建节点（N_then、N_else）；node = None；

[2] 是跳转后的下一条 → 它是 jmp_else 本身，已映射到 N_else；jumps 里的 L_end → 建 N_end；

[3]（L_then）已映射 N_then；[4] 的 jumps 目标 L_end 已存在；[5] 已映射；[6] 无 jumps

##### 第二趟（连边）：
[1]：N0 → N_then、N0 → N_else；

[2]：N_else → N_end；

[4]：N_then → N_end。

##### 第三趟（装指令）：
N0 = {mv, blt}；N_else = {j}；N_then = {add, j}；N_end = {mv a0, ret}。

```c
        N0 {mv, blt}
        /        \
  N_else {j}   N_then {add, j}
        \        /
         N_end {mv, ret}

```

##### 第 3 步：节点的 gen/kill（add_instruction 逐条累积）
以 N0 为例：先装 mv v1, a0（used={a0}, defined={v1}）→ gen={a0}, kill={v1}；

再装 blt（used={v1,v2}）→ gen = {a0} ∪ ({v1,v2} − {v1}) = {a0, v2}，kill 仍 {v1}。

注意 v1 没进 gen——因为它在块内被 mv 重定义了，"块入口处活跃"的是 a0 和 v2，方程正确。

其余：N_then gen={v1,v2} kill={v3}；N_end gen={v3} kill={a0}；N_else gen=∅ kill=∅。

##### 第 4 步：不动点迭代（liveness）
初始全部空，按节点序迭代（展示收敛过程，省略中间轮）：
| 节点 | gen | kill | 后继 | live_out | live_in |
| :--- | :--- | :--- | :--- | :--- | :--- |
| N_end | {v3} | {a0} | 无 | ∅ | {v3} |
| N_else | ∅ | ∅ | N_end | {v3} | {v3} |
| N_then | {v1,v2} | {v3} | N_end | {v3} | {v1,v2} |
| N0 | {a0,v2} | {v1} | N_else, N_then | {v1,v2,v3}（∪两个后继的 live_ins） | {a0,v2,v3} |

第二轮 N0 的结果不变 → 两轮收敛（日志 "Iterations: 2, nodes: 4"）。

读含义：v1 在 N0 出口仍活跃（N_then 要用它），所以 N0 里 mv v1, a0 定义的 v1 必须活到分支结束——它和 v3（在 N_then 定义）的活动区间不重叠吗？ 

看下一步。

##### 第 5 步：指令级活性 + 活动区间
块内倒推（以 N_then = [add, j] 为例）：j 的 live_out = {v3}（节点出口）、live_in = {v3}；

add 的 live_out = {v3}、live_in = {v1,v2}∪({v3}−{v3}) = {v1,v2}。157–158 行检查 j.live_in & add.live_out = {v3} & {v3} → v3 的活动区间记录为 (add, j)。

N0 = [mv, blt]：blt.live_out = {v1,v2,v3}、live_in = {v1,v2,v3}；mv.live_out = {v1,v2,v3}、live_in = {a0,v2,v3}。交集 {v1,v2,v3} → v1、v2、v3 的活动区间都延伸到 (mv, blt)。

最终 _live_ranges（示意）：
```c
v1: [(mv, blt)]          ← v1 从被定义活到 blt 使用
v2: [(mv, blt), (add, j) 附近]   ← 贯穿两个分支
v3: [(add, j), (mv a0, ret) 附近]  ← 定义于 then 分支，活到 N_end

```

干涉图从这里长出：v1 与 v2、v3 区间重叠 → 边；v1 与 v3 若区间不重叠可同寄存器。图着色分配器（registerallocator.py）据此着色——本文件的分析质量直接决定分配结果的优劣。


## 五、一句话总结
flowgraph.py 是寄存器分配的数据准备层：FlowGraph.__init__ 用三趟扫描（找 leader → 连边 → 装指令）把带 jumps 属性的线性抽象指令列表切成基本块流图，FlowGraphNode.add_instruction 按数据流传递函数累积每个块的 gen/kill；

calculate_liveness 用不动点迭代解 in = gen ∪ (out − kill)、out = ∪ in[succ]，再把块级活性细化到指令级并记录每个 vreg 的活动区间（_live_ranges）。它被 registerallocator.py:305-310 调用，

产出的活性/活动区间是构建干涉图、图着色分配物理寄存器的全部依据——指令选择决定"要算什么"，

flowgraph 决定"谁和谁不能共存"，寄存器分配完成"落在哪个寄存器"。