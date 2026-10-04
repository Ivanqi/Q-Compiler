"""把 IR 模块以文本形式写出（人类可读的 IR 打印格式）。

功能说明：
    提供 print_module / Writer，把 ir.Module 打印成"module/函数/基本块/指令"
    的文本格式（可选先 verify 校验）；与 reader.py 的解析格式互相对应，
    常用于调试输出与测试。属于中端 IR 的辅助工具。

Writing IR-code into a textual format."""

from qcc.midend import ir
from qcc.midend.irutils.verify import verify_module

IR_FORMAT_INDENT = 2


def print_module(module, file=None, verify=True):
    """把 IR 模块打印为文本（默认写到 stdout，可先校验）。

    Print an ir-module as text.

    Args:
        module (:class:`ir.Module`): The module to turn into textual format.
        file: An optional file like object to write to. Defaults to stdout.
        verify (bool): A boolean indicating whether or not the module should
                       be verified before writing.
    """
    Writer(file=file).write(module, verify=verify)


class Writer:
    """把 IR 代码按缩进格式写到文件对象（可自定义额外缩进）。

    Write ir-code to file"""

    def __init__(self, file=None, extra_indent=""):
        self.extra_indent = extra_indent
        self.file = file

    def _print(self, level, txt):
        indent = self.extra_indent + " " * (level * IR_FORMAT_INDENT)
        print(indent + txt, file=self.file)

    def write(self, module: ir.Module, verify=True):
        """Write ir-code to file f"""
        assert isinstance(module, ir.Module)
        if verify:
            verify_module(module)
        self._print(0, f"{module};")

        for external in module.externals:
            self._print(0, "")
            self._print(0, f"{external};")

        for variable in module.variables:
            self._print(0, "")
            self._print(0, str(variable))

        for function in module.functions:
            self._print(0, "")
            self.write_function(function)

    def write_function(self, function):
        self._print(0, f"{function} {{")
        for block in function.blocks:
            self._print(1, f"{block} {{")
            for ins in block:
                self._print(2, f"{ins};")
            self._print(1, "}")
            self._print(0, "")
        self._print(0, "}")
