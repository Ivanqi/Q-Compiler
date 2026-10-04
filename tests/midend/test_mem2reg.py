"""Mem2Reg（SSA 构造）补充测试。

对应 src/qcc/midend/opt/mem2reg.py（需求4 列出的迁移模块之一）。
test_opt.py 已覆盖基本提升场景，这里补充「地址逃逸则不可提升」
的反例，以及跨基本块（phi 插入）场景。
"""
import unittest

from qcc.backend.binutils.debuginfo import DebugDb
from qcc.midend import ir, irutils
from qcc.midend.irutils import verify_module
from qcc.midend.opt.mem2reg import Mem2RegPromotor


class Mem2RegTestCase(unittest.TestCase):
    def setUp(self):
        self.debug_db = DebugDb()
        self.builder = irutils.Builder()
        self.module = ir.Module("test", debug_db=self.debug_db)
        self.builder.set_module(self.module)
        self.function = self.builder.new_procedure(
            "testfunction", ir.Binding.GLOBAL
        )
        self.builder.set_function(self.function)
        entry = self.builder.new_block()
        self.function.entry = entry
        self.builder.set_block(entry)
        self.mem2reg = Mem2RegPromotor()

    def tearDown(self):
        verify_module(self.module)

    def test_escape_prevents_promotion(self):
        """地址被当作值存到别处（逃逸）时不得提升"""
        alloc = self.builder.emit(ir.Alloc("A", 4, 4))
        addr = self.builder.emit(ir.AddressOf(alloc, "addr"))
        # 第二个变量，用于承接逃逸的指针
        alloc2 = self.builder.emit(ir.Alloc("B", 8, 8))
        addr2 = self.builder.emit(ir.AddressOf(alloc2, "addr2"))
        cnst = self.builder.emit(ir.Const(1, "cnst", ir.i32))
        self.builder.emit(ir.Store(cnst, addr))
        # 地址逃逸：把指针本身存进另一块内存
        self.builder.emit(ir.Store(addr, addr2))
        self.builder.emit(ir.Load(addr, "Ld", ir.i32))
        self.builder.emit(ir.Exit())

        self.mem2reg.run(self.module)

        # 未提升：alloc / addr / load 仍在
        self.assertIn(alloc, self.function.entry.instructions)
        self.assertIn(addr, self.function.entry.instructions)

    def test_promotion_across_blocks(self):
        """跨基本块提升：合并点插入 phi 节点"""
        # entry: store 到 a，然后按条件分叉
        alloc = self.builder.emit(ir.Alloc("A", 4, 4))
        addr = self.builder.emit(ir.AddressOf(alloc, "addr"))
        c1 = self.builder.emit(ir.Const(1, "c1", ir.i32))
        self.builder.emit(ir.Store(c1, addr))
        block1 = self.builder.new_block()
        block2 = self.builder.new_block()
        join = self.builder.new_block()
        cond = self.builder.emit(ir.Const(0, "cond", ir.i32))
        self.builder.emit(ir.CJump(cond, "==", cond, block1, block2))
        self.builder.set_block(block1)
        c2 = self.builder.emit(ir.Const(2, "c2", ir.i32))
        self.builder.emit(ir.Store(c2, addr))
        self.builder.emit(ir.Jump(join))
        self.builder.set_block(block2)
        self.builder.emit(ir.Jump(join))
        self.builder.set_block(join)
        load = self.builder.emit(ir.Load(addr, "Ld", ir.i32))
        s = self.builder.emit(ir.add(load, c1, "s", ir.i32))
        self.builder.emit(ir.Exit())

        self.mem2reg.run(self.module)

        # 提升后：alloc 与 load 应消失，join 块出现 phi，
        # load 的使用者直接使用该 phi
        self.assertNotIn(alloc, self.function.entry.instructions)
        self.assertFalse(load.is_used)
        phi_instrs = [
            i for i in join.instructions if isinstance(i, ir.Phi)
        ]
        self.assertEqual(len(phi_instrs), 1)
        phi = phi_instrs[0]
        self.assertEqual(set(phi.inputs.keys()), {block1, block2})
        self.assertIs(phi.inputs[block1], c2)
        self.assertIs(phi.inputs[block2], c1)
        self.assertIs(s.a, phi)
        self.assertIs(s.b, c1)


if __name__ == "__main__":
    unittest.main()
