# instructionselector.py 详解
## 一、这个文件的作用是什么？
这是 qcc 后端的指令选择器——docstring（1–13 行）一句话："接收指令的 DAG，选出恰当的目标指令"。它还点明了算法的取舍：
- 从 DAG 上做指令选择是 NP 完全问题；最简单的策略是把 DAG 拆成树的森林再匹配树。

它是前两讲（irdag.py 建图、burg.py 定义规则）的总装与执行者，把 IR 翻译成"带虚拟寄存器的抽象机器指令"填进 frame：
```python
ir.Function
  └─ irdag.SelectionGraphBuilder.build ──▶ 选择 DAG（ADD/MUL/LDR/STR... 节点）
  └─ DagSplitter.split_into_trees ──▶ 树的森林
  └─ TreeSelector（BURG 算法）──────▶ 每条树 → 架构指令模式匹配 → frame.instructions

```

docstring 里的操作表（15–52 行）就是三方契约：irdag 负责产出这些操作节点（ADD、LDR、FPREL、CJMP……），架构的模式负责消费它们（reg -> ADDI32(reg, reg)），本文件负责组织匹配过程。

文件里有两个类：TreeSelector（BURG 匹配引擎，burg.py 算法的解释版）和 InstructionSelector1（流程编排：建图 → 拆树 → 匹配 → 填 frame），以及一个给模式模板函数使用的 InstructionContext。

## 二、每个方法的作用
### 常量表（69–117 行）
data_types（69 行）：所有 IR 类型名的大写（I32、U64、F32…）；

ops（71–103 行）：全部操作名（算术、位运算、一元、MOV/REG/UND、LDR/STR/CONST、CJMP、类型转换 I8TO…F64TO、FPREL/SPREL）；

terminals（107–117 行）：全部可能的终结符 = 操作 × 类型（如 ADDI32、MULF64）+ 无类型的 CALL/LABEL/MOVB/JMP/EXIT/ENTRY/ALLOCA/FREEA/ASM。它们在 __init__ 里注册进 BurgSystem（264–265 行），使规则系统知道哪些是终结符、哪些是开放端。

### InstructionContext（126–152 行）—— 模式模板函数的"工具箱"
模式命中后要发射指令，模板函数（架构里 @isa.pattern 装饰的函数）收到的 context 就是它：
- new_reg(cls)（135–137 行）：从 frame 开一个新虚拟寄存器（指定寄存器类）；
- new_label（139–141 行）：开新标签（跳转模式用）；
- move(dst, src)（143–145 行）：arch.move 生成一条架构相关的 mov 指令；
- emit(instruction)（147–152 行）：发射指令进 frame（frame.emit），若当前在匹配某棵树则把调试信息映射到该指令上（debug_db.map(tree, instruction)）——IR 到机器指令的调试映射链由此建立。

### TreeSelector（155–234 行）—— BURG 匹配引擎（上一讲算法的执行版）
#### gen(context, tree)（161–169 行）：
单棵树入口——检查树中名字都已定义 → burm_label 标注 → has_goal("stm") 检查（这棵树必须能化简成目标符号 stm，否则抛 "Tree not covered"，意味着架构模式集不完整）→ apply_rules 自顶向下发射指令；

#### burm_label(tree)（171–198 行）—— label 阶段：
先递归标注子节点（自底向上）；

给本节点挂新 State；逐条检查"根名匹配"的规则（get_rules_for_root + tree_terminal_equal 结构匹配）；

取开放端子节点（kids/nts）、检查 acceptance 条件和"每个开放端都已达成对应目标"；

代价 = Σ 子代价 + 规则代价（动态规划），经 mark_tree 写进状态；

#### mark_tree(tree, rule, cost, marked_rules)（200–208 行）：
set_cost(rule.non_term, cost + rule.cost, rule.nr) 记录"本节点达成 non_term 的最优代价与规则号"；

随即把链规则也按加算后的代价传播（206–208 行，递归防环）——一个节点能达成 reg 时，所有可由 reg 派生的非终结符也同时可达；

apply_rules(context, tree, goal)（210–224 行）—— select 阶段：state.get_rule(goal) 取该目标的最优规则号 → 先递归处理所有开放子树（拿到子结果，如已分配的寄存器）→ 调规则模板 rule_f(context, tree, *results)（NT0/NT1 就是这些子结果）→ 返回结果（通常是寄存器）；

