"""ARM（A32）指令定义与指令选择模式。

上半部分是机器指令字典：每条指令一个类，用 Operand/Syntax 描述操作数与汇编
语法，用 patterns 位域或手写 encode() 描述二进制编码，用 relocations() 挂链接期
重定位（arm_relocations.py），伪指令在发射期展开成真指令；同构指令由工厂函数
make_regregreg / make_regregimm / make_branch / inter_twine 批量生成。
下半部分是 @arm_isa.pattern 指令选择模式表，把 irdag 的树模式映射到发射函数，
供 InstructionSelector1 的 BURG 动态规划匹配后由 context.emit 落入 frame。

Definitions of arm instructions."""

# pylint: disable=no-member,invalid-name

from qcc.utils.bitfun import encode_imm32
from qcc.utils.tree import Tree
from qcc.backend.arch.encoding import (
    Constructor,
    Instruction,
    Operand,
    Syntax,
    Transform,
)
from qcc.backend.arch.generic_instructions import Global, RegisterUseDef
from qcc.backend.arch.arm.arm_relocations import (
    AdrImm12Relocation,
    Imm24Relocation,
    LdrImm12Relocation,
)
from qcc.backend.arch.arm.isa import ArmImmToken, ArmToken, Isa, arm_isa
from qcc.backend.arch.arm.registers import (
    R0,
    R1,
    R2,
    R11,
    ArmRegister,
    Coproc,
    Coreg,
    RegisterSet,
)

# Patterns:

# Condition patterns:
# 条件码（cond 位 [28:32]）常量，索引即编码值；COND_MAP 供 inter_twine 按名查表。
EQ, NE, CS, CC, MI, PL, VS, VC, HI, LS, GE, LT, GT, LE, AL = range(15)
COND_MAP = {
    "EQ": EQ,
    "NE": NE,
    "CS": CS,
    "CC": CC,
    "MI": MI,
    "PL": PL,
    "VS": VS,
    "VC": VC,
    "HI": HI,
    "LS": LS,
    "GE": GE,
    "LT": LT,
    "GT": GT,
    "LE": LE,
    "AL": AL,
}


class NoShift(Constructor):
    """不移位（shift_typ 与 shift_imm 均置 0，即 AL 默认的 LSL #0）。

    No shift"""

    syntax = Syntax([])
    patterns = {"shift_typ": 0, "shift_imm": 0}


class ShiftLsl(Constructor):
    """立即数逻辑左移 n 位（shift_typ=0）。

    Logical shift left n bits"""

    n = Operand("n", int)
    syntax = Syntax([",", " ", "lsl", " ", n])
    patterns = {"shift_typ": 0, "shift_imm": n}


class ShiftLsr(Constructor):
    """立即数逻辑右移 n 位（shift_typ=1）。

    Logical shift right n bits"""

    n = Operand("n", int)
    syntax = Syntax([",", " ", "lsr", " ", n])
    patterns = {"shift_typ": 1, "shift_imm": n}


class ShiftAsr(Constructor):
    """立即数算术右移 n 位（shift_typ=2）。

    Arithmetic shift right n bits"""

    n = Operand("n", int)
    syntax = Syntax([",", " ", "asr", " ", n])
    patterns = {"shift_typ": 2, "shift_imm": n}


# Shift suffix:
# 移位模式集合：数据处理指令的 shift 操作数取其中之一。
shift_modes = (NoShift, ShiftLsl, ShiftLsr, ShiftAsr)


# Instructions:


def inter_twine(a, cond):
    """按条件码派生同名带条件后缀的指令类（如 mov → movls）。

    Create a permutation for a instruction class"""
    # TODO: give this function the right name
    stx = list(a.syntax.syntax)
    ccode = COND_MAP[cond.upper()]
    mnemonic = stx[0] + cond
    stx[0] = mnemonic
    syntax = Syntax(stx)
    patterns = dict(a.patterns)
    patterns["cond"] = ccode
    members = {"syntax": syntax, "patterns": patterns}
    return type(mnemonic, (a,), members)


class ArmInstruction(Instruction):
    """所有 ARM（A32）真指令的基类：32 位定长指令字，注册在 arm_isa 上。"""

    tokens = [ArmToken]
    isa = arm_isa


class ArmExpand(Transform):
    """把 32 位立即数变换成 ARM 的 12 位旋转立即数编码（encode_imm32）。"""

    def forwards(self, value):
        return encode_imm32(value)


# 数据处理：立即数搬移 / 比较 / 乘除
class Mov1(ArmInstruction):
    """把 0–255 的立即数搬进寄存器：mov Rd, imm16（ArmImmToken 编码）。

    Mov Rd, imm16"""

    rd = Operand("rd", ArmRegister, write=True)
    imm = Operand("imm", int)
    tokens = [ArmImmToken]
    syntax = Syntax(["mov", " ", rd, ",", " ", imm])
    patterns = {
        "cond": AL,
        "opcode": 0b11101,
        "s": 0,
        "rn": 0,
        "rd": rd,
        "imm12": ArmExpand(imm),
    }


class Mov2(ArmInstruction):
    """寄存器到寄存器搬移（可带移位）：mov Rd, Rm{, shift}。

    Mov register to register"""

    rd = Operand("rd", ArmRegister, write=True)
    rm = Operand("rm", ArmRegister, read=True)
    shift = Operand("shift", shift_modes)
    syntax = Syntax(["mov", " ", rd, ",", " ", rm, shift])
    patterns = {
        "cond": AL,
        "opcode": 0b0001101,
        "S": 0,
        "rn": 0,
        "rd": rd,
        "shift_imm": 0,
        "shift_typ": 0,
        "b4": 0,
        "rm": rm,
    }


Mov2LS = inter_twine(Mov2, "ls")


class Cmp1(ArmInstruction):
    """寄存器与立即数比较（置标志位，供条件分支使用）：cmp Rn, imm。

    CMP Rn, imm"""

    reg = Operand("reg", ArmRegister, read=True)
    imm = Operand("imm", int)
    tokens = [ArmImmToken]
    syntax = Syntax(["cmp", " ", reg, ",", " ", imm])
    patterns = {
        "cond": AL,
        "opcode": 0b11010,
        "s": 1,
        "rn": reg,
        "rd": 0,
        "imm12": ArmExpand(imm),
    }


