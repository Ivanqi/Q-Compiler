"""优化 pass 基础框架与 transform 系列 pass 测试。

对应 src/qcc/midend/opt/transform.py（需求4 列出的迁移模块之一）。
覆盖：ModulePass/FunctionPass/BlockPass/InstructionPass 四个基类，
以及 DeleteUnusedInstructionsPass（死代码消除）和
RemoveAddZeroPass（去除 +0 / *1 等恒等运算）。
"""
import unittest

from qcc.backend.binutils.debuginfo import DebugDb
from qcc.midend import ir, irutils
from qcc.midend.irutils import verify_module
from qcc.midend.opt.transform import (
    BlockPass,
    DeleteUnusedInstructionsPass,
    FunctionPass,
    InstructionPass,
    ModulePass,
    RemoveAddZeroPass,
)


class TransformTestCase(unittest.TestCase):
    """公共脚手架：module/function/block 三件套"""

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


class PassBaseTestCase(TransformTestCase):
    """四个 pass 基类的运行结构测试（记录被访问的对象）"""

    def test_module_pass(self):
        """ModulePass：子类实现 run，直接遍历整个模块"""
        self.builder.emit(ir.Exit())
        seen = []

        class MyPass(ModulePass):
            def run(self, ir_module):
                seen.append(("module", ir_module))

        MyPass().run(self.module)
        self.assertEqual(seen, [("module", self.module)])

    def test_function_pass(self):
        """FunctionPass：基类 run 遍历每个函数，子类实现 on_function"""
        self.builder.emit(ir.Exit())
        seen = []

        class MyPass(FunctionPass):
            def on_function(self, function):
                seen.append(("function", function))

        MyPass().run(self.module)
        self.assertEqual(seen, [("function", self.function)])

    def test_block_pass(self):
        seen = []

        class MyPass(BlockPass):
            def on_block(self, block):
                seen.append(("block", block))

        self.builder.emit(ir.Exit())
        MyPass().run(self.module)
        self.assertEqual(seen, [("block", self.function.entry)])

    def test_instruction_pass(self):
        seen = []

        class MyPass(InstructionPass):
            def on_instruction(self, instruction):
                seen.append(instruction)

        self.builder.emit(ir.Exit())
        MyPass().run(self.module)
        self.assertEqual(seen, list(self.function.entry.instructions))


class DeleteUnusedTestCase(TransformTestCase):
    """死代码消除：结果无人使用的指令应被删除"""

    def test_delete_unused_instruction(self):
        dead = self.builder.emit(ir.Const(7, "dead", ir.i32))
        used = self.builder.emit(ir.Const(8, "used", ir.i32))
        self.builder.emit(ir.add(used, used, "s", ir.i32))
        self.builder.emit(ir.Exit())

        DeleteUnusedInstructionsPass().run(self.module)

        self.assertNotIn(dead, self.function.entry.instructions)
        self.assertIn(used, self.function.entry.instructions)

    def test_keep_instruction_with_effect(self):
        """带副作用（非纯值）的指令即使无使用者也不删除"""
        self.builder.emit(ir.Exit())
        DeleteUnusedInstructionsPass().run(self.module)
        # Exit 无 use 但保留（原有指令仍在）
        self.assertEqual(len(self.function.entry.instructions), 1)


class RemoveAddZeroTestCase(TransformTestCase):
    """恒等运算消除：x + 0、x - 0、x * 1、x / 1 等"""

    def test_remove_add_zero(self):
        x = self.builder.emit(ir.Const(5, "x", ir.i32))
        zero = self.builder.emit(ir.Const(0, "zero", ir.i32))
        added = self.builder.emit(ir.add(x, zero, "added", ir.i32))
        self.builder.emit(ir.Exit())

        RemoveAddZeroPass().run(self.module)

        self.assertFalse(added.is_used)
        self.assertIn(x, self.function.entry.instructions)

    def test_remove_mul_one(self):
        x = self.builder.emit(ir.Const(5, "x", ir.i32))
        one = self.builder.emit(ir.Const(1, "one", ir.i32))
        muled = self.builder.emit(ir.mul(x, one, "muled", ir.i32))
        self.builder.emit(ir.Exit())

        RemoveAddZeroPass().run(self.module)

        self.assertFalse(muled.is_used)

    def test_keep_normal_operation(self):
        x = self.builder.emit(ir.Const(5, "x", ir.i32))
        y = self.builder.emit(ir.Const(6, "y", ir.i32))
        s = self.builder.emit(ir.add(x, y, "s", ir.i32))
        self.builder.emit(ir.Exit())

        RemoveAddZeroPass().run(self.module)

        self.assertIn(s, self.function.entry.instructions)


if __name__ == "__main__":
    unittest.main()
