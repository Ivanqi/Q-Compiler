# interferencegraph.py 详解
## 一、这个文件的作用是什么？
这是 qcc 寄存器分配器的核心数据结构：干涉图（interference graph）。

上一讲 flowgraph.py 算出了每个虚拟寄存器的活性与活动区间，本文件把这些信息汇总成一张图：
- 节点 = 一个寄存器（虚拟寄存器 vreg，或已着色的物理寄存器）；
- 边 = 两个寄存器的"生命期重叠"——它们绝不能共用同一个物理寄存器（否则一个会覆盖另一个还在用的值）；
- 图是 MaskableGraph（可掩码图）：可以临时"隐藏"节点——着色失败要 spill、合并失败要回退时，摘除/恢复节点而不破坏图本体。

这张图交给 registerallocator.py 的图着色分配器：
```python
# registerallocator.py:311-312
self.frame.ig = InterferenceGraph()
self.frame.ig.calculate_interference(cfg)   # ← 本文件的建图入口

```

之后分配器的三个主要动作全部作用于本文件的图：着色（查邻居颜色）、合并 coalescing（combine，消掉 move 指令）、溢出 spilling（mask_node 摘除节点重试）。

所以本文件是"分析（flowgraph）"与"分配（着色）"之间的桥梁：它回答的唯一问题就是 interfere(a, b)——这两个寄存器能共存吗？

## 二、每个方法的作用
### InterferenceGraphNode（16–31 行）—— 图的节点 = 一个寄存器
#### __init__(graph, vreg)（19–24 行）：
- temps = {vreg}——节点代表的寄存器集合（初始只有一个；coalescing 合并后一个节点可代表多个临时寄存器，它们最终落同一个物理寄存器）；

- moves = set()——与本寄存器关联的 move 指令集合（mv v1, a0 这类；合并阶段要用它判断"两个节点之间是否只有 move 连接"）；

- reg = vreg if vreg.is_colored else None——物理寄存器本身就是已着色的节点（a0 这种进来就是 reg=a0，着色算法把它们当"预着色节点"处理）；

- reg_class = type(vreg)——寄存器类别（决定可着哪些颜色）；

#### is_colored（26–28 行）：

已着色判断（reg 非空）；

#### __repr__（30–31 行）：
调试显示 {vreg1, vreg2}(reg=a0, class=RiscvRegister)——一眼看出合并状态与着色结果。

### InterferenceGraph(MaskableGraph)（34–118 行）—— 干涉图
#### __init__（37–43 行）：

temp_map（寄存器对象 → 节点，全图唯一索引）；_def_map/_use_map（寄存器 → 定义/使用它的指令列表——溢出（spilling）阶段靠它们找到所有要插 spill store/load 的位置）。

#### defs(tmp)/uses(tmp)（45–49 行）：

上述两个 map 的访问器。

#### calculate_interference(flowgraph)（51–78 行）—— 建图核心：

遍历流图的每个块、每条指令，利用 flowgraph 算好的 live_in/live_out/kill：
- 56–57 行：指令 live_in 里的每个寄存器都确保有节点（活跃的寄存器都进入图）；

- 60 行：live_and_def = ins.live_out | ins.kill——"经过这条指令仍活跃的" ∪ "这条指令新定义的"。为什么是这两个集合？一条指令 d = op(...) 执行时：live_out 里的值此刻还活着、不能动；d 被写、必须与所有"此刻活着"的值不同寄存器——所以 live_and_def 内部两两都要加边；

- 63–67 行：两两 add_edge——干涉边由此产生；

- 69–72 行：clobbers 特殊处理——指令声明的"被破坏寄存器"（如 RISC-V 调用时 RegisterUseDef(uses=(R10,))、浮点运行时调用的 caller-save 寄存器）也与 live_and_def 全连边：调用破坏了它，就不能有任何活值呆在里面；

- 74–78 行：顺便记录 def/use 信息（给 spilling 备用）。

#### has_node/get_node(tmp, create=True)（80–96 行）：

temp_map 查询/按需创建（create=False 时只查不建，供"寄存器有没有入图"的判断）；断言维护 temp_map 与节点 temps 集合的一致性。

#### interfere(tmp1, tmp2)（98–104 行）：

全文件最有名的接口——查两个寄存器是否有边（= 能否共存）。图着色时"能不能用这个颜色"、合并时"合并后会不会冲突"，都调它。

#### combine(n, m)（106–117 行）—— coalescing（寄存器合并）的原语：
把节点 m 并入 n：
- 合并 temps、moves（109–110 行）；

- temp_map 重定向——m 名下所有寄存器改指 n（113–114 行）；

- 调父类 MaskableGraph.combine 合并边集（m 的邻居全部转给 n，116 行）。

### 父类 MaskableGraph（maskable_graph.py）
提供 mask_node/unmask_node：把节点加入/移出"掩码集"——被掩码的节点在图查询中暂时隐身。

分配器在"选节点 spill 后重试着色"（registerallocator.py:460）、"合并后回退"（754 行）时用它——试错而不破坏原图。

