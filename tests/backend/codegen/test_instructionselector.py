# -*- coding: utf-8 -*-
"""指令选择器（qcc/backend/codegen/instructionselector.py）专用测试。

InstructionSelector1 的完整流程：
    IR 函数 → SelectionGraphBuilder 建 DAG → DagSplitter 切成树森林
    → BURG 树匹配（架构的 @isa.pattern 规则）→ 发射抽象机器指令
    → 填入 frame.instructions（带虚拟寄存器）。

用 riscv 后端（规则齐全、编码直观）直接走 select()，
断言发射的指令序列（按助记符前缀）与结果确定性。
"""
import unittest

from qcc.backend.arch.riscv import RiscvArch
from qcc.backend.binutils.debuginfo import DebugDb
from qcc.backend.codegen.instructionselector import InstructionSelector1
from qcc.backend.codegen.irdag import SelectionGraphBuilder
from qcc.midend import ir
from qcc.midend.irutils import Builder, verify_module
from qcc.utils.reporting import DummyReportGenerator


class InstructionSelectorTestCase(unittest.TestCase):
    def setUp(self):
        self.arch = RiscvArch()
        self.sgraph_builder = SelectionGraphBuilder(self.arch)
        self.selector = InstructionSelector1(
            self.arch, self.sgraph_builder, DummyReportGenerator()
        )

    def select(self, build_fn, is_procedure=False):
        """构造 IR 函数并执行指令选择，返回 (function, frame)。

        is_procedure=True 时构造无返回值的过程（块以 Exit 结尾）。
        """
        debug_db = DebugDb()
        builder = Builder()
        module = ir.Module("dut", debug_db=debug_db)
        builder.set_module(module)
        if is_procedure:
            function = builder.new_procedure("tst", ir.Binding.GLOBAL)
        else:
            function = builder.new_function("tst", ir.Binding.GLOBAL, ir.i32)
        builder.set_function(function)
        entry = builder.new_block()
        function.entry = entry
        builder.set_block(entry)
        build_fn(builder, function)
        verify_module(module)

        frame = self.arch.new_frame("tst", function)
        frame.debug_db = debug_db
        self.selector.select(function, frame)
        return function, frame

    def mnemonics(self, frame):
        return [str(i).split(" ", 1)[0] for i in frame.instructions]

    def test_add_selected(self):
        """a + b 被选为 addi/add 类指令"""
        def build(builder, function):
            a = ir.Parameter("a", ir.i32)
            b = ir.Parameter("b", ir.i32)
            function.add_parameter(a)
            function.add_parameter(b)
            s = builder.emit(ir.add(a, b, "s", ir.i32))
            builder.emit(ir.Return(s))

        _, frame = self.select(build)
        mn = self.mnemonics(frame)
        self.assertTrue(any(m.startswith("add") for m in mn), mn)

    def test_mul_selected(self):
        """乘法被选为 mul 类指令"""
        def build(builder, function):
            a = ir.Parameter("a", ir.i32)
            function.add_parameter(a)
            c = builder.emit(ir.Const(3, "c", ir.i32))
            m = builder.emit(ir.mul(a, c, "m", ir.i32))
            builder.emit(ir.Return(m))

        _, frame = self.select(build)
        mn = self.mnemonics(frame)
        self.assertTrue(any(m.startswith("mul") for m in mn), mn)

    def test_condition_generates_branch_and_jump(self):
        """条件跳转：发射条件分支指令 + 无条件跳转（两段式）"""
        def build(builder, function):
            a = ir.Parameter("a", ir.i32)
            function.add_parameter(a)
            b1 = builder.new_block()
            b2 = builder.new_block()
            c = builder.emit(ir.Const(0, "c", ir.i32))
            builder.emit(ir.CJump(a, ">", c, b1, b2))
            builder.set_block(b1)
            builder.emit(ir.Exit())
            builder.set_block(b2)
            builder.emit(ir.Exit())

        _, frame = self.select(build, is_procedure=True)
        mn = self.mnemonics(frame)
        self.assertTrue(any(m == "bgt" for m in mn), mn)
        self.assertTrue(any(m == "j" for m in mn), mn)

    def test_function_call_selected(self):
        """函数调用被选为 jalr/jal 类指令"""
        def build(builder, function):
            other = builder.new_function(
                "other", ir.Binding.GLOBAL, ir.i32
            )
            p = ir.Parameter("p", ir.i32)
            other.add_parameter(p)
            other_entry = ir.Block("other_entry")
            other.add_block(other_entry)
            other.entry = other_entry
            other_entry.add_instruction(ir.Return(p))
            a = ir.Parameter("a", ir.i32)
            function.add_parameter(a)
            rv = builder.emit(
                ir.FunctionCall(other, [a], "rv", ir.i32)
            )
            builder.emit(ir.Return(rv))

        _, frame = self.select(build)
        mn = self.mnemonics(frame)
        self.assertTrue(
            any(m in ("jalr", "jal") for m in mn), mn
        )

    def test_global_variable_load_selected(self):
        """全局变量读：发射带重定位的 lw（符号地址由链接器回填）"""
        def build(builder, function):
            var = ir.Variable("g", ir.Binding.GLOBAL, 4, 4)
            function.module.add_variable(var)
            ld = builder.emit(ir.Load(var, "ld", ir.i32))
            builder.emit(ir.Return(ld))

        _, frame = self.select(build)
        mn = self.mnemonics(frame)
        self.assertTrue(any(m == "lw" for m in mn), mn)

    def test_selection_is_deterministic(self):
        """同一函数重复选择结果一致（无残留状态）"""
        def build(builder, function):
            a = ir.Parameter("a", ir.i32)
            function.add_parameter(a)
            s = builder.emit(ir.add(a, a, "s", ir.i32))
            builder.emit(ir.Return(s))

        _, frame1 = self.select(build)
        _, frame2 = self.select(build)
        self.assertEqual(
            [str(i) for i in frame1.instructions],
            [str(i) for i in frame2.instructions],
        )


if __name__ == "__main__":
    unittest.main()
