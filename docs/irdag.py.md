# irdag.py 详解
## 一、这个文件的作用是什么？
这是 qcc 后端指令选择的第一道工序：把 IR 翻译成"选择 DAG"（selection DAG）。文件 docstring（1–12 行）说得很清楚：
	- 指令选择之前要先建选择 DAG。一个 DAG 表示单个基本块的计算逻辑；为了用树匹配做指令选择，DAG 随后被拆分成一系列树（"树的森林"）。

为什么需要这道工序？

上一讲 codegen.py 的指令选择器干的是"模式匹配"——它需要一份显式的、以运算为中心的数据流图（谁用谁的结果、谁在谁前面执行），而不是 IR 那种线性指令列表。

本文件就是把 ir.Block 逐条"咀嚼"（docstring 用词）成图节点：
- 数据依赖：ADD 节点吃 a、b 的输出边——值从定义处流到使用处；
- 副作用顺序：load/store/调用用一条"控制链"（token/ctrl 边）串起来，保证它们的先后不被重排；
- 共享：同一 IR 值被多处使用时，DAG 里只有一个节点（这就是 DAG 相对树的优势——公共子表达式天然共享）。

产物是 selectiongraph.py 里的 SelectionGraph（节点集合 + 按块分组 + 根列表），交给指令选择器匹配架构指令模式。

## 二、每个方法的作用
### 函数与辅助类
#### prepare_function_info(arch, function_info, ir_function)（24–67 行）—— 全局函数信息准备：
28 行：epilog_label = Label(name + "_epilog")——每个函数统一的收尾标签（所有 return/exit 都跳这里）；


30–39 行：给每个基本块登记一个 Label（label_map），并给每个 Phi 节点预分配一个虚拟寄存器（phi_map，37 行按类型取寄存器类）——SSA 的 
Phi 在机器层就是"几条边把值 copy 进同一个 vreg"；

41–46 行：参数位置——架构有 determine_arg_locations 就按调用约定排（x86_64 的 RDI/RSI…），否则按序号；

48–59 行：每个参数：类型在 value_classes 里（标量）→ 建新 vreg；否则（blob 类型）→ 用物理位置（栈上传参）；

61–67 行：返回值类型在 value_classes 里 → 建 rv_vreg（返回寄存器类），否则 None（结构体返回值走隐式指针参数，不建）。

#### FunctionInfo（70–80 行）：
每个函数的"全局工作台"——frame、value_map（IR 值 → SGValue 的映射表，整个构建过程的核心字典）、label_map、epilog_label、phi_map、block_tails（每块的尾控制节点）。

##### depth_first_order(function)（83–93 行）：
按"先进先出的深度优先"排块序（从 entry 出发沿后继走），保证块的处理顺序与支配关系相容（Phi 的 copy 在正确时机生成）。

##### Operation（96–110 行）：
节点上的操作标记（如 ("ADD", i32)），__str__ 渲染成 ADDI32；MOV 不允许 ty=None（104 行显式断言，防止"移动到无类型寄存器"的图错误）。

##### make_map(cls) 装饰器（113–125 行）—— 整个文件的"魔法开关"：
自动把类里所有 do_xxx 方法注册进 f_map，键是对应的 IR 类——方法名 do_c_jump → 拆词首字母大写 → CJump → getattr(ir, "CJump") 得到 ir.CJump 类。

于是 block_to_sgraph:212 才能一行代码 self.f_map[type(instruction)](self, instruction) 完成分发：新增一种 IR 指令只需写一个 do_新名字 方法，无需改分发表。

#### SelectionGraphBuilder（128–613 行）—— 主体
##### __init__（135–138 行）：存架构，取 ptr_ty（架构指针类型信息；图中 ir.ptr 统一换成它，见 new_node 236–238 行）。

##### build(ir_function, function_info, debug_db)（140–187 行）—— 总入口：
153–160 行：给模块的全局变量、函数、外部符号各建一个 LABEL 节点并映射（它们的名字就是符号名）；

162–164 行：建全局 ENTRY 节点，产出控制 token（SGValue.CONTROL）——控制链的起点；

167–180 行：每个参数建 REG 节点（或栈参数建 FPREL 节点），add_map(arg, output)，并 chain 进控制链（参数"先于一切"）；

183–184 行：按 depth_first_order 逐块调 block_to_sgraph；

186–187 行：sgraph.check() 自检后返回图。

##### block_to_sgraph（189–219 行）：
单块处理——建块的 ENTRY 节点、重置当前 token；遍历每条指令：208–209 行 若是指令块的终止符（return/jump 等），先 copy_phis_of_successors（见下）；212 行 经 f_map 分发到对应 do_xxx；块尾记录 block_tails；最后补 EXIT 节点收口。

##### chain(sgnode)（227–230 行）—— 副作用顺序的核心机制：
把当前控制 token 作为输入连进新节点，新节点的控制输出成为新的当前 token。load、store、call、MOV、跳转都被 chain 起来，形成一条穿起所有"有副作用/有顺序要求"节点的链；

纯运算（ADD/MUL 等）不进链——只靠数据边，给指令选择留下重排自由。

