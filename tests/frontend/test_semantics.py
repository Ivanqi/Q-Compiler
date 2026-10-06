# -*- coding: utf-8 -*-
"""语义分析（qcc/frontend/c/semantics.py）专用测试。

parser 每识别一个语法结构就回调 CSemantics.on_xxx：
- 类型计算/类型检查（表达式结果类型、赋值兼容性）
- 隐式转换插入（整型提升、数组退化为指针、Cast 节点）
- 合法性检查（未定义变量、重复定义、lvalue、case 值等）

这里直接用 CBuilder 解析源码，对"带类型的 AST"做断言
（test_c.py 侧重编译成败，本文件侧重 AST 上的类型/转换/错误细节）。
"""
import io
import unittest

from qcc.backend.arch.example import ExampleArch
from qcc.common import CompilerError
from qcc.frontend.c import CBuilder, COptions
from qcc.frontend.c.nodes import declarations, expressions, statements, types
from qcc.frontend.c.nodes.visitor import Visitor


class CollectVisitor(Visitor):
    """收集 AST 中指定类型的节点"""

    def __init__(self, cls):
        self.cls = cls
        self.found = []

    def visit(self, node):
        if isinstance(node, self.cls):
            self.found.append(node)
        super().visit(node)


def collect(node, cls):
    v = CollectVisitor(cls)
    v.visit(node)
    return v.found


