"""Thumb 模式（16/32 位混合指令）链接期重定位。

为 Thumb 指令定义补丁规则，通过 @thumb_isa.register_relocation 注册进
thumb_isa，链接器遇到带 label 操作数的指令（见 thumb_instructions.py 的
B/Bw/Bl/Ldr3/Adr 及各条件分支的 relocations()）时按名查找：
Lit8Relocation（ldr rt, label 从字面量池取值）、WrapNew11Relocation（短分支 b）、
Rel8Relocation（条件分支 beq/bne 等）、BlImm11Relocation（bl/bw 的 T4 编码，
21 位偏移拆成 imm10:imm11:s）、BImm11Imm6Relocation（长条件分支 T3 编码，
拆成 imm11:imm6:s）。Thumb 偏移单位均为 2 字节，apply() 用 BitView 按位改写
已编码的 data 字节，32 位指令的 size() 为 4。

"""

from qcc.utils.bitfun import BitView, align, wrap_negative
from qcc.backend.arch.encoding import Relocation
from qcc.backend.arch.arm.isa import ThumbToken, thumb_isa


@thumb_isa.register_relocation
class Lit8Relocation(Relocation):
    """ldr rt, =value 的字面量池 pc 相对偏移（imm8，单位 4 字节）。"""

    name = "lit8"
    token = ThumbToken

    def apply(self, sym_value, data, reloc_value):
        assert sym_value % 4 == 0, f"{sym_value} not multiple of 4"
        offset = sym_value - (align(reloc_value + 2, 4))
        assert offset in range(0, 1024, 4), str(offset)
        rel8 = offset >> 2
        data[0] = rel8
        return data


@thumb_isa.register_relocation
class WrapNew11Relocation(Relocation):
    """短无条件分支 b target 的 11 位偏移（单位 2 字节，范围 ±2KB）。"""

    name = "wrap_new11"
    token = ThumbToken

    def apply(self, sym_value, data, reloc_value):
        offset = sym_value - (align(reloc_value, 2) + 4)
        assert offset in range(-2048, 2046, 2)
        imm11 = wrap_negative(offset >> 1, 11)
        bv = BitView(data, 0, 2)
        bv[0:11] = imm11
        return data


@thumb_isa.register_relocation
class Rel8Relocation(Relocation):
    """条件分支（beq/bne/blt…）的 8 位相对偏移（单位 2 字节）。"""

    name = "rel8"
    token = ThumbToken

    def apply(self, sym_value, data, reloc_value):
        assert sym_value % 2 == 0
        offset = sym_value - (align(reloc_value, 2) + 4)
        assert offset in range(-256, 254, 2), str(offset)
        imm8 = wrap_negative(offset >> 1, 8)
        data[0] = imm8
        return data


@thumb_isa.register_relocation
class BlImm11Relocation(Relocation):
    """bl / b.w（T4 编码）的 21 位偏移，拆成 imm10、imm11 与符号位 s 填入。"""

    name = "bl_imm11"

    @classmethod
    def size(cls):
        return 4

    def apply(self, sym_value, data, reloc_value):
        assert sym_value % 2 == 0
        offset = sym_value - (align(reloc_value, 2) + 4)
        assert offset in range(-16777216, 16777214, 2), str(offset)
        imm32 = wrap_negative(offset >> 1, 32)
        imm11 = imm32 & 0x7FF
        imm10 = (imm32 >> 11) & 0x3FF
        s = (imm32 >> 24) & 0x1
        bv = BitView(data, 0, 4)
        bv[0:10] = imm10
        bv[10:11] = s
        bv[16:27] = imm11
        return data


@thumb_isa.register_relocation
class BImm11Imm6Relocation(Relocation):
    """长条件分支（T3 编码）的偏移：imm11、imm6 与符号位 s 分段填入两个半字。"""

    name = "b_imm11_imm6"

    @classmethod
    def size(cls):
        return 4

    def apply(self, sym_value, data, reloc_value):
        assert sym_value % 2 == 0
        offset = sym_value - (align(reloc_value, 2) + 4)
        assert offset in range(-1048576, 1048574, 2), str(offset)
        imm32 = wrap_negative(offset >> 1, 32)
        imm11 = imm32 & 0x7FF
        imm6 = (imm32 >> 11) & 0x3F
        s = (imm32 >> 17) & 0x1
        # TODO: determine i1 and i2 better!
        i1 = s
        i2 = s
        j1 = i1
        j2 = i2
        data[2] = imm11 & 0xFF
        data[3] |= (imm11 >> 8) & 0x7
        data[3] |= (j1 << 5) | (j2 << 3)
        data[0] |= imm6
        data[1] |= s << 2
        return data
