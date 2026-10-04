"""CFG（控制流图）整形 pass：清理块级结构垃圾。

功能说明：
    前面的 pass（mem2reg、折叠、CSE、DCE 等）在指令级删删改改之后，会留下两类
    基本块级别的结构垃圾：① 空块——块里只剩一条 Jump；② 可合并的相邻块——块 B
    只有唯一前驱 A 且 A 以无条件 Jump 结尾，两块可以粘成一块。本 pass 分两个阶段
    收拾它们：remove_empty_blocks（跳转穿越，同时同步后继 Phi 的入口）与
    remove_one_preds + glue_blocks（循环粘合到不动点）。

    在 前端 → 中端 → 后端 流水线中的位置：
    属于中端优化阶段的"压轴"工序，由 api.optimize() 调用，排在优化 pass 列表
    最后一位——前 7 个 pass 负责指令级的"减料"，它负责把 CFG 的边角料缝合干净。
    它还与 codegenerator 的"entry 块 + 首块"两段式设计形成"先拆后合"的闭环。

    关键类/函数：
    - CleanPass：继承 FunctionPass 的 pass 主体；
    - remove_empty_blocks：穿越只含一条 Jump 的空块（入口块与自循环块受保护）；
    - remove_one_preds / glue_blocks：把"唯一前驱以无条件 Jump 结尾"的块粘进前驱，
      直到不动点（粘合会改变前驱关系，必须迭代）。

    详见 docs/clean.py.md。
"""

from qcc.midend import ir
from qcc.midend.opt.transform import FunctionPass


class CleanPass(FunctionPass):
    """块级收尾：删除只含一条 Jump 的空块，并粘合只有单个前驱的块。

    Glue blocks together if a block has only one predecessor.


    Remove blocks with a single jump in it.

        .. code::

        jump A
        A:
        jump B
        B:

        Transforms into:

        .. code::

        jump B
        B:

    """

    def on_function(self, function):
        """对单个函数：先删空块，再粘合单前驱块（顺序有意义：穿越空块后
        会暴露新的可粘合块）。"""
        self.remove_empty_blocks(function)
        self.remove_one_preds(function)

    def find_empty_blocks(self, function):
        """第一阶段之"找"：收集第一条指令就是 Jump 的块（即只剩一条跳转的空块）。

        Look for all blocks containing only a jump in it"""
        empty_blocks = []
        for block in function:
            # 入口块永远保留：它可能有特殊约束，改动会影响函数签名处信息
            if block.is_entry:
                continue
            if isinstance(block.first_instruction, ir.Jump):
                empty_blocks.append(block)
        return empty_blocks

    def remove_empty_blocks(self, function):
        """第一阶段之"删"（跳转穿越）：让所有跳向空块的边改跳向它的目标。

        Remove empty basic blocks from function."""
        stat = 0
        for block in self.find_empty_blocks(function):
            predecessors = block.predecessors
            successors = block.successors

            # Do not remove if preceeded by itself:
            # 自循环保护：L: jump L 这种死循环块不能删，删了无限循环就消失了
            if block in predecessors:
                continue

            # Update successor incoming blocks:
            # 后继块 Phi 里标着"从空块来"的入口改成"从空块的所有前驱来"，
            # 保持 CFG 边与 Phi 入口一一对应
            for successor in successors:
                successor.replace_incoming(block, predecessors)

            # Change the target of predecessors:
            # 前驱的 jump/cjmp 目标由空块改指向空块的目标块
            tgt = block.last_instruction.target
            for pred in predecessors:
                pred.change_target(block, tgt)

            # Remove block:
            block.last_instruction.delete()
            function.remove_block(block)
            stat += 1
        if stat > 0:
            self.logger.debug("Removed %s empty blocks", stat)

    def find_single_predecessor_block(self, function):
        """第二阶段之"找"：返回第一个"恰好一个前驱且前驱以无条件 Jump 结尾"的块。

        Find a block with a single predecessor"""
        for block in function:
            preds = block.predecessors

            # Check for amount of predecessors:
            if len(preds) != 1:
                continue

            # We have only one predessor:
            pred = preds[0]

            # Skip loops to self:
            if block is pred:
                continue

            # 前驱必须以无条件 Jump 收尾：若以 CJump 结尾，本块只是它两个出口之一，
            # 粘进去会改变另一条分支的控制流
            if isinstance(pred.last_instruction, ir.Jump):
                return block

    def remove_one_preds(self, function):
        """第二阶段之"粘"：循环粘合单前驱块，直到不动点。

        Remove basic blocks with only one predecessor"""
        change = True
        # 粘合会改变前驱关系（粘完一块可能让下一块变成单前驱），须迭代到不动点
        while change:
            change = False
            block = self.find_single_predecessor_block(function)
            if block is not None:
                (pred,) = block.predecessors  # Unpack 1 block
                self.glue_blocks(pred, block)
                change = True

    def glue_blocks(self, block1, block2):
        """把 block2 的指令并入前驱 block1，并更新 Phi 入口与块列表。

        Glue two blocks together into the first block"""
        self.logger.debug(
            "Inserting %s at the end of %s", block2.name, block1.name
        )

        # Remove the last jump:
        # 删掉 block1 末尾的 jump block2，block1 自然"落空"进 block2 的内容，
        # 这是粘合正确性的关键
        last_jump = block1.last_instruction
        block1.remove_instruction(last_jump)
        last_jump.delete()

        # Copy all instructions to block1:
        for instruction in block2:
            block1.add_instruction(instruction)

        # Replace incoming info:
        for successor in block2.successors:
            successor.replace_incoming(block2, [block1])

        # Remove block from function:
        block1.function.remove_block(block2)
