# -*- coding: utf-8 -*-
"""M6 补充：为子代理未完成的文件添加中文注释（只加注释，不改语义）。

用法：python3 tools/add_comments.py
"""
import os
import sys

ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "qcc"
)

# ---------------- 重点文件：详细功能说明（需求5，对应 docs/*.md） ----------------
DETAILED = {
    "midend/ir.py": """统一中间表示（IR）：编译器所有阶段交换信息的核心数据结构。

需求3 流水线的"中端"根基：前端（frontend/c/codegenerator）把 C 程序
翻译成这里的 IR；中端优化（midend/opt/）在 IR 上做变换；后端
（backend/codegen/）再把 IR 翻译成机器指令。层级结构：

    Module（编译单元）
    ├── ExternalSubRoutine / Variable（外部符号、全局变量）
    └── SubRoutine（函数/过程）
        ├── Parameter（参数）
        └── Block[]（基本块）
            └── Instruction[]（指令序列）

关键类型：
- Module —— 编译单元：functions/variables/externals，及 debug_db 调试库
- SubRoutine/Function/Procedure —— 函数：entry 入口块、blocks、arguments
- Block —— 基本块：线性指令列表、前驱/后继块、is_empty/is_closed 检查
- Value —— 值的基类：ty（类型）、used_by（使用它的指令集合，use-def 链）
- Instruction —— 指令基类：position（在块中的位置）、uses、replace_use
  等 use-def 维护机制；关键子类：
  - Const —— 常量
  - Binop —— 二元运算（+,-,*,/,&,|,<<,>>…）
  - Load / Store —— 内存读写（volatile 表示不可优化的硬件访问）
  - Alloc / AddressOf —— 栈上分配 / 取地址
  - Jump / CJump —— 无条件 / 条件跳转（块的终结指令）
  - Exit / Return —— 过程返回 / 函数返回
  - Phi —— SSA φ 节点（mem2reg 的产物）
  - FunctionCall / ProcedureCall —— 函数调用
- 类型系统：ptr/i8..i64/u8..u64/f32/f64/BlobDataTyp 等

每个 Value 维护 use-def 链（value_use 描述符自动追踪引用关系），
优化 pass 靠 replace_by/insert_instruction 等方法安全改写 IR。

详见 docs/流程梳理.md（阶段 2：IR 定义）。
""",

    "frontend/c/semantics.py": """C 前端语义分析（semantic analysis）：给语法树赋类型、做合法性检查。

在 C 前端流水线中的位置（需求3 的"前端"）：
    preprocessor（预处理） → lexer（词法） → parser（语法）
        → **semantics.py（语义：类型检查）** → codegenerator（AST → IR）

parser.py 只回答"Token 顺序是否合法"，每解析出一个语法结构就调用
self.semantics.on_xxx(...)，由本文件的 CSemantics 回答三个问题：
- 这个东西的类型是什么？（类型计算、类型检查）
- 要插入哪些隐式转换？（char→int 整型提升、数组退化为指针、常量选型等）
- 它是否合法？（变量未定义、lvalue 检查、重复定义、重复 case、
  参数个数/类型不匹配、返回类型错误等）

关键类/函数：
- CSemantics —— 语义分析器主体，on_xxx 系列方法按 C 语法条目组织；
  每个表达式/声明方法返回带类型的节点，非法时经 self.error 报错
- coerce —— 隐式类型转换（整型提升、数组转指针等）
- on_binop —— 二元运算：普通算术、指针运算（ptr + int 等）分别处理
- on_call —— 函数调用：参数个数/类型检查、可变参数、隐式函数声明
- on_switch/on_case —— switch 检查（重复 case、非整数 case 值等）
- check_initializer —— 初始化器检查（数组/结构体初始化列表）

产物是"带类型的 AST"（type-checked AST），交给 codegenerator.py
生成 IR。本文件不做常量求值（委托 context.eval_expr）与 sizeof 求值。

详见 docs/semantics.py.md。
""",

    "frontend/c/codegenerator.py": """C 前端最后一站：遍历带类型的 AST，生成 IR（中端中间表示）。

C 前端流水线（需求3 的"前端"）：
    builder.py 组装 → parser 检查语法 → semantics 赋语义（带类型的 AST）
        ↓
    **codegenerator.py：CCodeGenerator 遍历 AST → midend.ir.Module**
        ↓
    api.optimize()：opt/ 的 pass 优化 IR → 后端生成机器码

关键类/方法：
- CCodeGenerator —— 主代码生成器：emit/emit2 返回语句、局部变量（栈帧
  上的 alloc）、表达式求值；每个 C 语句/表达式类型有对应的 gen_xxx 方法
- gen_binop —— 二元运算：先求值左右子树再发 ir.Binop；
  除零等可用编译期常量折减
- gen_function —— 函数定义：prologue（参数存栈）、函数体、epilogue
- gen_call —— 函数调用：实参求值 → ir.FunctionCall/ProcedureCall
- gen_if/gen_switch/gen_while/gen_for —— 控制流：创建基本块并用
  CJump/Jump 连接（switch 用跳转表或级联比较）
- gen_address_of / gen_deref —— 取地址/解引用（左值 → 地址）
- emit_local —— 局部变量：栈帧内 ir.Alloc + 存初始值
- CExpressionEvaluator —— 编译期常量表达式求值（数组维度、case 值、
  静态初始化等）
- CBackend —— 已废弃的旧后端（历史遗留，当前使用 CCodeGenerator）

产物是"未优化"的 IR 模块（alloc/store/load 形态），随后由
optimize()（mem2reg 等）精简为 SSA 形态。

详见 docs/codegenerator.py.md。
""",

    "backend/codegen/burg.py": """BURG（Bottom-Up Rewrite Generator，自底向上重写生成器）：
指令选择阶段的树模式匹配工具（iburg/burg 的 ppci 实现）。

在后端流水线中的位置（需求3 的"后端"）：
    irdag.py 建选择 DAG → dagsplit.py 切成树的森林
        → **burg.py：树模式匹配，为每棵树选最小代价的机器指令组合**
        → registerallocator 分配物理寄存器

输入是一份模式描述（codegen/burg.grammar，本文件在 import 时用
frontend/tools/yacc 现场解析它生成语法解析器），输出一个匹配器类。
规则形如：
    reg -> ADDI32(reg, reg) 2 (. add NT0 NT1 .)
含义：树 ADDI32(reg,reg) 可用代价 2 匹配为"add 第0子,第1子"的指令。

生成匹配器用动态规划找最佳匹配，两步：
- label：自底向上遍历，给每个节点标注可匹配的规则及代价
- select：再次遍历，每步选到达目标（如 reg）最便宜的路径

关键类：
- BurgSystem —— 规则集合：非终结符/终结符、chain rules、
  get_rules_for_root 按根名查规则
- BurgGenerator —— 生成匹配器 Python 脚本（内含对 treematcher 的调用）
- TreeSelector（在 instructionselector.py）—— 使用生成匹配器的运行时

注意：本文件底部 generate() 生成的代码字符串里含
`from qcc.backend.codegen.treematcher import ...`，是运行时 exec 的
源码，修改时必须保持同步。

详见 docs/burg.py.md 与 docs/BURG 树匹配算法、图着色寄存器分配.md。
""",

    "backend/codegen/instructionselector.py": """指令选择器：把 IR 基本块的 DAG 翻译成目标机器的指令序列。

后端流水线核心环节（需求3 的"后端"）：
    ir.Function
      └─ irdag.SelectionGraphBuilder —— 建选择 DAG（ADD/MUL/LDR/STR 节点）
      └─ DagSplitter.split_into_trees —— 把 DAG 切成树的森林
      └─ **TreeSelector（本文件）—— 对每棵树做 BURG 模式匹配，发射架构指令**
      └─ 结果填入 frame.instructions（带虚拟寄存器）

要点：
- 在 DAG 上直接做指令选择是 NP 完全问题，所以先拆成树再匹配树；
- ContextInterface 是匹配上下文的抽象：new_reg 分配虚拟寄存器、
  emit 发射指令、move 生成搬移——后端 codegen.py 提供实现；
- 架构的 @isa.pattern 规则（各 arch/xxx/instructions.py）经 burg
  编译后由 TreeSelector 调用，按 size/cycles 代价选最优组合。

关键类：
- TreeSelector —— BURG 匹配器运行时：gen 方法对整棵树自底向上匹配
- InstructionSelector1 —— 对 function.instructions 逐块走完整流程
  （拆树 → 匹配 → 发射）

详见 docs/instructionselector.py.md（含 match 过程逐步演示）。
""",

    "backend/codegen/flowgraph.py": """流图（CFG）+ 活跃性分析：寄存器分配的前置分析。

后端流水线中的位置（需求3 的"后端"）：
    指令选择输出"线性抽象指令列表"（frame.instructions，虚拟寄存器）
        → **flowgraph.py：切基本块 + 计算每个寄存器在哪里活跃**
        → interferencegraph.py 建干涉图 → registerallocator.py 着色

本文件提供：
- 基本块划分：跳转目标与跳转后继是块边界，把线性列表切成 CFG 节点；
- 块级 gen/kill：每个块"使用（read）了哪些寄存器、重定义（write）了哪些"；
- 活性分析：不动点迭代，算出每个块、每条指令的 live_in/live_out；
- 活动区间（live ranges）：某 vreg 从哪条指令活到哪条指令
  —— 干涉图建图（interferencegraph）的依据。

关键类：
- FlowGraph —— 图本身：nodes（基本块）、get_node(ins) 按指令找块
- FlowGraphNode —— 基本块节点：instructions、gen/kill/live_in/live_out
- Def/DefUse/Use（arch/example.py 等）—— 指令的 read/write 寄存器
  由 arch 层定义（defined_registers/used_registers/reads/writes）

在 registerallocator 中被调用：建干涉图、判断两个 vreg 能否合并。

详见 docs/flowgraph.py.md。
""",

    "backend/codegen/interferencegraph.py": """干涉图（interference graph）：图着色寄存器分配的核心数据结构。

后端流水线中的位置（需求3 的"后端"）：
    flowgraph.py 算出每个虚拟寄存器的活性/活动区间
        → **interferencegraph.py：汇总成干涉图**
        → registerallocator.py：图着色 → 物理寄存器或溢出到栈

图的结构：
- 节点 = 一个寄存器（虚拟寄存器 vreg 或已着色的物理寄存器）；
- 边 = 两个寄存器的"生命期重叠"——绝不能分到同一物理寄存器
  （否则后写的会覆盖另一个还在用的值）；
- 底层是 MaskableGraph（可掩码图）：着色失败要 spill、合并失败要
  回退时，可以临时"隐藏"节点而不破坏图本体。

关键类/方法：
- InterferenceGraph —— 干涉图：calculate_interference(flowgraph)
  依据每条指令的 live_in/live_out 与 kill 加边
- interfere(tmp1, tmp2) —— 查询两寄存器是否干涉
- combine(n, m) —— 合并两个节点（寄存器合并成功后）
- get_node(tmp) —— 按寄存器取节点（临时映射 temp_map）

建图逻辑（calculate_interference）：
对每条指令，把 live_and_def = ins.live_out | ins.kill 中的寄存器
两两连边，再连上 ins.clobbers（被破坏的寄存器）。

详见 docs/interferencegraph.py.md。
""",

    "backend/codegen/registerallocator.py": """图着色寄存器分配器（Graph-Coloring Register Allocator）：
后端最后一道大工序，把虚拟寄存器映射到物理寄存器（放不下的溢出到栈）。

后端流水线中的位置（需求3 的"后端"）：
    指令选择（虚拟寄存器）→ 指令调度 → **registerallocator：图着色**
    → 栈帧发射（加 prologue/epilogue）→ 机器码输出

算法（迭代寄存器合并，Iterated Register Coalescing，Appel & George，
加处理多寄存器类的 pq-test）：
    build   —— 建干涉图（interferencegraph）
    simplify—— 反复删除度数 < K 的节点入栈（可着色）
    coalesce—— 合并 move 相关的寄存器（消除 mov 指令）
    freeze  —— 合并失败时冻结 move
    spill   —— 图着色失败：把变量"溢出"到栈内存
    select  —— 按出栈顺序反向选颜色（物理寄存器）

关键类：
- RegisterAllocator —— 抽象基类：alloc_frame(frame) 接口
- GraphColoringRegisterAllocator —— 本算法主体：
  alloc_frame 主循环（simplify/coalesce/freeze/spill/select），
  init_data 建干涉图与 move 列表
- GraphColoringRegisterAllocator 内部辅助：coalesc_register
  （合并寄存器）、select_colors（选色）、assign_colors、spill_var
  （溢出变量：定义处存栈、使用处取栈）

本文件同时被 backend/codegen/codegen.py 调用（后端主循环的第三步），
产出的 frame 交给 emit_frame_to_stream 生成序言/尾声与最终指令流。

详见 docs/registerallocator.py.md 与 docs/BURG 树匹配算法、图着色寄存器分配.md。
""",
}

