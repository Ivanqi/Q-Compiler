"""常量表达式求值器：在编译期计算 C 常量表达式的值。

供 CContext（sizeof、枚举值、数组长度、结构体布局等）调用，
是语义分析与 IR 生成阶段处理常量、初始化器尺寸的前提工具。

Constant expression evaluation.
"""

from qcc.frontend.c.nodes import declarations, expressions, types


class ConstantExpressionEvaluator:
    """常量表达式求值器：对 AST 表达式做编译期求值（整数/浮点）。

    Class which is capable of evaluating expressions.
    """

    def __init__(self, context):
        self.context = context

    def eval_expr(self, expr):
        """在编译期立即对表达式求值，按表达式种类分派到具体求值方法。

        Evaluate an expression right now! (=at compile time)
        """
        if isinstance(expr, expressions.BinaryOperator):
            value = self.eval_binop(expr)
        elif isinstance(expr, expressions.UnaryOperator):
            value = self.eval_unop(expr)
        elif isinstance(expr, expressions.VariableAccess):
            value = self.eval_variable_access(expr)
        elif isinstance(expr, expressions.NumericLiteral):
            value = expr.value
        elif isinstance(expr, expressions.CharLiteral):
            value = expr.value
        elif isinstance(expr, expressions.StringLiteral):
            value = self.eval_string_literal(expr)
        elif isinstance(expr, expressions.CompoundLiteral):
            value = self.eval_compound_literal(expr)
        elif isinstance(expr, expressions.Cast):
            value = self.eval_cast(expr)
        elif isinstance(expr, expressions.Sizeof):
            if isinstance(expr.sizeof_typ, types.CType):
                value = self.context.sizeof(expr.sizeof_typ)
            else:
                value = self.context.sizeof(expr.sizeof_typ.typ)
        elif isinstance(expr, int):
            value = expr
        else:  # pragma: no cover
            raise NotImplementedError(str(expr))
        return value

    def eval_variable_access(self, expr):
        """求值对变量/枚举常量的引用。

        Evaluate variable access.
        """
        declaration = expr.variable.declaration
        if isinstance(declaration, declarations.EnumConstantDeclaration):
            value = self.eval_enum(declaration)
        elif isinstance(
            declaration,
            (
                declarations.VariableDeclaration,
                declarations.FunctionDeclaration,
            ),
        ):
            value = self.eval_global_access(declaration)
        else:
            raise NotImplementedError(str(expr.variable))
        return value

    def eval_enum(self, declaration):
        """通过上下文查得枚举常量的值。

        Evaluate enum value.
        """
        value = self.context.get_enum_value(declaration.typ, declaration)
        return value

    def eval_global_access(self, declaration):
        raise NotImplementedError()

    def eval_string_literal(self, expr):
        raise NotImplementedError()

    def eval_compound_literal(self, expr):
        raise NotImplementedError()

    def eval_cast(self, expr):
        """求值强制类型转换（按目标类型转成整数或浮点）。

        Evaluate cast expression.
        """
        value = self.eval_expr(expr.expr)

        # do some real casting:
        if expr.typ.is_integer:
            value = int(value)
        elif expr.typ.is_float or expr.typ.is_double:
            value = float(value)
        else:
            pass
        return value

    def eval_unop(self, expr):
        """求值一元运算（取负、按位取反、取地址）。

        Evaluate unary operation.
        """
        if expr.op in ["-", "~"]:
            a = self.eval_expr(expr.a)
            op_map = {
                "-": lambda x: -x,
                "~": lambda x: ~x,
            }
            value = op_map[expr.op](a)
        elif expr.op == "&":
            value = self.eval_take_address(expr.a)
        else:  # pragma: no cover
            raise NotImplementedError(str(expr))
        return value

    def eval_take_address(self, expr):
        raise NotImplementedError("take address operator: &")

    def eval_binop(self, expr):
        """求值二元运算符（整数除法与移位按整数语义处理）。

        Evaluate binary operator.
        """
        lhs = self.eval_expr(expr.a)
        rhs = self.eval_expr(expr.b)
        op = expr.op

        op_map = {
            "+": lambda x, y: x + y,
            "-": lambda x, y: x - y,
            "*": lambda x, y: x * y,
        }

        # Ensure division is integer division:
        if expr.typ.is_integer:
            op_map["/"] = lambda x, y: x // y
            op_map[">>"] = lambda x, y: x >> y
            op_map["<<"] = lambda x, y: x << y
            op_map["|"] = lambda x, y: x | y
            op_map["&"] = lambda x, y: x & y
            op_map["^"] = lambda x, y: x ^ y
        else:
            op_map["/"] = lambda x, y: x / y

        value = op_map[op](lhs, rhs)
        return value
