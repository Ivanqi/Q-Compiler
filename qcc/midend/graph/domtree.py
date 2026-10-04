"""IR 函数 → 控制流图支配信息的适配层。

把 qcc.midend.ir 的 Function 交给 cfg.ir_function_to_graph 建成
ControlFlowGraph，计算支配边界（DF），并在 IR 的 Block 与图节点之间
维护双向映射（_block_map / _node_map）。

被 midend/opt/mem2reg.py（在支配边界对应的块插入 phi 指令）与
midend/irutils/verify.py（校验块的支配关系）使用。
"""

from qcc.midend.graph.cfg import ir_function_to_graph


class CfgInfo:
    """IR 函数的 CFG 信息封装：持有 IR 函数、对应控制流图以及“块 → 节点”的双向映射，并预先算好支配边界 df。

    Calculate control flow graph info, such as dominators
    dominator tree and dominance frontier
    """

    def __init__(self, function):
        # Store ir related info:
        self.function = function
        self.cfg, self._block_map = ir_function_to_graph(function)
        self._node_map = {n: b for b, n in self._block_map.items()}

        self._calculate_df()

    def __repr__(self):
        return f"CfgInfo(function={self.function})"

    def get_node(self, block):
        """由 IR 基本块取得对应的控制流图节点
        """
        return self._block_map[block]

    def get_block(self, node):
        """由控制流图节点反查对应的 IR 基本块
        """
        return self._node_map[node]

    def has_block(self, node):
        """判断某个图节点是否对应一个 IR 基本块（虚拟出口节点没有对应块，返回 False）
        """
        return node in self._node_map

    def _calculate_df(self):
        """调用 CFG 计算支配边界，并把“图节点 → 图节点”的 df 映射翻译成“IR 块 → IR 块”的 df 映射
        """
        self.cfg.calculate_dominance_frontier()
        self.df = {
            self._node_map[n]: {
                self.get_block(o) for o in m if self.has_block(o)
            }
            for n, m in self.cfg.df.items()
            if self.has_block(n)
        }