# ---------------- 简要文件：2-4 行功能说明 ----------------
BRIEF = {
    "backend/arch/encoding.py": """机器指令编码系统：把指令对象编码成二进制位的全部基础设施。

定义 Token（位域容器）+ bit/bit_range 描述符（把指令字段映射到比特位）、
Syntax（汇编语法渲染）、Relocation（重定位）与 Instruction 基类。
所有架构（arm/riscv/x86_64）的指令类都继承这里的基类，是"后端 → 机器码"
的关键一环。""",

    "backend/arch/generic_instructions.py": """架构无关的通用机器指令。

Label（跳转标签）、Alignment（对齐填充）、RegisterUseDef（标记函数
用到的寄存器，用于调用约定）、SectionInstruction 等。
被所有架构后端（codegen 与 arch/*/arch.py）复用。""",

    "backend/arch/isa.py": """Isa 类：架构指令集与汇编器的注册表。

@isa.pattern(...) 装饰器把"IR 树模式 → 机器指令"的规则挂到指令类上
（指令选择的规则来源，BURG 匹配的就是这些 pattern），
另有 register_relocation/register_instruction 等登记接口。""",

    "backend/arch/registers.py": """寄存器抽象：Register 基类与 RegisterClass 寄存器类。

Register 有 num（编号）与 color（着色后的物理寄存器）；is_colored 表示
已分配物理寄存器。RegisterClass 描述一组寄存器（如 x86_64 的通用寄存器
类）、别名（如 rax/eax/ax/al 是同一寄存器不同宽度）。
寄存器分配器（registerallocator.py）直接使用这些抽象。""",

    "backend/arch/stack.py": """栈帧布局：StackLocation 与 FramePointerLocation。

记录某个值在栈帧中的位置（相对帧指针/栈指针的偏移与大小），
以及该架构帧指针放在栈顶还是栈底（FramePointerLocation）。
代码生成器为局部变量分配栈槽（frame.alloc）时使用。""",

    "backend/arch/token.py": """位字段工具：Token 基类 + bit/bit_range 描述符。

一个 Token 是若干"位域"（bit_range）的组合——指令编码时把各字段
拼成整数、解码时切回字段。arch/encoding.py 的 Instruction 编码
依赖本模块；是机器码生成的最底层。""",

    "backend/arch/arm/thumb_instructions.py": """ARM Thumb 指令集（16 位压缩指令）的定义与编码。

thumb_isa 包含 Thumb 指令的汇编语法、encode/decode 与
@isa.pattern 树匹配规则（IR → Thumb 指令）。
ARM 架构在 Thumb 模式下生成更紧凑的代码。""",

    "backend/arch/riscv/rvf_instructions.py": """RISC-V F 扩展（单精度浮点）指令定义与编码。

flw/fsw/fadd.s/fmul.s/fcvt 等指令的汇编语法与编码；rvfisa 同时
注册 @isa.pattern 规则，把 IR 的浮点运算匹配为 RVF 指令。""",

    "backend/arch/riscv/rvfx_instructions.py": """RISC-V F 扩展指令的"树模式"匹配规则。

rvfxisa 用 @isa.pattern 把 IR 节点（如实浮点加减乘除、整数与浮点
互转）映射到 rvf_instructions 里定义的 RVF 指令——指令选择阶段
（BURG）使用的模式库之一。""",

    "backend/arch/x86_64/instructions.py": """x86_64 指令集定义与编码（本包最大的指令库）。

bits64/bits16/bits8 等类的每一条指令都有汇编语法、encode/decode、
relocations 与 @isa.pattern 树匹配规则；lea/movzx/movsx 等常用指令
有专门模式。指令选择阶段（BURG）根据这些 pattern 把 IR 变成
x86_64 机器指令，寄存器分配完成后按 encode() 生成真实机器码。""",

    "backend/arch/x86_64/registers.py": """x86_64 寄存器：通用寄存器（rax/rbx/... 及其 8/16/32 位
别名）、段寄存器、SSE 寄存器（xmm0-15）、x87 浮点栈。

还定义了 SysV 与 Windows 调用约定的参数/返回值/被调用者保存
寄存器列表，供栈帧发射（codegen）使用。""",

    "backend/arch/x86_64/sse2_instructions.py": """SSE2 指令集：浮点标量（movsd/addsd/...）与整数向量指令。

sse2_isa 提供汇编语法、编码以及 @isa.pattern 规则——IR 的浮点
运算主要靠这些指令在 x86_64 上实现。""",

    "backend/binutils/archive.py": """静态库（归档文件）支持。

get_archive 把 .a 文件解析成 Archive 对象；链接器（linker.py）在
未解析符号时从库里挑选定义了该符号的成员对象加入链接。""",

    "backend/binutils/assembler.py": """汇编器基类（BaseAssembler）：把汇编文本变成二进制字节流。

按行/按 Token 驱动 assembler 状态机，输出经 OutputStream 写入
目标文件的节。每个架构（arch/*/arch.py）都从它派生出自己的
assembler 类；api.asm() 通过 march.assembler 获得它。""",

    "backend/binutils/debuginfo.py": """调试信息：DebugInfo/DebugDb 与行号、变量、帧偏移的映射。

debug=True 编译时记录每条 IR 指令对应的源码位置与变量位置；
链接器（linker.py）用 SymbolIdAdjustingReplicator 在合并对象时
平移符号编号。是调试器（已裁剪）与编译报告共用的数据层。""",

    "backend/binutils/disasm.py": """反汇编器基类（Disassembler）。

api.disasm() 用它把二进制数据按架构的指令解码规则还原成文本，
便于人工检查生成的机器码。""",

    "backend/binutils/layout.py": """链接脚本（memory map）解析。

用 frontend/tools 的 LR 文法解析器解析
ENTRY(...) / MEMORY ... { SECTION(...) } / ALIGN(...) 语法，
得到 Layout（内存区段与节放置规则）；链接器按它给各节分配运行地址。""",

    "backend/binutils/objectfile.py": """目标文件（ObjectFile）：链接器与汇编器共用的核心数据结构。

ObjectFile = 架构 + 节（Section）+ 符号表（Symbol）+ 重定位条目
（RelocationEntry）+ 映像（Image，链接后的连续内存区）+ 调试信息；
支持序列化（save/load）与各节的拼接。编译（api.cc）、汇编（api.asm）
的输出都是 ObjectFile，链接器把它们合并成新的 ObjectFile。""",

    "backend/binutils/outstream.py": """输出流层次：机器指令最终"流向哪里"。

TextOutputStream（汇编文本）、BinaryOutputStream（写目标文件节）、
FunctionOutputStream（收集指令列表给报告）、MasterOutputStream
（扇出到多个流）。后端 codegen 的 emit_frame_to_stream 只面对
OutputStream 接口，输出到文本/二进制/报告由外部决定。""",

    "backend/binutils/__init__.py": """二进制工具包：汇编器、目标文件、链接器、输出流与调试信息。

需求3 流水线中"后端 → 目标文件"的支撑层（其中 linker.py 是需求4
列出的迁移模块）。""",

    "backend/codegen/dagsplit.py": """DagSplitter：把选择 DAG（irdag 的产物）切成树的森林。

在 DAG 上直接做指令选择是 NP 完全问题；把 DAG 按"多入边节点"
切开变成树，之后就能用 BURG 做树模式匹配（多项式时间）。
instructionselector.py 的 InstructionSelector1 调用本模块。""",

    "backend/codegen/instructionscheduler.py": """指令调度器（当前默认 NoScheduler，直接顺序发射）。

VLIW 类架构需要重排指令减少停顿；当前三个后端（arm/riscv/x86_64）
为顺序发射，保留此接口以备扩展。codegen 主循环的第二步。""",

    "backend/codegen/peephole.py": """PeepHoleStream：指令输出前的滑动窗口，做简单窥孔优化。

在最终输出前检查相邻的几条指令（如冗余的寄存器搬移、可合并的
跳转），用更短/更少的指令替换。codegen.emit_frame_to_stream
把指令先过 PeepHoleStream 再进入真实输出流。""",

    "backend/codegen/selectiongraph.py": """选择图（SelectionGraph）：指令选择阶段使用的 DAG 结构。

SGNode（节点：IR 指令、存储地址、常量等）/ SGValue（节点的输出值）
/ SelectionGraph（图本体 + 拓扑/支配辅助）。irdag.py 把 IR 基本块
翻译成这张图，dagsplit.py 再把它切成树。""",

    "backend/codegen/treematcher.py": """BURG 生成匹配器的运行时基类（BaseMatcher/State）。

burg.py 生成的 Matcher 类继承 BaseMatcher，靠 State（当前节点、
其子节点的匹配状态）做 label/select 两阶段的动态规划。
平时无需直接使用——由 TreeSelector（instructionselector）调用。""",

    "backend/codegen/__init__.py": """后端代码生成包：IR → 机器指令流水线。

需求3"后端"的核心：irdag 建图 → burg 树匹配 → 指令选择 →
调度 → 图着色寄存器分配 → 栈帧发射（codegen.CodeGenerator）。""",

    "backend/format/header.py": """二进制文件头描述工具：Header/Field 系列类。

用"字段"（大小、位置、初值）声明式地描述文件头（ELF 头、
PE 头共用），读/写时自动完成字节序与对齐处理。format/elf/headers.py
基于它定义 ELF 头结构。""",

    "backend/format/hexfile.py": """Intel HEX 格式读写（HexFile）。

api.objcopy(..., 'hex') 用 HexFile 把链接后的映像写成
":10010000..." 形式的 HEX 记录，常用于嵌入式烧录；也支持读回。""",

    "backend/format/__init__.py": """输出格式包：ELF（需求2 要求支持）与 Intel HEX。

compile → link → objcopy 的最后一站：把 ObjectFile 落成
elf/bin/hex 等具体文件格式。""",

    "backend/format/elf/file.py": """ElfFile：ELF 文件的内存表示（读回结果）。

持有 ELF 头、节（含名称/数据/地址）与符号信息；write_elf 写出、
read_elf 读回后都得到 ElfFile，供检查与测试校验。""",

    "backend/format/elf/headers.py": """ELF 头结构定义：ElfHeader / SectionHeader / ProgramHeader 等。

用 format/header.py 的 Header/Field 声明 ELF 各头部的字节布局
（e_machine、e_type、sh_name、p_flags…），并实现各架构的
machine 编号登记（x86_64/ARM/RISC-V）。""",

    "backend/format/elf/reader.py": """ELF 读取入口：read_elf(f) → ElfFile。

解析 ELF 头、节表与符号表，把文件字节还原成 ElfFile 对象
（file.py）。用于验证我们写出的 ELF 是否合法。""",

    "backend/format/elf/string.py": """ELF 字符串表（.strtab）解析。

节名、符号名在 ELF 里存为字符串表的偏移；本模块提供按偏移
取字符串的辅助。""",

    "backend/format/elf/writer.py": """ELF 写出器：ObjectFile → ELF 二进制文件（write_elf）。

按 ELF 规范导出：节数据、符号表（.symtab）、字符串表（.strtab）、
重定位表（.rela，经 arch.get_reloc_type 映射重定位类型）、
可执行文件另加程序头表（segment）。是"支持 ELF 格式"（需求2）
的核心实现，api.objcopy(..., 'elf') 调用它。""",

    "backend/format/elf/__init__.py": """ELF 格式包：read_elf / write_elf / ElfFile。""",

    "frontend/common.py": """跨语言公共设施：SourceLocation（源码位置）与 Token（词法单元）。

所有语言前端（本包中主要是 C 前端）的词法/语法分析都用这里的
SourceLocation 记录"哪个文件、哪行哪列、多长"，出错时据此渲染
带源码上下文的诊断信息。""",

    "frontend/c/synthesize.py": """CSynthesizer：把简单的 IR 结构还原成 C 程序文本（教学演示）。

与 codegenerator 方向相反：接收（部分）IR 模块，输出等价的 C 源码。
用于演示/调试"IR 长什么样"，不是编译链路的必经环节。""",

    "frontend/c/nodes/declarations.py": """C AST 的声明节点。

DeclSpec（声明说明符：存储类/类型限定符/类型名）、Typedef、
FunctionDeclaration、Declaration 等。parser 构造这些节点，
semantics 给它们填上类型。""",

    "frontend/c/nodes/expressions.py": """C AST 的表达式节点。

Literal/Binop/Ternop/Unop/Call/VariableAccess/FieldSelect/
ArrayIndex/Cast/Sizeof/CompoundLiteral 等约 30 种表达式节点；
都带 location（源码位置）与 typ（语义分析后填充的类型）。""",

    "frontend/c/nodes/nodes.py": """C AST 节点公共基类（CNode/CTypedCNode/CDeclaration/CStatement 等）。""",

    "frontend/c/nodes/statements.py": """C AST 的语句节点。

If/While/DoWhile/For/Switch/Break/Continue/Return/Compound/Goto/
Label/DeclarationStatement/ExpressionStatement 等。
codegenerator 按这些节点一一生成 IR 控制流。""",

    "frontend/c/nodes/types.py": """C 类型系统节点。

BasicType（int/char/float/void…）、PointerType、ArrayType、
StructOrUnionType（含位域）、EnumType、FunctionType、QualifiedType
（const/volatile 限定）以及 is_integer/is_signed 等判定。
semantics 用它们做类型检查与隐式转换。""",

    "frontend/c/nodes/visitor.py": """AST 访问者模式基类（CVisitor）。

visit_xxx 分派到各节点类型；递归遍历表达式/语句/声明。
是编写 AST 遍历工具（打印、代码生成）的公共底座。""",

    "frontend/c/nodes/__init__.py": """C AST 节点子包：declarations / expressions / statements / types / visitor。""",

    "frontend/generic/nodes.py": """跨语言通用 AST 节点（Expression/Statement 等）。""",

    "frontend/generic/__init__.py": """跨语言前端通用设施。""",

    "frontend/tools/recursivedescent.py": """递归下降解析器基类（RecursiveDescentParser）。

提供 peek/consume/has_consumed 等 Token 游标操作；C 前端的
CParser（parser.py）与 yacc 的语法解析器都基于它。""",

    "frontend/tools/yacc.py": """简易 yacc（LALR 解析器生成器）。

读入 yacc 风格的文法描述（XaccLexer/XaccParser），用 lr.py 生成
action/goto 解析表，再生成一个可运行的解析器 Python 脚本。
codegen/burg.py 在 import 时用它解析 burg.grammar——注意
generate_python_script 里生成的代码字符串内嵌
`from qcc.frontend.tools.grammar import ...`，改包名时须同步修改。""",

    "midend/irutils/io.py": """IR 的 JSON 序列化/反序列化（to_json/from_json）。

把 ir.Module 转成 JSON（配合 utils/binary_txt 编码二进制数据），
用于跨工具传递 IR（如命令行流水线、外部工具交互）。""",

    "midend/irutils/link.py": """IR 模块链接（ir_link）：把多个 IR 模块合并为一个。

类似目标文件的链接：符号解析后把各模块的函数/全局变量并入
同一个 ir.Module。""",

    "utils/binary_txt.py": """二进制与可打印文本互转（asc2bin/bin2asc）。

把二进制节数据编码成 ASCII 文本（base64 风格）以便在 JSON/文本
格式中携带，读取时再还原。irutils/io.py 使用。""",

    "utils/bitfun.py": """位操作工具：bits_to_bytes/value_to_bits/inrange/bits_required 等。

指令编码（arch/encoding）与常量计算大量使用：把整数按位数
打包进字节、检查范围、计算所需位数等。""",

    "utils/chunk.py": """Chunk：可变的字节块（数据 + 引用列表）。

二进制序列化基元：块内可含"引用"（指向其他 chunk 的偏移），
序列化时统一解析。objectfile 的节序列化等使用。""",

    "utils/collections.py": """集合工具：OrderedSet（有序集合）与迭代辅助。

OrderedSet 保序去重，是 IR use-def 链（Value.used_by）的底层
容器；另有 iter_instructions 等遍历辅助。""",

    "utils/graph2svg.py": """把图渲染成 SVG（编译报告里的流程图/干涉图配图）。

reporting.py 的 HTML 报告用它输出 IR 控制流图、干涉图等可视化。
分层布局（LayeredLayout）实现拓扑排序分层。""",

    "utils/hexdump.py": """十六进制转储（hexdump）。

把二进制数据按经典 hexdump 格式（偏移 + 十六进制 + ASCII）输出，
调试机器码/目标文件时使用。""",

    "utils/integer_set.py": """IntegerSet：稀疏整数集合（位集）。

C 语义分析（semantics）用它表示"类型能容纳的整数范围"，
并集/交集/差集/包含判断都比 Python set 更贴合位集语义。""",

    "utils/reporting.py": """编译报告生成器（ReportGenerator 体系）。

DummyReportGenerator（静默）/ TextReportGenerator / HtmlReportGenerator
（生成含 IR 转储、指令列表、图的 HTML 报告）。api.cc 的
reporter=... 参数即传入这里；html_reporter 是便捷上下文管理器。""",

    "utils/tree.py": """Tree：通用树结构（from_string 解析括号记法）。

BURG 树模式匹配（codegen/burg）的"树"就来自这里：
X(a, b) 表示节点 X 带两个子节点；kid 遍历、结构相等比较等
辅助齐全。""",

    "utils/__init__.py": """公共工具包：位操作、集合、报告、树、SVG 渲染等。""",
}

