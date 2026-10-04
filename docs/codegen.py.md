# codegen.py 详解
## 一、这个文件的作用是什么？
这是 qcc 的机器码生成器（后端总指挥）——docstring（1–4 行）："机器码生成器，架构在创建时提供"。

如果说前几讲把源码送到了 IR，那么本文件负责把 IR 变成能写进目标文件（ObjectFile）的真实机器指令。

它不亲自做具体技术活，而是编排后端流水线的四个经典阶段（在 __init__ 里把各"专家"请进来）：

```python
ir.Module（来自 C 前端 + 优化）
  ↓ ① 指令选择 InstructionSelector1 —— IR 指令 → 架构抽象指令（用虚拟寄存器）
  ↓ ② 指令调度 InstructionScheduler ——（图形方法未启用）
  ↓ ③ 寄存器分配 GraphColoringRegisterAllocator —— 虚拟寄存器 → 真实寄存器/栈槽
  ↓ ④ 帧发射 emit_frame_to_stream —— 加 prologue/epilogue，输出真实指令流
        └ 经 PeepHoleStream 窥孔优化 → BinaryOutputStream → ObjectFile

```

调用关系（把前面 api.py 一讲接上）：api.py:260-264 的 ir_to_stream 创建 CodeGenerator(march, reporter, optimize_for=opt) 并调 generate()；

而 IR 模块正是 lang/c/api.py 的 c_to_ir 经由 CBuilder.build 产出的。

## 二、每个方法的作用
### __init__(self, arch, reporter, optimize_for="size")（41–60 行）—— 组建后端专家团
42–43 行：断言 arch 是 Architecture 实例，保存架构与报告器；

45 行：Verifier()——IR 校验器（生成前检查 IR 合法性）；

46 行：SelectionGraphBuilder(arch)——备用的"图式"指令选择构建器（当前未用）；

47–53 行：优化目标权重表——"size" 权重 (10, 1, 1)（偏向小代码）、"speed" (3, 10, 1)（偏向快）、"co2" (1, 2, 10)（偏向低碳？一个有趣的环保优化目标）、"awesome" 全 13；未知目标用 (1,1,1)。这三个权重分别对应指令选择时的代码尺寸/执行开销/CO₂ 排放评分；

54–56 行：InstructionSelector1(arch, sgraph_builder, reporter, weights=...)——指令选择器（树覆盖法）；

57 行：InstructionScheduler()——指令调度器（用户选中的就是这行）；

58–60 行：GraphColoringRegisterAllocator(arch, selector, reporter)——图着色寄存器分配器。

### generate(self, ircode, output_stream, debug=False)（62–98 行）—— 总入口
64–68 行：断言输入是 ir.Module；有调试库就用模块的，否则建空 DebugDb；

74–79 行：先声明外部符号——选 data 节，对每个 external 调 _mark_global，外部函数再发 SetSymbolType(name, "func")；

81–84 行：生成全部全局变量——仍选 data 节，逐个 generate_global；

86–91 行：生成全部函数——选 code 节，逐个 generate_function（87–88 行注释说明核心思想："把程序咀嚼成一堆 frame，每个函数一个 frame，每个 frame 是抽象指令的扁平列表"）；

93–98 行：debug 模式下输出类型调试数据（DebugData）。

### generate_function(self, ir_function, output_stream, debug=False)（143–210 行）—— 单函数的完整后端流程
151–152 行：报告器记标题 + 转储该函数的 IR（HTML 报告里每个函数的 IR 章节）；

154–165 行：超大基本块切分——超过 200 条指令的块用 split_block 拆开（注释说明是为 ARM/Thumb 的字面量池可达性问题，TODO 承认 200 是拍脑袋的数）；

167–168 行：全局标记 + SetSymbolType(func)；

170–174 行：arch.new_frame(name, ir_function) 创建栈帧对象——架构相关，携带指令列表、虚拟寄存器、调试信息；

176–177 行：select_and_schedule——指令选择（见下）；

181–182 行：register_allocator.alloc_frame(frame)——图着色寄存器分配：给每条抽象指令的虚拟寄存器指派真实寄存器，放不下的溢出到栈；