#### kids/nts（226–234 行）：

从规则树模式经 BurgSystem.get_kids/get_nts 取开放子树与目标名。

### InstructionSelector1（237–408 行）—— 流程编排
#### __init__(arch, sgraph_builder, reporter, weights=(1,1,1))（246–290 行）：
256–259 行：接住 codegen.py 注入的 dag_builder（irdag 的 SelectionGraphBuilder）、架构、报告器，并新建 DagSplitter(arch)（拆树器）；

262–265 行：新建 BurgSystem，注册全部终结符；

268–269 行：两条特殊规则：stm -> CALL、stm -> ASM（语句级的调用和内联汇编树不需要算术模式，直接由 call_function/inline_asm 处理）；

272 行：_create_undefined_rules——为每个寄存器类的每种 IR 类型生成一条 UND 规则（292–319 行）：匹配 UNDI32 这类节点时开一个新寄存器并发 RegisterUseDef（声明"该寄存器被定义"）——对应 IR 里 mem2reg 留下的 Undefined 值（未初始化变量）；

274–287 行：灌入架构的全部模式（arch.isa.patterns，即各架构用 @isa.pattern 装饰器声明的规则），代价 = size×权重0 + cycles×权重1 + energy×权重2（276–280 行）——codegen.py 里 optimize_for 的权重表在这里生效；

289–290 行：sys.check() 自检，建 TreeSelector。

#### call_function（321–324 行）：

从树节点的 value 取出 (label, args, rv)，交给 arch.gen_call 生成调用序列（参数搬运、call 指令、结果取回）。

#### inline_asm（326–333 行）：
取出模板/寄存器/clobbers，发 InlineAssembly 指令（codegen.py 的 emit_frame_to_stream 里再交给汇编器）。

#### select(ir_function, frame)（335–382 行）—— 总入口（上一讲已看过，这里串成完整剧情）：
341–342 行：FunctionInfo(frame) + prepare_function_info（标签、Phi vreg、参数/返回值位置）；

345–347 行：dag_builder.build(...)——irdag 建选择 DAG；

354–357 行：dag_splitter.split_into_trees(...)——DAG → 树的森林（共享节点处切开成 REG 节点，这就是规避 NP 完全问题的关键一步）；

361–365 行：建 InstructionContext，发射 arch.gen_function_enter（函数序言指令：把物理参数寄存器搬进 vreg 等）；

368 行：munch_trees 逐树匹配发射主体；

371–377 行：发射 arch.gen_function_exit（函数尾部：返回值搬进约定寄存器、跳 epilog）。

#### munch_trees(context, trees)（384–404 行）：
遍历森林——树的切割点可能是现成的架构指令（isinstance(tree, Instruction) → 直接 context.emit），否则是 Tree → gen_tree → tree_selector.gen。

#### gen_tree（406–408 行）：
委托 TreeSelector.gen。

### 三、与前后文件的联动（一句话回顾）
```python
codegen.py:54-56   InstructionSelector1(arch, sgraph_builder, reporter, weights)
codegen.py:218     instruction_selector.select(ir_function, frame)
   ├─ irdag.py      prepare_function_info + SelectionGraphBuilder.build → 选择 DAG
   ├─ dagsplit.py   split_into_trees → 树的森林
   ├─ burg.py       BurgSystem + Tree（规则库），TreeSelector 执行 label/select
   ├─ arch/isa.py   @isa.pattern 模式（终结符树 + 模板函数），cost 按 weights 加权
   └─ frame         产出：抽象机器指令 + 虚拟寄存器 → 交给 codegen.py 的寄存器分配

```

## 四、详细例子
继续用贯穿全系列的 (a + b) * (a + b)，优化后的 IR：
```c
function f(a: i32, b: i32) -> i32 {
  entry:
    t3 = add a, b
    t5 = mul t3, t3
    return t5
}

```

