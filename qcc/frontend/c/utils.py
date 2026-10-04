"""C 前端通用工具：AST 打印、行信息、数字与转义字符解析、对齐填充计算。

被预处理(LineInfo)、词法(cnum/charval 等)、语法与上下文(context 的
required_padding)等阶段共享的基础小工具集合。

Generic utilities for the C frontend.
"""

import re

from qcc.frontend.c.nodes.visitor import Visitor


def required_padding(address, alignment):
    """计算把 address 对齐到 alignment 需要填充的字节数。

    Return how many padding bytes are needed to align address
    """
    rest = address % alignment
    if rest:
        # We need padding bytes:
        return alignment - rest
    return 0


def print_ast(ast, file=None):
    """以缩进树形方式打印抽象语法树。

    Display an abstract syntax tree.
    """
    CAstPrinter(file=file).print(ast)


class CAstPrinter(Visitor):
    """遍历 AST 并按层次缩进打印各结点（调试用）。

    Print AST of a C program
    """

    def __init__(self, file=None):
        self.indent = 0
        self.file = file

    def print(self, node):
        """打印入口：从给定结点开始访问。"""
        self.visit(node)

    def _print(self, node):
        print("    " * self.indent + str(node), file=self.file)

    def visit(self, node):
        self._print(node)
        self.indent += 1
        super().visit(node)
        self.indent -= 1


class LineInfo:
    """行信息：标记后续内容来自哪个文件与行号（对应预处理输出的 # 行指示）。

    Line information indicating where the following content comes from.

    Flags can be given.
    1: start of new file
    2: returning to a file after an include
    3: the following comes from a system header file
    4: The following should be wrapped inside extern "C" implicitly
    """

    FLAG_START_OF_NEW_FILE = 1
    FLAG_RETURN_FROM_INCLUDE = 2

    def __init__(self, line, filename, flags=()):
        self.line = line
        self.filename = filename
        self.flags = flags

    def __str__(self):
        if self.flags:
            flags = " " + " ".join(map(str, self.flags))
        else:
            flags = ""
        return f'# {self.line} "{self.filename}"{flags}'


def cnum(txt: str):
    """解析 C 整数字面量：确定进制并剥离 u/l 后缀，返回数值与类型说明符。

    Convert C number to integer
    """
    assert isinstance(txt, str)

    # Lower tha casing:
    num = txt.lower()

    # Determine base:
    if num.startswith("0x"):
        num = num[2:]
        base = 16
    elif num.startswith("0b"):
        num = num[2:]
        base = 2
    elif num.startswith("0"):
        base = 8
    else:
        base = 10

    # Determine suffix:
    type_specifiers = []
    while num.endswith(("l", "u")):
        if num.endswith("u"):
            num = num[:-1]
            type_specifiers.append("unsigned")
        elif num.endswith("l"):
            num = num[:-1]
            type_specifiers.append("long")
        else:
            raise NotImplementedError()

    # Take the integer:
    return int(num, base), type_specifiers


def float_num(txt: str):
    """解析 C 浮点字面量，返回数值与类型说明符（默认为 double）。

    Parse a C floating point literal.
    """
    assert isinstance(txt, str)

    # Lower tha casing:
    num = txt.lower()

    # Floating point
    type_specifiers = ["double"]
    return float(num), type_specifiers


def replace_escape_codes(txt: str):
    """把文本中的 C 转义序列（八进制/十六进制/\\u/\\U/常用转义）还原为实际字符。

    Replace escape codes inside the given text
    """
    prog = re.compile(
        r"(\\[0-7]{1,3})|(\\x[0-9a-fA-F]+)|"
        r'(\\[\'"?\\abfnrtve])|(\\u[0-9a-fA-F]{4})|(\\U[0-9a-fA-F]{8})'
    )
    pos = 0
    endpos = len(txt)
    parts = []
    while pos != endpos:
        # Find next match:
        mo = prog.search(txt, pos)
        if mo:
            # We have an escape code:
            if mo.start() > pos:
                parts.append(txt[pos : mo.start()])
            # print(mo.groups())
            octal, hx, ch, uni1, uni2 = mo.groups()
            if octal:
                char = chr(int(octal[1:], 8))
            elif hx:
                char = chr(int(hx[2:], 16))
            elif ch:
                mp = {
                    "a": "\a",
                    "b": "\b",
                    "f": "\f",
                    "n": "\n",
                    "r": "\r",
                    "t": "\t",
                    "v": "\v",
                    "e": "\x1b",  # Non-standard escape character
                    "\\": "\\",
                    '"': '"',
                    "'": "'",
                    "?": "?",
                }
                char = mp[ch[1:]]
            elif uni1:
                char = chr(int(uni1[2:], 16))
            elif uni2:
                char = chr(int(uni2[2:], 16))
            else:  # pragma: no cover
                raise RuntimeError()
            parts.append(char)
            pos = mo.end()
        else:
            # No escape code found:
            parts.append(txt[pos:])
            pos = endpos
    return "".join(parts)


def charval(txt: str):
    """解析字符字面量，返回其码点值与类型说明符。

    Get the character value of a char literal
    """
    # Wide char?
    if txt.startswith("L"):
        txt = txt[1:]

    # Strip out ' and '
    assert txt[0] == "'"
    assert txt[-1] == "'"
    txt = txt[1:-1]
    assert len(txt) == 1
    # TODO: implement wide characters!
    return ord(txt), ["char"]
