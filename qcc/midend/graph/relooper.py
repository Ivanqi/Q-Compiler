"""Rverse engineer the structured control flow of a program.

Relooper 算法：把控制流图“逆向还原”为结构化高层控制结构（顺序块 / if-else / loop），
用于 C 前端：frontend/c/synthesize.py 调用 find_structure，把 IR 的块图重新组合成
while/if 等可生成 C 源码的结构（StructureDetector.detect 是实际实现）。
判定思路：入口块若是循环头 → 循环；两个后继 → if-else；单后继 → 直行代码；
借助 CFG 的循环信息、直接后支配者与标记集合识别 break/continue 与汇合点。

Possible algorithms:


References:

- relooper
  https://github.com/kripken/Relooper/blob/master/paper.pdf

[Cifuentes1998]_

[Baker1977]_


A hint might be "Hammock graphs" which are single entry, single exit graphs.
They might be the join point between dominator and post dominator nodes.

The algorithm for finding a program structure is as following:

- Create a control flow graph from the ir-function.
- Find loops in the control flow graph
- Now start with entry node, and check if this node is:
   - a start of a loop
   - an if statement with two outgoing control flow paths
   - straight line code
"""

import logging

from qcc.midend.graph.cfg import Loop, ir_function_to_graph

# from ..utils.collections import OrderedSet, OrderedDict


def find_structure(ir_function):
    """对 IR 函数做结构还原：建 CFG → StructureDetector.detect 得到形状树，并返回形状节点到原 IR 块的反查表 rmap

    Figure out the block structure of

    Args:
        ir_function: an `ir.SubRoutine` containing a soup-of-blocks.

    Returns:
        A control flow tree structure.
    """
    logger = logging.getLogger("structure-detection")
    logger.debug("finding structure for %s", ir_function)
    cfg, block_map = ir_function_to_graph(ir_function)
    sd = StructureDetector()
    shape = sd.detect(cfg)
    rmap = {c: b for b, c in block_map.items()}
    # print()
    # print_shape(shape)
    # print()
    return shape, rmap


def print_shape(shape, indent=0, file=None):
    """调试辅助：按缩进递归打印形状树（code/if-then/else/loop 等），便于观察结构还原结果
    """
    if isinstance(shape, BasicShape):
        print("   " * indent + "code:", str(shape.content), file=file)
    elif isinstance(shape, (BreakShape, ContinueShape)):
        print("   " * indent + str(shape), file=file)
    elif isinstance(shape, SequenceShape):
        for sub_shape in shape.shapes:
            print_shape(sub_shape, indent=indent + 1, file=file)
    elif isinstance(shape, IfShape):
        print("   " * indent + "if-then", shape.content, file=file)
        if shape.yes_shape is not None:
            print_shape(shape.yes_shape, indent=indent + 1, file=file)

        if shape.no_shape is not None:
            print("   " * indent + "else", file=file)
            print_shape(shape.no_shape, indent=indent + 1, file=file)
        print("   " * indent + "end-if", file=file)
    elif isinstance(shape, LoopShape):
        print("   " * indent + "loop", file=file)
        print_shape(shape.body, indent=indent + 1, file=file)
        print("   " * indent + "end-loop", file=file)
    elif shape is None:
        pass
    else:  # pragma: no cover
        raise NotImplementedError(str(shape))


