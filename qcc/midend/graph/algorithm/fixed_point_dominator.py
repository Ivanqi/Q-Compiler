"""基于不动点迭代的支配者/后支配者集合计算（朴素数据流算法）。

对每个节点维护支配者集合（初始为全集，入口节点为自身），反复用
“自身 ∪ 各前驱支配者集合的交集”迭代更新，直到不再变化为止；
后支配者计算同理，只是沿出口节点和逆边方向进行。
本模块主要被 cfg.ControlFlowGraph._calculate_post_dominator_info 使用，
其直接支配者函数则与 lt.py 的 Lengauer-Tarjan 算法互为补充。

Fixed-point iteration based dominator set calculation.
"""


def calculate_dominators(nodes, entry_node):
    """用不动点迭代计算所有节点的支配者集合（每个节点被自身及前驱支配者集合的交集支配）

    Calculate the dominator sets iteratively
    """

    # Initialize dominator map:
    _dom = {}
    for node in nodes:
        if node is entry_node:
            _dom[node] = {node}
        else:
            _dom[node] = set(nodes)

    # Run fixed point iteration:
    change = True
    while change:
        change = False
        for node in nodes:
            # A node is dominated by itself and by the intersection of
            # the dominators of its predecessors
            pred_doms = [_dom[p] for p in node.predecessors]
            if pred_doms:
                new_dom_n = set.union({node}, set.intersection(*pred_doms))
                if new_dom_n != _dom[node]:
                    change = True
                    _dom[node] = new_dom_n
    return _dom


def calculate_post_dominators(nodes, exit_node):
    """用不动点迭代计算所有节点的后支配者集合：与支配者定义相同，但从出口节点出发、沿后继方向迭代

    Calculate the post dominator sets iteratively.

    Post domination is the same as domination, but then starting at
    the exit node.
    """

    # Initialize dominator map:
    _pdom = {}
    for node in nodes:
        if node is exit_node:
            _pdom[node] = {node}
        else:
            _pdom[node] = set(nodes)

    # Run fixed point iteration:
    change = True
    while change:
        change = False
        for node in nodes:
            # A node is post dominated by itself and by the intersection
            # of the post dominators of its successors
            succ_pdoms = [_pdom[s] for s in node.successors]
            if succ_pdoms:
                new_pdom_n = set.union({node}, set.intersection(*succ_pdoms))
                if new_pdom_n != _pdom[node]:
                    change = True
                    _pdom[node] = new_pdom_n

    return _pdom


def calculate_immediate_dominators(nodes, _dom, _sdom):
    """由支配者集合与严格支配者集合反推直接支配者：在严格支配者中找支配集恰好等于严格支配集的唯一节点 x

    Determine immediate dominators from dominators and strict dominators.
    """

    _idom = {}

    for node in nodes:
        if _sdom[node]:
            for x in _sdom[node]:
                if _dom[x] == _sdom[node]:
                    # This must be the only definition of idom:
                    assert node not in _idom
                    _idom[node] = x
    return _idom


def calculate_immediate_post_dominators(nodes, _pdom, _spdom):
    """计算所有节点的直接后支配者：在 spdom(x) 中选满足 pdom(n) == spdom(x) 的节点 n；无严格后支配者时置 None

    Calculate immediate post dominators for all nodes.

    Do this by choosing n from spdom(x) such that pdom(n) == spdom(x).
    """

    _ipdom = {}

    for node in nodes:
        if _spdom[node]:
            for x in _spdom[node]:
                if _pdom[x] == _spdom[node]:
                    # This must be the only definition of ipdom:
                    assert node not in _ipdom
                    _ipdom[node] = x
        else:
            # No strict post dominators, hence also no
            # immediate post dominator:
            _ipdom[node] = None
    return _ipdom