184–187 行：架构定义了 peephole 钩子就再跑一遍窥孔优化（架构级）；

191–198 行：MasterOutputStream 同时扇出到"指令列表收集流"（给报告）和主输出流，外套 PeepHoleStream（通用窥孔优化：相邻指令模式替换），调 emit_frame_to_stream 后 flush；

200–208 行：debug 模式下给函数补结束标签与 DebugData；

210 行：把最终指令列表转储进报告。

### select_and_schedule(self, ir_function, frame)（212–227 行）—— 指令选择

216–218 行：tree_method = True → instruction_selector.select(ir_function, frame)——树覆盖指令选择：把 IR 指令块建成表达式树，与架构的指令模式（pattern）匹配，替换成带虚拟寄存器的抽象机器指令（x86_64 的 addl、ARM 的 ADD 等），全部追加进 frame；

219–227 行：else 分支是图式方法（sgraph_builder + instruction_scheduler）——尚未实现（not_impl），目前是死代码。

### emit_frame_to_stream(self, frame, output_stream, debug=False)（229–293 行）—— 帧的最终发射
242–243 行：arch.gen_prologue(frame)——函数序言（保存寄存器、调整栈指针）；

245–278 行：遍历 frame 里寄存器分配后的指令：
	- 249–258 行：指令带调试位置 → 发源码行注释 + 标签（调试用）；
	- 260–274 行：VirtualInstruction（虚拟指令）分类处理：RegisterUseDef 忽略（262–263 行）、ArtificialInstruction 原样发射、InlineAssembly 走 _generate_inline_assembly（C 的 asm(...) 语句的机器码在这里面生成）；
	- 275–278 行：真实指令——断言所有寄存器已着色（all(r.is_colored ...)，寄存器分配完成的证明），然后发射；

280–281 行：arch.gen_epilogue(frame)——函数尾声（恢复寄存器、栈指针、返回）；

283–285 行：补发调试数据。

### _generate_inline_assembly(...)（295–320 行）—— 内联汇编
把 C 层 asm 语句里的 %0/%1 占位符替换成寄存器分配后的真实寄存器名（304–312 行），然后调用 arch.assembler 把模板汇编成指令（"穷人的汇编 API"，注释自嘲是从 api.py 抄的）。

C 前端的 InlineAssemblyCode 节点（上一系列 codegenerator.py:788 生成）最终在这里落地。

### _mark_global(self, output_stream, value)（322–327 行）
binding == GLOBAL 的符号发 Global 指令（导出符号）；LOCAL（static）不发。

## 三、怎么通过 lang/c/api.py 生成的 IR 被调用？
lang/c/api.py 是个小包装（20–49 行）：c_to_ir(source, march, coptions, reporter) 里 get_arch(march) → CBuilder(march.info, coptions) → cbuilder.build(...) → 返回 ir.Module。

它只负责产 IR，不碰本文件。真正的接线在上一讲的 qcc/api.py：
```python
# api.py: ir_to_stream (251-264)
code_generator = CodeGenerator(march, reporter, optimize_for=opt)   # ← 本文件的类
code_generator.generate(ir_module, output_stream, debug=debug)     # ← 总入口

# api.py: cc (330-369)
ir_module = c_to_ir(source, march, ...)          # lang/c/api.py 产 IR
optimize(ir_module, level=opt_level)             # opt/ 的 pass 优化 IR
return ir_to_object([ir_module], march, ...)     # → ir_to_stream → CodeGenerator.generate

```

所以完整链条（把整个系列串起来）：
```python
lang/c/api.py: c_to_ir
  └ CBuilder.build（预处理 → 解析 → 语义 → CCodeGenerator）
       → ir.Module            ┐
opt/ 的 8 个 pass（api.optimize）│ 上一系列
       → 优化后的 ir.Module    ┘
qcc/api.py: ir_to_stream
  └ CodeGenerator(march, reporter, optimize_for).generate(ir_module, ...)   ← 本文件
       → InstructionSelector1（指令选择，虚拟寄存器）
       → GraphColoringRegisterAllocator（分配真实寄存器）
       → PeepHoleStream + arch.peephole（窥孔优化）
       → BinaryOutputStream → ObjectFile（目标文件）

```
接口契约：本文件只认 ir.Module（含 functions/variables/externals 三个集合）——不管它是 C、C3 还是 Brainfuck 前端产的，一律同等对待。

