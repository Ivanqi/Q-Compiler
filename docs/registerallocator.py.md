# registerallocator.py 详解
## 一、这个文件的作用是什么？
这是 qcc 的图着色寄存器分配器（Graph-Coloring Register Allocator）——后端最后一道大工序：

指令选择产生的抽象指令用的是虚拟寄存器（vreg），本文件把它们分配到物理寄存器（放不下的"溢出"到栈内存）。

docstring（1–114 行）是一篇完整的领域导论，点名了它实现的算法：
- 迭代寄存器合并（Iterated Register Coalescing, Appel & George），外加处理多寄存器类的 pq-test（Runeson2003/Smith2004）。

核心概念（docstring 8–26 行）：虚拟/物理寄存器、干涉图、预着色寄存器、coalescing（合并）、spilling（溢出）、寄存器类、寄存器别名（如 x86 的 rax/eax/ax/al）。

算法骨架（docstring 86–94 行 + alloc_frame 230–284 行的主循环）：
```python
build（建干涉图：上一讲 interferencegraph.py）
  ↓ 反复迭代直到无溢出：
    simplify（摘除"平凡可着色"节点压栈）→ coalesc（保守合并 move 相连节点）
    → freeze（放弃合并某节点）→ select_spill（挑溢出候选）
  ↓ assign_colors（弹栈逐个着色，失败者真溢出 → rewrite_program 插入 load/store 重来）
  ↓ remove_redundant_moves（删除被合并掉的 move）→ apply_colors（把颜色写回 vreg）

```

它是前几讲链条的终点：flowgraph（活性）→ interferencegraph（冲突）→ 本文件（着色/合并/溢出），产出的 frame 交给 codegen.py 的 emit_frame_to_stream 输出真实指令。

## 二、每个方法的作用
### Spill 代码生成器（128–191 行）
MiniCtx（128–143 行）：实现 ContextInterface 的迷你上下文——emit 只把指令收进列表（不发射进 frame），new_reg 从 frame 开新 vreg。spill 代码先在这里生成、再由 frame 插回正确位置；


MiniGen（145–191 行）：溢出代码生成器——妙处在于复用指令选择器：gen_load（152–162 行）构造树 MOV{fmt}(LDR{fmt}(FPREL))、gen_store（164–173 行）构造 STR{fmt}(FPREL, REG)，gen（175–179 行）把它们交给 selector.gen_tree——spill 的 load/store 依然走 BURG 模式匹配（架构的 LDR/STR/FPREL 模式），零重复代码；make_fmt/make_at 按 vreg 类型与指针宽度拼出树节点名。

### 初始化（197–228 行）
#### __init__：spill_gen = MiniGen(arch, selector)；alias（214 行）:
寄存器别名表（着色时检查"邻居占用了 rax，eax 也不能用"）；K/cls_regs（218–226 行）：每个寄存器类的"颜色数 K"与颜色集合（如 RISC-V 整数类 16 个寄存器）。

### 主入口 alloc_frame（228–284 行）
240–257 行：外层 while True（一轮分配）+ 内层工作列表循环——按优先级执行四动作之一：simplify_worklist 非空 → simplify；否则 worklistMoves → coalesc；否则 freeze_worklist → freeze；否则 spill_worklist → select_spill；全空 → 跳出；

260–277 行：assign_colors 弹栈着色；有失败者 → 真溢出：rewrite_program 改写程序后整轮重来（最多 30 轮，266–269 行防死循环）；

283–284 行：成功收尾——删除冗余 move、把颜色写回 vreg。

### 建图与工作列表 init_data（301–361 行）
305–312 行：串联前两讲——FlowGraph(frame.instructions) + calculate_liveness() + InterferenceGraph.calculate_interference(cfg)；

318–333 行：收集全部 ismove 指令，link_move（286–299 行：move 同时登记到源/目的节点的 moves 集合）；move 分类五件套：coalesced/constrained/frozen/active/worklistMoves；

344–355 行：节点分类——已着色 → precolored；不可着色 → spill_worklist；与 move 相关 → freeze_worklist；否则 → simplify_worklist。

