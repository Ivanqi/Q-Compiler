"""Directed graph.

有向图数据结构模块：DiGraph 在公共邻接表之外，额外维护后继表 suc_map 与前驱表
pre_map，节点类为 DiNode，并提供可反向遍历的深度优先搜索 dfs。

被后端 codegen/flowgraph（指令级活跃变量分析流图）与本包的 cfg（控制流图）、
lt（Lengauer-Tarjan 支配树）复用，支配者/前驱后继等分析都建立在其上。

In a directed graph, the edges have a direction.
"""

from collections import defaultdict

from qcc.midend.graph.graph import BaseGraph, Node


class DiGraph(BaseGraph):
    """有向图：用 suc_map/pre_map 分别记录每个节点的后继与前驱，支持有向边的增删查与节点删除。

    Directed graph.
    """

    __slots__ = ("suc_map", "pre_map")

    def __init__(self):
        super().__init__()
        self.suc_map = defaultdict(set)
        self.pre_map = defaultdict(set)

    def del_node(self, node):
        """Remove a node from the graph"""
        s = list(self.successors(node))
        for m in s:
            self.del_edge(node, m)

        p = list(self.predecessors(node))
        for m in p:
            self.del_edge(m, node)
        self.nodes.remove(node)

    def add_edge(self, n, m):
        """添加一条从 n 指向 m 的有向边，同时更新后继表、前驱表与邻接表

        Add a directed edge from n to m
        """
        assert n in self.nodes
        assert m in self.nodes
        if not self.has_edge(n, m):
            self.suc_map[n].add(m)
            self.pre_map[m].add(n)
            self.adj_map[n].add(m)
            self.adj_map[m].add(n)

    def del_edge(self, n, m):
        """Delete a directed edge"""
        assert n != m
        assert n in self.nodes
        assert m in self.nodes
        if self.has_edge(n, m):
            self.suc_map[n].remove(m)
            self.pre_map[m].remove(n)
            self.adj_map[m].remove(n)
            self.adj_map[n].remove(m)

    def has_edge(self, n, m):
        """Test if there exist and edge between n and m"""
        return m in self.suc_map[n]

    def get_number_of_edges(self):
        """Get the number of edges in this graph"""
        n_edges = sum(len(self.adj_map[n]) for n in self.nodes)
        return n_edges

    def successors(self, node):
        """获取节点 node 的所有后继（出边目标）

        Get the successors of the node
        """
        return self.suc_map[node]

    def predecessors(self, node):
        """获取节点 node 的所有前驱（入边来源），支配者与 phi 插入分析都会用到

        Get the predecessors of the node
        """
        return self.pre_map[node]


class DiNode(Node):
    """有向图中的节点：除邻居外还可通过属性访问后继（successors）与前驱（predecessors）。

    Node in a directed graph
    """

    @property
    def successors(self):
        """获取本节点的后继节点集合

        Get the successors of this node
        """
        return self.graph.successors(self)

    @property
    def predecessors(self):
        """获取本节点的前驱节点集合

        Get the predecessors of this node
        """
        return self.graph.predecessors(self)


def dfs(start_node, reverse=False):
    """从 start_node 出发按深度优先顺序遍历，逐个产出 (父节点, 节点) 二元组；reverse=True 时沿反向边（前驱）遍历。

    Visit nodes in depth-first-search order.

    Args:
        - start_node: node to start with
        - reverse: traverse the graph by reversing the edge directions.
    """
    visited = set()
    worklist = [(None, start_node)]
    while worklist:
        parent, node = worklist.pop()
        if node not in visited:
            visited.add(node)
            yield parent, node
            if reverse:
                for predecessor in node.predecessors:
                    worklist.append((node, predecessor))
            else:
                for successor in node.successors:
                    worklist.append((node, successor))
