"""文法表示：描述语言的终结符、非终结符与产生式规则。

位于 C 前端流水线的语法分析阶段（预处理→词法→语法(parser.py)）：
为递归下降/LR/Earley 解析器以及 yacc 生成器提供统一的文法数据结构，
产生式上可挂语义动作函数，最终由 parser.py 构建语法树供语义分析使用。

Grammar and production representation used by the parser tooling.
"""

from qcc.frontend.tools.common import ParserGenerationException


class Grammar:
    """文法容器：保存终结符、非终结符、产生式及起始符号。

    Defines a grammar of a language"""

    def __init__(self):
        self.terminals = set()
        self.nonterminals = set()
        self.productions = []
        self.start_symbol = None

    def __repr__(self):
        return (
            f"Grammar with {len(self.productions)} rules and "
            + f"{len(self.terminals)} terminals"
        )

    def add_terminals(self, terminals):
        """批量添加终结符。

        Add all terminals to terminals for this grammar"""
        for terminal in terminals:
            self.add_terminal(terminal)

    def add_terminal(self, name):
        """添加一个终结符；与已有非终结符重名时报错。

        Add a terminal name"""
        if name in self.nonterminals:
            raise ParserGenerationException(
                f"Cannot redefine non-terminal {name} as terminal"
            )
        self.terminals.add(name)

    def add_production(self, name, symbols, semantics=None, priority=0):
        """添加一条产生式：name -> symbols，并可附带语义动作与优先级。

        Add a production rule to the grammar"""
        production = Production(name, symbols, semantics, priority=priority)
        self.productions.append(production)
        if name in self.terminals:
            raise ParserGenerationException(f"Cannot redefine terminal {name}")
        self.nonterminals.add(name)

    def dump(self):
        """打印本文法，便于调试。

        Print this grammar"""
        print_grammar(self)

    def add_one_or_more(self, element_nonterm, list_nonterm):
        """辅助方法：生成“一个或多个元素”的列表递归产生式。

        Helper to add the rule
        lst: elem
        lst: lst elem
        """

        def a(el):
            return [el]

        self.add_production(list_nonterm, [element_nonterm], a)

        def b(ls, el):
            ls.append(el)
            return ls

        self.add_production(list_nonterm, [list_nonterm, element_nonterm], b)

    def productions_for_name(self, name):
        """取出某个非终结符的全部产生式。

        Retrieve all productions for a non terminal"""
        return [p for p in self.productions if p.name == name]

    @property
    def symbols(self):
        """返回本文法定义的所有符号（终结符 ∪ 非终结符）。

        Get all the symbols defined by this grammar"""
        return self.nonterminals | self.terminals

    def is_terminal(self, name):
        """判断名字是否为终结符。

        Check if a name is a terminal"""
        return name in self.terminals

    def is_nonterminal(self, name):
        """判断名字是否为非终结符。

        Check if a name is a non-terminal"""
        return name in self.nonterminals

    def rewrite_eps_productions(self):
        """消去空产生式（epsilon），通过枚举组合展开可能为空的规则。

        Make the grammar free of empty productions.
        Do this by permutating all combinations of rules that would
        otherwise contain an empty place.
        """
        while True:
            eps = [rule for rule in self.productions if rule.is_epsilon]
            if not eps:
                # We are done!
                break

            # Process the first occasion:
            eps_rule = eps[0]

            # Remove the epsilon-rule
            self.productions.remove(eps_rule)

            # For each rule containing the empty production, create new rules:
            for rule in self.productions:
                if eps_rule.name in rule.symbols:
                    self.create_combinations(rule, eps_rule.name)

    def create_combinations(self, rule, non_terminal):
        """消去可空非终结符：生成移除该符号后的新产生式。

        Create n copies of rule where nt is removed.
        For example replace:
        A -> B C B
        by:
        A -> B C B
        A -> B C
        A -> C B
        A -> C
        if:
        B -> epsilon
        """
        count = rule.symbols.count(non_terminal)
        # TODO: refactor this restriction:
        assert count == 1

        rhs = []
        for x in rule.symbols:
            if x == non_terminal:
                pass
            else:
                rhs.append(x)
        self.add_production(rule.name, rhs)
        # self.productions.remove(rule)

    @property
    def is_normal(self):
        """判断文法是否“规范”：即不含任何空产生式。

        Check if this grammar is normal.
        Which means:
        - No empty productions (epsilon productions)
        """
        # If the grammar contains an epsilon production, it is not normal:
        return not any(rule.is_epsilon for rule in self.productions)

    def check_symbols(self):
        """检查所有产生式中的符号均已定义，否则抛出生成异常。

        Checks no symbols are undefined"""
        for production in self.productions:
            if production.is_epsilon:
                # raise ParserGenerationException("Epsilon!")
                pass
            for symbol in production.symbols:
                if symbol not in self.symbols:
                    raise ParserGenerationException(
                        f"Symbol {symbol} undefined"
                    )


class Production:
    """文法产生式：左部非终结符 + 右部符号串 + 归约时调用的语义动作。

    Production rule for a grammar. It consists of a left hand side
    non-terminal and a list of symbols as right hand side. Also it
    contains a function that must be called when this rule is applied.
    The right hand side may contain terminals and non-terminals.
    """

    def __init__(self, name, symbols, semantics, priority=0):
        self.name = name
        self.symbols = tuple(symbols)
        self.f = semantics
        self.priority = priority

    def __repr__(self):
        return f"{self.name} -> {self.symbols} P_{self.priority}"

    @property
    def is_epsilon(self):
        """判断该产生式是否为空产生式（右部为空）。

        Checks if this rule is an epsilon rule"""
        return len(self.symbols) == 0


def print_grammar(g):
    """以可读格式打印文法及其全部产生式。

    Pretty print a grammar"""
    print(g)
    for production in g.productions:
        print(production)
