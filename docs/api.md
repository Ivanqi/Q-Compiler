# api.py 详解
## 一、这个文件的作用是什么？
api.py 是 qcc 的"总服务台"——docstring（1–4 行）一句话概括："一组方便调用编译、汇编、链接的快捷函数"。

过去几讲我们看到的所有组件——builder.py（装配 C 前端）、codegenerator.py（AST → IR）、opt/ 里的 8 个优化 pass（mem2reg、CSE、DCE、CleanPass……）——全部在这个文件里被接上电源、按下开关。

它定义了 qcc 的统一编译模式：
```c
某个语言前端 (c_to_ir / c3_to_ir / bf_to_ir / ...)
        ↓ 产出 ir.Module
optimize(ir_module, level=...)      ← 把 opt/ 的 pass 清单跑起来（本文件 190–248 行）
        ↓ 优化后的 ir.Module
ir_to_object([ir_module], march)    ← 机器码生成（本文件 276–327 行）
        ↓ ObjectFile
（可选）link → objcopy → bin/hex/elf 可执行映像

```

cc、c3c、bfcompile、wasmcompile 等每个语言驱动函数都是这条流水线的不同"前端接线"，中后段全部复用 optimize + ir_to_object。

使用者（测试、命令行工具、Jupyter 用户）只需 from qcc.api import cc 就拿到整个编译器。

## 二、每个方法的作用
### 辅助函数
get_reporter(reporter)（77–89 行）：报告器规范化——None → DummyReportGenerator（静默）；以 .html 结尾的字符串 → 创建文件 + HtmlReportGenerator 并写报告头；其他字符串 → 报错；传入对象 → 原样返回。整个编译过程的"文档化输出"都走这个接口。

is_platform_supported()（92–94 行）：当前运行平台是否有可用架构。

chmod_x(filename)（565–568 行）：给文件加可执行位（objcopy 出 exe/elf 可执行文件后调用）。

### 构建系统
construct(buildfile, targets=())（97–118 行）：qcc 自带的 XML 构建系统入口——RecipeLoader 解析 build.xml 项目文件，TaskRunner 按依赖图执行各任务（编译、链接、打包）；

XML 或文件错误统一转成 TaskError。

### 汇编与反汇编
asm(source, march, debug=False)（121–161 行）：汇编器入口——get_arch(march) 拿架构对象 → march.assembler 取汇编器 → BinaryOutputStream 输出到新 ObjectFile 的 code 节；

CompilerError 经 DiagnosticsManager 打印带位置的诊断后转 TaskError。docstring 的 doctest 展示用法：asm(io.StringIO("db 0x77"), 'arm') → CodeObject of 1 bytes。

disasm(data, march)（164–184 行）：反汇编二进制到文本流（调试/检查用）。

### IR 优化与机器码生成（核心区）
OPT_LEVELS = ("0", "1", "2", "s")（187 行）：合法优化级别集合。

optimize(ir_module, level=0, reporter=None)（190–248 行）：前几讲所有 pass 的总指挥，逐行回顾：
	- 214–216 行：级别断言；"0" 直接返回（默认不优化）；
	
	- 218 行：TODO——目前 1/2/s 还不区分档位；
	- 221–230 行：8 个 pass 的列表 * 3（mem2reg → RemoveAddZero → ConstantFolder → CSE → TailCall → LoadAfterStore → DCE → 
	CleanPass，连跑三遍）——这是整个系列反复引用的那段代码；
	
	- 232–233 行：level == "3" 追加 CJumpPass()——注意 214 行的 assert 只放行 ("0","1","2","s")，"3" 会先撞 assert，这段是死代码（代码现状的一个小观察）；
	
	- 236–248 行：优化前后各 verify_module 校验 IR 合法性；有 reporter 时输出优化前后模块统计与 IR 转储（编译报告 HTML 里的 IR 章节就来自这里）。

ir_to_stream(ir_module, march, output_stream, ...)（251–264 行）：IR → 机器指令流——CodeGenerator(march, reporter, optimize_for=opt) 是后端（指令选择、寄存器分配、栈帧、指令调度），opt 可为 "speed"/"size"/"co2"（注意：这与 opt/ 目录的 IR 优化是两回事——那个在 optimize()，这个是机器码层的优化目标）。

