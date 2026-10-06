# -*- coding: utf-8 -*-
"""IR 代码生成（qcc/frontend/c/codegenerator.py）专用测试。

CCodeGenerator 遍历带类型的 AST 生成 midend.ir 的 IR（未优化形态：
局部变量是 alloc+store/load）。本文件直接断言 IR 结构：
- 局部变量 → Alloc/AddressOf/Store
- 二元运算 → Binop；常量表达式编译期求值
- 控制流 → CJump/Jump/基本块
- 函数调用 → FunctionCall；全局变量 → module.variables
"""
import io
import unittest

from qcc.backend.arch.example import ExampleArch
from qcc.midend import ir
from qcc.midend.irutils import verify_module
from qcc.frontend.c import CBuilder, COptions


class CCodeGeneratorTestCase(unittest.TestCase):
    def setUp(self):
        arch = ExampleArch()
        self.builder = CBuilder(arch.info, COptions())

    def build(self, src):
        ir_module = self.builder.build(io.StringIO(src), None)
        self.assertIsInstance(ir_module, ir.Module)
        verify_module(ir_module)
        return ir_module

    def function(self, ir_module, name):
        for f in ir_module.functions:
            if f.name == name:
                return f
        raise AssertionError(f"function {name} not found")

    def instructions(self, fn):
        return [i for b in fn.blocks for i in b]

    def test_local_variable_becomes_alloc(self):
        """局部变量在未优化 IR 中是栈上 alloc + store"""
        ir_module = self.build("int f(void) { int x = 42; return x; }")
        fn = self.function(ir_module, "f")
        instrs = self.instructions(fn)
        self.assertTrue(any(isinstance(i, ir.Alloc) for i in instrs))
        self.assertTrue(any(isinstance(i, ir.Store) for i in instrs))
        self.assertTrue(any(isinstance(i, ir.Load) for i in instrs))
        self.assertTrue(any(isinstance(i, ir.Return) for i in instrs))

    def test_binop_generated(self):
        ir_module = self.build("int f(int a, int b) { return a + b; }")
        fn = self.function(ir_module, "f")
        instrs = self.instructions(fn)
        binops = [i for i in instrs if isinstance(i, ir.Binop)]
        self.assertEqual(len(binops), 1)
        self.assertEqual(binops[0].operation, "+")

    def test_if_creates_cjump(self):
        """if/else 生成 CJump 与多个基本块"""
        ir_module = self.build(
            "int f(int a) { if (a > 3) { return 1; } else { return 2; } }"
        )
        fn = self.function(ir_module, "f")
        instrs = self.instructions(fn)
        cjumps = [i for i in instrs if isinstance(i, ir.CJump)]
        self.assertEqual(len(cjumps), 1)
        self.assertEqual(cjumps[0].cond, ">")
        self.assertGreaterEqual(len(fn.blocks), 3)

    def test_while_loop_structure(self):
        """while 循环：CJump 回边 + 出口"""
        ir_module = self.build(
            "int f(int n) { int s = 0; while (n > 0) { s = s + n; n = n - 1; }"
            " return s; }"
        )
        fn = self.function(ir_module, "f")
        instrs = self.instructions(fn)
        self.assertGreaterEqual(
            sum(1 for i in instrs if isinstance(i, ir.CJump)), 1
        )
        self.assertGreaterEqual(
            sum(1 for i in instrs if isinstance(i, ir.Jump)), 1
        )

    def test_function_call(self):
        ir_module = self.build(
            "int g(int a) { return a * 2; }\n"
            "int f(int a) { return g(a) + 1; }"
        )
        fn = self.function(ir_module, "f")
        instrs = self.instructions(fn)
        calls = [i for i in instrs if isinstance(i, ir.FunctionCall)]
        self.assertEqual(len(calls), 1)

    def test_global_variable(self):
        ir_module = self.build("int counter = 7;\nint f(void) { return counter; }")
        self.assertEqual(len(ir_module.variables), 1)
        self.assertEqual(ir_module.variables[0].name, "counter")
        # 全局变量地址作为 Load 的地址使用
        fn = self.function(ir_module, "f")
        instrs = self.instructions(fn)
        loads = [i for i in instrs if isinstance(i, ir.Load)]
        self.assertEqual(len(loads), 1)
        self.assertIs(loads[0].address, ir_module.variables[0])

    def test_string_literal_in_data(self):
        ir_module = self.build('void f(char *p) {}\nint g(void) { f("hi"); }')
        # 字符串字面量编译为 ir.LiteralData 指令（后端在字面量区放标签+数据）
        fn = self.function(ir_module, "g")
        instrs = self.instructions(fn)
        self.assertTrue(
            any(isinstance(i, ir.LiteralData) for i in instrs),
            "字符串字面量应生成 LiteralData 指令",
        )

    def test_compile_time_constant_expression(self):
        """case 值等常量表达式在编译期求值（不产生运行时指令）"""
        ir_module = self.build(
            "int f(int a) { switch (a) { case 2 + 3: return 1; } return 0; }"
        )
        fn = self.function(ir_module, "f")
        instrs = self.instructions(fn)
        # 常量 2+3 应被折叠为单个 Const(5)
        consts = [i for i in instrs if isinstance(i, ir.Const)]
        self.assertIn(5, [c.value for c in consts])

    def test_pointer_deref_and_address_of(self):
        ir_module = self.build(
            "int f(int *p) { int x = 5; p = &x; return *p; }"
        )
        fn = self.function(ir_module, "f")
        instrs = self.instructions(fn)
        self.assertTrue(any(isinstance(i, ir.AddressOf) for i in instrs))
        self.assertTrue(any(isinstance(i, ir.Load) for i in instrs))

    def test_recursive_function(self):
        ir_module = self.build(
            "int fib(int n) { if (n < 2) { return n; }"
            " return fib(n - 1) + fib(n - 2); }"
        )
        fn = self.function(ir_module, "fib")
        calls = [i for i in self.instructions(fn)
                 if isinstance(i, ir.FunctionCall)]
        self.assertEqual(len(calls), 2)  # 两个递归调用


if __name__ == "__main__":
    unittest.main()
