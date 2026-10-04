"""Graph algorithms module.

图数据结构与图算法基础包：汇总并导出无向图 Graph/Node、
有向图 DiGraph/DiNode，以及支持临时屏蔽节点的 MaskableGraph。

被中端优化（opt/mem2reg 的 phi 插入）、IR 校验（irutils/verify）与
后端代码生成（codegen/flowgraph 活跃变量流图、codegen/interferencegraph
的寄存器分配干涉图）共同复用，是支配树、支配边界等分析的底座。

Graph algorithms module.
"""

from qcc.midend.graph.digraph import DiGraph, DiNode
from qcc.midend.graph.graph import Graph, Node
from qcc.midend.graph.maskable_graph import MaskableGraph

__all__ = ("Graph", "Node", "DiGraph", "DiNode", "MaskableGraph")
