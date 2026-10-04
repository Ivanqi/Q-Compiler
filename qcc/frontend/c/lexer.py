"""C 词法器：把 C 源码的字符流切分成 Token 序列（预处理器最底层的依赖）。

在 C 前端流水线（预处理 → 词法 → 语法(parser.py) → 语义(semantics.py) → IR 生成(codegenerator.py)）
中负责词法环节：preprocessor.py 里 CPreProcessor.process_file 调用本模块的 CLexer，
逐行把源文件扫描成带位置/空白信息的 CToken，交由预处理器做宏展开与 # 指令处理，
最终经 prepare_for_parsing 适配后送给 parser。

C Language lexer
"""

import io
import logging

from qcc.frontend.tools.handlexer import HandLexerBase
from qcc.frontend.c.token import CToken


class SourceFile:
    """表示正在处理的源文件：记录文件名与当前行号（__LINE__ 等宏会用到）。

    Presents the current file.
    """

    def __init__(self, name):
        self.filename = name
        self.row = 1

    def __repr__(self):
        return f"<SourceFile at {self.filename}:{self.row}>"


# 三字符组（trigraph）映射：如 "??=" 等价于 "#"，供 trigraph_filter 使用。
tri_map = {
    "=": "#",
    "(": "[",
    ")": "]",
    "<": "{",
    ">": "}",
    "-": "~",
    "!": "|",
    "/": "\\",
    "'": "^",
}


def trigraph_filter(chunks):
    """把 chunk 序列中的三字符组（trigraph）替换成对应的单字符（C89 特性）。

    Replace trigraphs in a chunk sequence
    """

    for row, column, text in chunks:
        if len(text) > 2:
            j = i = 0

            while i < len(text) - 2:
                if (
                    text[i] == "?"
                    and text[i + 1] == "?"
                    and text[i + 2] in tri_map
                ):
                    char = tri_map[text[i + 2]]
                    if j < i:
                        yield (row, column + j, text[j:i])
                    yield (row, column + i, char)
                    j = i = i + 3
                else:
                    i += 1
            if j < len(text):
                yield (row, column + j, text[j:])
        else:
            yield (row, column, text)


def continued_lines_filter(chunks):
    r"""把以反斜杠（\）结尾的续行与下一行拼接成同一逻辑行。

    Glue lines which end with a backslash '\'
    """
    backslash = None
    for row, column, text in chunks:
        if backslash:
            # Assume here that text does not start with a newline.
            if text in "\r\n":
                row, column, text = backslash
                if len(text) > 1:
                    yield row, column, text[:-1]
            else:
                yield backslash
                yield row, column, text
            backslash = False
        else:
            if text.endswith("\\"):
                backslash = row, column, text
            elif text.endswith(("\\\r", "\\\n")):
                yield row, column, text[:-2]
            else:
                yield row, column, text

    if backslash:
        yield backslash


def lex_text(text, coptions):
    """把一段文本（字符串）词法分析成 Token 列表，供预处理器处理宏体等片段。

    Lex a piece of text
    """
    lexer = CLexer(coptions)
    return list(lexer.lex_text(text))


