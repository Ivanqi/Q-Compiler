"""RVC（压缩指令扩展）相关的重定位类型：链接期把跳转目标回填进 16/32 位指令字。

RVC 指令的立即数被打散到指令字中不连续的位段（见 apply_cool_mapping 的位重排），
因此这里每个 Relocation 子类都要把计算出的偏移按各自格式"拼图"进 token/原始字节。
除普通的拼位 apply 外，本模块还支持"收缩"优化：can_shrink 判断 32 位跳转能否塞进
16 位 C 指令，do_shrink 直接把指令字改写为 C.J / C.JAL 形态并返回新的重定位类型，
由链接器迭代调用（对应 rvc_instructions.py 中 CBl/CJal/CB/CJ 的 relocations()）。

"""

from qcc.utils.bitfun import BitView, wrap_negative
from qcc.backend.arch.encoding import Relocation
from qcc.backend.arch.riscv.tokens import RiscvcToken, RiscvToken


class CRel(Relocation):
    """RVC 重定位的公共基类，便于 isinstance 判定与统一处理。"""


class CBImm11Relocation(CRel):
    """32-bit relocation for `J` instruction"""

    name = "cb_imm11"
    token = RiscvToken

    def apply(self, sym_value, data, reloc_value):
        """把 pc 相对偏移按 J 型（20 位编码位段）写入 4 字节指令字。"""
        assert sym_value % 2 == 0


class CBImm11Relocation(CRel):
    """32-bit relocation for `J` instruction"""

    name = "cb_imm11"
    token = RiscvToken

    def apply(self, sym_value, data, reloc_value):
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = sym_value - reloc_value
        bv = BitView(data, 0, 4)
        rel20 = wrap_negative(offset >> 1, 20)
        bv[21:31] = rel20 & 0x3FF
        bv[20:21] = rel20 >> 10 & 0x1
        bv[12:20] = rel20 >> 11 & 0xFF
        bv[31:32] = rel20 >> 19 & 0x1
        return data

    def can_shrink(self, sym_value, reloc_value):
        """判断该 32 位跳转能否缩成 16 位 C.J（偏移是否在 12 位有符号范围内）。

        Test if we can optimize."""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = sym_value - reloc_value
        return isinsrange(12, offset)

    def do_shrink(self, sym_value, data, reloc_value):
        """执行收缩：把 32 位 jal 指令就地改写成 16 位 c.j 形态。

        Optimize instruction!

        Do several cool things now:
        - Patch memory to change opcode.
        - Return new relocation.
        """
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        bv = BitView(data, 0, 4)
        bv[0:2] = 0b01
        bv[13:16] = 0b101  # C.J opcode

        data = data[:2]  # Take first data!
        new_reloc = BcImm11Relocation(self.symbol_name)
        return data, [new_reloc]


class CBlImm11Relocation(CRel):
    """32 bit relocation for `jal` instruction."""

    name = "cbl_imm11"
    token = RiscvToken

    def apply(self, sym_value, data, reloc_value):
        """同 CBImm11Relocation：按 J 型位段回填 pc 相对偏移。"""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = sym_value - reloc_value
        bv = BitView(data, 0, 4)
        rel20 = wrap_negative(offset >> 1, 20)
        bv[21:31] = rel20 & 0x3FF
        bv[20:21] = rel20 >> 10 & 0x1
        bv[12:20] = rel20 >> 11 & 0xFF
        bv[31:32] = rel20 >> 19 & 0x1
        return data

    def can_shrink(self, sym_value, reloc_value):
        """判断 jal 能否缩成 c.jal：pc 相对偏移是否落在 12 位有符号范围内。"""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = sym_value - reloc_value
        return isinsrange(12, offset)

    def do_shrink(self, sym_value, data, reloc_value):
        """执行收缩：把 32 位 jal 指令就地改写为 16 位 c.jal（rd 固定为 ra）。"""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        bv = BitView(data, 0, 4)
        bv[0:2] = 0b01
        bv[13:16] = 0b001  # c.jal opcode

        data = data[:2]  # Take first data!
        new_reloc = BcImm11Relocation(self.symbol_name)
        return data, [new_reloc]


class BcImm11Relocation(CRel):
    """16 位 C.J/C.JAL 跳转的 11 位 pc 相对偏移重定位。"""

    name = "bc_imm11"
    token = RiscvcToken

    def apply(self, sym_value, data, reloc_value):
        """把偏移折成 11 位后按 C.J 的位排列写入（见 apply_cool_mapping）。"""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = sym_value - reloc_value
        rel11 = wrap_negative(offset >> 1, 11)
        bv = BitView(data, 0, 4)
        apply_cool_mapping(bv, rel11)
        return data


class BcImm8Relocation(CRel):
    """16 位 C.BEQZ/C.BNEZ 分支的 8 位 pc 相对偏移重定位。"""

    name = "bc_imm8"
    token = RiscvcToken

    def apply(self, sym_value, data, reloc_value):
        """把偏移折成 8 位后按 C.B 型分散位段逐位写入指令字。"""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = sym_value - reloc_value
        rel8 = wrap_negative(offset >> 1, 8)
        bv = BitView(data, 0, 4)
        bv[2:3] = rel8 >> 4 & 0x1
        bv[3:5] = rel8 & 0x3
        bv[5:7] = rel8 >> 5 & 0x3
        bv[10:12] = rel8 >> 2 & 0x3
        bv[12:13] = rel8 >> 7 & 0x1
        return data


def apply_cool_mapping(bv, rel11):
    """把 11 位偏移按 RVC 规范的散列位序搬进 C.J 指令字（纯粹的比特拼图）。

    This is some really nice bit fiddling!"""
    bv[2:3] = rel11 >> 4 & 0x1
    bv[3:6] = rel11 & 0x7
    bv[6:7] = rel11 >> 6 & 0x1
    bv[7:8] = rel11 >> 5 & 0x1
    bv[8:9] = rel11 >> 9 & 0x1
    bv[9:11] = rel11 >> 7 & 0x3
    bv[11:12] = rel11 >> 3 & 0x1
    bv[12:13] = rel11 >> 10 & 0x1


def isinsrange(bits, val):
    """判断 val 是否能被 bits 位（含符号位）的有符号立即数表示。

    Helper function to test if value is withing range."""
    msb = 1 << (bits - 1)
    ll = -msb
    return val <= (msb - 1) and (val >= ll)
