"""RISC-V（非压缩）指令的重定位类型：链接期把符号值/偏移补丁进已编码的指令字。

链接器遍历各指令 relocations() 返回的重定位对象，调用 apply(sym_value, data,
reloc_value) 在原始字节上就地改写：有的走"token + 字段名"的通用路线（calc 算出
字段值，setattr 回填后重新 encode，如 BImm12/Abs32Imm12/RelImm12），有的直接
用 BitView 按位段拼立即数（如 BImm20 的 J 型分散位、Abs32Imm20/RelImm20 的 U 型
高 20 位、AbsAddr32 的整字地址）。这些类在 instructions.py 的 isa.register_relocation
注册，并由各指令类的 relocations() 挂到具体重定位点上。

"""

from qcc.utils.bitfun import BitView, wrap_negative
from qcc.backend.arch.encoding import Relocation
from qcc.backend.arch.riscv.tokens import RiscvIToken, RiscvSBToken, RiscvToken


class BImm12Relocation(Relocation):
    """B 型分支（beq 等）的 12 位 pc 相对偏移重定位，写入 SB token 的 imm 字段。"""

    name = "b_imm12"
    token = RiscvSBToken
    field = "imm"

    def calc(self, sym_value, reloc_value):
        """计算 pc 相对偏移（/2 折算成半字单位）并折算成 12 位有符号值。"""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = (sym_value - reloc_value) // 2
        return wrap_negative(offset, 12)

    def apply(self, sym_value, data, reloc_value):
        """按字段名回填的重定位通用实现：解出 token、写入 calc 结果、重新编码。

        Apply this relocation type given some parameters.

        This is the default implementation which stores the outcome of
        the calculate function into the proper token."""
        assert self.token is not None
        token = self.token.from_data(data)
        assert self.field is not None
        assert hasattr(token, self.field)
        setattr(token, self.field, self.calc(sym_value, reloc_value))
        return token.encode()


class BImm20Relocation(Relocation):
    """J 型跳转（jal）的 20 位 pc 相对偏移重定位，按 UJ 型分散位段手工拼位。"""

    name = "b_imm20"
    token = RiscvToken

    def apply(self, sym_value, data, reloc_value):
        """把偏移/2 折成 20 位后按 J 型位排列（[21:31]、bit20、[12:20]、bit31）写入。"""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = sym_value - reloc_value
        rel20 = wrap_negative(offset >> 1, 20)
        bv = BitView(data, 0, 4)
        bv[21:31] = rel20 & 0x3FF
        bv[20:21] = rel20 >> 10 & 0x1
        bv[12:20] = rel20 >> 11 & 0xFF
        bv[31:32] = rel20 >> 19 & 0x1
        return data


class Abs32Imm20Relocation(Relocation):
    """U 型指令（lui/auipc）的绝对地址高 20 位重定位：取 sym_value 的 [12:32] 位。"""

    name = "abs32_imm20"
    token = RiscvToken

    def apply(self, sym_value, data, reloc_value):
        """把绝对地址的高 20 位写入指令字 [12:32]，负值先换算成 32 位补码形态。"""
        assert sym_value % 2 == 0
        bv = BitView(data, 0, 4)
        if sym_value & 0x800 == 0:
            bv[12:32] = (sym_value >> 12) & 0xFFFFF
        else:
            sym_value -= 0xFFFFF000
            bv[12:32] = (sym_value >> 12) & 0xFFFFF
        return data


class RelImm20Relocation(Relocation):
    """U 型指令的 pc 相对高位重定位（%pcrel_hi 语义）：取相对偏移的高 20 位。"""

    name = "rel_imm20"
    token = RiscvToken

    def apply(self, sym_value, data, reloc_value):
        """计算 sym - reloc 的相对偏移，再按高 20 位（含符号扩展补偿）写入 [12:32]。"""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = sym_value - reloc_value
        bv = BitView(data, 0, 4)
        if offset & 0x800 == 0:
            bv[12:32] = (offset >> 12) & 0xFFFFF
        else:
            offset -= 0xFFFFF000
            bv[12:32] = (offset >> 12) & 0xFFFFF

        return data


class Abs32Imm12Relocation(Relocation):
    """I 型指令的绝对地址低 12 位重定位（与 Abs32Imm20 配对凑出完整地址）。"""

    name = "abs32_imm12"
    token = RiscvIToken
    field = "imm"

    def calc(self, sym_value, reloc_value):
        """取绝对地址的低 12 位。"""
        assert sym_value % 2 == 0
        return sym_value & 0xFFF

    def apply(self, sym_value, data, reloc_value):
        """按字段名回填的重定位通用实现：解出 token、写入 calc 结果、重新编码。

        Apply this relocation type given some parameters.

        This is the default implementation which stores the outcome of
        the calculate function into the proper token."""
        assert self.token is not None
        token = self.token.from_data(data)
        assert self.field is not None
        assert hasattr(token, self.field)
        setattr(token, self.field, self.calc(sym_value, reloc_value))
        return token.encode()


class RelImm12Relocation(Relocation):
    """I 型指令的 pc 相对低位重定位（%pcrel_lo 语义），与 RelImm20Relocation 配对。

    注意偏移额外 +4：RISC-V 的 pcrel 约定以 auipc 之后的地址为基准。
    """

    name = "rel_imm12"
    token = RiscvIToken
    field = "imm"

    def calc(self, sym_value, reloc_value):
        """计算相对偏移（+4 补偿 auipc 基准）并取低 12 位。"""
        assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        offset = sym_value - reloc_value + 4
        return offset & 0xFFF

    def apply(self, sym_value, data, reloc_value):
        """按字段名回填的重定位通用实现：解出 token、写入 calc 结果、重新编码。

        Apply this relocation type given some parameters.

        This is the default implementation which stores the outcome of
        the calculate function into the proper token."""
        assert self.token is not None
        token = self.token.from_data(data)
        assert self.field is not None
        assert hasattr(token, self.field)
        setattr(token, self.field, self.calc(sym_value, reloc_value))
        return token.encode()


class AbsAddr32Relocation(Relocation):
    """32 位绝对地址重定位：把符号值整字写入 4 字节数据（如 dcd 标签引用）。"""

    name = "absaddr32"
    token = RiscvToken

    def apply(self, sym_value, data, reloc_value):
        """把符号地址原样覆盖到数据字的 [0:32] 位。"""
        offset = sym_value
        bv = BitView(data, 0, 4)
        bv[0:32] = offset
        return data