# ---------------- 类级锚点注释（在指定行前插入） ----------------
ANCHORS = {
    "frontend/c/semantics.py": [
        ("class CSemantics:", "\n# 语义分析器：parser 每识别一个语法结构就调用对应的 on_xxx，\n"
                              "# 返回带类型的 AST 节点；发现语义错误时经 self.error 抛 CompilerError。\n"),
    ],
    "frontend/c/codegenerator.py": [
        ("class CCodeGenerator:", "\n# C 代码生成器：遍历带类型的 AST，向 midend.ir.Module 发射 IR。\n"
                                 "# 每条语句/表达式一个 gen_xxx 方法；控制流用基本块 + CJump/Jump 连接。\n"),
    ],
    "backend/codegen/registerallocator.py": [
        ("def alloc_frame(", "\n    # 图着色寄存器分配主流程：simplify/coalesce/freeze/spill/select 循环。\n"),
    ],
    "midend/ir.py": [
        ("class Module:", "\n# 编译单元：函数、全局变量、外部符号的容器。\n"),
        ("class Value:", "\n# IR 值的基类：带类型，并由 used_by 集合追踪所有使用它的指令（use-def 链）。\n"),
        ("class Instruction:", "\n# 指令基类：属于某个基本块，维护 uses 集合与 use-def 一致性。\n"),
    ],
}


