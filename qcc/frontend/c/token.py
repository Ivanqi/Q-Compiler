"""C 词法记号（Token）定义：词法(lexer)阶段的产物、语法(parser)阶段的输入。

CToken 在通用 Token 之上额外记录前置空白与是否为行首记号，
配合 LineInfo 可原样还原源码排版，用于 token 流打印与调试。

C token definitions.
"""

import enum

from qcc.frontend.common import Token
from qcc.frontend.c.utils import LineInfo


class CToken(Token):
    """C 词法记号：额外携带前置空白信息，便于还原源码格式。

    C token (including optional preceeding spaces)
    """

    def __init__(self, typ, val, space, first, loc):
        super().__init__(typ, val, loc)
        self.space = space
        self.first = first
        # self.hideset = set()

    def __repr__(self):
        return (
            f"CToken({self.typ}, {self.val}, {self.first}, "
            + f'"{self.space}", {self.loc})'
        )

    def __str__(self):
        return self.space + self.val

    def copy(self, space=None, first=None):
        """复制当前记号，可顺带替换前置空白与行首标志。

        Return a new token which is a mildly modified copy
        """
        if space is None:
            space = self.space
        if first is None:
            first = self.first
        return CToken(self.typ, self.val, space, first, self.loc)


class TokenType(enum.Enum):
    """部分标点类记号的类型枚举。"""
    OPEN_BRACK = "["
    CLOSE_BRACK = "]"
    DOT = "."
    COMMA = ","
    ARROW = "->"


class CTokenPrinter:
    """把 token 流按原始排版还原成文本（用于预处理输出等）。

    Printer that can turn a stream of token-lines into text
    """

    def dump(self, tokens, file=None):
        """将 token 序列打印到文件，遇 LineInfo 时输出 # 行指示。"""
        first_line = True
        for token in tokens:
            # print(token.typ, token.val, token.first)
            if isinstance(token, LineInfo):
                # print(token, str(token))
                print(str(token), file=file)
                first_line = True
            else:
                if token.first:
                    # Insert newline!
                    if first_line:
                        first_line = False
                    else:
                        print(file=file)
                text = str(token)
                print(text, end="", file=file)