##### new_node（232–244 行）：
建 SGNode(Operation(name, ty))、连输入、挂 value、设 group = current_block（按块分组，供后续拆分）、加入图。

##### new_vreg（246–250 行）：
从 frame 按类型新开一个虚拟寄存器。

##### add_map/get_value（252–258 行）：
value_map 的读写——"IR 值 → 图输出"的翻译记忆。

#### 指令处理（f_map 分发到的各 do_xxx）：
##### do_jump（221–225 行）：
JMP 节点，目标标签进 value；入链；

##### do_return（260–273 行）：
MOV 把返回值搬进 rv_vreg，再 JMP 到 epilog 标签（return 被拆成"送值 + 跳转"两步）；

##### do_c_jump（275–288 行）：
CJMP 节点，value 携带 (条件, yes 标签, no 标签)；

##### do_exit（290–294 行）：
JMP 到 epilog；

##### do_address_of（296–300 行）：
直接复用被取地址值的映射（AddressOf 在图里就是原值）；

##### do_alloc（302–320 行）：
frame.alloc(amount, alignment) 分一块栈槽（StackLocation），建 FPREL 节点（帧指针 + 偏移寻址），输出 wants_vreg=False（地址不占寄存器），调试信息记 FpOffsetAddress；

##### do_copy_blob（322–327 行）：
MOVB 节点（内存块拷贝）；

##### get_address（329–339 行）：
全局符号 → 新建 LABEL 节点；普通地址 → 查 value_map；

##### do_load（341–348 行）：
LDR 节点 + chain——load 必须排在它前面的 store/调用之后；

##### do_store（350–360 行）：

STR（blob 类型改发 MOVB）+ chain；

##### do_inline_asm（362–415 行）：

输入值 MOV 到新 vreg，ASM 节点携带 (模板, 输出寄存器, 输入寄存器, clobbers)，输出寄存器再 STR 回地址；

##### do_const（417–428 行）：
CONST 节点，wants_vreg=False——常量不进寄存器，直接作指令立即数（这是与 IR 的关键区别：IR 里 const 3 是一条指令，DAG 里它只是节点上的 value）；

##### do_literal_data（430–434 行）：
字符串数据 → frame.add_constant 存进常量池，建 LABEL 引用；

##### do_unop（436–443 行）：
NEG/INV；

##### do_binop（445–464 行）：
+ → ADD、- → SUB、| → OR、<< → SHL、* → MUL、& → AND、>> → SHR、/ → DIV、% → REM、^ → XOR——IR 运算符到架构无关操作名的翻译；

##### do_cast（466–496 行）：
无操作数的类型转换直接消除——同为整数、同位数、同寄存器类（如 u32↔i32 重解释）时，add_map(node, src_value) 共享同一输出；

否则建 I32TO 这类转换节点；

##### do_undefined（498–503 行）：

UND 节点；

##### _prep_call_arguments（505–524 行）：
调用实参逐个搬到新 vreg（MOV，因为实参要按调用约定落到参数寄存器，不能继续占用计算寄存器）；

#### _make_call（526–543 行）：

直接调用 → 目标就是函数名；间接调用 → 函数指针先 MOV 进 vreg；建 CALL 节点（value 携带 (目标, 实参, 返回值位置)），入链；

##### do_procedure_call/do_function_call（545–567 行）：

有返回值的调用再建一个 REG 节点指向结果 vreg 并映射，调用表达式就用它；

##### do_phi（569–576 行）：

Phi 在机器层就是 prepare_function_info 预分配的那个 vreg——建 REG 节点映射它；

##### copy_phis_of_successors（578–613 行）—— Phi 的"并行拷贝"：

块结束时（处理终止符前），把每个后继块里 Phi 的对应输入值 copy 进 Phi 的 vreg。分两步：

第一步把所有输入值先 MOV 到临时 vreg（585–593 行，注释说明这是为了应对"Phi 的输入计算本身用到 Phi 的 vreg"以及 Phi 循环——并行语义，不能逐个直接 copy）；

第二步再把临时值 MOV 进各 Phi 的 vreg（596–613 行）。

## 三、怎么与 codegen.py 联动？
联动链条（经过上一讲的指令选择器中转）：
```python
codegen.py: CodeGenerator.__init__
  ├─ 46 行: self.sgraph_builder = SelectionGraphBuilder(arch)     ← 本文件实例化
  └─ 54–56 行: InstructionSelector1(arch, self.sgraph_builder, reporter, weights)
               ← 作为 dag_builder 注入选择器

codegen.py: generate → generate_function → select_and_schedule (176–177 行)
  └─ instruction_selector.select(ir_function, frame)      (codegen.py:218)
       ├─ function_info = FunctionInfo(frame)             (instructionselector.py:341)
       ├─ prepare_function_info(arch, function_info, ...) (instructionselector.py:342 → 本文件 24 行)
       ├─ sgraph = self.dag_builder.build(...)            (instructionselector.py:345–347 → 本文件 140 行)
       ├─ forest = dag_splitter.split_into_trees(sgraph, ...)   ← DAG → 树的森林
       └─ munch_trees → tree_selector.gen → frame.instructions（抽象机器指令 + 虚拟寄存器）

codegen.py: generate_function
  ├─ register_allocator.alloc_frame(frame)      (182 行) ← 给本文件 new_vreg 出的虚拟寄存器着色
  └─ emit_frame_to_stream → 真实指令流            (197 行)

```

