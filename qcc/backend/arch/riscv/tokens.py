"""RISC-V 指令字（token）的位域布局定义。

每条机器指令的二进制形态由若干 token 类描述：token 是"指令字的一个片段"，
bit_range/bit/bit_concat 把 32 位（或 16 位压缩指令）字切分成 opcode、rd、funct3、
rs1、rs2、funct7、imm 等命名字段，instructions.py 里各指令的 encode()/patterns
按字段名填值，再由 Token.encode() 拼成最终机器码。
本模块是编码（encode）与重定位（relocations.py 中按 token/field 回填）共同的底层约定：
RiscvToken 为 R 型，RiscvIToken/RiscvSToken/RiscvSBToken 分别是 I/S/SB 型，
RiscvcToken 则是 16 位压缩指令（RVC 扩展）的 token。
"""

from qcc.backend.arch.token import Token, bit, bit_concat, bit_range


class RiscvToken(Token):
    """32 位 R 型指令字：opcode(0:7) | rd | funct3 | rs1 | rs2 | funct7(25:32)。"""

    class Info:
        size = 32

    opcode = bit_range(0, 7)
    rd = bit_range(7, 12)
    funct3 = bit_range(12, 15)
    rs1 = bit_range(15, 20)
    rs2 = bit_range(20, 25)
    funct7 = bit_range(25, 32)


class RiscvIToken(Token):
    """32 位 I 型指令字：opcode | rd | funct3 | rs1 | imm(20:32)（12 位立即数）。"""

    class Info:
        size = 32

    opcode = bit_range(0, 7)
    rd = bit_range(7, 12)
    funct3 = bit_range(12, 15)
    rs1 = bit_range(15, 20)
    imm = bit_range(20, 32)


class RiscvSToken(Token):
    """32 位 S 型（store）指令字：imm 由 [25:32] 与 [7:12] 两段拼接（imm[11:5] 与 imm[4:0]）。"""

    class Info:
        size = 32

    opcode = bit_range(0, 7)
    funct3 = bit_range(12, 15)
    rs1 = bit_range(15, 20)
    rs2 = bit_range(20, 25)
    imm = bit_concat(bit_range(25, 32), bit_range(7, 12))


class RiscvSBToken(Token):
    """32 位 SB 型（分支）指令字：imm 由 bit31 | bit7 | [25:31] | [8:12] 四段拼成。"""

    class Info:
        size = 32

    opcode = bit_range(0, 7)
    funct3 = bit_range(12, 15)
    rs1 = bit_range(15, 20)
    rs2 = bit_range(20, 25)
    imm = bit(31) + bit(7) + bit_range(25, 31) + bit_range(8, 12)


class RiscvcToken(Token):
    """16 位压缩指令字（RVC 扩展）：op | rd | funct3 | imm，offset 为带符号的访存偏移。"""

    class Info:
        size = 16

    op = bit_range(0, 2)
    rd = bit_range(7, 12)
    funct3 = bit_range(13, 16)
    imm = bit(12) + bit_range(2, 7)
    offset = bit_range(10, 13, signed=True) + bit_range(2, 7)
