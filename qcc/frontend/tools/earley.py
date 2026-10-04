"""Earley 解析算法实现：一种不依赖预生成分析表的通用语法分析策略。

位于 C 前端流水线的语法分析阶段（预处理→词法→语法(parser.py)）：
直接在运行时使用文法推导，支持任意上下文无关文法，
解析结果可直接回溯出语法树，供后续语义分析与 IR 生成使用。

Implementation of the earley parser strategy.

See also:
- https://en.wikipedia.org/wiki/Earley_parser

And the book:
Parsing Techniques: A Practical Guide (2nd edition)
"""

from qcc.common import ParseError


class Item:
    """Earley 项：带“点”位置的文法规则，表示某条规则已匹配到哪一步。

    Partially parsed grammar rule"""

    def __init__(self, rule, dot, origin):
        self.rule = rule
        self.dot = dot
        self.origin = origin

    @property
    def is_reduce(self):
        return not self.is_shift

    @property
    def is_shift(self):
        return self.dot < len(self.rule.symbols)

    def shifted(self):
        assert self.is_shift
        return Item(self.rule, self.dot + 1, self.origin)

    @property
    def nxt(self):
        return self.rule.symbols[self.dot]

    def __repr__(self):
        symbols = [f"'{symbol}'" for symbol in self.rule.symbols]
        if self.is_shift:
            dot_part1 = " ".join(symbols[: self.dot])
            dot_part2 = " ".join(symbols[self.dot :])
            dot_part = dot_part1 + " . " + dot_part2
        else:
            dot_part = " ".join(symbols) + " ."
        return f"{self.rule.name} -> {dot_part}, {self.origin}"

    def __eq__(self, other):
        return self.__hash__() == other.__hash__()

    def __hash__(self):
        return (
            self.rule.name,
            self.rule.symbols,
            self.rule.priority,
            self.dot,
            self.origin,
        ).__hash__()


class Column:
    """Earley 分析列：对应某个输入 Token 位置上的项集合。

    A set of partially parsed items for a given token position"""

    def __init__(self, i, token):
        self.i = i
        self.token = token
        self.items = set()
        self.item_list = []

    def __iter__(self):
        return iter(self.item_list)

    def __repr__(self):
        return f"Column at {self.token}"

    def add(self, item):
        if item not in self.items:
            self.items.add(item)
            self.item_list.append(item)


def make_tokens(tokens):
    """把 Token 迭代器包装成生成器：先产出占位的 None，遇到 EOF 即停止。"""
    # Start with an non-token column!
    yield None
    token = tokens.next_token()
    while token.typ != "EOF":
        yield token
        token = tokens.next_token()


class EarleyParser:
    """Earley 解析器：用 predict/scan/complete 三个操作增量推进解析。

    Earley parser.

    As opposed to an LR parser, the Earley parser does not construct
    tables from a grammar. It uses the grammar when parsing.

    The Earley parser has 3 key functions:

    - predict: what have we parsed so far, and what productions can be
      made with this.

    - scan: parse the next input symbol, en create a new set of possible
      parsings

    - complete: when we have scanned something according to a rule, this
      rule can be applied.

    When an earley parse is complete, the parse can be back-tracked to
    yield the resulting parse tree or the syntax tree.
    """

    def __init__(self, grammar):
        self.grammar = grammar
        self.states = []

    def predict(self, item, col):
        """预测：把该非终结符的所有产生式以“点在最前”的形式加入当前列。

        Add all rules for a certain non-terminal"""
        nx = item.nxt
        assert self.grammar.is_nonterminal(nx)
        for rule in self.grammar.productions_for_name(nx):
            new_item = Item(rule, 0, col.i)
            col.add(new_item)

    def scan(self, item, col):
        """扫描：若下一个符号与当前 Token 匹配，则把点右移后放入下一列。

        Check if the item can be shifted into the next column"""
        if item.nxt == col.token.typ:
            col.add(item.shifted())

    def complete(self, completed_item, start_col, current_column):
        """完成：某规则归约后，回查起始列中可继续前移的项并推进。

        Complete a rule, check if any other rules can be shifted!"""
        assert completed_item.is_reduce
        worklist = list(start_col)
        while worklist:
            item = worklist.pop(0)
            if item.is_shift and item.nxt == completed_item.rule.name:
                new_item = item.shifted()
                current_column.add(new_item)
                if current_column is start_col:
                    worklist.append(new_item)

    def parse(self, tokens, debug_dump=False):
        """解析给定 Token 序列，成功时返回构造出的语法树。

        Parse the given token string"""

        # Create the state stack:
        columns = [Column(i, tok) for i, tok in enumerate(make_tokens(tokens))]
        for rule in self.grammar.productions_for_name(
            self.grammar.start_symbol
        ):
            columns[0].add(Item(rule, 0, 0))

        # Loop through all input.
        for col in columns:
            # print(col)
            processed_items = set()
            while processed_items != col.items:
                item = iter(col.items - processed_items).__next__()
                if item.is_shift:
                    if self.grammar.is_nonterminal(item.nxt):
                        self.predict(item, col)
                    elif col.i + 1 < len(columns):
                        self.scan(item, columns[col.i + 1])
                else:
                    self.complete(item, columns[item.origin], col)
                processed_items.add(item)

        # Check if the parse was a success:
        last_column = columns[-1]
        for item in last_column:
            if (
                item.is_reduce
                and item.rule.name == self.grammar.start_symbol
                and item.origin == 0
            ):
                break
        else:
            # self.dump_parse(columns)
            raise ParseError("Parsing failed")

        if debug_dump:
            self.dump_parse(columns)

        # Reconstruct the parse tree:
        return self.make_tree(columns, self.grammar.start_symbol)

    def make_tree(self, columns, nt):
        """从解析列回溯构造语法树。

        Make a parse tree"""
        # print('Top tree item:', nt)
        tree, end = self.walk(columns, len(columns) - 1, nt)
        assert end == 0
        return tree

    def walk(self, columns, end, nt):
        """递归回溯列数据，对匹配的产生式调用其语义动作。

        Process the parsed columns back to a parse tree"""
        items = columns[end]
        items = filter(lambda i: i.rule.name == nt and i.is_reduce, items)
        items = sorted(items, key=lambda i: i.rule.priority)
        if not items:
            raise RuntimeError("Unable build tree")  # pragma: no cover

        # We found an item
        item = items[0]
        r = []
        # assert len(item.rule.symbols) > 0
        for x in reversed(item.rule.symbols):
            if self.grammar.is_nonterminal(x):
                x, end = self.walk(columns, end, x)
                r.insert(0, x)
            else:
                r.insert(0, columns[end].token)
                end -= 1

        # Apply semantics, if any!
        res = item.rule.f(*r) if item.rule.f else None
        return res, end

    def dump_parse(self, columns):
        """调试用：打印各列中的项，便于排查解析过程。"""
        print("Parse result:")
        for col in columns:
            print(f"  {col}")
            for item in col:
                print(f"    {item}")
