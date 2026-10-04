class State:
    """BURG 生成匹配器的运行时基类（BaseMatcher/State）。

burg.py 生成的 Matcher 类继承 BaseMatcher，靠 State（当前节点、
其子节点的匹配状态）做 label/select 两阶段的动态规划。
平时无需直接使用——由 TreeSelector（instructionselector）调用。State used to label tree nodes"""

    __slots__ = ("labels",)

    def __init__(self):
        self.labels = {}

    def has_goal(self, goal):
        return goal in self.labels

    def get_cost(self, goal):
        return self.labels[goal][0]

    def get_rule(self, goal):
        return self.labels[goal][1]

    def set_cost(self, goal, cost, rule):
        """
        Mark that this tree can be matched with a rule for a certain cost
        """
        if self.has_goal(goal):
            if self.get_cost(goal) > cost:
                self.labels[goal] = (cost, rule)
        else:
            self.labels[goal] = (cost, rule)


class BaseMatcher:
    """Base class for matcher objects."""

    def kids(self, tree, rule):
        return self.kid_functions[rule](tree)

    def nts(self, rule):
        return self.nts_map[rule]

    def burm_label(self, tree):
        """Label all nodes in the tree bottom up"""
        for c in tree.children:
            self.burm_label(c)
        self.burm_state(tree)

    def apply_rules(self, tree, goal):
        rule = tree.state.get_rule(goal)
        results = [
            self.apply_rules(kid_tree, kid_goal)
            for kid_tree, kid_goal in zip(
                self.kids(tree, rule), self.nts(rule)
            )
        ]
        return self.pat_f[rule](tree, *results)
