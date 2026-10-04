"""公共子表达式消除（CommonSubexpressionEliminationPass）测试。

对应 src/qcc/midend/opt/cse.py（需求4 列出的迁移模块之一）。
ppci 原测试套件未直接覆盖本 pass，这里补充。
"""
import unittest

from qcc.backend.binutils.debuginfo import DebugDb
from qcc.midend import ir, irutils
from qcc.midend.irutils import verify_module
from qcc.midend.opt.cse import CommonSubexpressionEliminationPass


class CseTestCase(unittest.TestCase):
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

    def tearDown(self):
        verify_module(self.module)

    def test_replace_duplicate_binop(self):
        """重复的二元运算：第二次应被替换为第一次的结果"""
        a = self.builder.emit(ir.Const(3, "a", ir.i32))
        b = self.builder.emit(ir.Const(7, "b", ir.i32))
        x = self.builder.emit(ir.add(a, b, "x", ir.i32))
        y = self.builder.emit(ir.add(a, b, "y", ir.i32))
        self.builder.emit(ir.Exit())

        CommonSubexpressionEliminationPass().run(self.module)

        # y 的所有使用者都应换成 x
        self.assertFalse(y.is_used)
        self.assertEqual(list(x.used_by), [])

    def test_replace_duplicate_const(self):
        """重复的常量：第二次应被替换"""
        c1 = self.builder.emit(ir.Const(42, "c1", ir.i32))
        c2 = self.builder.emit(ir.Const(42, "c2", ir.i32))
        s = self.builder.emit(ir.add(c1, c2, "s", ir.i32))
        self.builder.emit(ir.Exit())

        CommonSubexpressionEliminationPass().run(self.module)

        self.assertFalse(c2.is_used)
        self.assertEqual(s.a, c1)
        self.assertEqual(s.b, c1)

    def test_no_replace_different_ops(self):
        """不同运算不得被消除"""
        a = self.builder.emit(ir.Const(3, "a", ir.i32))
        b = self.builder.emit(ir.Const(7, "b", ir.i32))
        x = self.builder.emit(ir.add(a, b, "x", ir.i32))
        y = self.builder.emit(ir.mul(a, b, "y", ir.i32))
        self.builder.emit(ir.Exit())

        CommonSubexpressionEliminationPass().run(self.module)

        self.assertIn(x, self.function.entry.instructions)
        self.assertIn(y, self.function.entry.instructions)


if __name__ == "__main__":
    unittest.main()