class StructureDetector:
    """结构化控制流检测器：以 CFG 与循环信息为基础，从入口块递归推导出形状树，并维护已标记集合与循环栈以识别 break/continue。
    """

    logger = logging.getLogger("structure-detector")

    def detect(self, cfg):
        """结构还原入口：先算循环、压入包含整个 CFG 的顶层伪循环，再从入口节点开始 make_shape

        Find structure in control flow graph
        """
        self.cfg = cfg
        # self.cfg.calculate_dominator_tree()

        # Loop info:
        self.loops = self.cfg.calculate_loops()
        self.loop_headers = {loop.header: loop for loop in self.loops}

        self.marked = {self.cfg.exit_node}
        self.follow_stack = [self.cfg.exit_node]
        top_loop = Loop(
            header=self.cfg.entry_node,
            rest=(self.cfg.nodes - {self.cfg.entry_node}),
        )

        # Stack of loops with follow nodes
        self.loop_stack = [(top_loop, None)]
        self.shapes = []
        shape = self.make_shape(self.cfg.entry_node)
        return shape

    def make_shape(self, entry):
        """核心递归：按入口块的后继个数判定结构 —— 0 个后继（出口）返回 None，循环头生成 LoopShape，单后继为直行代码，双后继用直接后支配者定位汇合点并生成 IfShape

        Given a set of blocks and an entry block, determine the shape
        """

        # Decide between loop, if-else or straight line code:
        if entry is self.cfg.exit_node:
            shape = None
        elif self.is_inactive_header(entry):
            # Loop found!
            loop = self.loop_headers[entry]
            follow_up = self.follows_loop(loop)
            # assert follow_up
            self.loop_stack.append((loop, follow_up))
            self.marked.add(entry)
            self.marked.add(follow_up)

            self.logger.debug("--> Loop: %s break to %s", entry, follow_up)
            s1 = self.make_shape(entry)
            self.logger.debug("--> end loop")

            # Cleanup stacks:
            self.loop_stack.pop(-1)

            # Create shape:
            shape = LoopShape(s1)
            if follow_up:
                s3 = self.make_shape(follow_up)
                shape = SequenceShape([shape, s3])
        elif len(entry.successors) == 1:
            # Simple straight ahead:
            self.logger.debug("--> code: %s", entry)
            (follow_up,) = entry.successors
            shape = BasicShape(entry)
            s2 = self.test(follow_up)
            if s2:
                shape = SequenceShape([shape, s2])
        elif len(entry.successors) == 2:
            # If statement!
            merger = self.cfg.get_immediate_post_dominator(entry)
            if merger in self.loop_stack[-1][0].rest:
                follow_up = merger
                self.marked.add(follow_up)
            else:
                follow_up = None
            yes, no = entry.yes, entry.no  # TODO: major hack for yes and no
            self.logger.debug("--> code %s", entry)
            self.logger.debug("--> if (based on) %s", entry)
            yes_shape = self.test(yes)
            self.logger.debug("--> else")
            no_shape = self.test(no)
            self.logger.debug("--> end if %s", entry)
            shape = IfShape(entry, yes_shape, no_shape)
            if follow_up:  # follow_up in same_loop:
                s2 = self.make_shape(follow_up)
                shape = SequenceShape([shape, s2])
        else:  # pragma: no cover
            raise NotImplementedError(str(entry))

        return shape

    def test(self, node):
        """检查节点是否已被标记：若指向当前循环头则生成 ContinueShape，指向循环后继节点则生成 BreakShape，否则递归 make_shape

        Check if a node is marked, or else shape it!
        """
        if node in self.marked:
            # Break or continue!
            if node is self.loop_stack[-1][0].header:
                return ContinueShape(0)
            elif node is self.loop_stack[-1][1]:
                return BreakShape(0)
            else:
                return None
        else:
            return self.make_shape(node)

    def is_inactive_header(self, block):
        """判断 block 是否是“尚未激活”的循环头：它属于某个循环，但该循环未在当前的 loop_stack 中（即需要在此新开一个循环结构）
        """
        if block in self.loop_headers:
            active_headers = {loop[0].header for loop in self.loop_stack}
            return block not in active_headers
        else:
            return False

    def follows_loop(self, loop):
        """确定循环的 follow 节点：收集从循环内跳到循环外（且不被循环头严格支配）的后继；多于一个则报错，说明无法结构化

        Determine the node that follows this loop
        """
        reachable_outside_loop = set()
        all_loop_nodes = [loop.header] + loop.rest
        for node in all_loop_nodes:
            for s in node.successors:
                if s not in all_loop_nodes:
                    if not self.cfg.strictly_dominates(loop.header, s):
                        reachable_outside_loop.add(s)

        if reachable_outside_loop:
            if len(reachable_outside_loop) != 1:
                reachables = ", ".join(map(str, reachable_outside_loop))
                raise ValueError(
                    f"Loop followed by more then one node: {reachables}"
                )
            return list(reachable_outside_loop)[0]


class Relooper:
    """Relooper 算法本体（尚未实现，当前结构还原功能实际由 StructureDetector 完成）

    Implementation of the relooper algorithm
    """

    # TODO: implement the relooper algorithm
    pass


class Shape:
    """控制流形状的基类：结构还原结果的抽象表示。

    A control flow shape.
    """

    def __init__(self):
        pass


class BasicShape(Shape):
    """基本块形状：表示一段直行的代码（content 为对应的 CFG 节点/IR 块）。
    """

    def __init__(self, content):
        super().__init__()
        self.content = content


class BreakShape(Shape):
    """break 形状：跳出第 level 层循环。
    """

    def __init__(self, level):
        super().__init__()
        self.level = level

    def __repr__(self):
        return f"Break-shape {self.level}"


class ContinueShape(Shape):
    """continue 形状：继续第 level 层循环的下一轮迭代。
    """

    def __init__(self, level):
        super().__init__()
        self.level = level

    def __repr__(self):
        return f"Continue-shape {self.level}"


class SequenceShape(Shape):
    """顺序形状：依次包含多个子形状（shapes 列表）。
    """

    def __init__(self, shapes):
        super().__init__()
        self.shapes = shapes

    def __repr__(self):
        return f"Sequence of {len(self.shapes)}"


class LoopShape(Shape):
    """循环形状：body 为循环体形状。

    Loop shape
    """

    def __init__(self, body):
        super().__init__()
        self.body = body

    def __repr__(self):
        return "Loop-shape"


class IfShape(Shape):
    """if 语句形状：content 为条件节点，yes_shape/no_shape 分别为真/假分支（可为 None）。

    If statement
    """

    def __init__(self, content, yes_shape, no_shape):
        super().__init__()
        self.content = content
        self.yes_shape = yes_shape
        self.no_shape = no_shape


class MultipleShape(Shape):
    """多分支形状：可能对应 switch 语句（目前未使用）。

    Can be a switch statement?
    """

    pass
