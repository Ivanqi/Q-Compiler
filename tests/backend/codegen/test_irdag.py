# -*- coding: utf-8 -*-
"""选择 DAG 构建（qcc/backend/codegen/irdag.py）专用测试。

SelectionGraphBuilder 把 IR 函数翻译成"选择 DAG"（SelectionGraph）：
- 每个 IR 值对应一个 SGValue（function_info.value_map 可查，带虚拟寄存器）；
- 运算/访存/常量/标签各自成为 DAG 节点（ADDI32/LDRI32/CONSTI32/LABEL…）；
- 按基本块分组，function_info.block_roots 记录每块的 DAG 根。
DagSplitter 随后把 DAG 切成树给 BURG 匹配（见 test_burg / test_burm）。

本文件在 test_codegen.py 的两个回归用例之外，补充结构性断言。
"""
import unittest

from qcc.backend.arch.example import ExampleArch
from qcc.backend.binutils.debuginfo import DebugDb
from qcc.backend.codegen.dagsplit import DagSplitter
from qcc.backend.codegen.irdag import (
    FunctionInfo,
    SelectionGraphBuilder,
    prepare_function_info,
)
from qcc.midend import ir
from qcc.midend.irutils import Builder, verify_module


class IrDagTestCase(unittest.TestCase):
    """公共脚手架：IR 函数 → 选择 DAG"""

    def setUp(self):
        self.arch = ExampleArch()
        self.debug_db = DebugDb()

    def build_dag(self, build_fn, is_procedure=False):
        """构造 IR 函数并翻译为选择 DAG，返回
        (module, function, function_info, sgraph)。

        is_procedure=True 时构造无返回值的过程（块以 Exit 结尾）。
        """
        builder = Builder()
        module = ir.Module("dut", debug_db=self.debug_db)
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

        frame = self.arch.new_frame("a", function)
        function_info = FunctionInfo(frame)
        prepare_function_info(self.arch, function_info, function)
        dag_builder = SelectionGraphBuilder(self.arch)
        dag_builder.build(function, function_info, self.debug_db)
        return module, function, function_info, dag_builder.sgraph

    def test_value_map_covers_all_values(self):
        """每个 IR 值在 DAG 中都有对应的 SGValue"""
        def build(builder, function):
            a = ir.Parameter("a", ir.i32)
            function.add_parameter(a)
            c = builder.emit(ir.Const(3, "c", ir.i32))
            builder.emit(ir.add(a, c, "s", ir.i32))
            zero = builder.emit(ir.Const(0, "zero", ir.i32))
            builder.emit(ir.Return(zero))

        _, function, function_info, _ = self.build_dag(build)
        for ins in [i for b in function.blocks for i in b]:
            if isinstance(ins, ir.Value):
                self.assertIn(ins, function_info.value_map,
                              f"{ins} 缺少 DAG 值映射")

    def test_binop_creates_dag_node(self):
        """加法生成 ADDI32 节点"""
        def build(builder, function):
            a = ir.Parameter("a", ir.i32)
            function.add_parameter(a)
            c = builder.emit(ir.Const(3, "c", ir.i32))
            builder.emit(ir.add(a, c, "s", ir.i32))
            builder.emit(ir.Exit())

        _, function, function_info, sgraph = self.build_dag(build, is_procedure=True)
        ops = [node.name.op for node in sgraph.nodes]
        self.assertIn("ADD", ops)
        self.assertIn("CONST", ops)

    def test_each_block_has_label(self):
        """每个基本块都在 label_map 中登记了 DAG 标签"""
        def build(builder, function):
            a = ir.Parameter("a", ir.i32)
            function.add_parameter(a)
            block2 = builder.new_block()
            builder.emit(ir.Jump(block2))
            builder.set_block(block2)
            builder.emit(ir.Exit())

        _, function, function_info, _ = self.build_dag(build, is_procedure=True)
        for block in function.blocks:
            self.assertIn(block, function_info.label_map)

    def test_global_variable_becomes_label(self):
        """全局变量在 DAG 中是 LABEL 节点"""
        def build(builder, function):
            var = ir.Variable("g", ir.Binding.GLOBAL, 4, 4)
            function.module.add_variable(var)
            builder.emit(ir.Load(var, "ld", ir.i32))
            builder.emit(ir.Exit())

        _, _, _, sgraph = self.build_dag(build, is_procedure=True)
        ops = [node.name.op for node in sgraph.nodes]
        self.assertIn("LABEL", ops)

    def test_phi_gets_virtual_register(self):
        """分支合并处的 phi 有虚拟寄存器（SGValue.vreg）"""
        def build(builder, function):
            a = ir.Parameter("a", ir.i32)
            function.add_parameter(a)
            b1 = builder.new_block()
            b2 = builder.new_block()
            join = builder.new_block()
            cond = builder.emit(ir.Const(0, "cond", ir.i32))
            builder.emit(ir.CJump(cond, "==", cond, b1, b2))
            builder.set_block(b1)
            c1 = builder.emit(ir.Const(1, "c1", ir.i32))
            builder.emit(ir.Jump(join))
            builder.set_block(b2)
            c2 = builder.emit(ir.Const(2, "c2", ir.i32))
            builder.emit(ir.Jump(join))
            builder.set_block(join)
            phi = builder.emit(ir.Phi("p", ir.i32))
            phi.set_incoming(b1, c1)
            phi.set_incoming(b2, c2)
            builder.emit(ir.Exit())

        _, function, function_info, _ = self.build_dag(
            build, is_procedure=True
        )
        phis = [
            i for b in function.blocks for i in b
            if isinstance(i, ir.Phi)
        ]
        self.assertEqual(len(phis), 1)
        # phi → 虚拟寄存器 的映射登记在 phi_map 中
        vreg = function_info.phi_map[phis[0]]
        self.assertIsNotNone(vreg)

    def test_dag_split_into_trees(self):
        """DagSplitter 能把 DAG 切成树森林（BURG 的输入）"""
        def build(builder, function):
            a = ir.Parameter("a", ir.i32)
            function.add_parameter(a)
            c = builder.emit(ir.Const(3, "c", ir.i32))
            s = builder.emit(ir.add(a, c, "s", ir.i32))
            builder.emit(ir.add(s, c, "s2", ir.i32))
            builder.emit(ir.Exit())

        _, function, function_info, sgraph = self.build_dag(
            build, is_procedure=True
        )
        splitter = DagSplitter(self.arch)
        trees = splitter.split_into_trees(
            sgraph, function, function_info, self.debug_db
        )
        self.assertGreaterEqual(len(trees), 1)


if __name__ == "__main__":
    unittest.main()