class CSemanticsTestCase(unittest.TestCase):
    def setUp(self):
        arch = ExampleArch()
        self.builder = CBuilder(arch.info, COptions())

    def parse(self, src):
        """解析源码，返回 (编译单元 AST, 完整 ir.Module 无碍)。"""
        cu = self.builder._create_ast(src, None)
        self.assertIsNotNone(cu)
        return cu

    def expect_error(self, src, message):
        f = io.StringIO(src)
        with self.assertRaises(CompilerError) as cm:
            self.builder.build(f, None)
        self.assertRegex(cm.exception.msg, message)

    # ---- 类型计算 ----

    def test_binop_type_int(self):
        """整型运算结果类型为 i32"""
        cu = self.parse("int x = 1 + 2;")
        binops = collect(cu, expressions.BinaryOperator)
        self.assertEqual(len(binops), 1)
        self.assertEqual(binops[0].typ.type_id, types.BasicType.INT)

    def test_char_promoted_to_int(self):
        """char 参与算术时整型提升为 int"""
        cu = self.parse("int x = 'a' + 1;")
        binops = collect(cu, expressions.BinaryOperator)
        self.assertEqual(binops[0].typ.type_id, types.BasicType.INT)

    def test_float_expression_type(self):
        """浮点表达式类型为 double"""
        cu = self.parse("double d = 1.0 + 2.5;")
        binops = collect(cu, expressions.BinaryOperator)
        self.assertEqual(binops[0].typ.type_id, types.BasicType.DOUBLE)

    def test_compare_type(self):
        """比较运算结果为整型（本前端无独立 bool 类型，bool 即 int）"""
        cu = self.parse("int b = (3 > 2);")
        binops = collect(cu, expressions.BinaryOperator)
        self.assertEqual(binops[0].typ.type_id, types.BasicType.INT)

    def test_pointer_arithmetic(self):
        """指针 + 整数 → 指针类型"""
        cu = self.parse("int a[4]; int *p = a + 2;")
        binops = collect(cu, expressions.BinaryOperator)
        self.assertEqual(len(binops), 1)
        self.assertIsInstance(binops[0].typ, types.PointerType)

    def test_array_decays_to_pointer(self):
        """数组名退化为指针（初始化指针时，经 ImplicitCast 包裹）"""
        cu = self.parse("int a[4]; int *p = a;")
        inits = [d.initial_value for d in cu.declarations]
        # 数组→指针退化被包在 ImplicitCast 中（lvalue 是 bool 标志位）：
        # 内层 expr 是数组类型，cast 的结果类型（typ/to_typ）是指针
        cast = inits[1]
        self.assertIsInstance(cast, expressions.ImplicitCast)
        self.assertTrue(cast.is_array_decay)
        self.assertIsInstance(cast.expr.typ, types.ArrayType)
        self.assertIsInstance(cast.typ, types.PointerType)

    # ---- 隐式转换 ----

    def test_implicit_cast_inserted(self):
        """int 赋给 char 时插入 Cast 节点"""
        cu = self.parse("char c = 300;")
        casts = collect(cu, expressions.Cast)
        self.assertEqual(len(casts), 1)
        # Cast 节点上 typ/to_typ 是 BasicType 实例（与 BinaryOperator 的
        # typ 存字符串不同——ppci AST 的历史遗留差异）
        self.assertEqual(casts[0].typ.type_id, types.BasicType.CHAR)
        self.assertEqual(casts[0].to_typ.type_id, types.BasicType.CHAR)

    def test_int_to_float_cast(self):
        """int 参与浮点运算时插入 int→double 转换"""
        cu = self.parse("double d = 1 + 2.5;")
        casts = collect(cu, expressions.Cast)
        self.assertEqual(len(casts), 1)
        self.assertEqual(casts[0].to_typ.type_id, types.BasicType.DOUBLE)

    # ---- 错误检查 ----

    def test_undefined_variable(self):
        self.expect_error("int x = nope;", "nope")

    def test_duplicate_variable(self):
        # 注：全局重复声明是合法 C（试探性定义），须在函数内测试
        self.expect_error("int f(void) { int x; int x; return 0; }",
                          "redefinition")

    def test_duplicate_function(self):
        self.expect_error(
            "int f(void) { return 0; }\nint f(void) { return 1; }",
            "redefin",
        )

    def test_assign_to_non_lvalue(self):
        self.expect_error("void f(void) { 3 = 4; }", "lvalue")

    def test_wrong_argument_count(self):
        self.expect_error(
            "int f(int a) { return a; }\nint g(void) { return f(); }",
            "argument",
        )

    def test_return_without_value(self):
        self.expect_error("int f(void) { return; }", "Must return a value")

    def test_duplicate_case_value(self):
        self.expect_error(
            """
            void f(int a) {
              switch (a) {
                case 3: break;
                case 3: break;
              }
            }
            """,
            "Duplicate case",
        )

    def test_return_value_in_void_function(self):
        self.expect_error("void f(void) { return 1; }", "Cannot return")

    # ---- 合法构造的语义结果 ----

    def test_struct_member_access_type(self):
        """结构体成员访问的类型正确"""
        cu = self.parse(
            "struct P { int x; int y; };\n"
            "struct P p;\n"
            "int a = p.x + p.y;"
        )
        fields = collect(cu, expressions.FieldSelect)
        self.assertEqual(len(fields), 2)
        for f in fields:
            self.assertEqual(f.typ.type_id, types.BasicType.INT)

    def test_enum_value(self):
        """枚举常量被语义分析求值为整型常量（A=5, B=6 → 11）"""
        import io as _io
        from qcc.midend import ir as _ir
        ir_module = self.builder.build(
            _io.StringIO("enum E { A = 5, B };\nint x = A + B;"), None
        )
        # 全局变量的初始化器在编译期求值：x 的初值被打包为
        # 小端字节 (b'\x0b\x00\x00\x00',) = 11
        import struct as _struct
        x_var = ir_module.variables[0]
        value = _struct.unpack("<I", x_var.value[0])[0]
        self.assertEqual(value, 11)

    def test_initializer_type_checked(self):
        """初始化器表达式类型与声明类型一致"""
        cu = self.parse("int a[3] = {1, 2, 3};")
        decl = cu.declarations[0]
        self.assertIsInstance(decl.typ, types.ArrayType)
        # 数组维度在 AST 中是常量表达式节点（编译期求值）
        self.assertIsInstance(
            decl.typ.size, expressions.NumericLiteral
        )
        self.assertEqual(decl.typ.size.value, 3)


if __name__ == "__main__":
    unittest.main()