ir_to_assembly(ir_modules, march, add_binary=False)（267–273 行）：IR → 汇编文本（TextOutputStream），返回字符串。

ir_to_object(ir_modules, march, ...)（276–327 行）：IR → 目标文件——建 ObjectFile，构造 MasterOutputStream（同时扇出到 BinaryOutputStream 写二进制 + FunctionOutputStream 收集指令列表给报告 + 可选用户流），对每个 IR 模块跑 ir_to_stream。

### 语言前端驱动（统一模式）

| 函数 | 前端 | 流水线 |
| :--- | :--- | :--- |
| `cc` (330–369 行) | C: `c_to_ir`（内部就是 CBuilder） | → `optimize(level)` → `ir_to_object` |
| `wasmcompile` (372–388 行) | WebAssembly: `read_wasm` + `wasm_to_ir` | → `optimize(2)` → `ir_to_object` |
| `llc` (391–395 行) | LLVM IR 文本: `llvm_to_ir` | → `ir_to_object` |
| `c3c` (398–443 行) | C3 语言: `c3_to_ir` | → `optimize(level)` → `ir_to_object`（s 级把机器码目标也切成 "size"） |
| `pascal` (446–461 行) | Pascal: `pascal_to_ir` | → `ir_to_object` |
| `bfcompile` (464–491 行) | Brainfuck: `bf_to_ir` | → `optimize()` → `ir_to_object` |
| `pycompile` (494–502 行) | 类型标注的 Python: `python_to_ir` | → `ir_to_object` |
| `fortrancompile` (505–509 行) | Fortran: `fortran_to_ir` | → `ir_to_object` (TODO) |

cc（330–369 行）是旗舰，逐行看：
- 359–363 行：报告器和 C 选项的默认值（DummyReportGenerator、COptions()）；

- 365 行：c_to_ir(...)——就是前几讲的完整 C 前端（builder：预处理 → 解析 → 语义 → codegen），产出 ir.Module；

- 366–367 行：把未优化的 IR 统计/转储写进报告；

- 368 行：optimize(ir_module, level=opt_level)——opt/ 目录的 8 个 pass 在这里启动；

- 369 行：ir_to_object 生成目标文件返回。

### 输出与格式
objcopy(obj, image_name, fmt, output_filename)（512–562 行）：目标文件 → 各种格式——bin（裸二进制）、hex（Intel HEX）、elf（可执行文件还会 chmod +x）、exe、ldb（调试信息）、uimage（u-boot 映像）；不支持的格式报 TaskError。

## 三、通过什么方式使用 api.py？
### 1. 作为 Python 库（主要方式）
__all__（55–74 行）列出公共接口，任何 Python 代码都能直接调用：
```python
import io
from qcc.api import cc, asm, link, objcopy

source_file = io.StringIO("int add(int a, int b) { return a + b; }")
obj = cc(source_file, 'x86_64', opt_level=2)   # 一步完成 C → 目标文件

``` 

cc docstring 里就有 doctest（349–357 行）：cc(io.StringIO("void main() { int a; }"), 'x86_64') → CodeObject of 20 bytes。qcc 的测试套件、示例、外部项目都以这种方式使用。

### 2. 被命令行工具调用
qcc 自带的 CLI（qcc-cc、qcc-build、qcc-objdump 等）只是这些函数的薄封装——命令行参数解析后调 cc()/construct()/objcopy()。

### 3. 函数间组合成完整工具链
cc/asm 产出 ObjectFile → link() 链接成可执行 → objcopy() 转成目标格式：
```python
obj1 = cc(io.StringIO("int g(void) { return 42; }"), 'arm')
obj2 = asm(io.StringIO("db 0x77"), 'arm')     # 汇编一个目标文件
exe = link([obj1, obj2], 'arm')               # 链接
objcopy(exe, 'code', 'hex', 'output.hex')     # 输出 Intel HEX

```

