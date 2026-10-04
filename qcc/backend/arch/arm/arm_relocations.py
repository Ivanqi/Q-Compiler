"""ARM 模式（A32）链接期重定位。

为 32 位 ARM 指令中存放地址/偏移的位域定义补丁规则，通过
@arm_isa.register_relocation 注册进 arm_isa，链接器遇到带 label 操作数的指令
（见 arm_instructions.py 中 Bl/BranchBaseRoot/Arm 的 relocations()）时按名查找：
Rel8Relocation（条件分支的 imm8 相对偏移）、Imm24Relocation（bl/b 的 24 位 pc
相对跳转）、LdrImm12Relocation（ldr rt, label 的字面量池 pc 相对寻址，U 位表示
加减）、AdrImm12Relocation（adr 的 12 位旋转立即数偏移）。
calc() 返回字段值交给通用编码流程回填，apply() 则直接按小端字节改写已编码 data。

"""

from qcc.utils.bitfun import align, encode_imm32, wrap_negative
from qcc.backend.arch.encoding import Relocation
from qcc.backend.arch.arm.isa import ArmToken, arm_isa


@arm_isa.register_relocation
class Rel8Relocation(Relocation):
    """条件分支的相对偏移（imm8，单位 2 字节，范围 ±255 条指令）。"""

    name = "rel8"
    token = ArmToken
    field = "imm8"

    def calc(self, sym_value, reloc_value):
        assert sym_value % 2 == 0
        offset = sym_value - (align(reloc_value, 2) + 4)
        assert offset in range(-256, 254, 2), str(offset)
        return wrap_negative(offset >> 1, 8)


@arm_isa.register_relocation
class Imm24Relocation(Relocation):
    """bl/b 的 24 位 pc 相对跳转偏移（单位 4 字节，范围 ±32MB）。"""

    name = "imm24"
    token = ArmToken
    field = "imm24"

    def calc(self, sym_value, reloc_value):
        assert sym_value % 4 == 0
        assert reloc_value % 4 == 0
        offset = sym_value - (reloc_value + 8)
        return wrap_negative(offset >> 2, 24)


@arm_isa.register_relocation
class LdrImm12Relocation(Relocation):
    """ldr rt, label 的 pc 相对字面量池寻址（imm12 + U 位表示加/减）。"""

    name = "ldr_imm12"
    token = ArmToken

    def apply(self, sym_value, data, reloc_value):
        assert sym_value % 4 == 0
        assert reloc_value % 4 == 0
        offset = sym_value - (reloc_value + 8)
        U = 1
        if offset < 0:
            offset = -offset
            U = 0
        assert offset < 4096, f"{offset} < 4096 {sym_value} {data}"
        data[2] |= U << 7
        data[1] |= (offset >> 8) & 0xF
        data[0] = offset & 0xFF
        return data


@arm_isa.register_relocation
class AdrImm12Relocation(Relocation):
    """adr 指令的偏移（12 位旋转立即数，U 位区分加/减，8 位有效位）。"""

    name = "adr_imm12"
    token = ArmToken

    def apply(self, sym_value, data, reloc_value):
        assert sym_value % 4 == 0
        assert reloc_value % 4 == 0
        offset = sym_value - (reloc_value + 8)
        U = 2
        if offset < 0:
            offset = -offset
            U = 1
        assert offset < 4096
        offset = encode_imm32(offset)
        data[2] |= U << 6
        data[1] |= (offset >> 8) & 0xF
        data[0] = offset & 0xFF
        return data