### 判定工具（多寄存器类的关键）
has_edge(t, r)（366–387 行）：查边时穿透寄存器别名——t 是预着色节点时，检查 alias[t.reg] 里的别名寄存器是否与 r 有边（占 eax ⇒ rax 不能用）；

q(B, C)（389–401 行，lru_cache 缓存）：pq-test 的基础量——"一个 C 类寄存器最多能堵住几个 B 类寄存器"（由别名关系决定，如 x86 一个 r64 堵一个 r32）；

is_colorable(node)（403–426 行）：平凡可着色判定——单类时是经典的 度数 < K；多类时用 pq-test：num_blocked < K[类]（403–415 行注释解释：不管邻居怎么着色，本节点都保证有颜色可用）；

calc_num_blocked（428–437 行）：num_blocked = Σ q(自己类, 邻居类)——加权度数（别名使一个邻居可能堵住多个颜色）；

release_pressure（439–442 行）：节点被摘除后给邻居减压（度数维护）；

common_reg_class（616–631 行）：两节点的最小公共寄存器类（合并后新节点用哪类颜色）。

### 主循环四动作
simplify（451–463 行）：从 simplify_worklist 弹出节点压入 select_stack（Chaitin 算法：先摘掉、最后回填），mask_node 在图中暂时隐藏，给邻居 release_pressure + decrement_degree；

decrement_degree（465–477 行）：邻居度数下降后，spill 列表里变得可着色的节点"获救"——移入 freeze/simplify 列表（乐观：先假定能着色）；

coalesc（486–531 行）：保守合并，四种结局：
- 同一节点（mov x, x）→ 直接记入 coalescedMoves（501–507 行）；
- 两边都预着色或有冲突边 → constrainedMoves，放弃（508–517 行）；
- George 测试（518–522 行）：u 已着色且 u 类是 v 类的子类、v 的所有邻居都 ok → 可合并（合并进预着色节点——v 直接获得 u 的颜色）；
- Briggs 测试（522 行 conservative，546–565 行）：合并后的新节点"不可平凡着色邻居数 < K" → 可合并（保证合并不引入溢出——docstring 73–78 行的保守原则）；

combine(u, v)（567–614 行）：更新工作列表、重算邻居压力、ig.combine(u, v)（上一讲干涉图的合并原语）、新节点若不可着色则降入 spill 列表；

ok(t, r)（542–544 行）：George 测试的辅助判定；

freeze/freeze_moves（633–664 行）：对某节点放弃合并——冻结其全部 move，节点移入 simplify 列表（否则 coalescing 与 simplifying 互相卡死，这是 IRC 算法保证前进性的关键）；

select_spill（666–693 行）：从 spill 列表挑溢出优先级最低的节点：priority = (使用数 + 定义数) / 度数（683–688 行——用的少、冲突多的先试 spill），把它移入 simplify 列表（乐观溢出：也许着色时发现不用真溢出）。

### 收尾阶段
assign_colors（743–777 行）：逆序弹 select_stack（后摘先放，保证每次放回时邻居已着色）：收集邻居颜色（含别名展开，757–763 行），从本类颜色里挑一个空闲的（768–773 行）；没颜色可选 → 记入 spilled_nodes——乐观溢出的假设此时证伪，真溢出；

rewrite_program(node)（695–741 行）：真溢出改写——按寄存器类位宽分配栈槽（702–704 行）；对该节点名下每个临时寄存器的每个使用点插 load、每个定义点插 store（708–738 行）：指令里的旧 vreg 换成新 vreg（722 行），MiniGen.gen_load/gen_store 生成代码、insert_code_before/after 插回 frame；

remove_redundant_moves（779–782 行）：删除全部 coalescedMoves（源目同寄存器后 move 无意义）；

apply_colors（784–800 行）：把每个节点最终的 reg 颜色写回所有 vreg（set_color），并把用到的物理寄存器登记进 frame.used_regs（供序言/尾声决定保存哪些寄存器）；

check_invariants（802–819 行）：调试断言——各工作列表满足不变量（simplify 列表全可着色且无 move、spill 列表全不可着色、move 五分类互斥）。

