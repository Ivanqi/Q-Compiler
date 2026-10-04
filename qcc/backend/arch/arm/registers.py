"""ARM 寄存器定义与寄存器集合工具。

定义寄存器类：ArmRegister（32 位通用寄存器）、LowArmRegister（低寄存器
R0–R7，Thumb 16 位指令通常只能编码它们）、VfpRegister（VFP 浮点寄存器）、
Coreg 与 Coproc（协处理器寄存器/编号）；随后实例化 R0…R12、SP、LR、PC，
并给出 low/high/all 分组与 num2regmap（供 from_num 使用）。
RegisterSet 表示汇编中 "{}" 寄存器列表语法（arch.py 的 add_extra_rules 把它
接入汇编器），register_range 用于展开 "r0-r3" 这类范围写法。
ArmArch 用这些类构造 RegisterClass（可分配寄存器与适用类型），供寄存器分配使用。

"""

from qcc.backend.arch.registers import Register

# pylint: disable=invalid-name


class ArmRegister(Register):
    """32 位 ARM 通用寄存器基类（R0–R15，含 SP/LR/PC）。"""

    bitsize = 32

    @classmethod
    def from_num(cls, num):
        """按编号取对应寄存器对象。

        Based on a number, get the corresponding register"""
        return num2regmap[num]


class LowArmRegister(ArmRegister):
    """低寄存器 R0–R7：Thumb 16 位指令通常只能编码这些寄存器。"""

    pass


class VfpRegister(Register):
    """VFP 浮点寄存器。"""

    bitsize = 32


def register_range(a, b):
    """返回 a 到 b（含两端）之间的寄存器集合。

    Return set of registers from a to b"""
    assert a.num < b.num
    return {ArmRegister.from_num(n) for n in range(a.num, b.num + 1)}


class RegisterSet(set):
    """寄存器集合，对应汇编里的 {} 寄存器列表语法，支持集合运算。"""

    def __repr__(self):
        reg_names = sorted(str(r) for r in self)
        return ", ".join(reg_names)


R0 = LowArmRegister("R0", num=0)
R1 = LowArmRegister("R1", num=1)
R2 = LowArmRegister("R2", num=2)
R3 = LowArmRegister("R3", num=3)
R4 = LowArmRegister("R4", num=4)
R5 = LowArmRegister("R5", num=5)
R6 = LowArmRegister("R6", num=6)
R7 = LowArmRegister("R7", num=7)
R8 = ArmRegister("R8", num=8)
R9 = ArmRegister("R9", num=9)
R10 = ArmRegister("R10", num=10)
R11 = ArmRegister("R11", num=11)
R12 = ArmRegister("R12", num=12)
SP = ArmRegister("SP", num=13)
LR = ArmRegister("LR", num=14)
PC = ArmRegister("PC", num=15)

registers_low = (R0, R1, R2, R3, R4, R5, R6, R7)
registers_high = (R8, R9, R10, R11, R12, SP, LR, PC)
all_registers = registers_low + registers_high
num2regmap = {r.num: r for r in all_registers}


LowArmRegister.registers = registers_low
ArmRegister.registers = all_registers


class Coreg(Register):
    """协处理器寄存器（c0–c15），用于 mcr/mrc 协处理器传送指令。"""

    pass


c0 = Coreg("c0", 0)
c1 = Coreg("c1", 1)
c2 = Coreg("c2", 2)
c3 = Coreg("c3", 3)
c4 = Coreg("c4", 4)
c5 = Coreg("c5", 5)
c6 = Coreg("c6", 6)
c7 = Coreg("c7", 7)
c8 = Coreg("c8", 8)
c9 = Coreg("c9", 9)
c10 = Coreg("c10", 10)
c11 = Coreg("c11", 11)
c12 = Coreg("c12", 12)
c13 = Coreg("c13", 13)
c14 = Coreg("c14", 14)
c15 = Coreg("c15", 15)

Coreg.registers = [
    c0,
    c1,
    c2,
    c3,
    c4,
    c5,
    c6,
    c7,
    c8,
    c9,
    c10,
    c11,
    c12,
    c13,
    c14,
    c15,
]


class Coproc(Register):
    """协处理器编号（p8–p15）。"""

    pass


p8 = Coproc("p8", 8)
p9 = Coproc("p9", 9)
p10 = Coproc("p10", 10)
p11 = Coproc("p11", 11)
p12 = Coproc("p12", 12)
p13 = Coproc("p13", 13)
p14 = Coproc("p14", 14)
p15 = Coproc("p15", 15)

Coproc.registers = [p8, p9, p10, p11, p12, p13, p14, p15]
