"""Control flow graph algorithms.

控制流图（CFG）与支配关系分析模块：ir_function_to_graph 把 IR 函数转成
ControlFlowGraph，后者提供支配者/严格支配者/直接支配者、后支配者、
可达性、循环（calculate_loops）与支配边界（calculate_dominance_frontier）等查询。

直接支配者由 lt.py 的 Lengauer-Tarjan 算法计算（_calculate_dominator_info），
支配树被编号成区间以便 O(1) 判断支配关系；本模块的 CfgInfo 封装在
domtree.py 中，供 opt/mem2reg（按支配边界插入 phi）与 irutils/verify 使用，
relooper 也借助 calculate_loops 还原结构化控制流。

Functions present:

- dominators
- post dominators
- reachability
- dominator tree
- dominance frontier

"""

import logging
from collections import namedtuple

from qcc.midend.graph import lt
from qcc.midend.graph.algorithm.fixed_point_dominator import (
    calculate_immediate_post_dominators,
    calculate_post_dominators,
)

# TODO: this is possibly the third edition of flow graph code.. Merge at will!
from qcc.midend.graph.digraph import DiGraph, DiNode


class DomTreeNode:
    """支配树中的一个节点：包裹 CFG 节点，记录其孩子列表与 DFS 进出区间 interval，用于 O(1) 支配判断。

    A single node in the dominator tree.
    """

    __slots__ = ("node", "children", "interval")

    def __init__(self, node, children, interval):
        self.node = node
        self.children = children
        self.interval = interval

    def below_or_same(self, other):
        """Test if this node is a descendant of this node (or is self)"""
        return (
            other.interval[0] <= self.interval[0]
            and self.interval[1] <= other.interval[1]
        )

    def below(self, other):
        """Test if this node is a descendant of this node."""
        return (
            other.interval[0] < self.interval[0]
            and self.interval[1] < other.interval[1]
        )


Loop = namedtuple("Loop", ["header", "rest"])
logger = logging.getLogger("cfg")


def ir_function_to_graph(ir_function):
    """把 IR 函数转换为控制流图：为每个 Block 建一个 ControlFlowNode，按块间后继加边，并统一连向虚拟出口节点

    Take an ir function and create a cfg of it
    """
    block_map = {}
    cfg = ControlFlowGraph()
    cfg.exit_node = ControlFlowNode(cfg, name=None)

    # Create nodes:
    block_list = []
    worklist = [ir_function.entry]
    while worklist:
        block = worklist.pop(0)
        block_list.append(block)
        node = ControlFlowNode(cfg, name=block.name)
        assert block not in block_map
        block_map[block] = node
        for successor_block in block.successors:
            if successor_block not in block_map:
                if successor_block not in worklist:
                    worklist.append(successor_block)

    cfg.entry_node = block_map[ir_function.entry]

    # Add edges:
    for block in block_list:
        # Fetch node:
        node = block_map[block]

        # Add proper edges:
        if len(block.successors) == 0:
            # Exit or return!
            node.add_edge(cfg.exit_node)
        else:
            for successor_block in block.successors:
                successor_node = block_map[successor_block]
                node.add_edge(successor_node)

            # TODO: hack to store yes and no blocks:
            if len(block.successors) == 2:
                node.yes = block_map[block.last_instruction.lab_yes]
                node.no = block_map[block.last_instruction.lab_no]

    logger.debug(
        "created cfg for %s with %s nodes", ir_function.name, len(cfg)
    )
    return cfg, block_map


