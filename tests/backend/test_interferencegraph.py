"""干涉图（InterferenceGraph）测试。

对应 src/qcc/backend/codegen/interferencegraph.py
（需求4 列出的迁移模块之一）。
干涉图是图着色寄存器分配器的输入：两个虚拟寄存器
若存活区间重叠则不能分到同一物理寄存器，图上加一条边。
"""
import unittest

from qcc.backend.arch.example import Def, DefUse, ExampleRegister
from qcc.backend.codegen.flowgraph import FlowGraph
from qcc.backend.codegen.interferencegraph import InterferenceGraph


def make_ig(instrs):
    """构造指令序列对应的干涉图"""
    cfg = FlowGraph(instrs)
    cfg.calculate_liveness()
    ig = InterferenceGraph()
    ig.calculate_interference(cfg)
    return ig


class InterferenceGraphTestCase(unittest.TestCase):
    def test_overlapping_registers_interfere(self):
        """t1 与 t2 存活区间重叠 → 必须有一条干涉边"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        t3 = ExampleRegister("t3")
        t4 = ExampleRegister("t4")
        # t1 从第 1 条活到第 4 条；t2 从第 2 条活到第 3 条 → 重叠
        ig = make_ig(
            [Def(t1), Def(t2), DefUse(t3, t2), DefUse(t4, t1)]
        )
        self.assertTrue(ig.interfere(t1, t2))

    def test_non_overlapping_registers_do_not_interfere(self):
        """t1 在 t2 定义前已不再使用 → 无干涉边"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        t3 = ExampleRegister("t3")
        t4 = ExampleRegister("t4")
        # t1 只在第 1 条被使用；t2 从第 2 条才开始活跃 → 不重叠
        ig = make_ig(
            [DefUse(t3, t1), Def(t2), DefUse(t4, t2)]
        )
        self.assertFalse(ig.interfere(t1, t2))

    def test_registers_never_used_do_not_interfere(self):
        """从未使用的寄存器之间无干涉"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        t3 = ExampleRegister("t3")
        ig = make_ig([Def(t1), Def(t3)])
        self.assertFalse(ig.interfere(t2, t3))

    def test_degree_of_node(self):
        """节点度数 = 与其干涉的寄存器数"""
        t1 = ExampleRegister("t1")
        t2 = ExampleRegister("t2")
        t3 = ExampleRegister("t3")
        t4 = ExampleRegister("t4")
        t5 = ExampleRegister("t5")
        # t1 一直活跃到第 6 条 → 与 t2/t3/t4 全部干涉
        ig = make_ig(
            [
                Def(t1),
                Def(t2),
                Def(t3),
                DefUse(t4, t2),
                DefUse(t4, t3),
                DefUse(t5, t1),
            ]
        )
        self.assertEqual(ig.get_node(t1).degree, 3)  # 与 t2/t3/t4 全部干涉


if __name__ == "__main__":
    unittest.main()
