"""宏定义/展开的辅助：定义 C 预处理器使用的宏对象模型。

在 C 前端流水线（预处理 → 词法 → 语法(parser.py) → 语义(semantics.py) → IR 生成(codegenerator.py)）
中为预处理环节服务：preprocessor.py 把 #define 的结果记录为本模块的 Macro/FunctionMacro，
宏展开时再据此做参数替换、字符串化(#)与拼接(##)。

Preprocessor macros.
"""


class BaseMacro:
    """宏基类：保存宏名与 protected 标记（protected 的宏不允许被重新定义）。

    Base macro
    """

    def __init__(self, name, protected=False):
        self.name = name
        self.protected = protected


class Macro(BaseMacro):
    """普通 #define 宏：保存替换用的 Token 序列、形参列表与可变参数名。

    Macro define
    """

    def __init__(self, name, value, args=None, protected=False, variadic=None):
        super().__init__(name, protected=protected)
        self.value = value
        self.args = args
        self.variadic = variadic


class FunctionMacro(BaseMacro):
    """特殊宏（如 __FILE__、__LINE__）：展开时调用注册的 Python 回调函数动态求值。

    Special macro, like __FILE__
    """

    def __init__(self, name, function):
        super().__init__(name, protected=True)
        self.function = function