### 第 1 步：select() 的前半程——图与森林
- prepare_function_info：label_map[entry]、epilog_label、参数 a/b 各建 vreg、返回类型建 rv_vreg；
- dag_builder.build（irdag）：REG(a)、REG(b)、ADD（输出映射 t3）、MUL（两个输入都指向同一 ADD 输出——DAG 共享）、MOV（搬进 rv_vreg）、JMP(epilog)，还有控制链；
- dag_splitter.split_into_trees：在共享点/副作用链处切割，得到若干棵树，比如（简化）：

```python
树1: STM树： MULI32(ADDI32(REGI32, REGI32), ADDI32(...))   ← 或拆成更小的树
树2: MOVI32(...)
树3: JMP（独立指令树）
```

### 第 2 步：munch_trees → TreeSelector.gen 对每棵树
以 MULI32(ADDI32(REGI32, REGI32), REGI32) 为例（假设架构规则集里有）：
```c
reg -> REGI32 0                  (. NT0 .)      ← REG 节点直接用其 vreg，代价 0
reg -> ADDI32(reg, reg) 2        (. add NT0, NT1 .)
reg -> MULI32(reg, reg) 3        (. mul NT0, NT1 .)

```

label 阶段（burm_label 自底向上）：
- 叶子 REGI32：规则命中，代价 0 → state[reg] = (0, r_reg)；
- ADDI32：两个子节点都有 reg 目标 → 代价 0+0+2 = 2 → state[reg] = (2, r_add)；
- 顶层 MULI32：左子 reg 代价 2、右子 reg 代价 0 → 2+0+3 = 5 → state[reg] = (5, r_mul)。

select 阶段（apply_rules(tree, "stm")，经链规则 stm 可达 reg）：顶层选 r_mul → 递归左子树选 r_add → 两个 REG 叶子返回它们的 vreg。

模板函数依次执行（架构的 Python 函数）：
```c
mul  vreg_t5, vreg_t3, vreg_t3    ← 顶层：mul NT0 NT1（NT0 是 ADD 子树的结果 vreg_t3）
add  vreg_t3, vreg_a, vreg_b      ← 左子树：add NT0 NT1

```

经 InstructionContext.emit → frame.emit——frame 里有了带虚拟寄存器的抽象指令。

### 第 3 步：select() 的收尾
gen_function_enter 发射参数搬运（把物理参数寄存器/栈位置搬进 vreg_a/vreg_b），gen_function_exit 发射尾部（vreg_t5 的结果在 do_return 已 MOV 进 rv_vreg，这里生成"把 rv_vreg 送到调用约定返回寄存器 + 跳 epilog"）。

### 第 4 步：交回 codegen.py
select_and_schedule 返回后，codegen.py 的 register_allocator.alloc_frame(frame) 给所有 vreg 着色（vreg_t3/vreg_t5 可复用 eax，vreg_a/vreg_b → edi/esi），emit_frame_to_stream 加序言/尾声输出：
```c
f:
    push rbp
    mov rbp, rsp
    lea  eax, [rdi + rsi]    ; add —— 图匹配 + 寻址模式融合
    imul eax, eax            ; mul
    pop rbp
    ret

```

### 补充：代价权重如何影响选择
若架构同时有普通 mul（size=1, cycles=3）和复杂乘加 muladd 模式，__init__ 的 276–280 行按 codegen.py 传入的权重算代价——optimize_for="size"（权重 10,1,1）倾向短指令，"speed"（3,10,1）倾向少周期，"co2"（1,2,10）倾向低能耗。

同一棵树在不同优化目标下可能选出不同的模式覆盖——BURG 动态规划 + 加权代价 = 面向目标的选择。

## 五、一句话总结
instructionselector.py 是"选择 DAG → 架构指令"的匹配与编排中枢：文件头定义操作/终结符词汇表作为 irdag 与架构模式的共同契约；

InstructionSelector1.__init__ 把终结符、特殊规则（CALL/ASM）、未定义值规则和 arch.isa.patterns（代价按 size/cycles/energy 权重加权）灌入 BurgSystem；

select 编排"建 DAG → 拆树森林（规避 NP 完全）→ 序言 → 逐树 BURG 匹配（label 标代价 + select 选最优规则，链规则传播目标）→ 尾部"的完整流程，TreeSelector 执行 burg.py 的动态规划算法，InstructionContext 把模板发射的抽象指令（虚拟寄存器）填进 frame，最终交给寄存器分配与帧发射生成机器码。