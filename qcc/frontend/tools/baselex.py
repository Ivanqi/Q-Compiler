"""正则驱动的词法器基础设施：Lexer 基类与词法规则注册机制。

本模块位于 C 前端流水线的词法阶段（预处理→词法→语法）：
把源码文本切分为带位置信息的 Token 流，供 parser.py 等语法分析器消费。
其中 SimpleLexer 用装饰器注册正则规则，BaseLexer 用 tok_spec 列表构造大正则。

Base infrastructure for regex-based lexers and token rule registration.
"""

import re

from qcc.common import CompilerError
from qcc.frontend.common import SourceLocation, Token

EOF = "EOF"
EPS = "EPS"


def on(pattern, flags=0, order=0):
    """装饰器：把正则规则绑到处理方法上，order 越小优先级越高。

    Register method to the given pattern.

    Args:
        order: a sorting priority. Lower number comes first.
    """
    prog = re.compile(pattern, flags=flags)

    def wrapper(f):
        setattr(f, "$lex", (prog, order))
        return f

    return wrapper


class LexMeta(type):
    """元类：扫描被 on 装饰的方法，按 order 排序生成 lexmap 规则表。

    Meta class which inspects the functions decorated with 'on'"""

    def __new__(cls, name, bases, attrs):
        lexmap = []
        for n, value in attrs.items():
            if n.startswith("__"):
                continue
            if hasattr(value, "$lex"):
                prog, order = getattr(value, "$lex")
                lexmap.append((prog, order, value))
        lexmap.sort(key=lambda x: x[1])
        attrs["lexmap"] = lexmap
        return type.__new__(cls, name, bases, attrs)


class Lexer:
    """词法器基类：仅作类型标识，具体实现见 SimpleLexer / BaseLexer。"""

    pass


class SimpleLexer(Lexer, metaclass=LexMeta):
    """简易词法器：通过子类化并用 on 装饰处理方法来定义词法规则。

    Simple class for lexing.

    Use this class by subclassing it and decorating handler methods
    with the 'on' function.
    """

    def gettok(self):
        """从当前位置起依次尝试各条正则规则，返回匹配到的 Token。

        Find a match at the given position"""
        for prog, _, func in self.lexmap:
            mo = prog.match(self.txt, self.pos)
            if mo:
                column = mo.start() - self.line_start
                length = mo.end() - mo.start()
                loc = SourceLocation(self.filename, self.line, column, length)
                self.pos = mo.end()
                val = mo.group(0)

                # Update row and column information:
                if "\n" in val:
                    self.line += val.count("\n")
                    # TODO: this is wrong, and must be improved:
                    self.line_start = mo.start()

                # print(func, '"%s"' % val)

                res = func(self, val)
                if res:
                    typ, val = res
                    return Token(typ, val, loc)
                else:
                    return

        # No match found!
        char = self.txt[self.pos]
        column = self.pos - self.line_start
        loc = SourceLocation(self.filename, self.line, column, 1)
        message = f"Unexpected char: {char} (0x{ord(char):X})"
        raise CompilerError(message, loc=loc)

    def tokenize(self, txt, eof=False):
        """生成器：把文本逐段切分为 Token，可选在末尾产出 EOF。

        Generator that generates lexical tokens from text.

        Optionally yield the EOF token.
        """
        self.line = 1
        self.line_start = 0
        self.pos = 0
        self.txt = txt
        while len(txt) != self.pos:
            tok = self.gettok()
            if tok:
                yield tok

        # Emit 'eof' (end of file) if requested
        if eof:
            loc = SourceLocation(self.filename, self.line, 0, 0)
            yield Token(EOF, EOF, loc)


class BaseLexer(Lexer):
    """词法器基类：由 tok_spec 列表合并生成大正则，并负责源码位置跟踪。

    Base class for a lexer.

    This class can be overridden to create a
    lexer. This class handles the regular expression generation and
    source position accounting.
    """

    def __init__(self, tok_spec):
        tok_re = "|".join(f"(?P<{p[0]}>{p[1]})" for p in tok_spec)
        self.gettok = re.compile(tok_re).match
        self.func_map = {pair[0]: pair[2] for pair in tok_spec}
        self.filename = None
        self.line = 1
        self.line_start = 0
        self.pos = 0

    def feed(self, txt):
        """喂入输入文本，并重置内部的 Token 生成器。

        Feeds the lexer with extra input"""
        self.tokens = self.tokenize(txt)

    def tokenize(self, txt, eof=False):
        """生成器：用合并后的正则扫描文本产出 Token，最后可产出 EOF。

        Generator that generates lexical tokens from text.

        Optionally yield the EOF token.
        """
        self.line = 1
        self.line_start = 0
        self.pos = 0
        self.txt = txt
        mo = self.gettok(txt)
        while mo:
            typ = mo.lastgroup
            val = mo.group(typ)
            column = mo.start() - self.line_start
            length = mo.end() - mo.start()
            loc = SourceLocation(self.filename, self.line, column, length)
            func = self.func_map[typ]
            new_pos = mo.end()
            if func:
                res = func(typ, val)
                if res:
                    typ, val = res
                    yield Token(typ, val, loc)
            self.pos = new_pos
            mo = self.gettok(txt, self.pos)
        if len(txt) != self.pos:
            char = txt[self.pos]
            column = self.pos - self.line_start
            loc = SourceLocation(self.filename, self.line, column, 1)
            message = f"Unexpected char: {char} (0x{ord(char):X})"
            raise CompilerError(message, loc=loc)
        if eof:
            loc = SourceLocation(self.filename, self.line, 0, 0)
            yield Token(EOF, EOF, loc)

    def newline(self):
        """进入新的一行：更新行号与行首位置。

        Enters a new line"""
        self.line_start = self.pos
        self.line = self.line + 1

    def next_token(self):
        """取下一个 Token；迭代耗尽时返回 EOF Token。"""
        try:
            return next(self.tokens)
        except StopIteration:
            loc = SourceLocation(self.filename, self.line, 0, 0)
            return Token(EOF, EOF, loc)
