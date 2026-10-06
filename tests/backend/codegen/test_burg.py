# -*- coding: utf-8 -*-
"""BURG 树模式匹配（qcc/backend/codegen/burg.py）专用测试。

BURG = Bottom-Up Rewrite Generator：输入模式描述（burg.grammar 语法），
输出一个能对树做最优模式匹配的 Matcher 类（label/select 动态规划）。
本文件直接测试：
- BurgSystem 规则库 API（add_rule/goal/chain rules/终端匹配/取子节点）
- 用自定义小语法现场生成 Matcher 并匹配树（端到端）
test_burm.py 的 sample4 用例（Fraser 论文经典文法）保持不变。
"""
import argparse
import io
import unittest

from qcc.backend.codegen import burg
from qcc.backend.codegen.burg import BurgSystem
from qcc.utils.tree import Tree


class BurgSystemTestCase(unittest.TestCase):
    """规则库本身的单元测试"""

    def setUp(self):
        self.system = BurgSystem()
        self.system.add_terminal("ADDI32")
        self.system.add_terminal("CONSTI32")
        self.system.add_terminal("MULI32")

    def test_add_rule_and_goal(self):
        """第一条规则的左端成为 goal（匹配目标）"""
        self.system.add_rule(
            "reg", Tree("ADDI32", Tree("reg"), Tree("reg")), 2, None, None
        )
        self.assertEqual(self.system.goal, "reg")
        self.assertEqual(len(self.system.rules), 1)

    def test_get_rules_for_root(self):
        """按树根名查候选规则"""
        r1 = self.system.add_rule(
            "reg", Tree("ADDI32", Tree("reg"), Tree("reg")), 2, None, None
        )
        self.system.add_rule(
            "reg", Tree("CONSTI32"), 1, None, None
        )
        rules = self.system.get_rules_for_root("ADDI32")
        self.assertEqual(rules, [r1])

    def test_chain_rules(self):
        """链规则：reg -> rc 这类非终结符到非终结符的规则"""
        self.system.add_rule("reg", Tree("CONSTI32"), 1, None, None)
        self.system.add_rule("reg", Tree("rc"), 0, None, None)
        chain = self.system.chain_rules_for_nt("rc")
        self.assertEqual(len(chain), 1)
        self.assertIs(chain[0].non_term, "reg")

    def test_tree_terminal_equal(self):
        """树模式匹配判定：终端相同且子结构匹配"""
        self.system.add_rule("reg", Tree("CONSTI32"), 1, None, None)
        t1 = Tree("ADDI32", Tree("reg"), Tree("reg"))
        t2 = Tree("ADDI32", Tree("reg"), Tree("reg"))
        t3 = Tree("MULI32", Tree("reg"), Tree("reg"))
        self.assertTrue(self.system.tree_terminal_equal(t1, t2))
        self.assertFalse(self.system.tree_terminal_equal(t1, t3))

    def test_get_kids_and_nts(self):
        """从模板中提取匹配到的子树（kids）与非终结符（nts）"""
        self.system.add_rule("reg", Tree("CONSTI32"), 1, None, None)
        template = Tree("ADDI32", Tree("reg"), Tree("reg"))
        tree = Tree("ADDI32", Tree("CONSTI32"), Tree("CONSTI32"))
        kids = self.system.get_kids(tree, template)
        self.assertEqual(len(kids), 2)
        self.assertIs(kids[0], tree.children[0])
        nts = self.system.get_nts(template)
        self.assertEqual(nts, ["reg", "reg"])

    def test_cannot_redefine_terminal(self):
        """终端不能被用作非终结符"""
        from qcc.backend.codegen.burg import BurgError
        with self.assertRaises(BurgError):
            self.system.non_term("ADDI32")


# 自定义小语法：匹配"加法或乘法"的表达式树
SMALL_GRAMMAR = """\
%%

%terminal ADD MUL NUM

%%

expr: ADD(expr, expr) 1 'self.tr("add")'
expr: MUL(expr, expr) 2 'self.tr("mul")'
expr: NUM 0 'self.tr("num")'
"""


class BurgGeneratorTestCase(unittest.TestCase):
    """从语法文本现场生成 Matcher 并匹配（端到端）"""

    def _make_matcher(self, grammar):
        buf = io.StringIO()
        args = argparse.Namespace(source=io.StringIO(grammar), output=buf)
        burg.main(args)
        namespace = {}
        exec(buf.getvalue(), namespace)
        return namespace["Matcher"]

    def test_generated_matcher_labels_tree(self):
        """生成器输出可执行的 Matcher，能把树匹配到目标非终结符"""
        matcher = self._make_matcher(SMALL_GRAMMAR)

        class MyMatcher(matcher):
            def __init__(self):
                super().__init__()
                self.trace = []

            def tr(self, name):
                self.trace.append(name)

        m = MyMatcher()
        tree = Tree(
            "ADD", Tree("MUL", Tree("NUM"), Tree("NUM")), Tree("NUM")
        )
        m.gen(tree)
        self.assertIn("add", m.trace)
        self.assertIn("mul", m.trace)
        self.assertIn("num", m.trace)
        # 根节点必须匹配到 goal（expr）
        self.assertTrue(tree.state.has_goal("expr"))

    def test_cost_selection_prefers_cheaper(self):
        """代价低的规则优先（label/select 动态规划）"""
        matcher = self._make_matcher(SMALL_GRAMMAR)

        class MyMatcher(matcher):
            def __init__(self):
                super().__init__()
                self.trace = []

            def tr(self, name):
                self.trace.append(name)

        m = MyMatcher()
        tree = Tree("ADD", Tree("NUM"), Tree("NUM"))
        m.gen(tree)
        # 每条 NUM 叶子只被选中一次
        self.assertEqual(m.trace.count("num"), 2)


if __name__ == "__main__":
    unittest.main()