class ControlFlowGraph(DiGraph):
    """控制流图：带虚拟 entry_node/exit_node 的有向图，缓存并惰性计算各类支配信息。

    支配信息（idom/支配树/区间编号）由 Lengauer-Tarjan 计算，后支配信息
    用不动点迭代计算；此外还提供可达性（calculate_reach）、循环
    （calculate_loops）与支配边界（calculate_dominance_frontier）。

    Control flow graph.

    Has methods to query properties of the control flow graph and its nodes.

    Such as:
    - Dominators
    - Strict dominators
    - Immediate dominators
    - Post dominators
    - Strict post dominators
    - Immediate post dominators
    - Reachable nodes
    - Loops
    """

    def __init__(self):
        super().__init__()
        self.entry_node = None
        self.exit_node = None

        # Dominator info:
        self._idom = None  # immediate_dominators

        # Post dominator info:
        self._pdom = None  # post dominators
        self._spdom = None  # Strict post dominators
        self._ipdom = None  # post dominators
        self._reach = None  # Reach map
        self.root_tree = None

    def validate(self):
        """Run some sanity checks on the control flow graph"""
        assert self.entry_node
        assert self.exit_node

    def dominates(self, one, other):
        """判断节点 one 是否支配 other：比较二者在支配树上的 DFS 区间，区间包含即支配（O(1)）

        Test whether a node dominates another node.

        To test this, use the dominator tree, check where
        of the other node is below the one node in the tree
        by comparing discovery and finish intervals.
        """
        if self._idom is None:
            self._calculate_dominator_info()
        return self.tree_map[other].below_or_same(self.tree_map[one])

    def strictly_dominates(self, one, other):
        """判断节点 one 是否严格支配 other（区间真包含，即 one != other）

        Test whether a node strictly dominates another node
        """
        if self._idom is None:
            self._calculate_dominator_info()
        return self.tree_map[other].below(self.tree_map[one])

    def post_dominates(self, one, other):
        """判断节点 one 是否后支配 other（沿出口方向），查询后支配集合 _pdom

        Test whether a node post dominates another node
        """
        if self._pdom is None:
            self._calculate_post_dominator_info()
        return one in self._pdom[other]

    def get_immediate_dominator(self, node):
        """获取节点 node 的直接支配者（idom），不存在时返回 None；首次调用会触发支配信息计算

        Retrieve a nodes immediate dominator
        """
        if self._idom is None:
            self._calculate_dominator_info()
        return self._idom.get(node, None)

    def get_immediate_post_dominator(self, node):
        """获取节点 node 的直接后支配者（ipdom），relooper 用它定位 if 分支的汇合点

        Retrieve a nodes immediate post dominator
        """
        if self._ipdom is None:
            self._calculate_post_dominator_info()
        return self._ipdom[node]

    def can_reach(self, one, other):
        """判断节点 one 是否可达 other（惰性调用 calculate_reach 后查表）
        """
        if self._reach is None:
            self.calculate_reach()
        return other in self._reach[one]

    def _calculate_dominator_info(self):
        """计算支配信息：先用 lt.calculate_idom 求出直接支配者，再据此构建支配树并做区间编号

        Calculate dominator information
        """
        self.validate()

        # First calculate the dominator tree:
        self._idom = lt.calculate_idom(self, self.entry_node)
        self._calculate_dominator_tree()

    def _legacy_dom_sets(self):
        """旧版实现：借助支配树先序编号累加求出支配集合 _dom 与严格支配集合 _sdom（现已被区间编号方案取代）
        """
        # Now calculate dominator sets:
        # Old method used the fixed point iteration:

        # These dominator sets have lookup time O(1) but suffer
        # from large memory usage.
        # self._dom = calculate_dominators(self.nodes, self.entry_node)
        self._dom = {}
        for parent, t in pre_order(self.root_tree):
            if parent:
                self._dom[t.node] = {t.node} | self._dom[parent.node]
            else:
                self._dom[t.node] = {t.node}
            logger.debug("Ugh %s, %s", t.node, len(self._dom[t.node]))

        logger.debug("calculate sdom")

        self._sdom = {}
        for node in self.nodes:
            if node not in self._dom:
                self._dom[node] = {node}
                self._sdom[node] = set()
            else:
                self._sdom[node] = self._dom[node] - {node}

        logger.debug("calculate sdom --> DONE")

    def _calculate_dominator_tree(self):
        """根据 idom 关系构建支配树 tree_map（父节点为孩子 idom，根为入口节点），并调用 _number_dominator_tree 编号

        Create a dominator tree.
        """

        self.tree_map = {}
        for node in self.nodes:
            self.tree_map[node] = DomTreeNode(node, [], None)

        # Add all nodes except for the root node into the tree:
        for node in self.nodes:
            idom_node = self.get_immediate_dominator(node)
            if idom_node:
                parent = self.tree_map[idom_node]
                node = self.tree_map[node]
                parent.children.append(node)

        self.root_tree = self.tree_map[self.entry_node]

        self._number_dominator_tree()

    def _number_dominator_tree(self):
        """对支配树做 DFS 并为每个节点分配 (进入时间, 离开时间) 区间，使支配判断可在 O(1) 内完成

        Assign intervals to the dominator tree.

        Very cool idea to check if one node dominates
        another node.

        First, assign an interval to each node in the dominator
        tree, which marks its entrance and exit of depth
        first search of the tree.

        To test dominance, determine the interval of both
        nodes. If the interval of node a falls within the
        interval of node b, b dominates a. This allows for
        constant time dominance checking!
        """

        t = 0

        worklist = [self.root_tree]
        discovered = {}  # when the node was discovered
        while worklist:
            node = worklist[-1]
            if node.node in discovered:
                # finished event
                node.interval = (discovered[node.node], t)
                worklist.pop()
            else:
                # discovery event
                discovered[node.node] = t
                for child in node.children:
                    worklist.append(child)
            t += 1

    def _calculate_post_dominator_info(self):
        """用不动点迭代计算后支配集合 _pdom，并由此推出严格后支配集合 _spdom 与直接后支配者 _ipdom

        Calculate the post dominator sets iteratively.

        Post domination is the same as domination, but then starting at
        the exit node.
        """
        self.validate()

        self._pdom = calculate_post_dominators(self.nodes, self.exit_node)

        # Determine strict post dominators:
        self._spdom = {}
        for node in self.nodes:
            self._spdom[node] = self._pdom[node] - {node}

        self._ipdom = calculate_immediate_post_dominators(
            self.nodes, self._pdom, self._spdom
        )

    def calculate_reach(self):
        """用不动点迭代计算可达性映射 _reach：_reach[n] 为 n 经任意条边可达的所有节点

        Calculate which nodes can reach what other nodes
        """
        self.validate()

        # Initialize reach map:
        self._reach = {}
        for node in self.nodes:
            self._reach[node] = self.successors(node)

        # Run fixed point iteration:
        change = True
        while change:
            change = False
            for node in self.nodes:
                # Fill reachable condition:
                new_reach = set(self._reach[node])  # Take the old reach
                for m in node.successors:
                    new_reach |= self._reach[m]

                if new_reach != self._reach[node]:
                    change = True
                    self._reach[node] = new_reach

    def calculate_loops(self):
        """基于支配信息识别循环：对每条使 header 支配其后继节点的回边（header, node）构造 Loop(header, rest)

        Calculate loops by use of the dominator info
        """
        if self._reach is None:
            self.calculate_reach()

        loops = []
        for node in self.nodes:
            for header in self.successors(node):
                if header.dominates(node):
                    # Back edge!
                    # Determine the other nodes in the loop:
                    loop_nodes = [
                        ln
                        for ln in self._reach[header]
                        if (
                            header.dominates(ln)
                            and ln.can_reach(header)
                            and ln is not header
                        )
                    ]
                    loop = Loop(header=header, rest=loop_nodes)
                    loops.append(loop)
        return loops

    def calculate_dominance_frontier(self):
        """计算支配边界 df：自底向上遍历支配树，用 Cytron 等人的局部规则与向上规则求每个节点的 DF 集合（mem2reg 插入 phi 的依据）

        Calculate the dominance frontier.

        Algorithm from Ron Cytron et al.

        how to calculate the dominance frontier for all nodes using
        the dominator tree.
        """
        if self.root_tree is None:
            self._calculate_dominator_info()

        self.df = {}
        for x in self.bottom_up(self.root_tree):
            # Initialize dominance frontier to the empty set:
            self.df[x] = set()

            # Local rule for dominance frontier:
            for y in self.successors(x):
                if self.get_immediate_dominator(y) != x:
                    self.df[x].add(y)

            # upwards rule:
            for z in self.children(x):
                for y in self.df[z]:
                    if self.get_immediate_dominator(y) != x:
                        self.df[x].add(y)

    def bottom_up(self, tree):
        """Generator that yields all nodes in bottom up way"""
        for t in bottom_up(tree):
            yield t.node

    def children(self, n):
        """产出节点 n 在支配树上的所有孩子（支配边界计算的向上规则会用到）

        Return all children for node n
        """
        tree = self.tree_map[n]
        for c in tree.children:
            yield c.node