def inject_docstring(path, text):
    """把中文功能说明插入模块 docstring 开头（若无 docstring 则新建）。"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if "功能说明" in src or text.strip()[:8] in src:
        return False  # 已注入
    # 定位第一个模块级 docstring
    marker = '"""'
    idx = src.find(marker)
    if idx == -1 or "=" in src[:idx] or "import" in src[:idx]:
        # 无 docstring：在文件头插入
        # 跳过 shebang
        lines = src.split("\n")
        insert_at = 1 if lines and lines[0].startswith("#!") else 0
        doc = '"""' + text + '"""'
        lines.insert(insert_at, doc)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return True
    # 有 docstring：在 ''' 之后插入
    close = src.find('"""', idx + 3)
    assert close != -1, path
    new_src = src[:idx + 3] + text + src[idx + 3:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    return True


def inject_anchor(path, anchor, comment):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if comment.strip() in src:
        return False
    idx = src.find(anchor)
    assert idx != -1, (path, anchor)
    # 在 anchor 所在行的行首插入
    line_start = src.rfind("\n", 0, idx) + 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(src[:line_start] + comment + src[line_start:])
    return True


def main():
    total = 0
    for rel, text in DETAILED.items():
        path = os.path.join(ROOT, rel)
        if inject_docstring(path, text):
            total += 1
            print("详注:", rel)
        else:
            print("跳过(已有):", rel)
    for rel, text in BRIEF.items():
        path = os.path.join(ROOT, rel)
        if inject_docstring(path, text):
            total += 1
            print("简注:", rel)
        else:
            print("跳过(已有):", rel)
    for rel, items in ANCHORS.items():
        path = os.path.join(ROOT, rel)
        for anchor, comment in items:
            inject_anchor(path, anchor, comment)
            print("锚点注释:", rel, anchor[:30])
    print(f"\n共更新 {total} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