## 三、详细例子
继续用贯穿全系列的 (a+b)*(a+b)，RISC-V 指令选择产物（a0/a1 是预着色物理寄存器）：
```c
[0] mv  v1, a0        ; 参数 a 搬进 v1
[1] mv  v2, a1        ; 参数 b 搬进 v2
[2] add v3, v1, v2    ; t3 = a + b
[3] mul v4, v3, v3    ; t5 = t3 * t3
[4] mv  a0, v4        ; 返回值搬进 a0
[5] ret

```

### 第 1 步：init_data（建图 + 分类）
活性（flowgraph）→ 干涉图：v1—v2、v1—v3、v2—v3（[2] 处三者同活）、v3—v4（[3] 处）；a0 与 v1、v4 之间无边（[0]、[4] 都是"最后一个使用"处的 move——这正是 coalescing 的机会）；

节点分类：a0/a1 → precolored；v1/v2/v4 有 move 相连 → freeze_worklist；v3 无 move → simplify_worklist；

moves：[0]、[1]、[4] → worklistMoves。

### 第 2 步：主循环
simplify：v3 摘除压栈（度数 3 < K=16）；

coalesc（worklistMoves 依次处理）：
- [4] mv a0, v4：u=a0（预着色）、v=v4；无冲突边；George 测试：u.is_colored ✓、v4 的邻居 {v3} 都可着色 ✓ → combine(a0, v4)：v4 并入 a0，move [4] 进 coalescedMoves；
- [0] mv v1, a0：同样通过 → combine(a0, v1)，move [0] 删除；
- [1] mv v2, a1：combine(a1, v2)，move [1] 删除；

工作列表全空 → 跳出。

### 第 3 步：assign_colors（弹栈着色）
弹 v3：unmask_node；邻居（合并后）是 a0、a1 → takenregs = {x10, x11}；本类空闲颜色里选一个 → v3 → t0；无溢出 → 不再 rewrite。

### 第 4 步：收尾
remove_redundant_moves：[0]、[1]、[4] 三条 move 全部删除；

apply_colors：v1 → a0、v2 → a1、v4 → a0、v3 → t0。

#### 最终代码：
```c
f:
    add  t0, a0, a1      ; v3 = v1 + v2（v1、v2 已合并到 a0/a1）
    mul  a0, t0, t0      ; v4 = v3 * v3（v4 合并到 a0——结果直接落在返回寄存器）
    ret

```

三条 move 全部被 coalescing 消灭，连"返回值搬运"都省了——mul 直接写进 a0。

这就是迭代寄存器合并相对朴素图着色的价值：move 指令的源和目的落同一寄存器，move 本身被删除（docstring 59–63 行）。

### 补充：spilling 的一条路径
若把 K 缩小到 2（假设只有两个可用寄存器）：v1/v2/v3 三角无法两色 → 全部落入 spill_worklist；select_spill 按 (uses+defs)/degree 挑一个（比如 v2）移入 simplify；

着色时 v2 无空闲颜色 → 真溢出 → rewrite_program(v2)：分栈槽，在 [1]（定义点）后插 store、在 [2]（使用点）前插 load（经 MiniGen 走 BURG 模式），整轮重来，直到着色成功。

## 四、一句话总结
registerallocator.py 是 qcc 的图着色寄存器分配器：它以 Appel & George 的迭代寄存器合并算法为骨架——init_data 串联 flowgraph 的活性与 interferencegraph 的冲突建出干涉图并初始化五类 move 集合和四类节点工作列表；

主循环按优先级执行 simplify（摘可着色节点压栈）→ coalesc（identity/constrained/George/Briggs 四种判定下的保守合并，用 combine 落地）→ freeze（放弃合并保证前进）→ select_spill（按 (uses+defs)/degree 挑乐观溢出候选）；

assign_colors 逆序弹栈着色（含别名穿透），失败者经 rewrite_program 用 MiniGen（复用指令选择器）插入栈 load/store 后整轮重试；

最终 remove_redundant_moves 删掉被合并的 move、apply_colors 把颜色写回每个 vreg——让指令选择产出的虚拟寄存器逐一落进真实 CPU 寄存器，必要时以栈内存为代价换取着色成功。