class CLexer(HandLexerBase):
    """C 词法分析器：被 CPreProcessor 使用，把源文件逐字符扫描成 CToken 序列。

    Lexer used for the preprocessor
    """

    logger = logging.getLogger("clexer")
    lower_letters = "abcdefghijklmnopqrstuvwxyz"
    upper_letters = lower_letters.upper()
    binary_numbers = "01"
    octal_numbers = binary_numbers + "234567"
    numbers = octal_numbers + "89"
    hex_numbers = numbers + "abcdefABCDEF"

    def __init__(self, coptions):
        super().__init__()
        self.coptions = coptions

    def lex(self, src, source_file):
        """读取源文件对象并生成 Token 序列（先做 trigraph 与续行过滤，再分词）。

        Read a source and generate a series of tokens
        """
        self.logger.debug("Lexing %s", source_file.filename)

        self._source_file = source_file
        self._filename = source_file.filename
        chunks = self.create_chunks(src)
        if self.coptions["trigraphs"]:
            chunks = trigraph_filter(chunks)
        chunks = continued_lines_filter(chunks)
        # print('=== lex ')
        # print(s)
        # print('=== end lex ')

        # s = '\n'.join(r)
        return self.tokenize(source_file.filename, chunks)

    def lex_text(self, txt):
        """把给定字符串包装成 StringIO 后调用 lex，直接得到 Token 序列。

        Create tokens from the given text
        """
        f = io.StringIO(txt)
        filename = None
        source_file = SourceFile(filename)
        return self.lex(f, source_file)

    def tokenize(self, filename, chunks):
        """由字符 chunk 生成 CToken：合并空白、标记行首（BOL）、并在末尾补发换行。

        Generate tokens from characters
        """
        space = ""
        first = True
        token = None
        for token in super().tokenize(filename, chunks, self.lex_c):
            if token.typ == "BOL":
                if first:
                    # Yield an extra start of line
                    yield CToken("BOL", "", "", first, token.loc)
                first = True
                space = ""
            elif token.typ == "WS":
                space += token.val
            else:
                yield CToken(token.typ, token.val, space, first, token.loc)
                space = ""
                first = False

        # Emit last newline:
        if first and token:
            # Yield an extra start of line
            yield CToken("BOL", "", "", first, token.loc)

    def create_chunks(self, f):
        """把文件逐行切成 (行号, 列号, 文本) 的 chunk 序列，行号随读取递增（制表符展开）。

        Create a sequence of chunks.

        Each chunk is a tuple of (row, column, text)
        """
        for line in f:
            # TODO: expand tabs brakes the column info..
            line = line.expandtabs()
            yield (self._source_file.row, 1, line)
            self._source_file.row += 1

    def lex_c(self):
        """词法状态机主入口：按当前字符分派到对应的 lex_xxx 子过程。

        Root parsing function
        """
        char = self.next_char()

        if char is None:
            pass
        elif char == "L":
            # Wide char or identifier
            if self.accept("'"):
                return self.lex_char
            else:
                return self.lex_identifier
        elif char in self.lower_letters + self.upper_letters + "_":
            return self.lex_identifier
        elif char in self.numbers:
            self.backup_char(char)
            return self.lex_number
        elif char in " \t":
            return self.lex_whitespace
        elif char in "\n":
            self.emit("BOL")
            return self.lex_c
        elif char == "\f":
            # Skip form feed ^L chr(0xc) character
            self.ignore()
            return self.lex_c
        elif char == "/":
            if self.accept("/"):
                if self.coptions["std"] == "c89":
                    self.error("C++ style comments are not allowed in C90")
                return self.lex_linecomment
            elif self.accept("*"):
                return self.lex_blockcomment
            elif self.accept("="):
                self.emit("/=")
                return self.lex_c
            else:
                self.emit("/")
                return self.lex_c
        elif char == '"':
            return self.lex_string
        elif char == "'":
            return self.lex_char
        elif char == "<":
            if self.accept("="):
                self.emit("<=")
            elif self.accept("<"):
                if self.accept("="):
                    self.emit("<<=")
                else:
                    self.emit("<<")
            else:
                self.emit("<")
            return self.lex_c
        elif char == ">":
            if self.accept("="):
                self.emit(">=")
            elif self.accept(">"):
                if self.accept("="):
                    self.emit(">>=")
                else:
                    self.emit(">>")
            else:
                self.emit(">")
            return self.lex_c
        elif char == "=":
            if self.accept("="):
                self.emit("==")
            else:
                self.emit("=")
            return self.lex_c
        elif char == "!":
            if self.accept("="):
                self.emit("!=")
            else:
                self.emit("!")
            return self.lex_c
        elif char == "|":
            if self.accept("|"):
                self.emit("||")
            elif self.accept("="):
                self.emit("|=")
            else:
                self.emit("|")
            return self.lex_c
        elif char == "&":
            if self.accept("&"):
                self.emit("&&")
            elif self.accept("="):
                self.emit("&=")
            else:
                self.emit("&")
            return self.lex_c
        elif char == "#":
            if self.accept("#"):
                self.emit("##")
            else:
                self.emit("#")
            return self.lex_c
        elif char == "+":
            if self.accept("+"):
                self.emit("++")
            elif self.accept("="):
                self.emit("+=")
            else:
                self.emit("+")
            return self.lex_c
        elif char == "-":
            if self.accept("-"):
                self.emit("--")
            elif self.accept("="):
                self.emit("-=")
            elif self.accept(">"):
                self.emit("->")
            else:
                self.emit("-")
            return self.lex_c
        elif char == "*":
            if self.accept("="):
                self.emit("*=")
            else:
                self.emit("*")
            return self.lex_c
        elif char == "%":
            if self.accept("="):
                self.emit("%=")
            else:
                self.emit("%")
            return self.lex_c
        elif char == "^":
            if self.accept("="):
                self.emit("^=")
            else:
                self.emit("^")
            return self.lex_c
        elif char == "~":
            if self.accept("="):
                self.emit("~=")
            else:
                self.emit("~")
            return self.lex_c
        elif char == ".":
            if self.accept(self.numbers):
                # We got .[0-9]
                return self.lex_float
            elif self.accept("."):
                if self.accept("."):
                    self.emit("...")
                else:
                    self.error("Expected . or ...")
            else:
                self.emit(".")
            return self.lex_c
        elif char in ";{}()[],?:":
            self.emit(char)
            return self.lex_c
        elif char == "\\":
            self.emit(char)
            return self.lex_c
        else:  # pragma: no cover
            self.error(f"Invalid character: {char}")

    def lex_identifier(self):
        """扫描标识符/关键字名（含 L 前缀宽字符），产出 ID Token。"""
        id_chars = self.lower_letters + self.upper_letters + self.numbers + "_"
        self.accept_run(id_chars)
        self.emit("ID")
        return self.lex_c

    def lex_number(self):
        """扫描一个整数字面量（识别十进制/八进制/十六进制/二进制及 L、U 后缀）。

        Lex a single numeric literal.
        """
        if self.accept("0"):
            # Octal, binary or hex!
            if self.accept("xX"):
                number_chars = self.hex_numbers
                base = 16
            elif self.accept("bB"):
                number_chars = self.binary_numbers
                base = 2
            elif self.accept("."):
                return self.lex_float()
            else:
                number_chars = self.octal_numbers
                base = 8
        else:
            number_chars = self.numbers
            base = 10

        # Accept a series of number characters:
        self.accept_run(number_chars)

        if self.peek() == "+":
            text = self.get_lexeme()
            if text[-1] in "eE":
                self.error("invalid suffix on integer constant")

        if base == 10 and self.accept("."):
            # For example 12.3
            return self.lex_float()
        elif base == 10 and self.accept("eEpP"):
            # For example 12e7
            return self.lex_float()
        else:
            # Accept some integer suffixes, such as 'L', or 'ull'
            long_suffixes = 0
            unsigned_suffixes = 0
            while long_suffixes < 2 or unsigned_suffixes < 1:
                if long_suffixes < 2 and self.accept("lL"):
                    long_suffixes += 1
                elif unsigned_suffixes < 1 and self.accept("uU"):
                    unsigned_suffixes += 1
                else:
                    break

            self.emit("NUMBER")
            return self.lex_c

    def lex_float(self):
        """从小数点之后（或指数部分）继续扫描浮点数字面量，产出 FLOAT Token。

        Lex floating point number from decimal dot onwards.
        """
        self.accept_run(self.numbers)
        if self.accept("eEpP"):
            self.accept("+-")
            self.accept_run(self.numbers)

        self.emit("FLOAT")
        return self.lex_c

    def lex_whitespace(self):
        """扫描连续的空格/制表符，产出 WS Token（空白量信息后续宏展开会用到）。"""
        self.accept_run(" \t")
        self.emit("WS")
        return self.lex_c

    def lex_linecomment(self):
        """扫描 "//" 行注释直到行尾，注释内容直接丢弃。"""
        c = self.next_char()
        while c and c != "\n":
            c = self.next_char()
        self.backup_char(c)
        self.ignore()
        return self.lex_c

    def lex_blockcomment(self):
        """扫描 "/* ... */" 块注释，注释内容直接丢弃。"""
        while True:
            if self.accept("*"):
                if self.accept("/"):
                    self.ignore()
                    # self.emit('WS')
                    break
            else:
                self.next_char(eof=False)
        return self.lex_c

    def lex_string(self):
        """扫描完整的字符串字面量（含转义序列），产出 STRING Token。

        Scan for a complete string
        """
        c = self.next_char(eof=False)
        while c != '"':
            if c == "\\":
                self._handle_escape_character()
            c = self.next_char(eof=False)
        self.emit("STRING")
        return self.lex_c

    def lex_char(self):
        """扫描字符常量（含转义序列），产出 CHAR Token。

        Scan for a complete character constant
        """
        if self.accept("\\"):
            self._handle_escape_character()
        else:
            # Normal char:
            self.next_char(eof=False)

        self.expect("'")

        self.emit("CHAR")
        return self.lex_c

    def _handle_escape_character(self):
        """处理字面量中的转义序列（简单转义、八进制、\\x、\\u、\\U）。"""
        # Escape char!
        if self.accept("'\"?\\abfnrtve"):
            pass
        elif self.accept(self.octal_numbers):
            self.accept(self.octal_numbers)
            self.accept(self.octal_numbers)
        elif self.accept("x"):
            self.accept(self.hex_numbers)
            self.accept(self.hex_numbers)
        elif self.accept("u"):
            self.accept(self.hex_numbers)
            self.accept(self.hex_numbers)
            self.accept(self.hex_numbers)
            self.accept(self.hex_numbers)
        elif self.accept("U"):
            self.accept(self.hex_numbers)
            self.accept(self.hex_numbers)
            self.accept(self.hex_numbers)
            self.accept(self.hex_numbers)
        else:
            self.error("Unexpected escape character")
