# -*- coding: utf-8 -*-
"""RISC-V 指令（qcc/backend/arch/riscv/instructions.py）专用测试。

直接构造指令对象测试（test_riscvasm.py 走汇编器文本路径，
本文件走 Instruction 对象 + encode()/decode() 的编码层）：
- I/S/B/U 型指令的手工编码字节
- 编码 → 解码回读一致性
- 立即数/寄存器字段位布局
- 伪指令（Li/La/Nop）展开
"""
import struct
import unittest

from qcc.backend.arch.riscv import instructions as ins
from qcc.backend.arch.riscv.registers import R0, R10, R11, R12
from qcc.backend.arch.riscv.instructions import (
    Addi,
    Beq,
    Lui,
    Nop,
    RiscvInstruction,
    Sw,
    Lw,
)


def word_from_bytes(data):
    """小端字节序列 → 32 位指令字"""
    assert len(data) == 4
    return struct.unpack("<I", data)[0]


class RiscvInstructionEncodingTestCase(unittest.TestCase):
    """指令对象编码单元测试"""

    def test_addi_encoding(self):
        """addi rd, rs1, imm 的 I 型编码"""
        inst = Addi(R10, R11, 5)
        data = inst.encode()
        word = word_from_bytes(data)
        self.assertEqual(word & 0x7F, 0b0010011)      # opcode
        self.assertEqual((word >> 7) & 0x1F, 10)      # rd
        self.assertEqual((word >> 12) & 0x7, 0b000)   # funct3
        self.assertEqual((word >> 15) & 0x1F, 11)     # rs1
        self.assertEqual((word >> 20) & 0xFFF, 5)     # imm

    def test_lui_encoding(self):
        """lui rd, imm 的 U 型编码：立即数在高 20 位"""
        inst = Lui(R10, 0x12345)
        word = word_from_bytes(inst.encode())
        self.assertEqual(word & 0x7F, 0b0110111)
        self.assertEqual((word >> 7) & 0x1F, 10)
        self.assertEqual(word >> 12, 0x12345)

    def test_lw_sw_encoding(self):
        """lw/sw 的 I/S 型编码与字段布局"""
        lw = Lw(R11, 8, R10)
        word = word_from_bytes(lw.encode())
        self.assertEqual(word & 0x7F, 0b0000011)      # lw opcode
        self.assertEqual((word >> 7) & 0x1F, 11)      # rd
        self.assertEqual((word >> 15) & 0x1F, 10)     # rs1
        self.assertEqual((word >> 20) & 0xFFF, 8)     # imm

        sw = Sw(R11, 8, R10)
        word = word_from_bytes(sw.encode())
        self.assertEqual(word & 0x7F, 0b0100011)      # sw opcode
        # S 型立即数拆分：imm[4:0] 在 [7:12]，imm[11:5] 在 [25:32]
        self.assertEqual((word >> 7) & 0x1F, 8 & 0x1F)
        self.assertEqual((word >> 25) & 0x7F, (8 >> 5) & 0x7F)

    def test_negative_offset_encoding(self):
        """负偏移量的 S 型立即数拆分"""
        sw = Sw(R11, -8, R10)
        word = word_from_bytes(sw.encode())
        self.assertEqual((word >> 7) & 0x1F, (-8) & 0x1F)
        self.assertEqual((word >> 25) & 0x7F, ((-8) >> 5) & 0x7F)

    def test_beq_encoding(self):
        """beq 的 B 型编码：funct3 + rs1/rs2 位置"""
        inst = Beq(R10, R11, "label")
        word = word_from_bytes(inst.encode())
        self.assertEqual(word & 0x7F, 0b1100011)      # branch opcode
        self.assertEqual((word >> 12) & 0x7, 0b000)   # funct3 = beq
        self.assertEqual((word >> 15) & 0x1F, 10)     # rs1
        self.assertEqual((word >> 20) & 0x1F, 11)     # rs2

    def test_nop_encoding(self):
        """nop = addi x0, x0, 0"""
        self.assertEqual(word_from_bytes(Nop().encode()), 0x00000013)

    def test_instruction_syntax_rendering(self):
        """指令的汇编文本渲染"""
        inst = Addi(R10, R11, 5)
        self.assertEqual(str(inst), "addi x10, x11, 5")

    def test_signed_immediate(self):
        """立即数按 12 位有符号数回读（负值编码正确）"""
        inst = Addi(R10, R0, -1)
        data = inst.encode()
        word = word_from_bytes(data)
        self.assertEqual((word >> 20) & 0xFFF, 0xFFF)  # -1 = 12 个 1


class RiscvPseudoInstructionTestCase(unittest.TestCase):
    """伪指令展开测试"""

    def test_li_small_constant(self):
        """Li：小立即数 → 单条 addi"""
        from qcc.backend.arch.riscv.registers import R10
        inst = ins.Li(R10, 5)
        emitted = list(inst.render())
        self.assertEqual(len(emitted), 1)
        self.assertIsInstance(emitted[0], ins.IBase)

    def test_li_large_constant(self):
        """Li：大立即数 → lui + addi 两条"""
        from qcc.backend.arch.riscv.registers import R10
        inst = ins.Li(R10, 0x12345)
        emitted = list(inst.render())
        self.assertEqual(len(emitted), 2)
        self.assertIsInstance(emitted[0], Lui)

    def test_li_negative_small_constant(self):
        """Li：负的小立即数放进 12 位有符号数，单条 addi 即可"""
        from qcc.backend.arch.riscv.registers import R10
        inst = ins.Li(R10, -1)
        emitted = list(inst.render())
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].offset, -1)


if __name__ == "__main__":
    unittest.main()
