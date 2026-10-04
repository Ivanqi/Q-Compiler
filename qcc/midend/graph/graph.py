"""Graph package.

无向图数据结构基础模块：定义抽象基类 BaseGraph、通用无向图 Graph 与节点类 Node。

Graph 用邻接表 adj_map 存放无向边（两端各记录一次），是后端寄存器分配
所用干涉图 MaskableGraph 的父类；模块级函数 topological_sort 提供基于
Tarjan 的拓扑排序，用于需要按依赖顺序处理节点的场景。

Graph package.
"""

import abc
from collections import defaultdict

from qcc.utils.collections import OrderedSet


def topological_sort(nodes):
    """基于 Tarjan 算法对节点做拓扑排序，按依赖关系（children 先输出）返回节点列表；要求图是有向无环图，遇环则断言失败。

    Sort nodes topological, use Tarjan algorithm here
    See: https://en.wikipedia.org/wiki/Topological_sorting
    """
    unmarked = set(nodes)
    marked = set()
    temp_marked = set()
    L = []

    def visit(n):
        # print(n)
        assert n not in temp_marked, "DAG has cycles"
        if n in unmarked:
            temp_marked.add(n)
            for m in n.children:
                visit(m)
            temp_marked.remove(n)
            marked.add(n)
            unmarked.remove(n)
            L.insert(0, n)

    while unmarked:
        n = next(iter(unmarked))
        visit(n)

    return L


class BaseGraph(abc.ABC):
    """图抽象基类：持有节点集合 nodes 与邻接表 adj_map，定义增删节点/边等抽象接口，由 Graph 等子类实现。

    Base graph class
    """

    __slots__ = ("nodes", "adj_map")

    def __init__(self):
        self.nodes = OrderedSet()

        # Fast lookup dictionaries:
        self.adj_map = defaultdict(OrderedSet)

    def __iter__(self):
        yield from self.nodes

    def __len__(self):
        return len(self.nodes)

    def add_node(self, node):
        """Add a node to the graph"""
        self.nodes.add(node)

    @abc.abstractmethod
    def del_node(self, node):  # pragma: no cover
        """Remove a node from the graph"""
        raise NotImplementedError()

    @abc.abstractmethod
    def add_edge(self, n, m):  # pragma: no cover
        raise NotImplementedError()

    @abc.abstractmethod
    def del_edge(self, n, m):  # pragma: no cover
        raise NotImplementedError()

    @abc.abstractmethod
    def has_edge(self, n, m):
        """Test if there exist and edge between n and m"""
        raise NotImplementedError()

    @abc.abstractmethod
    def get_number_of_edges(self):
        """Get the number of edges in this graph"""
        raise NotImplementedError()

    def get_degree(self, node):
        """Get the degree of a certain node"""
        return len(self.adj_map[node])

    def adjecent(self, n):
        """返回与节点 n 相邻的所有节点（邻接表查询）

        Return all unmasked nodes with edges to n
        """
        return self.adj_map[n]


class Graph(BaseGraph):
    """通用无向图：实现无向边的添加/删除/查询、节点删除以及节点合并（combine）。

    合并操作将节点 m 的邻居全部改接到 n 后删除 m，是寄存器分配中
    合并两个虚拟寄存器（干涉图节点）的基础操作。

    Generic graph base class.

    Can dump to graphviz dot format for example!
    """

    def del_node(self, node):
        """Remove a node from the graph"""
        # Delete edges:
        for neighbour in list(self.adj_map[node]):
            self.del_edge(node, neighbour)
        self.nodes.remove(node)

    def add_edge(self, n, m):
        """在 n 与 m 之间添加一条无向边（自环忽略，重复边不重复记录）

        Add an edge between n and m
        """
        if n == m:
            return
        assert n in self.nodes
        assert m in self.nodes
        if not self.has_edge(n, m):
            self.adj_map[n].add(m)
            self.adj_map[m].add(n)

    def del_edge(self, n, m):
        """删除 n 与 m 之间的无向边（两端的邻接表同步删除）

        Delete edge between n and m
        """
        assert n != m
        assert n in self.nodes
        assert m in self.nodes
        if self.has_edge(n, m):
            self.adj_map[m].remove(n)
            self.adj_map[n].remove(m)

    def has_edge(self, n, m):
        """Test if there exist and edge between n and m"""
        assert n in self.nodes
        assert m in self.nodes
        return m in self.adj_map[n]

    def get_number_of_edges(self):
        """Get the number of edges in this graph"""
        n_edges = sum(len(self.adj_map[n]) for n in self.nodes)
        # Since this is an undirected graph, we will now have
        # twice the amount of edges, since adj_map contains neighbour
        # information for both directions. So divide this number by 2.
        return n_edges // 2

    def combine(self, n, m):
        """把节点 m 合并进节点 n：先将 m 的所有邻居改接到 n，再删除 m（用于寄存器合并）

        Merge nodes n and m into node n
        """
        assert n != m
        # assert not self.has_edge(n, m)
        # if self.has_edge(n, m):
        #    self.degree_map[n] += 1

        # assert not self.has_edge(n, m)

        # Reroute all edges:
        m_adjecent = set(self.adj_map[m])
        for a in m_adjecent:
            self.del_edge(m, a)
            self.add_edge(n, a)

        # Remove node m:
        assert len(self.adj_map[m]) == 0  # Node should not have neighbours
        self.del_node(m)

    def to_dot(self):
        """Render current graph to dot format"""
        pass


class Node:
    """图中的节点：构造时自动注册进所属图 graph，并通过 graph 提供邻居、度数查询与加边操作。

    Node in a graph.
    """

    __slots__ = ("graph",)

    def __init__(self, graph):
        self.graph = graph
        self.graph.add_node(self)

    @property
    def adjecent(self):
        """Get adjecent nodes in the graph"""
        return self.graph.adjecent(self)

    @property
    def degree(self):
        """Get the degree of this node (the number of neighbours)"""
        return self.graph.get_degree(self)

    def add_edge(self, other):
        """Create an edge to the other node"""
        self.graph.add_edge(self, other)
