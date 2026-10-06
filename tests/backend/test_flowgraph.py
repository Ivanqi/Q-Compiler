"""流图（FlowGraph）测试。

对应 src/qcc/backend/codegen/flowgraph.py（需求4 列出的迁移模块之一）。
FlowGraph 把线性机器指令序列按跳转目标切成基本块，
并计算每个块的活跃变量集合（live_in / live_out）。
"""
import unittest

from qcc.backend.arch.example import Def, DefUse, ExampleRegister
from qcc.backend.codegen.flowgraph import FlowGraph


class FlowGraphTestCase(unittest.TestCase):
    def test_linear_cfg(self):
        """无跳转的指令序列：单块 CFG，入度/出度均为 0"""
        r1 = ExampleRegister("r1")
        r2 = ExampleRegister("r2")
        instrs = [Def(r1), Def(r2)]
        cfg = FlowGraph(instrs)
        self.assertEqual(len(cfg.nodes), 1)
        node = cfg.nodes[0]
        self.assertEqual(list(cfg.successors(node)), [])
        self.assertEqual(list(cfg.predecessors(node)), [])

    def test_branch_cfg(self):
        """跳转把序列切成两块，边的方向正确"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        t3 = ExampleRegister("t3")
        i1 = DefUse(t3, t2)
        i2 = Def(t1, jumps=[i1])  # i2 → i1
        cfg = FlowGraph([i2, i1])
        self.assertEqual(len(cfg.nodes), 2)
        n1 = cfg.get_node(i1)
        n2 = cfg.get_node(i2)
        self.assertEqual(list(cfg.successors(n2)), [n1])
        self.assertEqual(list(cfg.predecessors(n1)), [n2])

    def test_loop_cfg(self):
        """循环：a ↔ b 两块的 CFG（借鉴 graph/test_graph.py 的用例）"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        t3 = ExampleRegister("t3")
        t4 = ExampleRegister("t4")
        i1 = DefUse(t1, t4)            # a 块
        i3 = DefUse(t3, t2)            # b 块
        i2 = Def(t2, jumps=[i3])       # a → b
        i4 = DefUse(t4, t1, jumps=[i1])  # b → a
        cfg = FlowGraph([i1, i2, i3, i4])
        self.assertEqual(len(cfg.nodes), 2)
        na = cfg.get_node(i1)
        nb = cfg.get_node(i3)
        self.assertEqual(set(cfg.successors(na)), {nb})
        self.assertEqual(set(cfg.successors(nb)), {na})

    def test_liveness(self):
        """活跃性计算：t1 在定义后到使用前保持活跃"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        t3 = ExampleRegister("t3")
        i1 = Def(t1)             # t1 定义
        i2 = Def(t2)             # t2 定义
        i3 = DefUse(t3, t1)      # t1 在此使用
        cfg = FlowGraph([i1, i2, i3])
        cfg.calculate_liveness()
        # t1 在 i1 的出口、i2 与 i3 的入口处活跃
        self.assertIn(t1, i1.live_out)
        self.assertIn(t1, i2.live_in)
        self.assertIn(t1, i3.live_in)
        # t1 在 i3 之后不再活跃
        self.assertNotIn(t1, i3.live_out)
        # t2 从未被使用，任何位置都不活跃
        self.assertNotIn(t2, i2.live_out)
        self.assertNotIn(t2, i3.live_in)

    def test_fall_through_is_single_block(self):
        """无跳转的相邻指令属于同一基本块（线性流不产生边）"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        i1 = Def(t1)
        i2 = Def(t2)
        cfg = FlowGraph([i1, i2])
        # 只有第一条指令是块首（leader），后续指令归入该块
        node = cfg.get_node(i1)
        self.assertEqual(node.instructions, [i1, i2])

    def test_live_ranges_recorded(self):
        """活动区间（_live_ranges）记录寄存器活跃的指令跨度"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        t3 = ExampleRegister("t3")
        i1 = Def(t1)
        i2 = Def(t2)
        i3 = DefUse(t3, t1)
        cfg = FlowGraph([i1, i2, i3])
        cfg.calculate_liveness()
        ranges = cfg._live_ranges
        self.assertIn(t1, ranges)
        # t1 的区间跨越 (i1, i2) 与 (i2, i3)
        self.assertIn((i1, i2), ranges[t1])
        self.assertIn((i2, i3), ranges[t1])

    def test_node_instructions(self):
        """节点携带其基本块的指令列表与 gen/kill 信息"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        i1 = Def(t1)
        i2 = DefUse(t2, t1)
        cfg = FlowGraph([i1, i2])
        node = cfg.get_node(i1)
        self.assertEqual(node.instructions, [i1, i2])
        # 块级 kill = 被定义的寄存器集合；块级 gen 只统计
        # "在本块内使用、且未在本块内先定义"的寄存器。
        # t1 在块内先被 Def 定义、后被 DefUse 使用 → 只进 kill 不进 gen
        self.assertIn(t1, node.kill)
        self.assertIn(t2, node.kill)
        self.assertNotIn(t1, node.gen)
        self.assertNotIn(t2, node.gen)


if __name__ == "__main__":
    unittest.main()
