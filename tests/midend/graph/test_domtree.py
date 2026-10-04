"""支配树（CfgInfo）测试。

对应 src/qcc/midend/graph/domtree.py（mem2reg 等 pass 依赖的
支配者/支配边界计算，是需求4 中端模块的基础设施）。
"""
import unittest

from qcc.backend.binutils.debuginfo import DebugDb
from qcc.midend import ir, irutils
from qcc.midend.graph.domtree import CfgInfo


class CfgInfoTestCase(unittest.TestCase):
    def setUp(self):
        self.debug_db = DebugDb()
        self.builder = irutils.Builder()
        self.module = ir.Module("test", debug_db=self.debug_db)
        self.builder.set_module(self.module)
        self.function = self.builder.new_procedure(
            "testfunction", ir.Binding.GLOBAL
        )
        self.builder.set_function(self.function)
        entry = self.builder.new_block()
        self.function.entry = entry
        self.builder.set_block(entry)

    def _make_diamond(self):
        """构造菱形 CFG：entry → (a, b) → join → exit"""
        entry = self.function.entry
        a = self.builder.new_block()
        b = self.builder.new_block()
        join = self.builder.new_block()
        exit_block = self.builder.new_block()
        cond = self.builder.emit(ir.Const(0, "cond", ir.i32))
        self.builder.emit(ir.CJump(cond, "==", cond, a, b))
        self.builder.set_block(a)
        self.builder.emit(ir.Jump(join))
        self.builder.set_block(b)
        self.builder.emit(ir.Jump(join))
        self.builder.set_block(join)
        self.builder.emit(ir.Jump(exit_block))
        self.builder.set_block(exit_block)
        self.builder.emit(ir.Exit())
        return entry, a, b, join, exit_block

    def test_dominator_relation(self):
        """菱形 CFG 中 entry 支配所有块，a/b 不互相支配"""
        entry, a, b, join, exit_block = self._make_diamond()
        info = CfgInfo(self.function)
        cfg = info.cfg
        entry_node = info.get_node(entry)
        join_node = info.get_node(join)
        a_node = info.get_node(a)
        # entry 支配 join
        self.assertTrue(cfg.dominates(entry_node, join_node))
        # a 不支配 join（b 也是 join 的前驱）
        self.assertFalse(cfg.dominates(a_node, join_node))
        self.assertFalse(cfg.dominates(info.get_node(b), join_node))
        # a 严格支配其自身为假
        self.assertFalse(cfg.strictly_dominates(a_node, a_node))
        # a 的立即支配者是 entry
        self.assertIs(cfg.get_immediate_dominator(a_node), entry_node)

    def test_dominance_frontier(self):
        """块 a 的支配边界应包含 join（df 以块为键、值也是块）"""
        entry, a, b, join, exit_block = self._make_diamond()
        info = CfgInfo(self.function)
        self.assertIn(join, info.df[a])
        # entry 的支配边界为空（它支配所有可达块）
        self.assertEqual(info.df[entry], set())
        # b 的支配边界同样包含 join
        self.assertIn(join, info.df[b])

    def test_root_tree(self):
        """支配树的根应是 entry 块"""
        entry, a, b, join, exit_block = self._make_diamond()
        info = CfgInfo(self.function)
        root = info.cfg.root_tree
        self.assertIs(root.node, info.get_node(entry))


if __name__ == "__main__":
    unittest.main()