这就是"语言前端与机器后端解耦"的接缝。

## 四、详细例子
沿用贯穿全系列的 (a+b)*(a+b)，目标 x86_64：
```c
int f(int a, int b) { return (a + b) * (a + b); }

```

### 第 1 步：进入后端之前的 IR
c_to_ir 产"笨 IR" → optimize(level=2) 把 mem2reg/CSE/DCE/CleanPass 全部跑完（上一系列详述），交给本文件的 IR 是：
```c
module main
function f(a: i32, b: i32) -> i32 {
  entry:
    t3 = add a, b
    t5 = mul t3, t3
    return t5
}

```

### 第 2 步：generate(ir_module, output_stream) 分派
ircode.externals 空、ircode.variables 空 → 直接进入函数循环；

generate_function(f, ...)：块只有 2 条指令不用切分 → _mark_global + SetSymbolType("f", "func") → arch.new_frame("f", ir_function) 建 x86_64 栈帧。


### 第 3 步：指令选择（select_and_schedule → InstructionSelector1.select）
IR 指令与 x86_64 架构模式匹配，换成带虚拟寄存器的抽象指令（示意）：
```c
vreg0:  addl  %a, %b          ← t3 = a + b（实参在 edi/esi）
vreg1:  imull %vreg0, %vreg0  ← t5 = t3 * t3
        movl  %vreg1, %eax    ← 返回值约定放 eax

```

### 第 4 步：寄存器分配（GraphColoringRegisterAllocator.alloc_frame）
对 frame 的干涉图着色，把虚拟寄存器指派到真实寄存器（参数 a/b 按 System V 调用约定已是 edi/esi；vreg0/vreg1 可复用 eax），必要时溢出到栈槽。

### 第 5 步：帧发射（emit_frame_to_stream → PeepHoleStream → BinaryOutputStream）
gen_prologue 加序言、逐条发射着色后的指令（277 行断言"全部寄存器已着色"通过）、gen_epilogue 加尾声，窥孔流合并冗余模式。最终写进 ObjectFile code 节的机器码，反汇编大致是：
```c
f:
    push rbp
    mov rbp, rsp
    lea  eax, [rdi + rsi]    ; t3 = a + b
    imul eax, eax            ; t5 = t3 * t3
    pop rbp
    ret

```

### 第 6 步：回到 api.py
generate 返回后，ir_to_object 拿到填好的 ObjectFile 返回给 cc() 调用者；再经 link() + objcopy(..., 'hex', ...) 就是可烧录固件。

### 串联视角：一个 C 文件经过的所有"车间"
```c
"int f(...)..." ──lang/c/api.py──▶ IR ──opt/ 8 pass──▶ 优化 IR
                                                          │
        ObjectFile ◀──codegen/codegen.py──┘  (指令选择→寄存器分配→帧发射)

```

## 五、一句话总结
codegen.py 是 qcc 机器后端的总指挥：__init__ 按优化目标（size/speed/co2 权重表）组装备好"指令选择器 + 指令调度器 + 图着色寄存器分配器 + 窥孔优化流"；

generate 依次把 IR 模块的外部符号、全局变量（字节映像 → DByte/DZero/标签引用）和每个函数送进流水线；

generate_function 负责"建帧 → 指令选择（虚拟寄存器）→ 寄存器分配（真实寄存器）→ 序言/尾声 + 窥孔发射"，emit_frame_to_stream 完成最终指令流的输出。它通过 qcc/api.py 的 ir_to_stream 被调用，

消费 lang/c/api.py c_to_ir 产出（并经 optimize 优化）的 ir.Module——前端管语义，opt/ 管 IR 质量，本文件管机器落地，三者只以 ir.Module 为接口交接。

