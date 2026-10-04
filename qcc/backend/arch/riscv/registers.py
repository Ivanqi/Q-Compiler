"""RISC-V 寄存器定义：物理寄存器实例、寄存器类（RegisterClass）与 gdb 寄存器表。

本模块是后端"寄存器分配"环节的字典：为整数（x0-x31，RiscvRegister）、浮点
（f0-f31，RiscvFRegister）、程序计数器（PC）与 CSR（RiscvCsrRegister）各建一类，
按 RISC-V 编号（num）与 ABI 别名（aka）实例化出全部物理寄存器。
register_classes_hwfp / register_classes_swfp 供 RiscvArch.info 使用：硬件浮点时
浮点值分配进 freg 类，软浮点（swfp）时则统一占用整数寄存器，这决定了后端把浮点
运算编成 F 扩展指令还是调用软浮点运行时函数。

"""

from qcc.midend import ir
from qcc.backend.arch.registers import Register, RegisterClass

# pylint: disable=invalid-name


class RiscvRegister(Register):
    """32 位通用寄存器（x0-x31），可能带 ABI 别名（zero/ra/sp/a0…）。"""

    bitsize = 32

    def __repr__(self):
        if self.is_colored:
            return get_register(self.color).name
            return f"{self.name}={self.color}"
        else:
            return self.name


class RiscvProgramCounterRegister(Register):
    """程序计数器（PC），编号 32，仅用于 gdb 寄存器表。"""

    bitsize = 32


class RiscvFRegister(Register):
    """32 位浮点寄存器（f0-f31），用于 rvf（硬件浮点）扩展。"""

    bitsize = 32


class RiscvCsrRegister(Register):
    """控制状态寄存器（CSR），如 mstatus/mie/mtvec，编号即 CSR 地址。"""

    bitsize = 32


def get_register(n):
    """按编号取回对应的物理寄存器（查 num2regmap）。

    Based on a number, get the corresponding register"""
    return num2regmap[n]


def register_range(a, b):
    """返回编号在 [a, b] 闭区间内的全部寄存器集合。

    Return set of registers from a to b"""
    assert a.num < b.num
    return {get_register(n) for n in range(a.num, b.num + 1)}


# 整数通用寄存器 x0-x31：aka 为 ABI 别名；调用约定中谁保存由 arch.py 的
# callee_save / caller_save 决定（如 a0/x10 传返回值，a2-a7/x12-x17 传参数）
R0 = RiscvRegister("x0", num=0, aka=("zero",))
LR = RiscvRegister("x1", num=1, aka=("ra",))
SP = RiscvRegister("x2", num=2, aka=("sp",))
R3 = RiscvRegister("x3", num=3, aka=("gp",))
R4 = RiscvRegister("x4", num=4, aka=("tp",))
R5 = RiscvRegister("x5", num=5, aka=("t0",))
R6 = RiscvRegister("x6", num=6, aka=("t1",))
R7 = RiscvRegister("x7", num=7, aka=("t2",))
FP = RiscvRegister("x8", num=8, aka=("s0", "fp"))
R9 = RiscvRegister("x9", num=9, aka=("s1",))
R10 = RiscvRegister("x10", num=10, aka=("a0",))
R11 = RiscvRegister("x11", num=11, aka=("a1",))
R12 = RiscvRegister("x12", num=12, aka=("a2",))
R13 = RiscvRegister("x13", num=13, aka=("a3",))
R14 = RiscvRegister("x14", num=14, aka=("a4",))
R15 = RiscvRegister("x15", num=15, aka=("a5",))
R16 = RiscvRegister("x16", num=16, aka=("a6",))
R17 = RiscvRegister("x17", num=17, aka=("a7",))
R18 = RiscvRegister("x18", num=18, aka=("s2",))
R19 = RiscvRegister("x19", num=19, aka=("s3",))
R20 = RiscvRegister("x20", num=20, aka=("s4",))
R21 = RiscvRegister("x21", num=21, aka=("s5",))
R22 = RiscvRegister("x22", num=22, aka=("s6",))
R23 = RiscvRegister("x23", num=23, aka=("s7",))
R24 = RiscvRegister("x24", num=24, aka=("s8",))
R25 = RiscvRegister("x25", num=25, aka=("s9",))
R26 = RiscvRegister("x26", num=26, aka=("s10",))
R27 = RiscvRegister("x27", num=27, aka=("s11",))
R28 = RiscvRegister("x28", num=28, aka=("t3",))
R29 = RiscvRegister("x29", num=29, aka=("t4",))
R30 = RiscvRegister("x30", num=30, aka=("t5",))
R31 = RiscvRegister("x31", num=31, aka=("t6",))

# 程序计数器：只出现在 gdb 寄存器表中，不参与分配
PC = RiscvProgramCounterRegister("PC", num=32)