class Cmp2(ArmInstruction):
    """两寄存器比较（置标志位，供条件分支使用）：cmp Rn, Rm。

    CMP Rn, Rm"""

    rn = Operand("rn", ArmRegister, read=True)
    rm = Operand("rm", ArmRegister, read=True)
    shift = Operand("shift", shift_modes)
    syntax = Syntax(["cmp", " ", rn, ",", " ", rm, shift])
    patterns = {
        "cond": AL,
        "opcode": 0b1010,
        "S": 1,
        "rm": rm,
        "rd": 0,
        "b4": 0,
        "rn": rn,
    }


class Mul1(ArmInstruction):
    """乘法：rd = rn * rm（手写位编码，cond 恒为 AL）。"""

    rd = Operand("rd", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    rm = Operand("rm", ArmRegister, read=True)
    syntax = Syntax(["mul", " ", rd, ",", " ", rn, ",", " ", rm])

    def encode(self):
        tokens = self.get_tokens()
        tokens[0][0:4] = self.rn.num
        tokens[0][4:8] = 0b1001
        tokens[0][8:12] = self.rm.num
        tokens[0][16:20] = self.rd.num
        tokens[0].S = 0
        tokens[0].cond = AL
        return tokens[0].encode()


class Sdiv(ArmInstruction):
    """有符号除法：rd = rn / rm，手写编码（并非所有 ARM 核都实现该指令）。

    Encoding A1
    rd = rn / rm

    This instruction is not always present
    """

    rd = Operand("rd", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    rm = Operand("rm", ArmRegister, read=True)
    syntax = Syntax(["sdiv", rd, ",", rn, ",", rm])

    def encode(self):
        tokens = self.get_tokens()
        tokens[0][0:4] = self.rn.num
        tokens[0][4:8] = 0b0001
        tokens[0][8:12] = self.rm.num
        tokens[0][12:16] = 0b1111
        tokens[0][16:20] = self.rd.num
        tokens[0][20:28] = 0b1110001
        tokens[0].cond = AL
        return tokens[0].encode()


class Udiv(ArmInstruction):
    """无符号除法：rd = rn / rm，手写编码。

    Encoding A1
    rd = rn / rm
    """

    rd = Operand("rd", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    rm = Operand("rm", ArmRegister, read=True)
    syntax = Syntax(["udiv", rd, ",", rn, ",", rm])
    patterns = {"cond": AL}

    def encode(self):
        tokens = self.get_tokens()
        self.set_all_patterns(tokens)
        tokens[0][0:4] = self.rn.num
        tokens[0][4:8] = 0b0001
        tokens[0][8:12] = self.rm.num
        tokens[0][12:16] = 0b1111
        tokens[0][16:20] = self.rd.num
        tokens[0][20:28] = 0b1110011
        return tokens.encode()


class Mls(ArmInstruction):
    """乘减：rd = ra - rn * rm（求余运算用它与除法组合实现）。

    Multiply substract
    Semantics:
    rd = ra - rn * rm
    """

    rd = Operand("rd", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    rm = Operand("rm", ArmRegister, read=True)
    ra = Operand("ra", ArmRegister, read=True)
    syntax = Syntax(["mls", rd, ",", rn, ",", rm, ",", ra])
    patterns = {"cond": AL}

    def encode(self):
        tokens = self.get_tokens()
        self.set_all_patterns(tokens)
        tokens[0][0:4] = self.rn.num
        tokens[0][4:8] = 0b1001
        tokens[0][8:12] = self.rm.num
        tokens[0][12:16] = self.ra.num
        tokens[0][16:20] = self.rd.num
        tokens[0][20:28] = 0b00000110
        return tokens.encode()


def make_regregreg(mnemonic, opcode):
    """工厂：造一个三寄存器（rd, rn, rm + 移位）数据处理指令类。

    Create a new instruction class with three registers as operands"""
    rd = Operand("rd", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    rm = Operand("rm", ArmRegister, read=True)
    shift = Operand("shift", shift_modes)
    syntax = Syntax([mnemonic, " ", rd, ",", " ", rn, ",", " ", rm, shift])
    patterns = {
        "cond": AL,
        "opcode": opcode,
        "S": 0,
        "rn": rn,
        "rd": rd,
        "b4": 0,
        "rm": rm,
    }
    members = {
        "syntax": syntax,
        "rd": rd,
        "rn": rn,
        "rm": rm,
        "shift": shift,
        "patterns": patterns,
    }
    return type(mnemonic + "_ins", (ArmInstruction,), members)


# 由工厂批量生成的三寄存器算术/逻辑指令（参数为 opcode 位段值）：
Adc = make_regregreg("adc", 0b0000101)
Add = make_regregreg("add", 0b0000100)
And = make_regregreg("and", 0b0000000)
Eor = make_regregreg("eor", 0b0000001)
Orr = make_regregreg("orr", 0b0001100)
Sub = make_regregreg("sub", 0b0000010)


# sub 的条件后缀变体（借位减法，用于 64 位/多字运算的进位链）：
Sub1CC = inter_twine(Sub, "cc")
Sub1cs = inter_twine(Sub, "cs")
Sub1NE = inter_twine(Sub, "ne")


class ShiftBase(ArmInstruction):
    """寄存器移位指令基类（三个操作数，opcode 区分 lsl/lsr/asr）。

    ? rd, rn, rm"""

    rd = Operand("rd", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    rm = Operand("rm", ArmRegister, read=True)
    patterns = {"cond": AL, "S": 0}

    def encode(self):
        tokens = self.get_tokens()
        self.set_all_patterns(tokens)
        tokens[0][0:4] = self.rn.num
        tokens[0][4:8] = self.opcode
        tokens[0][8:12] = self.rm.num
        tokens[0][12:16] = self.rd.num
        tokens[0][21:28] = 0b1101
        return tokens[0].encode()


# 移位指令（变长移位，移位量在寄存器里）：
class Lsr1(ShiftBase):
    """逻辑右移：lsr rd, rn, rm（rm 存放移位量）。"""

    opcode = 0b0011
    syntax = Syntax(
        [
            "lsr",
            " ",
            ShiftBase.rd,
            ",",
            " ",
            ShiftBase.rn,
            ",",
            " ",
            ShiftBase.rm,
        ]
    )


Lsr = Lsr1


class Lsl1(ShiftBase):
    """逻辑左移：lsl rd, rn, rm（rm 存放移位量）。"""

    opcode = 0b0001
    syntax = Syntax(
        ["lsl", " ", ShiftBase.rd, ",", ShiftBase.rn, ",", ShiftBase.rm]
    )


Lsl = Lsl1


class Asr(ShiftBase):
    """算术右移：asr rd, rn, rm（rm 存放移位量）。"""

    opcode = 0b0101
    syntax = Syntax(
        ["asr", " ", ShiftBase.rd, ",", ShiftBase.rn, ",", ShiftBase.rm]
    )


class OpRegRegImm(ArmInstruction):
    """寄存器-立即数运算基类（imm12 经 encode_imm32 旋转编码）。

    add rd, rn, imm12"""

    def encode(self):
        tokens = self.get_tokens()
        tokens[0][0:12] = encode_imm32(self.imm)
        tokens[0].rd = self.rd.num
        tokens[0].rn = self.rn.num
        tokens[0].S = 0  # Set flags
        tokens[0][21:28] = self.opcode
        tokens[0].cond = AL
        return tokens[0].encode()


def make_regregimm(mnemonic, opcode):
    """工厂：造一个寄存器-立即数运算指令类（rd, rn, imm）。"""
    rd = Operand("rd", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    imm = Operand("imm", int)
    syntax = Syntax([mnemonic, " ", rd, ",", " ", rn, ",", " ", imm])
    members = {
        "syntax": syntax,
        "rd": rd,
        "rn": rn,
        "imm": imm,
        "opcode": opcode,
    }
    return type(mnemonic + "_ins", (OpRegRegImm,), members)


# 由工厂批量生成的立即数型运算指令（rsb 用于取负，即 rsb rd, rn, #0）：
AdcImm = make_regregimm("adc", 0b0010101)
AddImm = make_regregimm("add", 0b0010100)
AndImm = make_regregimm("and", 0b0010000)
EorImm = make_regregimm("eor", 0b0010001)
OrrImm = make_regregimm("orr", 0b0011100)
RsbImm = make_regregimm("rsb", 0b0010011)
RscImm = make_regregimm("rsc", 0b0010111)
SbcImm = make_regregimm("sbc", 0b0010110)
SubImm = make_regregimm("sub", 0b0010010)


# Branches:
# 分支指令：目标为标签字符串，24 位 pc 相对偏移交给 Imm24Relocation 在链接期回填。


class BranchBaseRoot(ArmInstruction):
    """分支基类：encode 只填 cond 与 opcode，偏移由链接期重定位填充。"""

    target = Operand("target", str)

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = self.cond
        tokens[0][24:28] = self.opcode
        return tokens[0].encode()

    def relocations(self):
        return [Imm24Relocation(self.target)]


class BranchBase(BranchBaseRoot):
    """普通条件/无条件分支（b 系列，opcode 0b1010）。"""

    opcode = 0b1010


class BranchLinkBase(BranchBaseRoot):
    """带链接的分支（bl，返回地址写入 LR，opcode 0b1011）。"""

    opcode = 0b1011


class Bl(BranchLinkBase):
    """函数调用：bl target。"""

    cond = AL
    syntax = Syntax(["bl", " ", BranchBaseRoot.target])


def make_branch(mnemonic, cond):
    """工厂：造一个以条件码区分的分支指令类。"""
    target = Operand("target", str)
    syntax = Syntax([mnemonic, " ", target])
    members = {"syntax": syntax, "target": target, "cond": cond}
    return type(mnemonic + "_ins", (BranchBase,), members)


# 由工厂批量生成的分支指令（cond 即指令的条件码字段）：
B = make_branch("b", AL)
Beq = make_branch("beq", EQ)
Bgt = make_branch("bgt", GT)
Bge = make_branch("bge", GE)
Bls = make_branch("bls", LS)
Ble = make_branch("ble", LE)
Blt = make_branch("blt", LT)
Bne = make_branch("bne", NE)
Bhs = make_branch("bhs", CS)  # Hs (higher or same) is synonim for CS
Blo = make_branch("blo", CC)  # Lo (unsigned lower) is synonim for CC


class Blx(ArmInstruction):
    """间接调用：blx Rm（目标地址在寄存器中，可切换 ARM/Thumb 状态）。

    Branch with link to a subroutine pointer to by register"""

    rm = Operand("rm", ArmRegister, read=True)
    syntax = Syntax(["blx", " ", rm])
    patterns = {"cond": AL, "rm": rm}

    def encode(self):
        tokens = self.get_tokens()
        self.set_all_patterns(tokens)
        tokens[0][4:28] = 0x12FFF3
        return tokens.encode()


def reg_list_to_mask(reg_list):
    """把寄存器集合换算成 16 位位掩码（bit n 对应 Rn），用于 push/pop 编码。"""
    mask = 0
    for reg in reg_list:
        mask |= 1 << reg.num
    return mask


# 栈操作：push/pop 用 16 位寄存器掩码编码（同时压入/弹出多个寄存器）
class Push(ArmInstruction):
    """入栈：push {reg_list}，寄存器列表编码成位掩码。"""

    reg_list = Operand("reg_list", RegisterSet)
    syntax = Syntax(["push", " ", reg_list])

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = AL
        tokens[0][16:28] = 0b100100101101
        tokens[0][0:16] = reg_list_to_mask(self.reg_list)
        return tokens[0].encode()


class Pop(ArmInstruction):
    """出栈：pop {reg_list}，寄存器列表编码成位掩码。"""

    reg_list = Operand("reg_list", RegisterSet)
    syntax = Syntax(["pop", " ", reg_list])

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = AL
        tokens[0][16:28] = 0b100010111101
        tokens[0][0:16] = reg_list_to_mask(self.reg_list)
        return tokens[0].encode()


def LdrPseudo(rt, lab, add_lit):
    """伪指令：ldr rt, =lab 展开成从字面量池取值（登记常量并转为 ldr rt, label）。

    Ldr rt, =lab ==> ldr rt, [pc, offset in litpool] ... dcd lab"""
    lit_lbl = add_lit(lab)
    return Ldr3(rt, lit_lbl)


# 访存指令：基址寄存器 + 12 位偏移（bit23 为 U 位表示加减，立即数不旋转）
class LdrStrBase(ArmInstruction):
    """load/store 基类：rn 为基址，offset 为立即数偏移（负偏移清 U 位）。"""

    rn = Operand("rn", ArmRegister, read=True)
    offset = Operand("offset", int)

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = AL
        tokens[0].rn = self.rn.num
        tokens[0][25:28] = self.opcode
        tokens[0][22] = self.bit22
        tokens[0][20] = self.bit20
        tokens[0][12:16] = self.rt.num
        tokens[0][24] = 1  # Index
        if self.offset >= 0:
            tokens[0][23] = 1  # U == 1 'add'
            tokens[0][0:12] = self.offset
        else:
            tokens[0][23] = 0
            tokens[0][0:12] = -self.offset
        return tokens.encode()


class Str1(LdrStrBase):
    """存字：str rt, [rn, offset]（bit20 选择 load/store）。"""

    rt = Operand("rt", ArmRegister, read=True)
    opcode = 0b010
    bit20 = 0
    bit22 = 0
    syntax = Syntax(
        [
            "str",
            " ",
            rt,
            ",",
            " ",
            "[",
            LdrStrBase.rn,
            ",",
            " ",
            LdrStrBase.offset,
            "]",
        ]
    )


class Ldr1(LdrStrBase):
    """取字：ldr rt, [rn, #offset]。"""

    rt = Operand("rt", ArmRegister, write=True)
    opcode = 0b010
    bit20 = 1
    bit22 = 0
    syntax = Syntax(
        [
            "ldr",
            " ",
            rt,
            ",",
            " ",
            "[",
            LdrStrBase.rn,
            ",",
            " ",
            "#",
            LdrStrBase.offset,
            "]",
        ]
    )


class Strh(ArmInstruction):
    """存半字：strh rd, [rn, #imm]（半字偏移拆成 imm4h:imm4l 两段）。

    Store half word at register + immediate"""

    rd = Operand("rd", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    imm = Operand("imm", int)
    syntax = Syntax(
        ["strh", " ", rd, ",", " ", "[", rn, ",", " ", "#", imm, "]"]
    )
    patterns = {"rn": rn, "rd": rd, "cond": AL}

    def set_user_patterns(self, tokens):
        if self.imm < 0:
            offset = -self.imm
            u = 0
        else:
            offset = self.imm
            u = 1

        tokens[0][0:4] = offset & 0xF
        tokens[0][4:8] = 0b1011
        tokens[0][8:12] = (offset >> 4) & 0xF
        tokens[0][20] = 0
        tokens[0][21] = 0  # W
        tokens[0][22] = 1
        tokens[0][23] = u
        tokens[0][24] = 1  # P


class Strb(LdrStrBase):
    """存字节：strb rt, [rn, #offset]（bit22 区分字/字节）。

    ldrb rt, [rn, offset] # Store byte at address"""

    rt = Operand("rt", ArmRegister, read=True)
    opcode = 0b010
    bit20 = 0
    bit22 = 1
    syntax = Syntax(
        [
            "strb",
            " ",
            rt,
            ",",
            " ",
            "[",
            LdrStrBase.rn,
            ",",
            " ",
            "#",
            LdrStrBase.offset,
            "]",
        ]
    )


class Ldrb(LdrStrBase):
    """取字节（零扩展）：ldrb rt, [rn, #offset]。

    ldrb rt, [rn, offset]"""

    rt = Operand("rt", ArmRegister, write=True)
    opcode = 0b010
    bit20 = 1
    bit22 = 1
    syntax = Syntax(
        [
            "ldrb",
            " ",
            rt,
            ",",
            " ",
            "[",
            LdrStrBase.rn,
            ",",
            " ",
            "#",
            LdrStrBase.offset,
            "]",
        ]
    )


class Ldrsb(ArmInstruction):
    """取字节并符号扩展：ldrsb rt, [rn, #offset]。

    ldrsb rt, [rn, offset].

    Load byte and sign extend.
    """

    rt = Operand("rt", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    offset = Operand("offset", int)
    syntax = Syntax(
        ["ldrsb", " ", rt, ",", " ", "[", rn, ",", " ", "#", offset, "]"]
    )

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = AL
        tokens[0][25:28] = 0
        tokens[0][24] = 1  # Index
        tokens[0][22] = 1
        tokens[0][21] = 0  # W?
        tokens[0][20] = 1
        tokens[0].rn = self.rn.num
        tokens[0][12:16] = self.rt.num
        tokens[0][4:8] = 0b1101
        if self.offset >= 0:
            tokens[0][23] = 1  # U == 1 'add'
            offset = self.offset
        else:
            tokens[0][23] = 0
            offset = -self.offset
        tokens[0].imm4h_imm4l = offset
        return tokens.encode()


class Ldrh_imm(ArmInstruction):
    """取半字并零扩展：ldrh rt, [rn, #offset]。

    ldrh rt, [rn, offset].

    Load half word and zero extend.
    """

    rt = Operand("rt", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    offset = Operand("offset", int)
    syntax = Syntax(
        ["ldrh", " ", rt, ",", " ", "[", rn, ",", " ", "#", offset, "]"]
    )

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = AL
        tokens[0][25:28] = 0
        tokens[0][24] = 1  # Index
        tokens[0][22] = 1
        tokens[0][21] = 0  # W?
        tokens[0][20] = 1
        tokens[0].rn = self.rn.num
        tokens[0][12:16] = self.rt.num
        tokens[0][4:8] = 0b1011
        if self.offset >= 0:
            tokens[0][23] = 1  # U == 1 'add'
            offset = self.offset
        else:
            tokens[0][23] = 0
            offset = -self.offset
        tokens[0].imm4h_imm4l = offset
        return tokens.encode()


class Ldrsh_imm(ArmInstruction):
    """取半字并符号扩展：ldrsh rt, [rn, #offset]。

    ldrsh rt, [rn, offset].

    Load signed half word and sign extend.
    """

    rt = Operand("rt", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    offset = Operand("offset", int)
    syntax = Syntax(
        ["ldrsh", " ", rt, ",", " ", "[", rn, ",", " ", "#", offset, "]"]
    )

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = AL
        tokens[0][25:28] = 0
        tokens[0][24] = 1  # Index
        tokens[0][22] = 1
        tokens[0][21] = 0  # W?
        tokens[0][20] = 1
        tokens[0].rn = self.rn.num
        tokens[0][12:16] = self.rt.num
        tokens[0][4:8] = 0b1111
        if self.offset >= 0:
            tokens[0][23] = 1  # U == 1 'add'
            offset = self.offset
        else:
            tokens[0][23] = 0
            offset = -self.offset
        tokens[0].imm4h_imm4l = offset
        return tokens.encode()


class Ldrsh_reg(ArmInstruction):
    """取半字并符号扩展（寄存器变址）：ldrsh rt, [rn, rm]。

    ldrsh rt, [rn, rm].

    Load signed half word and sign extend.
    """

    rt = Operand("rt", ArmRegister, write=True)
    rn = Operand("rn", ArmRegister, read=True)
    rm = Operand("rm", ArmRegister, read=True)
    offset = Operand("offset", int)
    syntax = Syntax(["ldrsh", " ", rt, ",", " ", "[", rn, ",", " ", rm, "]"])

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = AL
        tokens[0][25:28] = 0
        tokens[0][24] = 1  # Index
        tokens[0][23] = 1  # U == 1 'add'
        tokens[0][22] = 0
        tokens[0][21] = 0  # W?
        tokens[0][20] = 1
        tokens[0].rn = self.rn.num
        tokens[0][12:16] = self.rt.num
        tokens[0][8:12] = 0b0000
        tokens[0][4:8] = 0b1111
        tokens[0][0:4] = self.rm.num
        return tokens.encode()


class Adr(ArmInstruction):
    """把标签地址装入寄存器：adr rd, label（12 位偏移由链接期重定位回填）。"""

    rd = Operand("rd", ArmRegister, write=True)
    label = Operand("label", str)
    syntax = Syntax(["adr", rd, ",", label])

    def relocations(self):
        return [AdrImm12Relocation(self.label)]

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = AL
        tokens[0][0:12] = 0  # Filled by linker
        tokens[0][12:16] = self.rd.num
        tokens[0][16:20] = 0b1111
        tokens[0][25] = 1
        return tokens[0].encode()


class Ldr3(ArmInstruction):
    """从 pc 相对的字面量池取常量：ldr rt, label（偏移由 LdrImm12Relocation 回填）。

    Load PC relative constant value
    LDR rt, label
    encoding A1
    """

    rt = Operand("rt", ArmRegister, write=True)
    label = Operand("label", str)
    syntax = Syntax(["ldr", " ", rt, ",", " ", label])

    def relocations(self):
        return [LdrImm12Relocation(self.label)]

    def encode(self):
        tokens = self.get_tokens()
        tokens[0].cond = AL
        tokens[0][0:12] = 0  # Filled by linker
        tokens[0][12:16] = self.rt.num
        tokens[0][16:23] = 0b0011111
        tokens[0][24:28] = 0b0101
        return tokens[0].encode()


# 协处理器传送：ARM 寄存器 <-> 协处理器寄存器（bit20 选择方向）
class McrBase(ArmInstruction):
    """协处理器传送基类，按 opc1/crn/crm/opc2 等字段手写编码。

    Mov arm register to coprocessor register"""

    def encode(self):
        tokens = self.get_tokens()
        tokens[0][0:4] = self.crm.num
        tokens[0][4] = 1
        tokens[0][5:8] = self.opc2
        tokens[0][8:12] = self.coproc.num
        tokens[0][12:16] = self.rt.num
        tokens[0][16:20] = self.crn.num
        tokens[0][20] = self.b20
        tokens[0][21:24] = self.opc1
        tokens[0][24:28] = 0b1110
        tokens[0].cond = AL
        return tokens[0].encode()


class Mcr(McrBase):
    """ARM 寄存器写入协处理器寄存器：mcr coproc, opc1, rt, crn, crm, opc2。

    Move from register to co processor register"""

    coproc = Operand("coproc", Coproc, read=True)
    opc1 = Operand("opc1", int)
    rt = Operand("rt", ArmRegister, read=True)
    crn = Operand("crn", Coreg, read=True)
    crm = Operand("crm", Coreg, read=True)
    opc2 = Operand("opc2", int)
    b20 = 0
    syntax = Syntax(
        ["mcr", coproc, ",", opc1, ",", rt, ",", crn, ",", crm, ",", opc2]
    )


class Mrc(McrBase):
    """从协处理器寄存器读到 ARM 寄存器：mrc coproc, opc1, rt, crn, crm, opc2。"""

    coproc = Operand("coproc", Coproc, read=True)
    opc1 = Operand("opc1", int)
    rt = Operand("rt", ArmRegister, write=True)
    crn = Operand("crn", Coreg, read=True)
    crm = Operand("crm", Coreg, read=True)
    opc2 = Operand("opc2", int)
    b20 = 1
    syntax = Syntax(
        ["mrc", coproc, ",", opc1, ",", rt, ",", crn, ",", crm, ",", opc2]
    )


# Instruction selection patterns:
@arm_isa.pattern(
    "mem",
    "ADDI32(reg, CONSTI32)",
    size=0,
    condition=lambda t: t[1].value < 256,
)
def pattern_mem_reg_offset(context, tree, c0):
    offset = tree.children[0].children[1].value
    return c0, offset


@arm_isa.pattern("mem", "FPRELU32", size=0, cycles=0, energy=0)
def pattern_mem_fprel32(context, tree):
    offset = tree.value.offset
    return R11, offset


@arm_isa.pattern("mem", "reg", size=0, cycles=0, energy=0)
def pattern_mem_reg(context, tree, c0):
    return c0, 0


@arm_isa.pattern("stm", "STRI32(mem, reg)", size=4)
@arm_isa.pattern("stm", "STRU32(mem, reg)", size=4)
def pattern_str32(self, tree, c0, c1):
    base_reg, offset = c0
    self.emit(Str1(c1, base_reg, offset))


@arm_isa.pattern("stm", "STRI16(mem, reg)", size=4)
@arm_isa.pattern("stm", "STRU16(mem, reg)", size=4)
def pattern_str16(self, tree, c0, c1):
    base_reg, offset = c0
    self.emit(Strh(c1, base_reg, offset))


@arm_isa.pattern("stm", "STRI8(mem, reg)", size=4)
@arm_isa.pattern("stm", "STRU8(mem, reg)", size=4)
def pattern_str8(context, tree, c0, c1):
    base_reg, offset = c0
    context.emit(Strb(c1, base_reg, offset))


@arm_isa.pattern("stm", "MOVB(reg, reg)", size=40)
def pattern_movb(context, tree, c0, c1):
    # Emit memcpy
    dst = c0
    src = c1
    tmp = context.new_reg(ArmRegister)
    size = tree.value
    for instruction in context.arch.gen_arm_memcpy(dst, src, tmp, size):
        context.emit(instruction)


@arm_isa.pattern("stm", "MOVI32(reg)", size=4)
@arm_isa.pattern("stm", "MOVU32(reg)", size=4)
def pattern_mov32(context, tree, c0):
    context.move(tree.value, c0)


@arm_isa.pattern("stm", "MOVI16(reg)", size=4)
@arm_isa.pattern("stm", "MOVU16(reg)", size=4)
def pattern_mov16(context, tree, c0):
    context.move(tree.value, c0)


@arm_isa.pattern("stm", "MOVI8(reg)", size=4)
@arm_isa.pattern("stm", "MOVU8(reg)", size=4)
def pattern_mov8(context, tree, c0):
    context.move(tree.value, c0)


@arm_isa.pattern("stm", "JMP", size=2)
def pattern_jmp(context, tree):
    tgt = tree.value
    context.emit(B(tgt.name, jumps=[tgt]))


@arm_isa.pattern("reg", "REGI32", size=0, cycles=0, energy=0)
@arm_isa.pattern("reg", "REGU32", size=0, cycles=0, energy=0)
def pattern_reg32(context, tree):
    return tree.value


@arm_isa.pattern("reg", "REGI16", size=0, cycles=0, energy=0)
@arm_isa.pattern("reg", "REGU16", size=0, cycles=0, energy=0)
def pattern_reg16(context, tree):
    return tree.value


@arm_isa.pattern("reg", "REGI8", size=0, cycles=0, energy=0)
@arm_isa.pattern("reg", "REGU8", size=0, cycles=0, energy=0)
def pattern_reg8(context, tree):
    return tree.value


@arm_isa.pattern("reg", "I8TOI32(reg)", size=0)
@arm_isa.pattern("reg", "U8TOI32(reg)", size=0)
@arm_isa.pattern("reg", "I8TOU32(reg)", size=0)
@arm_isa.pattern("reg", "U8TOU32(reg)", size=0)
def pattern_i8toi32(self, tree, c0):
    # TODO: do something?
    # Sign extend for example?
    return c0


@arm_isa.pattern("reg", "U32TOI8(reg)", size=0)
@arm_isa.pattern("reg", "U32TOU8(reg)", size=0)
@arm_isa.pattern("reg", "I32TOI8(reg)", size=0)
@arm_isa.pattern("reg", "I32TOU8(reg)", size=0)
def pattern_i32toi8(context, tree, c0):
    d2 = context.new_reg(ArmRegister)
    context.emit(AndImm(d2, c0, 0xFF))
    return d2


@arm_isa.pattern("reg", "U32TOU16(reg)", size=4)
@arm_isa.pattern("reg", "U32TOI16(reg)", size=4)
@arm_isa.pattern("reg", "I32TOI16(reg)", size=4)
@arm_isa.pattern("reg", "I32TOU16(reg)", size=4)
def pattern_i32toi16(context, tree, c0):
    # d2 = context.new_reg(ArmRegister)
    # context.emit(Sxth(d2, c0))
    return c0


@arm_isa.pattern("reg", "I16TOI32(reg)", size=4)
def pattern_i16toi32(context, tree, c0):
    # d2 = context.new_reg(ArmRegister)
    # TODO:
    # context.emit(Sxth(d2, c0))
    return c0


@arm_isa.pattern("reg", "I16TOU32(reg)", size=4)
@arm_isa.pattern("reg", "U16TOI32(reg)", size=4)
@arm_isa.pattern("reg", "U16TOU32(reg)", size=4)
def pattern_i16tou32(context, tree, c0):
    return c0


@arm_isa.pattern("reg", "CONSTI32", size=8)
@arm_isa.pattern("reg", "CONSTU32", size=8)
@arm_isa.pattern("reg", "CONSTI16", size=8)
@arm_isa.pattern("reg", "CONSTU16", size=8)
def pattern_const32(context, tree):
    d = context.new_reg(ArmRegister)
    ln = context.frame.add_constant(tree.value)
    context.emit(Ldr3(d, ln))
    return d


@arm_isa.pattern(
    "reg", "CONSTU32", size=4, condition=lambda t: t.value in range(256)
)
@arm_isa.pattern(
    "reg", "CONSTI32", size=4, condition=lambda t: t.value in range(256)
)
def pattern_const32_1(context, tree):
    d = context.new_reg(ArmRegister)
    c0 = tree.value
    assert isinstance(c0, int)
    assert c0 < 256 and c0 >= 0
    context.emit(Mov1(d, c0))
    return d


@arm_isa.pattern(
    "reg", "CONSTI16", size=4, condition=lambda t: t.value in range(256)
)
@arm_isa.pattern(
    "reg", "CONSTU16", size=4, condition=lambda t: t.value in range(256)
)
@arm_isa.pattern(
    "reg", "CONSTI8", size=4, condition=lambda t: t.value in range(256)
)
@arm_isa.pattern(
    "reg", "CONSTU8", size=4, condition=lambda t: t.value in range(256)
)
def pattern_const8_1(context, tree):
    d = context.new_reg(ArmRegister)
    c0 = tree.value
    assert isinstance(c0, int)
    assert c0 < 256 and c0 >= 0
    context.emit(Mov1(d, c0))
    return d


@arm_isa.pattern("stm", "CJMPI32(reg, reg)", size=2)
@arm_isa.pattern("stm", "CJMPI16(reg, reg)", size=2)
@arm_isa.pattern("stm", "CJMPI8(reg, reg)", size=2)
def pattern_cjmp_signed(context, tree, c0, c1):
    op, yes_label, no_label = tree.value
    opnames = {"<": Blt, ">": Bgt, "==": Beq, "!=": Bne, "<=": Ble, ">=": Bge}
    Bop = opnames[op]
    context.emit(Cmp2(c0, c1, NoShift()))
    jmp_ins = B(no_label.name, jumps=[no_label])
    context.emit(Bop(yes_label.name, jumps=[yes_label, jmp_ins]))
    context.emit(jmp_ins)


@arm_isa.pattern("stm", "CJMPU32(reg, reg)", size=2)
@arm_isa.pattern("stm", "CJMPU16(reg, reg)", size=2)
@arm_isa.pattern("stm", "CJMPU8(reg, reg)", size=2)
def pattern_cjmp_unsigned(context, tree, c0, c1):
    op, yes_label, no_label = tree.value
    opnames = {
        "<": (Blo, False),
        ">": (Blo, True),
        "==": (Beq, False),
        "!=": (Bne, False),
        "<=": (Bhs, True),
        ">=": (Bhs, False),
    }
    Bop, do_swap = opnames[op]
    if do_swap:
        context.emit(Cmp2(c1, c0, NoShift()))
    else:
        context.emit(Cmp2(c0, c1, NoShift()))
    jmp_ins = B(no_label.name, jumps=[no_label])
    context.emit(Bop(yes_label.name, jumps=[yes_label, jmp_ins]))
    context.emit(jmp_ins)


@arm_isa.pattern("reg", "ADDI32(reg, reg)", size=2)
@arm_isa.pattern("reg", "ADDU32(reg, reg)", size=2)
def pattern_add32(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Add(d, c0, c1, NoShift()))
    return d


@arm_isa.pattern("reg", "ADDI16(reg, reg)", size=2)
@arm_isa.pattern("reg", "ADDU16(reg, reg)", size=2)
def pattern_add16(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Add(d, c0, c1, NoShift()))
    return d


@arm_isa.pattern("reg", "ADDI8(reg, reg)", size=4)
@arm_isa.pattern("reg", "ADDU8(reg, reg)", size=4)
def pattern_add8(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Add(d, c0, c1, NoShift()))
    d2 = context.new_reg(ArmRegister)
    context.emit(AndImm(d2, d, 0xFF))
    return d2


@arm_isa.pattern(
    "reg",
    "ADDI32(reg, CONSTI32)",
    size=4,
    condition=lambda t: t.children[1].value in range(256),
)
def pattern_add32_1(context, tree, c0):
    d = context.new_reg(ArmRegister)
    c1 = tree.children[1].value
    context.emit(AddImm(d, c0, c1))
    return d


@arm_isa.pattern(
    "reg",
    "ADDI32(CONSTI32, reg)",
    size=4,
    condition=lambda t: t.children[0].value in range(256),
)
def pattern_add32_2(context, tree, c0):
    d = context.new_reg(ArmRegister)
    c1 = tree.children[0].value
    context.emit(AddImm(d, c0, c1))
    return d


@arm_isa.pattern("reg", "SUBI32(reg, reg)", size=4)
@arm_isa.pattern("reg", "SUBU32(reg, reg)", size=4)
def pattern_sub32(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Sub(d, c0, c1, NoShift()))
    return d


@arm_isa.pattern("reg", "SUBI16(reg, reg)", size=4)
@arm_isa.pattern("reg", "SUBU16(reg, reg)", size=4)
def pattern_sub16(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Sub(d, c0, c1, NoShift()))
    return d


@arm_isa.pattern("reg", "SUBI8(reg, reg)", size=4)
def pattern_sub8(context, tree, c0, c1):
    # TODO: temporary fix this with an 32 bits sub
    d = context.new_reg(ArmRegister)
    context.emit(Sub(d, c0, c1, NoShift()))
    d2 = context.new_reg(ArmRegister)
    context.emit(AndImm(d2, d, 0xFF))
    return d2


@arm_isa.pattern("reg", "LABEL", size=8)
def pattern_label(context, tree):
    d = context.new_reg(ArmRegister)
    ln = context.frame.add_constant(tree.value)
    context.emit(Ldr3(d, ln))
    return d


@arm_isa.pattern("reg", "FPRELU32", size=4, cycles=2, energy=2)
def pattern_fprel32(context, tree):
    d = context.new_reg(ArmRegister)
    c1 = tree.value.offset
    assert isinstance(c1, int)
    if c1 in range(-255, 256):
        if c1 >= 0:
            context.emit(AddImm(d, R11, c1))
        else:
            context.emit(SubImm(d, R11, -c1))
    else:
        d2 = context.new_reg(ArmRegister)
        ln = context.frame.add_constant(c1)
        context.emit(Ldr3(d2, ln))
        context.emit(Add(d, R11, d2, NoShift()))
    return d


@arm_isa.pattern("reg", "LDRI8(mem)", size=4)
def pattern_ldr_i8(context, tree, c0):
    d = context.new_reg(ArmRegister)
    base_reg, offset = c0
    context.emit(Ldrsb(d, base_reg, offset))
    return d


@arm_isa.pattern("reg", "LDRU8(mem)", size=4)
def pattern_ldr_u8(context, tree, c0):
    d = context.new_reg(ArmRegister)
    base_reg, offset = c0
    context.emit(Ldrb(d, base_reg, offset))
    return d


@arm_isa.pattern("reg", "LDRI16(mem)", size=4, energy=8)
def pattern_ldr_i16(context, tree, c0):
    d = context.new_reg(ArmRegister)
    base_reg, offset = c0
    context.emit(Ldrsh_imm(d, base_reg, offset))
    return d


@arm_isa.pattern("reg", "LDRU16(mem)", size=4, energy=8)
def pattern_ldr_u16(context, tree, c0):
    d = context.new_reg(ArmRegister)
    base_reg, offset = c0
    context.emit(Ldrh_imm(d, base_reg, offset))
    return d


@arm_isa.pattern("reg", "LDRI32(mem)", size=4)
@arm_isa.pattern("reg", "LDRU32(mem)", size=4)
def pattern_ld32(context, tree, c0):
    d = context.new_reg(ArmRegister)
    base_reg, offset = c0
    context.emit(Ldr1(d, base_reg, offset))
    return d


@arm_isa.pattern("reg", "ANDI8(reg, reg)", size=4)
@arm_isa.pattern("reg", "ANDU8(reg, reg)", size=4)
@arm_isa.pattern("reg", "ANDI16(reg, reg)", size=4)
@arm_isa.pattern("reg", "ANDU16(reg, reg)", size=4)
@arm_isa.pattern("reg", "ANDI32(reg, reg)", size=4)
@arm_isa.pattern("reg", "ANDU32(reg, reg)", size=4)
def pattern_and(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(And(d, c0, c1, NoShift()))
    return d


@arm_isa.pattern("reg", Tree("ORI8", Tree("reg"), Tree("reg")), size=4)
@arm_isa.pattern("reg", Tree("ORU8", Tree("reg"), Tree("reg")), size=4)
@arm_isa.pattern("reg", Tree("ORI16", Tree("reg"), Tree("reg")), size=4)
@arm_isa.pattern("reg", Tree("ORU16", Tree("reg"), Tree("reg")), size=4)
@arm_isa.pattern("reg", Tree("ORI32", Tree("reg"), Tree("reg")), size=4)
@arm_isa.pattern("reg", Tree("ORU32", Tree("reg"), Tree("reg")), size=4)
def pattern_or32(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Orr(d, c0, c1, NoShift()))
    return d


@arm_isa.pattern("reg", "SHRI32(reg, reg)", size=4)
def pattern_shr_i32(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Asr(d, c0, c1))
    return d


@arm_isa.pattern("reg", "SHRU32(reg, reg)", size=4)
def pattern_shr_u32(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Lsr1(d, c0, c1))
    return d


@arm_isa.pattern("reg", "SHRI16(reg, reg)", size=4)
def pattern_shr_i16(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    # TODO: mask with 0xffff at some point?
    context.emit(Asr(d, c0, c1))
    return d


@arm_isa.pattern("reg", "SHRU16(reg, reg)", size=4)
def pattern_shr_u16(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    # TODO: mask with 0xffff at some point?
    context.emit(Lsr1(d, c0, c1))
    return d


@arm_isa.pattern("reg", "SHRI8(reg, reg)", size=4)
def pattern_shr8(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    # TODO: mask with 0xffff at some point?
    context.emit(Asr(d, c0, c1))
    return d


@arm_isa.pattern("reg", "SHRU8(reg, reg)", size=4)
def pattern_shr_u8(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    # TODO: mask with 0xffff at some point?
    context.emit(Lsr1(d, c0, c1))
    return d


@arm_isa.pattern("reg", Tree("SHLI32", Tree("reg"), Tree("reg")), size=4)
@arm_isa.pattern("reg", Tree("SHLU32", Tree("reg"), Tree("reg")), size=4)
def pattern_shl32(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Lsl1(d, c0, c1))
    return d


@arm_isa.pattern("reg", Tree("SHLI16", Tree("reg"), Tree("reg")), size=4)
@arm_isa.pattern("reg", Tree("SHLU16", Tree("reg"), Tree("reg")), size=4)
def pattern_shl16(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    # TODO: mask with 0xffff at some point?
    context.emit(Lsl1(d, c0, c1))
    return d


@arm_isa.pattern("reg", Tree("SHLI8", Tree("reg"), Tree("reg")), size=4)
@arm_isa.pattern("reg", Tree("SHLU8", Tree("reg"), Tree("reg")), size=4)
def pattern_shl8(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    # TODO: mask with 0xffff at some point?
    context.emit(Lsl1(d, c0, c1))
    return d


@arm_isa.pattern("reg", "MULI32(reg, reg)", size=4)
@arm_isa.pattern("reg", "MULU32(reg, reg)", size=4)
def pattern_mul32(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Mul1(d, c0, c1))
    return d


@arm_isa.pattern("reg", "LDRI32(ADDI32(reg, CONSTI32))", size=4)
def pattern_ldr32(context, tree, c0):
    d = context.new_reg(ArmRegister)
    c1 = tree.children[0].children[1].value
    assert isinstance(c1, int)
    context.emit(Ldr1(d, c0, c1))
    return d


def call_internal2(context, name, a, b):
    """Call internal helper with two parameters and one return value"""
    d = context.new_reg(ArmRegister)
    # Generate call into runtime lib function!
    context.move(R1, a)
    context.move(R2, b)
    context.emit(RegisterUseDef(uses=(R1, R2)))
    context.emit(Global(name))
    context.emit(Bl(name))
    context.emit(RegisterUseDef(defs=(R0,)))
    context.move(d, R0)
    return d


@arm_isa.pattern("reg", "DIVI32(reg, reg)")
def pattern_divi32(context, tree, c0, c1):
    return call_internal2(context, "__sdiv", c0, c1)


@arm_isa.pattern("reg", "DIVU32(reg, reg)")
def pattern_divu32(context, tree, c0, c1):
    return call_internal2(context, "__udiv", c0, c1)


@arm_isa.pattern("reg", "REMI32(reg, reg)")
def pattern_rem32(context, tree, c0, c1):
    # Implement remainder as a combo of div and mls (multiply substract)
    d = call_internal2(context, "__sdiv", c0, c1)
    d2 = context.new_reg(ArmRegister)
    context.emit(Mls(d2, d, c1, c0))
    return d2


@arm_isa.pattern("reg", "XORI8(reg, reg)", size=4)
@arm_isa.pattern("reg", "XORU8(reg, reg)", size=4)
@arm_isa.pattern("reg", "XORI16(reg, reg)", size=4)
@arm_isa.pattern("reg", "XORU16(reg, reg)", size=4)
@arm_isa.pattern("reg", "XORI32(reg, reg)", size=4)
@arm_isa.pattern("reg", "XORU32(reg, reg)", size=4)
def pattern_xor(context, tree, c0, c1):
    d = context.new_reg(ArmRegister)
    context.emit(Eor(d, c0, c1, NoShift()))
    return d


@arm_isa.pattern("reg", "NEGI32(reg)", size=4)
@arm_isa.pattern("reg", "NEGU32(reg)", size=4)
@arm_isa.pattern("reg", "NEGI16(reg)", size=4)
@arm_isa.pattern("reg", "NEGU16(reg)", size=4)
@arm_isa.pattern("reg", "NEGI8(reg)", size=4)
@arm_isa.pattern("reg", "NEGU8(reg)", size=4)
def pattern_neg32(context, tree, c0):
    d = context.new_reg(ArmRegister)
    # Implement as rsb with immediate value
    context.emit(RsbImm(d, c0, 0))
    return d


@arm_isa.pattern("reg", "INVI32(reg)", size=4)
@arm_isa.pattern("reg", "INVU32(reg)", size=4)
def pattern_inv32(context, tree, c0):
    d = context.new_reg(ArmRegister)
    context.move(R1, c0)
    context.emit(Bl("__inv32"))
    context.move(d, R0)
    return d


# TODO: Do that here, or in irdag?


isa_with_div = Isa()


@isa_with_div.pattern("reg", "DIVI32(reg, reg)", size=4)
def pattern_23(self, tree, c0, c1):
    d = self.new_reg(ArmRegister)
    self.emit(Udiv, dst=[d], src=[c0, c1])
    return d


@isa_with_div.pattern("reg", "REMI32(reg, reg)", size=4)
def pattern_24(self, tree, c0, c1):
    # Implement remainder as a combo of div and mls (multiply substract)
    d = self.new_reg(ArmRegister)
    self.emit(Udiv, dst=[d], src=[c0, c1])
    d2 = self.new_reg(ArmRegister)
    self.emit(Mls, dst=[d2], src=[d, c1, c0])
    return d2
