"""手写词法器框架：基于“状态函数 + 子生成器”实现逐字符扫描。

处于 C 前端流水线的词法阶段（预处理→词法→语法）：
把源码切分为 (行, 列, 文本) 块序列，作为游标供状态函数消费，
产出 Token 流交给 parser.py 做语法分析；C 词法器即基于此基类实现。

Hand written lexer.

The idea of lexers is to split the sourcecode into chunks.

Chunks consists of a tuple (row, column, text)

A source file can be split up into a sequence of these chunks.

A cursor is a pointer to a specific character in the chunk sequence.

"""

from qcc.common import CompilerError
from qcc.frontend.common import SourceLocation, Token


class HandLexerBase:
    """手写词法器基类：以状态机方式驱动扫描，负责缓冲、回退与位置跟踪。

    Base class for handwritten lexers based on an idea of Rob Pike.

    See also:
    http://eli.thegreenplace.net/2012/08/09/
    using-sub-generators-for-lexical-scanning-in-python/

    And:
    https://www.youtube.com/watch?v=HxaD_trXwRE
    """

    def __init__(self):
        self.token_buffer = []  # emitted tokens
        self.current_text = []
        self._start_loc = None
        self._chunk = None
        self._chunk_index = 0
        self._chunk_start = 0

    def tokenize(self, filename, chunks, start_state):
        """从起始状态出发不断调用状态函数，产出 Token 序列。

        Return a sequence of tokens"""
        self._filename = filename
        self._chunk_iter = iter(chunks)
        self._next_chunk()
        self._mark_start()
        state = start_state
        while state:
            while self.token_buffer:
                yield self.token_buffer.pop(0)
            state = state()

    def next_char(self, eof=True):
        """读取下一个字符；eof=False 时遇到文件结尾直接报错。

        Retrieve next character.

        If eof is False, raise an error when end of file is encountered.
        """
        char = self._get_char()

        if not eof and char is None:
            self.error("Expected a character, but at end of file")

        return char

    def backup_char(self, char):
        """回退一个字符，使游标重新指向刚读过的位置。

        go back one item"""
        if char:
            assert self._chunk_index > 0
            self._chunk_index -= 1

    def get_chunk(self):
        """取出下一块文本，需由子类实现。

        Retrieve the next chunk of text

        This function must be implemented by subclasses.

        Must yield tuples of: (row, column, text)

        """
        return next(self._chunk_iter, None)

    def _get_char(self):
        if self._chunk:
            if self._chunk_index < len(self._chunk[2]):
                c = self._chunk[2][self._chunk_index]
                self._chunk_index += 1
            else:
                self._next_chunk()
                c = self._get_char()
        else:
            c = None
        return c

    def get_location(self):
        """返回游标当前的源码位置。

        Return current location."""
        if self._chunk:
            row = self._chunk[0]
            column = self._chunk[1] + self._chunk_index
            return SourceLocation(self._filename, row, column, 1)

    def _next_chunk(self):
        """进入下一块文本，并保存当前块的剩余内容。

        Enter next text chunk."""
        if self._chunk:
            text = self._chunk[2][self._chunk_start :]
            self.current_text.append(text)
        self._chunk = self.get_chunk()
        self._chunk_index = 0
        self._chunk_start = 0

    def _mark_start(self):
        """记录当前 Token 的起始位置，并清空文本缓冲。

        Store location, and reset text buffer."""
        loc = self.get_location()
        if loc is not None:
            self._start_loc = loc
        self.current_text.clear()
        self._chunk_start = self._chunk_index

    def peek(self) -> str:
        """预读下一个字符（不移动游标）。

        Take a peek at the next upcoming character"""
        c = self.next_char()
        self.backup_char(c)
        return c

    def get_lexeme(self) -> str:
        """取出游标下当前 Token 对应的完整文本。

        Get the lexeme currently under the cursor."""
        parts = []
        parts.extend(self.current_text)
        # Check if we have some text in this chunk:
        if self._chunk_index > self._chunk_start:
            text = self._chunk[2][self._chunk_start : self._chunk_index]
            parts.append(text)
        return "".join(parts)

    def emit(self, typ):
        """把当前 lexeme 作为 typ 类型的 Token 放入输出缓冲。

        Emit the current text under scope as a token"""
        val = self.get_lexeme()
        token = self.make_token(typ, val)
        self.token_buffer.append(token)
        self._mark_start()

    def make_token(self, typ, val):
        location = self._start_loc
        assert location
        location.length = len(val)
        return Token(typ, val, location)

    def ignore(self):
        """忽略游标下的文本（如空白与注释）。

        Ignore text under cursor"""
        self._mark_start()

    def accept(self, valid):
        """若下一个字符属于 valid 集合则消费并返回 True，否则回退。

        Accept a single character if it is in the valid set"""
        char = self.next_char()
        if char and char in valid:
            return True
        else:
            self.backup_char(char)
            return False

    def accept_run(self, valid):
        """连续消费所有属于 valid 集合的字符。"""
        while self.accept(valid):
            pass

    def error(self, message):
        """在当前游标位置抛出编译错误。"""
        location = self.get_location()
        raise CompilerError(message, location)

    def expect(self, valid):
        """要求下一个字符属于 valid 集合，否则报错。"""
        if not self.accept(valid):
            self.error("Expected {}".format(", ".join(valid)))