### 4. 生成编译报告
cc(..., reporter="report.html") → get_reporter 建 HtmlReportGenerator，整个流水线（前端报告、优化前后 IR、机器指令列表）落成一份 HTML。

## 四、详细例子：cc() 一条龙——整个系列的收官
以这段 C 为例，走通 api.py 的旗舰函数：
```c
int f(int a, int b) { return (a + b) * (a + b); }

```

调用：obj = cc(io.StringIO(src), 'x86_64', opt_level=2)

### 第 1 步：c_to_ir（365 行）—— C 前端整条链
builder.py 装配：CPreProcessor 出 Token → CParser + CSemantics 出带类型的 AST → CCodeGenerator 出 IR；

产出的"笨 IR"（alloc + store/load 形态）：
```c
module main
function f(a: i32, b: i32) -> i32 {
  entry:
    a_addr = alloc 4
    b_addr = alloc 4
    jump block1
  block1:
    store a, a_addr
    store b, b_addr
    t1 = load a_addr
    t2 = load b_addr
    t3 = add t1, t2
    t4 = add t1, t2        ← (a+b) 被算了第二遍
    t5 = mul t3, t4
    return t5
}

```

### 第 2 步：optimize(ir_module, level=2)（368 行）—— opt/ 的 8 个 pass 连跑三遍
（把前几讲每一集串起来的一次完整演出：）

| 遍次 | pass | 对这段 IR 的动作 |
| :--- | :--- | :--- |
| 1 | Mem2RegPromtor | alloc/load/store 全部提升：`t1→a`、`t2→b`，内存操作清零 |
| 1 | RemoveAddZeroPass | 无 `+0` / `*1`，无事 |
| 1 | ConstantFolder | 无常量-常量运算，无事 |
| 1 | CSE | `t4 = add a, b` 命中 `t3` → `t4.replace_by(t3)` |
| 1 | TailCall / LoadAfterStore | 无递归、无内存，无事 |
| 1 | DeleteUnusedInstructionsPass | 删除被掏空的 `t4` |
| 1 | CleanPass | 把只剩 Jump 的 entry 与 block1 粘合成一块 |
| 2~3 | 上述 pass 再各跑两遍 | 已收敛，无事 |

优化后 IR：
```c
module main
function f(a: i32, b: i32) -> i32 {
  entry:
    t3 = add a, b
    t5 = mul t3, t3
    return t5
}

```

从 10 条指令（含 4 次访存）压缩到 2 条纯寄存器运算——optimize() 里 221–230 行那段 pass 清单就是这场演出的节目单。

### 第 3 步：ir_to_object（369 行）—— 机器码生成
ir_to_stream 创建 CodeGenerator(march, optimize_for="speed")：指令选择把 add/mul 映射成 x86_64 的 addl/imull，寄存器分配把 a/b/t3/t5 分配到 edi/esi/eax 等，最后经 BinaryOutputStream 写入 ObjectFile；


返回的 obj 就是 x86_64 的目标文件，可直接被 link() 链接。

### 第 4 步（可选）：组装完整工具链
```c
from qcc.api import cc, link, objcopy
obj = cc(io.StringIO(src), 'x86_64', opt_level=2)
exe = link([obj], 'x86_64')
objcopy(exe, 'code', 'hex', 'f.hex')   # 得到 Intel HEX 固件映像

```

## 五、一句话总结
api.py 是 qcc 面向用户的总入口：它把"语言前端 → IR → 优化 → 机器码 → 目标文件"固化成统一模式——每个语言驱动（cc/c3c/bfcompile/wasmcompile…）都是 xxx_to_ir + optimize() + ir_to_object() 的三段接线；

optimize()（190–248 行）负责调度 opt/ 目录的全部 pass（三遍迭代、级别控制、前后校验、报告输出），ir_to_object/ir_to_stream 负责把 IR 送进机器码生成器产出 ObjectFile；

再配合 asm/link/objcopy/construct，构成了从源码字符串到可烧录映像的完整工具链。

之前每一讲讲的部件，最终都在这个文件的一行 cc(source, 'x86_64') 里被接通。