def bottom_up_recursive(tree):
    """递归版自底向上遍历：先产出所有孩子，再产出树节点本身（大树上可能栈溢出）

    Generator that yields all nodes in bottom up way
    """
    for c in tree.children:
        yield from bottom_up_recursive(c)
    yield tree


def bottom_up(tree):
    """迭代版自底向上遍历支配树（用显式工作栈替代递归），支配边界计算依赖该顺序

    Generator that yields all nodes in bottom up way
    """
    worklist = [tree]
    visited = set()
    while worklist:
        node = worklist[-1]
        if id(node) in visited:
            worklist.pop()
            yield node
        else:
            visited.add(id(node))
            for child in node.children:
                worklist.append(child)


def pre_order(tree):
    """先序遍历支配树，逐个产出 (父节点, 当前节点) 二元组（根节点的父为 None）

    Traverse tree in pre-order
    """
    worklist = [(None, tree)]
    while worklist:
        parent, node = worklist.pop(0)
        yield parent, node
        for child in node.children:
            worklist.append((node, child))


class ControlFlowNode(DiNode):
    """控制流图中的节点：对应一个 IR 基本块（额外持有 name、之后会被打上 yes/no 分支字段），并把支配查询转发给所属 CFG
    """

    def __init__(self, graph, name=None):
        super().__init__(graph)
        self.name = name

    def dominates(self, other):
        """Test whether this node dominates the other node"""
        return self.graph.dominates(self, other)

    def post_dominates(self, other):
        """Test whether this node post-dominates the other node"""
        return self.graph.post_dominates(self, other)

    def can_reach(self, other):
        """Test if this node can reach the another node"""
        return self.graph.can_reach(self, other)

    def reached(self):
        """Test if this node is reached"""
        return self.graph._reach[self]

    def __repr__(self):
        value = self.name if self.name else id(self)
        return f"CFG-node({value})"
