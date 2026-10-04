"""二进制数据指令模块：用指令的形式描述汇编中的数据指示（db/dw/dd/dq、
.byte/.zero/ds 等），使数据段与代码段共用同一条发射与编码流水线。数据
指令复用 Isa/Instruction/Token 机制，并通过重定位在链接期填入符号地址。

Contains instruction set for creating binary data.

For example:

db 0 -> 00
dw 0 -> 0000
dd 2 -> 02000000

"""

from qcc.backend.arch.encoding import Instruction, Operand, Relocation, Syntax
from qcc.backend.arch.isa import Isa
from qcc.backend.arch.token import Token, bit_range, u32

data_isa = Isa()


class ByteToken(Token):
    """8 位数据 token，用于 db/.byte 指示。"""

    class Info:
        size = 8

    value = bit_range(0, 8)


class WordToken(Token):
    """16 位数据 token，用于 dw 指示。"""

    class Info:
        size = 16

    value = bit_range(0, 16)


class DwordToken(Token):
    """32 位数据 token，用于 dd 指示。"""

    class Info:
        size = 32

    value = bit_range(0, 32)


class QwordToken(Token):
    """64 位数据 token，用于 dq 指示。"""

    class Info:
        size = 64

    value = bit_range(0, 64)


class DataInstruction(Instruction):
    """数据指示指令基类：绑定 data_isa，所有数据指令都由此继承。"""

    isa = data_isa


class Db(DataInstruction):
    """定义字节：db v，把立即数 v 编码为 1 字节。"""

    tokens = [ByteToken]
    v = Operand("v", int)
    syntax = Syntax(["db", " ", v])
    patterns = {"value": v}


class Dw(DataInstruction):
    """定义字：dw v，把立即数 v 编码为 2 字节。"""

    tokens = [WordToken]
    v = Operand("v", int)
    syntax = Syntax(["dw", " ", v])
    patterns = {"value": v}


class Dw2(DataInstruction):
    """定义 16 位符号引用：dw v（v 为符号名），经重定位填入地址。"""

    tokens = [WordToken]
    v = Operand("v", str)
    syntax = Syntax(["dw", " ", v])

    def relocations(self):
        return [U16DataRelocation(self.v)]


class DByte(DataInstruction):
    """定义字节（汇编写法 .byte v）。"""

    tokens = [ByteToken]
    v = Operand("v", int)
    syntax = Syntax([".", "byte", " ", v])
    patterns = {"value": v}


class DZero(DataInstruction):
    """保留 v 个零字节空间（.zero v），encode 直接产出 v 个 0。

    Reserve an amount of space"""

    tokens = []
    v = Operand("v", int)
    syntax = Syntax([".", "zero", " ", v])

    def encode(self):
        return bytes([0] * self.v)


@data_isa.register_relocation
class U16DataRelocation(Relocation):
    """16 位绝对地址重定位（absaddr16）：把符号地址写入 2 字节数据字段。"""

    name = "absaddr16"
    token = WordToken
    field = "value"

    def calc(self, sym_value, reloc_value):
        # The value of the symbol is not necessarily aligned at two bytes.
        # assert sym_value % 2 == 0
        assert reloc_value % 2 == 0
        return sym_value


class Dd(DataInstruction):
    """定义双字：dd v，把立即数 v 编码为 4 字节。"""

    tokens = [DwordToken]
    v = Operand("v", int)
    syntax = Syntax(["dd", " ", v])
    patterns = {"value": v}


@data_isa.register_relocation
class U32DataRelocation(Relocation):
    """32 位绝对地址重定位（absaddr32）：把符号地址写入 4 字节数据字段。"""

    name = "absaddr32"
    token = DwordToken
    field = "value"

    def calc(self, sym_value, reloc_value):
        assert reloc_value % 4 == 0
        return sym_value


class Dcd2(DataInstruction):
    """定义 32 位符号引用：dcd = v，先编码 0 占位再由重定位填入地址。"""

    v = Operand("v", str)
    syntax = Syntax(["dcd", " ", "=", v])

    def encode(self):
        return u32(0)

    def relocations(self):
        return [U32DataRelocation(self.v)]


class Dq(DataInstruction):
    """定义四字：dq v，编码为 8 字节，并携带 64 位绝对地址重定位。"""

    v = Operand("v", int)
    tokens = [QwordToken]
    syntax = Syntax(["dq", " ", v])
    patterns = {"value": v}

    def relocations(self):
        return [U64DataRelocation(self.v)]


@data_isa.register_relocation
class U64DataRelocation(Relocation):
    """64 位绝对地址重定位（absaddr64）：把符号地址写入 8 字节数据字段。"""

    name = "absaddr64"
    token = QwordToken
    field = "value"

    def calc(self, sym_value, reloc_value):
        # Not always true for example when referencing
        # a string literal aligned at 1 byte:
        # assert sym_value % 4 == 0

        assert reloc_value % 4 == 0
        return sym_value


class Dq2(DataInstruction):
    """定义 64 位符号引用：dq = v（patterns 先填 0 占位，链接期重定位）。"""

    v = Operand("v", str)
    tokens = [QwordToken]
    syntax = Syntax(["dq", " ", "=", v])
    patterns = {"value": 0}

    def relocations(self):
        return [U64DataRelocation(self.v)]


class Ds(DataInstruction):
    """保留 v 个零字节空间（ds v），encode 直接产出 v 个 0。

    Reserve an amount of space"""

    tokens = []
    v = Operand("v", int)
    syntax = Syntax(["ds", " ", v])

    def encode(self):
        return bytes([0] * self.v)
