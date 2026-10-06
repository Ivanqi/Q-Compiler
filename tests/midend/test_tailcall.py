# -*- coding: utf-8 -*-
"""尾调用优化（qcc/midend/opt/tailcall.py）专用测试。

TailCallOptimization 把"函数最后一步是调用自身、调用结果直接返回"
的递归转换成"改写参数 + 跳回入口块"的循环（消除栈增长），
转换后的函数 is_leaf() 为真。
本文件补充 test_opt.py 之外的场景：
- 带参数传递（phi 回写）的自尾递归
- 非尾位置的自调用不得优化
- 调用其他函数（互递归）不得优化
- 无参数自尾递归
"""
import unittest

from qcc.midend import ir, irutils
from qcc.midend.irutils import verify_module
from qcc.midend.opt.tailcall import TailCallOptimization


class TailCallTestCase(unittest.TestCase):
    def setUp(self):
        self.debug_db = None
        self.builder = irutils.Builder()
        self.module = ir.Module("test")
        self.builder.set_module(self.module)
        self.function = self.builder.new_function(
            "fact", ir.Binding.GLOBAL, ir.i32
        )
        self.builder.set_function(self.function)
        entry = self.builder.new_block()
        self.function.entry = entry
        self.builder.set_block(entry)
        self.n = ir.Parameter("n", ir.i32)
        self.acc = ir.Parameter("acc", ir.i32)
        self.function.add_parameter(self.n)
        self.function.add_parameter(self.acc)
        self.opt = TailCallOptimization()

    def tearDown(self):
        verify_module(self.module)

    def test_self_tail_call_with_phi(self):
        """自尾递归：call+return 被替换为 jump + phi 参数回写"""
        one = self.builder.emit(ir.Const(1, "one", ir.i32))
        zero = self.builder.emit(ir.Const(0, "zero", ir.i32))
        n1 = self.builder.emit(ir.Binop(self.n, "-", one, "n1", ir.i32))
        acc1 = self.builder.emit(ir.Binop(self.acc, "*", self.n, "acc1", ir.i32))
        result = self.builder.emit(
            ir.FunctionCall(self.function, [n1, acc1], "rv", ir.i32)
        )
        self.builder.emit(ir.Return(result))

        self.assertFalse(self.function.is_leaf())
        self.opt.run(self.module)

        # 转换后：无递归调用（is_leaf），入口块出现 phi，返回被跳转替代
        self.assertTrue(self.function.is_leaf())
        instrs = [i for b in self.function.blocks for i in b]
        self.assertFalse(any(isinstance(i, ir.FunctionCall) for i in instrs))
        self.assertTrue(any(isinstance(i, ir.Phi) for i in instrs))
        self.assertTrue(any(isinstance(i, ir.Jump) for i in instrs))
        # 参数 n 与 acc 被 phi 回写（phi 顺序不保证，聚合检查全部输入）
        phis = [i for i in instrs if isinstance(i, ir.Phi)]
        all_inputs = [v for p in phis for v in p.inputs.values()]
        self.assertIn(self.n, all_inputs)
        self.assertIn(self.acc, all_inputs)
        self.assertEqual(len(phis), 2)

    def test_non_tail_call_not_optimized(self):
        """非尾位置的自调用（结果还要参与运算）不得优化"""
        one = self.builder.emit(ir.Const(1, "one", ir.i32))
        n1 = self.builder.emit(ir.Binop(self.n, "-", one, "n1", ir.i32))
        call = self.builder.emit(
            ir.FunctionCall(self.function, [n1, self.acc], "rv", ir.i32)
        )
        two = self.builder.emit(ir.Const(2, "two", ir.i32))
        result = self.builder.emit(ir.Binop(call, "*", two, "result", ir.i32))
        self.builder.emit(ir.Return(result))

        self.opt.run(self.module)

        # 调用保留，仍非叶子
        self.assertFalse(self.function.is_leaf())
        instrs = [i for b in self.function.blocks for i in b]
        self.assertTrue(any(isinstance(i, ir.FunctionCall) for i in instrs))

    def test_call_to_other_function_not_optimized(self):
        """调用别的函数（互递归）不得优化"""
        other = self.builder.new_function("other", ir.Binding.GLOBAL, ir.i32)
        p = ir.Parameter("p", ir.i32)
        other.add_parameter(p)
        # other 也要有合法的块体（verify 检查所有函数）
        other_entry = ir.Block("other_entry")
        other.add_block(other_entry)
        other.entry = other_entry
        other_entry.add_instruction(ir.Return(p))
        one = self.builder.emit(ir.Const(1, "one", ir.i32))
        n1 = self.builder.emit(ir.Binop(self.n, "-", one, "n1", ir.i32))
        call = self.builder.emit(
            ir.FunctionCall(other, [n1], "rv", ir.i32)
        )
        self.builder.emit(ir.Return(call))

        self.opt.run(self.module)

        self.assertFalse(self.function.is_leaf())

    def test_tail_call_without_arguments(self):
        """无参数自尾递归：直接跳回入口，无 phi。

        独立构造 module/function（setUp 的带参函数不适合本用例，
        先给 setUp 函数补一个合法返回，否则 tearDown 校验会发现空块）。
        """
        self.builder.emit(ir.Return(self.n))
        builder = irutils.Builder()
        module = ir.Module("test2")
        builder.set_module(module)
        function = builder.new_function("loop", ir.Binding.GLOBAL, ir.i32)
        builder.set_function(function)
        entry = builder.new_block()
        function.entry = entry
        builder.set_block(entry)
        call = builder.emit(ir.FunctionCall(function, [], "rv", ir.i32))
        builder.emit(ir.Return(call))

        self.assertFalse(function.is_leaf())
        self.opt.run(module)

        self.assertTrue(function.is_leaf())
        instrs = [i for b in function.blocks for i in b]
        self.assertFalse(any(isinstance(i, ir.FunctionCall) for i in instrs))
        self.assertFalse(any(isinstance(i, ir.Phi) for i in instrs))
        verify_module(module)


if __name__ == "__main__":
    unittest.main()
