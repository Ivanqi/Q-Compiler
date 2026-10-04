"""ARM / Thumb 指令集容器与机器码位域（Token）定义。

定义四个 Isa 容器：arm_isa、thumb_isa、neon_isa、vfp_isa——它们收集
@isa.pattern 指令选择模式（见 arm_instructions.py / thumb_instructions.py）并
注册重定位类型（见 arm_relocations.py / thumb_relocations.py 的
@arm_isa.register_relocation），InstructionSelector1 与链接器据此工作；
同时定义编码指令二进制字段用的 Token 子类：ArmToken（32 位 ARM 指令，
cond/opcode/S/rd/rn/rm/shift 等位域）、ArmImmToken（32 位立即数型 ARM 指令）、
ThumbToken（16 位 Thumb 指令，只声明 rd 低位，其余字段在各指令类里手写）。
每条指令类通过 tokens 声明使用哪个 Token，encode() 时按位域填值再打包成机器码。

"""

from qcc.backend.arch.isa import Isa
from qcc.backend.arch.token import Token, bit, bit_range

arm_isa = Isa()
thumb_isa = Isa()
neon_isa = Isa()
vfp_isa = Isa()


# Tokens:
class ArmToken(Token):
    """32 位 ARM（A32）指令的位域定义，按标准编码格式切分字段。"""

    class Info:
        size = 32

    cond = bit_range(28, 32)
    opcode = bit_range(21, 28)
    S = bit(20)
    rd = bit_range(12, 16)
    rn = bit_range(16, 20)
    rm = bit_range(0, 4)
    b4 = bit(4)
    shift_typ = bit_range(5, 7)
    shift_imm = bit_range(7, 12)
    imm24 = bit_range(0, 24)
    imm8 = bit_range(0, 8)
    imm4h_imm4l = bit_range(8, 12) + bit_range(0, 4)


class ArmImmToken(Token):
    """32 位 ARM 立即数型指令（mov/cmp 等）的位域定义，imm12 存旋转立即数。"""

    class Info:
        size = 32

    cond = bit_range(28, 32)
    opcode = bit_range(21, 28)
    s = bit(20)
    rn = bit_range(16, 20)
    rd = bit_range(12, 16)
    imm12 = bit_range(0, 12)


class ThumbToken(Token):
    """16 位 Thumb 指令的最小位域定义，其余字段由各指令类的 encode() 手写。"""

    class Info:
        size = 16

    rd = bit_range(0, 3)