要点：
- SelectionGraphBuilder 不直接产机器码——它只产图；InstructionSelector1 把图拆成树、用动态规划树覆盖匹配架构指令模式，产出 frame 里的抽象指令；

- frame 与 FunctionInfo 共享——本文件分配的 vreg（new_vreg、phi_map、rv_vreg）都在 frame 上登记，之后寄存器分配器对它们统一着色；本文件分的栈槽（do_alloc → frame.alloc）就是最终栈布局的依据；

- codegen.py 里注释掉的 221–227 行是图式备选方案（sgraph_builder.build + instruction_scheduler.schedule）——若哪天启用，本文件无需改动，换个消费者即可。

## 四、详细例子
继续用贯穿全系列的 (a + b) * (a + b)，IR 经 optimize 后只剩：
```c
function f(a: i32, b: i32) -> i32 {
  entry:
    t3 = add a, b
    t5 = mul t3, t3
    return t5
}

```

### 第 1 步：prepare_function_info
epilog_label = Label("f_epilog")；label_map[entry] = Label("entry")；

无 Phi；arg_types = [i32, i32]，x86_64 的 determine_arg_locations 给出物理位置，但 i32 ∈ value_classes → 为 a、b 各建一个 vreg（arg_vregs）；

返回类型 i32 在 value_classes 里 → 建 rv_vreg（返回寄存器类）。

### 第 2 步：build 骨架
无全局变量；current_token = ENTRY 的 control 输出；

参数：REG(vreg_a) 节点 → value_map[a]，chain；b 同理。

### 第 3 步：block_to_sgraph(entry) 逐条翻译
| IR 指令 | 分发 | 图动作 |
| :--- | :--- | :--- |
| t3 = add a, b | do_binop | get_value(a)、get_value(b) → ADD(i32) 节点，输出映射为 value_map[t3] |
| t5 = mul t3, t3 | do_binop | get_value(t3) 两次返回同一个 SGValue → MUL(i32) 节点两个输入指向同一个 ADD 输出——这就是 DAG 的共享（若画成树，ADD 会被复制两份） |
| return t5 | 先 copy_phis_of_successors（无后继 Phi，跳过）→ do_return | get_value(t5) → MOV 节点 (value=rv_vreg) 搬进返回寄存器；再 JMP 节点指向 f_epilog；两条都入控制链 |

块尾记录 block_tails，补 EXIT 节点；sgraph.check() 通过。

图形态：
```c
ENTRY ──ctrl──▶ REG(a) ──▶ ADD ──┬─▶ MUL ──▶ MOV(rv) ──▶ JMP(f_epilog) ──▶ EXIT
        ──ctrl──▶ REG(b) ──▶─────┘      ▲
                              (同一 ADD 输出被 MUL 引用两次 = DAG 共享)

```

### 第 4 步：交回 codegen.py 的流水线
InstructionSelector1 拿到图 → split_into_trees 把 DAG 切成树森林 → 树覆盖匹配 x86_64 模式：ADD → addl、MUL → imull、MOV → movl、JMP → jmp → frame 得到带虚拟寄存器的抽象指令；

之后 codegen.py 的 alloc_frame 给 vreg 着色（t3/t5/rv 可复用 eax），emit_frame_to_stream 加序言/尾声后输出。

最终机器码与上一讲一致：
```c
f:
    push rbp
    mov rbp, rsp
    lea  eax, [rdi + rsi]    ; ADD —— 甚至被折叠进寻址模式
    imul eax, eax            ; MUL —— 共享的 ADD 结果复用 eax
    pop rbp
    ret

```

顺带说明：为什么 CSE 和 DAG 都要——CSE 在 IR 层消掉重复表达式（跨块也能），DAG 共享是图表示的自然特性（块内免费），两者互补；

本例中 t3 的两处使用在 IR 里已是共享值，DAG 如实反映即可。

## 五、一句话总结
irdag.py 是"IR → 选择 DAG"的翻译层：SelectionGraphBuilder.build 借助 make_map 装饰器自动注册的 do_xxx 分发表，把每个基本块的 IR 指令翻译成架构无关的图节点（ADD/MUL/LDR/STR/CALL/CJMP…），用 value_map 记住"IR 值 → 图输出"的对应、用 chain 串起副作用顺序、用 new_vreg/frame.alloc 预约寄存器和栈槽；

prepare_function_info 预先铺好标签、Phi vreg、参数与返回值位置，copy_phis_of_successors 用两步拷贝实现 Phi 的并行语义。

它被 codegen.py:46 实例化后注入 InstructionSelector1（作为 dag_builder），在 select_and_schedule 里经 select → build → 拆树 → 匹配 产出抽象指令，再由寄存器分配与帧发射完成机器码落地——是"IR 语义"与"机器指令"之间的关键翻译桥梁。