"""x87 浮点协处理器（FPU）指令定义。

x87 是 x86 的传统浮点单元，以 st0-st7 八层寄存器栈为操作对象；本模块定义其取数、
存数与开方指令（Fld32/Fld64/Fld80、Fst32/Fstp32/Fst64、Fsqrt），并单独建立
x87_isa 指令集容器。指令沿用 instructions.py 的 Token 组合编码：D9/DD/DB 三个
opcode 分别对应 s/l/t 后缀，ModR/M 的 reg 字段充当扩展操作码（如 flds 的 reg=0、
fstps 的 reg=3），tokens 与 patterns 的写法与整数指令一致。

衔接：X86_64Arch 仅在启用 "x87" 选项时才把 x87_isa 并入总 isa；本模块的指令选择
模式（@x87_isa.pattern）尚未实现（pattern_str_f32 直接抛 NotImplementedError），
因此实际浮点运算由 sse2_instructions.py 承担。

x87 floating point unit instructions"""

from qcc.backend.arch.encoding import Instruction, Operand, Syntax
from qcc.backend.arch.isa import Isa
from qcc.backend.arch.x86_64.instructions import (
    ModRmToken,
    OpcodeToken,
    RexToken,
    SecondaryOpcodeToken,
    mem_modes,
)

# x87 指令集容器：由 X86_64Arch 在启用 x87 选项时并入总 isa
x87_isa = Isa()


class X87Instruction(Instruction):
    """x87 协处理器指令基类：所有 x87 指令都归属 x87_isa。

    x87 FPU instruction"""

    isa = x87_isa


class Fsqrt(X87Instruction):
    """浮点平方根 fsqrt：无操作数，编码为 D9 FA（SecondaryOpcodeToken 充当扩展操作码）。

    Floating point square root"""

    syntax = Syntax(["fsqrt"])
    patterns = {"opcode": 0xD9, "opcode2": 0xFA}
    tokens = [OpcodeToken, SecondaryOpcodeToken]


class Fld32(X87Instruction):
    """把 32 位内存操作数压入 FPU 栈顶 flds（s 后缀=32 位）：opcode D9，ModR/M.reg=0。

    Push 32 bit operand on the FPU stack, suffix s=32 bit"""

    m = Operand("m", mem_modes)
    syntax = Syntax(["flds", " ", m])
    patterns = {"opcode": 0xD9, "reg": 0}
    tokens = [RexToken, OpcodeToken, ModRmToken]


class Fld64(X87Instruction):
    """把 64 位内存操作数压入 FPU 栈顶 fldl（l 后缀=64 位）：opcode DD，ModR/M.reg=0。

    Push 64 bit operand on the FPU stack, suffix l=64 bit"""

    m = Operand("m", mem_modes)
    syntax = Syntax(["fldl", " ", m])
    patterns = {"opcode": 0xDD, "reg": 0}
    tokens = [RexToken, OpcodeToken, ModRmToken]


class Fld80(X87Instruction):
    """把 80 位扩展精度内存操作数压入 FPU 栈顶 fldt（t 后缀=80 位）：opcode DB，ModR/M.reg=5。

    Push 80 bit operand on the FPU stack, suffix t=80 bit"""

    m = Operand("m", mem_modes)
    syntax = Syntax(["fldt", " ", m])
    patterns = {"opcode": 0xDB, "reg": 5}
    tokens = [RexToken, OpcodeToken, ModRmToken]


class Fst32(X87Instruction):
    """把栈顶 32 位浮点值存入内存 fsts（不弹栈）：opcode D9，ModR/M.reg=2。

    Store 32 bit float into memory"""

    m = Operand("m", mem_modes)
    syntax = Syntax(["fsts", " ", m])
    patterns = {"opcode": 0xD9, "reg": 2}
    tokens = [RexToken, OpcodeToken, ModRmToken]


class Fstp32(X87Instruction):
    """把栈顶 32 位浮点值存入内存并弹出栈顶 fstps：opcode D9，ModR/M.reg=3。

    Store 32 bit float into memory and pop"""

    m = Operand("m", mem_modes)
    syntax = Syntax(["fsts", " ", m])
    patterns = {"opcode": 0xD9, "reg": 3}
    tokens = [RexToken, OpcodeToken, ModRmToken]


class Fst64(X87Instruction):
    """把栈顶 64 位浮点值存入内存 fstl：opcode DD，ModR/M.reg=2。

    Store 64 bit float into memory"""

    m = Operand("m", mem_modes)
    syntax = Syntax(["fstl", " ", m])
    patterns = {"opcode": 0xDD, "reg": 2}
    tokens = [RexToken, OpcodeToken, ModRmToken]


@x87_isa.pattern("stm", "STRF32(reg64, regfp)", size=2)
def pattern_str_f32(context, tree, c0, c1):
    """x87 路径的 f32 存储指令选择模式：尚未实现，暂以 NotImplementedError 占位。"""

    # context.emit(Fst32(RmMem(c0)))
    # TODO: exchange?

    # context.emit(Fst32(RmMem(c0)))
    raise NotImplementedError()
