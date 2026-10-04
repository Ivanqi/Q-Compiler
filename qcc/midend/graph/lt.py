"""Lengauer-Tarjan 支配树（直接支配者）快速算法实现。

先对图做 DFS 得到 dfnum（深度优先编号）与 DFS 生成树父节点 parent，
再按逆 DFS 序计算半支配者（semi）并用并查集（ancestor/best + 路径压缩）
求每个节点的直接支配者 idom，时间复杂度接近 O(E·α(E))。

被 cfg.ControlFlowGraph._calculate_dominator_info 调用，是支配者判断、
支配边界与 mem2reg 中 phi 插入位置计算的基础（见 Appel 书 448 页
算法 19.9/19.10）。

Dominators in graphs are handy informations.

Lengauer and Tarjan developed a fast algorithm to calculate dominators
from a graph.

Algorithm 19.9 and 19.10 as can be found on page 448 of Appel.
"""

import logging

from qcc.midend.graph.digraph import dfs

logger = logging.getLogger("lt")


def calculate_idom(graph, entry, reverse=False):
    """计算直接支配者表的对外入口：创建 LengauerTarjan 实例并对 graph 从 entry 开始求解，返回 {节点: 直接支配者}
    """
    x = LengauerTarjan(reverse)
    return x.compute(graph, entry)


class LengauerTarjan:
    """Lengauer-Tarjan 支配树算法：通过 DFS 编号 + 半支配者 + 并查集路径压缩求直接支配者。

    The lengauer Tarjan algorithm for calculating dominators
    """

    def __init__(self, reverse):
        self._reverse = reverse

        # Filled during dfs:
        self.dfnum = {}  # depth-first number
        self.vertex = []  # Linear list of nodes
        self.parent = {}

        # Filled later:
        self.ancestor = {}
        self.best = {}
        self.semi = {}

    def compute(self, graph, entry):
        """算法主流程：先 DFS 编号，再按逆 DFS 序逐个确定半支配者并收敛到直接支配者，返回 idom 字典
        """
        logger.debug("Computing dominator tree from %s nodes", len(graph))
        bucket = {}

        # Fill maps:
        for n in graph:
            bucket[n] = set()

        # Step 1: calculate semi dominators
        self.dfs(entry)

        idom = {}
        samedom = {}

        # Loop over nodes in reversed dfs order:
        for n in reversed(self.vertex[1:]):
            p = self.parent[n]

            # Determine semi dominator for n:
            s = p
            for v in n.predecessors:
                if self.dfnum[v] <= self.dfnum[n]:
                    s2 = v
                else:
                    s2 = self.semi[self.ancestor_with_lowest_semi(v)]
                assert s2 is not None

                # Select candidate with lowest dfnum:
                if self.dfnum[s2] < self.dfnum[s]:
                    s = s2

            assert n not in self.semi
            self.semi[n] = s
            bucket[s].add(n)

            self.link(p, n)

            for v in bucket[p]:
                y = self.ancestor_with_lowest_semi(v)
                if self.semi[y] is self.semi[v]:
                    idom[v] = p
                else:
                    samedom[v] = y
            bucket[p].clear()

        for n in self.vertex[1:]:
            if n in samedom:
                idom[n] = idom[samedom[n]]
            else:
                assert n in idom
        return idom

    def dfs(self, start_node):
        """深度优先搜索并编号：填充 dfnum（DFS 序）、parent（DFS 树父节点）与 vertex（按序节点表）

        Depth first search nodes
        """
        for dfnum, pair in enumerate(dfs(start_node)):
            parent, node = pair
            assert node not in self.dfnum
            self.dfnum[node] = dfnum
            assert node not in self.parent
            self.parent[node] = parent
            self.vertex.append(node)

    def link(self, p, n):
        """并查集合并：把 p 记为 n 的并查集父节点（ancestor），并初始化 best[n] = n

        Mark p as parent from n
        """
        assert n not in self.ancestor
        self.ancestor[n] = p
        self.best[n] = n

    def ancestor_with_lowest_semi_naive(self, v):
        """朴素实现（O(N^2)）：沿 ancestor 链一路上溯，找 semi 的 dfnum 最小的祖先

        O(N^2) implementation.

        This is a slow algorithm, path compression can be used
        to increase speed.
        """
        u = v
        while v in self.ancestor:
            if self.dfnum[self.semi[v]] < self.dfnum[self.semi[u]]:
                u = v

            # Traverse upwards:
            v = self.ancestor[v]
        return u

    def ancestor_with_lowest_semi(self, v):
        """带路径压缩的 O(log N) 实现（迭代版）：先上溯到最高祖先，再回程压缩路径并更新 best，返回 semi 的 dfnum 最小的祖先

        O(log N) implementation with path compression.

        Rewritten from recursive function to prevent hitting the
        recursion limit for large graphs.
        """
        original_v = v

        # Traverse to highest parent
        path = []
        a = self.ancestor[v]
        while a in self.ancestor:
            path.append((v, a))
            v = a
            a = self.ancestor[v]

        # traverse back to v:
        for v, a in reversed(path):
            b = self.best[a]
            self.ancestor[v] = self.ancestor[a]
            if self.dfnum[self.semi[b]] < self.dfnum[self.semi[self.best[v]]]:
                self.best[v] = b

        return self.best[original_v]

    def ancestor_with_lowest_semi_fast(self, v):
        """带路径压缩的 O(log N) 递归实现（大图可能触碰递归深度限制，已由上面的迭代版替代）

        The O(log N) implementation with path compression.

        This version suffers from a recursion limit for large graphs.
        """
        a = self.ancestor[v]
        if a in self.ancestor:
            b = self.ancestor_with_lowest_semi(a)
            self.ancestor[v] = self.ancestor[a]
            if self.dfnum[self.semi[b]] < self.dfnum[self.semi[self.best[v]]]:
                self.best[v] = b
        return self.best[v]