# 浮点寄存器 f0-f31：rvf/rvfx 扩展使用（rvfx 把浮点值仍放在整数寄存器里）
F0 = RiscvFRegister("f0", num=0)
F1 = RiscvFRegister("f1", num=1)
F2 = RiscvFRegister("f2", num=2)
F3 = RiscvFRegister("f3", num=3)
F4 = RiscvFRegister("f4", num=4)
F5 = RiscvFRegister("f5", num=5)
F6 = RiscvFRegister("f6", num=6)
F7 = RiscvFRegister("f7", num=7)
F8 = RiscvFRegister("f8", num=8)
F9 = RiscvFRegister("f9", num=9)
F10 = RiscvFRegister("f10", num=10)
F11 = RiscvFRegister("f11", num=11)
F12 = RiscvFRegister("f12", num=12)
F13 = RiscvFRegister("f13", num=13)
F14 = RiscvFRegister("f14", num=14)
F15 = RiscvFRegister("f15", num=15)
F16 = RiscvFRegister("f16", num=16)
F17 = RiscvFRegister("f17", num=17)
F18 = RiscvFRegister("f18", num=18)
F19 = RiscvFRegister("f19", num=19)
F20 = RiscvFRegister("f20", num=20)
F21 = RiscvFRegister("f21", num=21)
F22 = RiscvFRegister("f22", num=22)
F23 = RiscvFRegister("f23", num=23)
F24 = RiscvFRegister("f24", num=24)
F25 = RiscvFRegister("f25", num=25)
F26 = RiscvFRegister("f26", num=26)
F27 = RiscvFRegister("f27", num=27)
F28 = RiscvFRegister("f28", num=28)
F29 = RiscvFRegister("f29", num=29)
F30 = RiscvFRegister("f30", num=30)
F31 = RiscvFRegister("f31", num=31)

# 控制状态寄存器（CSR）：num 即 CSR 地址，供 csrr/csrw 等指令引用
MSTATUS = RiscvCsrRegister("mstatus", num=0x300)
MIE = RiscvCsrRegister("mie", num=0x304)
MTVEC = RiscvCsrRegister("mtvec", num=0x305)
MEPC = RiscvCsrRegister("mepc", num=0x341)
MCAUSE = RiscvCsrRegister("mcause", num=0x342)
MHARTID = RiscvCsrRegister("mhartid", num=0xF14)
FRM = RiscvCsrRegister("frm", num=0x2)

# 全部整数寄存器列表：注册给 RiscvRegister 基类，供寄存器着色/分配遍历
registers = [
    R0,
    LR,
    SP,
    R3,
    R4,
    R5,
    R6,
    R7,
    FP,
    R9,
    R10,
    R11,
    R12,
    R13,
    R14,
    R15,
    R16,
    R17,
    R18,
    R19,
    R20,
    R21,
    R22,
    R23,
    R24,
    R25,
    R26,
    R27,
    R28,
    R29,
    R30,
    R31,
]
RiscvRegister.registers = registers

fregisters = [
    F0,
    F1,
    F2,
    F3,
    F4,
    F5,
    F6,
    F7,
    F8,
    F9,
    F10,
    F11,
    F12,
    F13,
    F14,
    F15,
    F16,
    F17,
    F18,
    F19,
    F20,
    F21,
    F22,
    F23,
    F24,
    F25,
    F26,
    F27,
    F28,
    F29,
    F30,
    F31,
]

RiscvFRegister.registers = fregisters
# 编号 -> 寄存器对象 的映射，get_register() 依赖它
num2regmap = {r.num: r for r in registers}

# gdb 视角的寄存器表：32 个通用寄存器 + PC（arch.py 中赋给 gdb_registers/gdb_pc）
gdb_registers = registers + [PC]
RiscvCsrRegister.registers = [MSTATUS, MIE, MTVEC, MEPC, MCAUSE, MHARTID, FRM]

# 硬件浮点（rvf）时的寄存器类：整数运算用 reg 类，浮点值单独用 freg 类、
# 由 f0-f31 承载，指令选择时发射 F 扩展浮点指令
register_classes_hwfp = [
    RegisterClass(
        "reg",
        [ir.i8, ir.i16, ir.i32, ir.ptr, ir.u8, ir.u16, ir.u32],
        RiscvRegister,
        [
            R9,
            R10,
            R11,
            R12,
            R13,
            R14,
            R15,
            R16,
            R17,
            R18,
            R19,
            R20,
            R21,
            R22,
            R23,
            R24,
            R25,
            R26,
            R27,
        ],
    ),
    RegisterClass("freg", [ir.f32, ir.f64], RiscvFRegister, fregisters),
]

# 软浮点（无 rvf）时的寄存器类：f32/f64 也并入整数寄存器类，
# 浮点运算改由运行时软件例程（如 float32_add）完成
register_classes_swfp = [
    RegisterClass(
        "reg",
        [ir.i8, ir.i16, ir.i32, ir.ptr, ir.u8, ir.u16, ir.u32, ir.f32, ir.f64],
        RiscvRegister,
        [
            R9,
            R10,
            R11,
            R12,
            R13,
            R14,
            R15,
            R16,
            R17,
            R18,
            R19,
            R20,
            R21,
            R22,
            R23,
            R24,
            R25,
            R26,
            R27,
        ],
    )
]
