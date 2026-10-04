"""干涉图（interference graph）：图着色寄存器分配的核心数据结构。

后端流水线中的位置（需求3 的"后端"）：
    flowgraph.py 算出每个虚拟寄存器的活性/活动区间
        → **interferencegraph.py：汇总成干涉图**
        → registerallocator.py：图着色 → 物理寄存器或溢出到栈

图的结构：
- 节点 = 一个寄存器（虚拟寄存器 vreg 或已着色的物理寄存器）；
- 边 = 两个寄存器的"生命期重叠"——绝不能分到同一物理寄存器
  （否则后写的会覆盖另一个还在用的值）；
- 底层是 MaskableGraph（可掩码图）：着色失败要 spill、合并失败要
  回退时，可以临时"隐藏"节点而不破坏图本体。

关键类/方法：
- InterferenceGraph —— 干涉图：calculate_interference(flowgraph)
  依据每条指令的 live_in/live_out 与 kill 加边
- interfere(tmp1, tmp2) —— 查询两寄存器是否干涉
- combine(n, m) —— 合并两个节点（寄存器合并成功后）
- get_node(tmp) —— 按寄存器取节点（临时映射 temp_map）

建图逻辑（calculate_interference）：
对每条指令，把 live_and_def = ins.live_out | ins.kill 中的寄存器
两两连边，再连上 ins.clobbers（被破坏的寄存器）。

详见 docs/interferencegraph.py.md。


.. autoclass:: qcc.backend.codegen.interferencegraph.InterferenceGraph
    :members: get_node, combine, interfere

"""

import logging
from collections import defaultdict

from qcc.backend.arch.registers import Register
from qcc.midend.graph.graph import Node
from qcc.midend.graph.maskable_graph import MaskableGraph


class InterferenceGraphNode(Node):
    """Node in an interference graph. Represents a single register"""

    def __init__(self, graph, vreg):
        super().__init__(graph)
        self.temps = {vreg}
        self.moves = set()
        self.reg = vreg if vreg.is_colored else None
        self.reg_class = type(vreg)

    @property
    def is_colored(self):
        return self.reg is not None

    def __repr__(self):
        return f"{self.temps}(reg={self.reg},class={self.reg_class})"


class InterferenceGraph(MaskableGraph):
    """Interference graph."""

    def __init__(self):
        """Create a new interference graph from a flowgraph"""
        super().__init__()
        self.logger = logging.getLogger("interferencegraph")
        self.temp_map = {}
        self._def_map = defaultdict(list)
        self._use_map = defaultdict(list)

    def defs(self, tmp):
        return self._def_map[tmp]

    def uses(self, tmp):
        return self._use_map[tmp]

    def calculate_interference(self, flowgraph):
        """Construct interference graph"""
        for n in flowgraph:
            for ins in n.instructions:
                # ins.live_out |= ins.
                for tmp in ins.live_in:
                    self.get_node(tmp)

                # Live out and zero length defined variables:
                live_and_def = ins.live_out | ins.kill

                # Add interfering edges:
                for tmp in live_and_def:
                    n1 = self.get_node(tmp)
                    for tmp2 in live_and_def - {tmp}:
                        n2 = self.get_node(tmp2)
                        self.add_edge(n1, n2)

                    # Add clobbered interfering edges:
                    for tmp2 in ins.clobbers:
                        n2 = self.get_node(tmp2)
                        self.add_edge(n1, n2)

                # Generate usage info:
                for reg in ins.defined_registers:
                    self._def_map[reg].append(ins)
                for reg in ins.used_registers:
                    self._use_map[reg].append(ins)

    def has_node(self, tmp):
        """Check if there exists a node for this temp register"""
        assert isinstance(tmp, Register)
        return tmp in self.temp_map

    def get_node(self, tmp, create=True):
        """Get the node for a register"""
        assert isinstance(tmp, Register)
        if tmp in self.temp_map:
            node = self.temp_map[tmp]
            assert tmp in node.temps
        else:
            assert create
            node = InterferenceGraphNode(self, tmp)
            self.add_node(node)
            self.temp_map[tmp] = node
        return node

    def interfere(self, tmp1, tmp2):
        """Checks if tmp1 and tmp2 interfere"""
        assert isinstance(tmp1, Register)
        assert isinstance(tmp2, Register)
        node1 = self.get_node(tmp1)
        node2 = self.get_node(tmp2)
        return self.has_edge(node1, node2)

    def combine(self, n, m):
        """Combine n and m into n and return n"""
        # Copy associated moves and temporaries into n:
        n.temps |= m.temps
        n.moves.update(m.moves)

        # Update local temp map:
        for tmp in m.temps:
            self.temp_map[tmp] = n

        super().combine(n, m)
        return n
