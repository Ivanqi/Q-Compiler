"""
示例目标架构：包含若干指令的最小化教学后端，被测试用例使用，也是编写新
后端时的起点模板——演示 Architecture/ArchInfo/Isa/Register 的最小装配。

This is an example target with some instructions. It is used in test-cases
and serves as a minimal example.
"""

from qcc.midend import ir
from qcc.backend.arch.arch import Architecture
from qcc.backend.arch.arch_info import ArchInfo, TypeInfo
from qcc.backend.arch.encoding import Instruction, Operand, Syntax
from qcc.backend.arch.isa import Isa
from qcc.backend.arch.registers import Register, RegisterClass


class ExampleArch(Architecture):
    """最小示例架构：注册寄存器类、类型信息与空实现的调用/序言/尾声钩子，
    是创建新后端时的起始模板。

    Simple example architecture. This is intended as starting point
    when creating a new backend"""

    name = "example"

    def __init__(self, options=None):
        super().__init__(options=options)
        register_classes = [
            RegisterClass(
                "reg", [ir.i32, ir.ptr], ExampleRegister, [R0, R1, R2, R3, R10]
            ),
            RegisterClass("hreg", [ir.i16], HalfExampleRegister, [R10l]),
        ]
        self.gdb_registers = gdb_registers
        self.gdb_pc = R0
        self.isa = Isa()
        self.info = ArchInfo(
            type_infos={
                ir.i8: TypeInfo(1, 1),
                ir.u8: TypeInfo(1, 1),
                ir.i16: TypeInfo(2, 2),
                ir.u16: TypeInfo(2, 2),
                ir.i32: TypeInfo(4, 4),
                ir.u32: TypeInfo(4, 4),
                ir.f32: TypeInfo(4, 4),
                ir.f64: TypeInfo(8, 8),
                "int": ir.i32,
                "long": ir.i32,
                "ptr": ir.u32,
            },
            register_classes=register_classes,
        )

    def gen_prologue(self, frame):
        return []

    def gen_epilogue(self, frame):
        return []

    def gen_call(self, label, args, rv):
        return []

    def gen_function_enter(self, args):
        return []

    def gen_function_exit(self, rv):
        return []

    def determine_arg_locations(self, arg_types):
        """按参数类型依次分配 r0..r3 作为实参位置。

        Given a set of argument types, determine locations"""
        arg_locs = []
        regs = [R0, R1, R2, R3]
        for _ in arg_types:
            r = regs.pop(0)
            arg_locs.append(r)
        return arg_locs

    def determine_rv_location(self, ret_type):
        rv = R0
        return rv


class ExampleRegister(Register):
    """示例 32 位寄存器类（r0..r10），可按编号取单例。

    Example register class"""

    bitsize = 32

    @classmethod
    def from_num(cls, num):
        return num_reg_map[num]


class HalfExampleRegister(Register):
    """示例 16 位寄存器类（与 r10 别名，演示寄存器别名机制）。

    Example register class"""

    bitsize = 16

    @classmethod
    def from_num(cls, num):
        assert num == 100
        return R10l


R0 = ExampleRegister("r0", 0)
R1 = ExampleRegister("r1", 1)
R2 = ExampleRegister("r2", 2)
R3 = ExampleRegister("r3", 3)
R4 = ExampleRegister("r4", 4)
R5 = ExampleRegister("r5", 5)
R6 = ExampleRegister("r6", 6)
# Two aliasing registers:
R10 = ExampleRegister("r10", 10)
R10l = HalfExampleRegister("r10l", 100, aliases=(R10,))

all_regs = [R0, R1, R2, R3, R4, R5, R6, R10]
num_reg_map = {r.num: r for r in all_regs}

gdb_registers = (R0, R1, R2)


class ExampleInstruction(Instruction):
    """全部示例指令的基类（无 tokens，仅演示语法与操作数声明）。

    Base class for all example instructions"""

    tokens = []


class Def(ExampleInstruction):
    """示例指令：只定值一个 32 位寄存器（def rd）。"""

    rd = Operand("rd", ExampleRegister, write=True)
    syntax = Syntax(["def", " ", rd])


class DefHalf(ExampleInstruction):
    """示例指令：只定值一个 16 位寄存器（def rd）。"""

    rd = Operand("rd", HalfExampleRegister, write=True)
    syntax = Syntax(["def", " ", rd])


class Use(ExampleInstruction):
    """示例指令：只使用一个 32 位寄存器（use rn）。"""

    rn = Operand("rn", ExampleRegister, read=True)
    syntax = Syntax(["use", " ", rn])


class UseHalf(ExampleInstruction):
    """示例指令：只使用一个 16 位寄存器（use rn）。"""

    rn = Operand("rn", HalfExampleRegister, read=True)
    syntax = Syntax(["use", " ", rn])


class DefUse(ExampleInstruction):
    """示例指令：读一个寄存器、写另一个寄存器（cpy rd, rn）。"""

    rd = Operand("rd", ExampleRegister, write=True)
    rn = Operand("rn", ExampleRegister, read=True)
    syntax = Syntax(["cpy", " ", rd, ",", " ", rn])


class Add(ExampleInstruction):
    """示例指令：三寄存器加法（add rd, rm, rn）。"""

    rd = Operand("rd", ExampleRegister, write=True)
    rm = Operand("rm", ExampleRegister, read=True)
    rn = Operand("rn", ExampleRegister, read=True)
    syntax = Syntax(["add", " ", rd, ",", " ", rm, ",", " ", rn])


class Cmp(ExampleInstruction):
    """示例指令：读两个寄存器做比较（cmp rm, rn）。"""

    rm = Operand("rm", ExampleRegister, read=True)
    rn = Operand("rn", ExampleRegister, read=True)
    syntax = Syntax(["cmp", " ", rm, ",", " ", rn])


class Use3(ExampleInstruction):
    """示例指令：读三个寄存器（use3 rm, rn, ro），用于测试多操作数场景。"""

    rm = Operand("rm", ExampleRegister, read=True)
    rn = Operand("rn", ExampleRegister, read=True)
    ro = Operand("ro", ExampleRegister, read=True)
    syntax = Syntax(["use3", " ", rm, ",", " ", rn, ",", " ", ro])


class Mov(ExampleInstruction):
    """示例指令：寄存器搬移（mov rd, rm）。"""

    rd = Operand("rd", ExampleRegister, write=True)
    rm = Operand("rm", ExampleRegister, read=True)
    syntax = Syntax(["mov", " ", rd, ",", " ", rm])