## 三、与前几讲怎么衔接
```python
flowgraph.py：线性指令 → CFG + 活性（live_in/live_out/kill）
   ↓ registerallocator.py:311-312
InterferenceGraph.calculate_interference(cfg)   ← 本文件：活性 → 冲突边
   ↓
registerallocator.py（图着色 + 合并 + 溢出）
   ├─ interfere()    查冲突（选颜色、判断合并安全性）
   ├─ combine()      合并 move 相连的节点（消 move 指令）
   ├─ mask_node()    spill 时摘除节点重试
   └─ defs()/uses()  找 spill 插入点（每个定义后 store、每个使用前 load）

```

## 四、详细例子
沿用上一讲流图的例子（RISC-V 指令选择产物，v1/v2/v3 是虚拟寄存器，a0 是物理寄存器）：
```python
[0] mv   v1, a0         ; 定义 v1，使用 a0
[1] blt  v1, v2, L_then ; 使用 v1, v2
[2] jmp_else: j L_end
[3] L_then: add v3, v1, v2   ; 定义 v3，使用 v1, v2
[4] j L_end
[5] L_end: mv a0, v3         ; 定义 a0，使用 v3
[6] ret

```

### 第 1 步：flowgraph 的活性结果（上一讲的输出）
| 指令 | live_in | live_out | kill |
| :--- | :--- | :--- | :--- |
| mv v1, a0 | {a0, v2, v3} | {v1, v2, v3} | ∅ |
| blt | {v1, v2, v3} | {v1, v2, v3} | ∅ |
| add v3, v1, v2 | {v1, v2} | {v3} | {v3} |
| mv a0, v3 | {v3} | ∅ | {a0} |

### 第 2 步：calculate_interference 逐指令加边
- mv v1, a0：live_and_def = {v1,v2,v3} ∪ {v1} = {v1, v2, v3} → 两两加边：v1—v2、v1—v3、v2—v3（此刻三个值同时活跃，三对全冲突）；

- blt：live_and_def = {v1,v2,v3} → 同样的三条边（已存在）；

- add：live_and_def = {v3} ∪ {v3} = {v3} → 单元素无对；

- mv a0, v3：live_and_def = ∅ ∪ {a0} = {a0} → 无对。注意 a0 与 v3 之间没有边——虽然 a0 在 mv 处被定义、v3 在此刻活跃，但 v3 是本条指令最后一个使用，写 a0 不会破坏任何后续要用的值（这正是 live_out 不含 v3 的功劳）。

#### 最终图：
```
   v1 ──── v2
    │
    └──── v3        a0（孤立，与 v3 无边）

```

### 第 3 步：图着色（registerallocator.py）
假设可分配颜色 = {a0, t0, t1, t2}：v1、v2、v3 两两冲突 → 必须占 3 个不同寄存器（如 t0, t1, t2）——这就是着色算法"邻居颜色不能重复"的直观体现。

### 第 4 步：coalescing 用 combine 消 move
分配器检查 move 相连的节点对（mv v1, a0 和 mv a0, v3）：若两节点不冲突（interfere() 返回 False），就 combine 合并——合并后两个寄存器落在同一物理寄存器，那条 move 指令可以直接删除。

本例中：v1 与 a0 无冲突边 → 可以合并 → v1 落 a0，mv v1, a0 消失；但 v1 与 v3 冲突（有边），若 v3 也想合并进 a0，interfere(v1, v3) 为真 → 拒绝合并，mv a0, v3 保留为真实 move。

#### 最终分配（示意）：
```c
v1 → a0    （合并，mv 消除）
v2 → t0
v3 → t1

[0] （mv v1, a0 已删——同寄存器）
[1] blt a0, t0, L_then
[3] add t1, a0, t0
[5] mv  a0, t1
[6] ret

```

### 第 5 步：如果寄存器不够——spilling 用 defs/uses 和 mask_node
若把例子放大到"同时活跃 20 个 vreg、只有 10 个寄存器"：着色在某节点卡住（邻居颜色占满）→ 分配器选一个节点 spill：defs(v)/uses(v) 给出它所有的定义/使用指令位置，在每个定义后插 store、每个使用前插 load，把它搬进栈槽；

然后 mask_node 把它摘出图、重试着色（其余节点少了一个邻居，可能就能着完）；

unmask_node 恢复。_def_map/_use_map 就是为这一刻准备的。

## 五、一句话总结
interferencegraph.py 是寄存器分配的冲突模型：InterferenceGraphNode 把寄存器（及其合并集合、关联 move、着色状态）建模为节点；

calculate_interference 遍历流图，按"指令的 live_out ∪ kill 两两冲突、clobbers 与一切活跃值冲突"的规则建边，并顺手记录每个寄存器的 def/use 位置；

interfere()/combine()/mask_node() 三个接口分别支撑着色的冲突检查、coalescing 的节点合并（消 move）和 spilling 的试错摘除。

它被 registerallocator.py:311-312 调用，承接 flowgraph 的活性分析、为图着色输出"谁能与谁共存"的完整答案——分析定寿命，干涉图定冲突，着色定归宿。