"""Graph type that supports temporary removal of nodes.

支持“临时移除”（屏蔽）节点的无向图：被屏蔽的节点从 nodes 中移出，但其边信息
被保留在 _masked_adj 中，解除屏蔽时自动恢复连接。

用于后端寄存器分配：backend/codegen/interferencegraph.py 的干涉图继承本类，
在合并/不动节点时把节点屏蔽起来，从而在保留边关系的同时参与图着色。

Edge information is retained, and restored when the node is placed back.
"""

from collections import defaultdict
from itertools import chain

from qcc.utils.collections import OrderedSet
from qcc.midend.graph.graph import Graph


class MaskableGraph(Graph):
    """可掩蔽图：支持把节点临时移出图（屏蔽）后再放回，屏蔽期间边关系不丢失。

    A graph that allows masking nodes temporarily
    """

    __slots__ = ("_masked_nodes", "_masked_adj")

    def __init__(self):
        super().__init__()
        self._masked_nodes = set()
        self._masked_adj = defaultdict(OrderedSet)

    def mask_node(self, node):
        """屏蔽节点：将其从图中移除，并把所有相邻边转移到 _masked_adj 中暂存

        Add the node into the masked set
        """
        assert not self.is_masked(node)
        self._masked_nodes.add(node)

        # Update neighbour adjecency:
        for neighbour in chain(self.adj_map[node], self._masked_adj[node]):
            self.adj_map[neighbour].remove(node)
            self._masked_adj[neighbour].add(node)

        self.nodes.remove(node)

    def unmask_node(self, node):
        """解除屏蔽：把节点放回图中，并从 _masked_adj 恢复其所有相邻边

        Unmask a node (put it back into the graph
        """
        assert self.is_masked(node)
        self._masked_nodes.remove(node)
        self.nodes.add(node)

        # Restore connections:
        for neighbour in chain(self.adj_map[node], self._masked_adj[node]):
            self.adj_map[neighbour].add(node)
            self._masked_adj[neighbour].remove(node)

    def is_masked(self, node):
        """Test if a node is masked"""
        return node in self._masked_nodes

    def combine(self, n, m):
        """合并节点 n 与 m（要求 n 未被屏蔽）：先解除 m 的屏蔽并转移其暂存的掩蔽边，再调用父类合并

        Merge nodes n and m into node n
        """
        assert n != m
        # assert not self.has_edge(n, m)
        # if self.has_edge(n, m):
        #    self.degree_map[n] += 1

        # node m is going away, make sure to unmask it first:
        if self.is_masked(m):
            self.unmask_node(m)

        # Move stored masked edges:
        for neighbour in list(self._masked_adj[m]):
            # Move connection end 1:
            self.adj_map[neighbour].remove(m)
            self.adj_map[neighbour].add(n)

            # Move connection end 2:
            self._masked_adj[m].remove(neighbour)
            self._masked_adj[n].add(neighbour)

        assert len(self._masked_adj[m]) == 0

        assert not self.is_masked(n), "Combining only allowed for non-masked"
        super().combine(n, m)
