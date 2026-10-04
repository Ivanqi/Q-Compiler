"""条件跳转静态求值 pass：当 cjmp 的两个操作数都是常量时，直接改成无条件跳转。

功能说明：
    若条件跳转的比较双方都是编译期常量，就在编译期判断条件真假，把 cjmp 替换为
    跳向对应分支的 Jump（另一分支若因此不可达，由后续清理接手）。
    属于中端优化阶段，仅在优化等级为 "3" 时由 api.optimize() 追加到流水线末尾。

    关键类：CJumpPass（继承 InstructionPass，逐条指令匹配 ir.CJump）。
"""

import operator

from qcc.midend import ir
from qcc.midend.opt.transform import InstructionPass


class CJumpPass(InstructionPass):
    """把操作数全为常量的条件跳转折叠成无条件跳转。"""

    def on_instruction(self, instruction):
        """若指令是常量比较的 CJump，按比较结果替换为跳向 yes/no 分支的 Jump。"""
        if (
            isinstance(instruction, ir.CJump)
            and isinstance(instruction.a, ir.Const)
            and isinstance(instruction.b, ir.Const)
        ):
            a = instruction.a.value
            b = instruction.b.value
            mp = {
                "==": operator.eq,
                "<": operator.lt,
                ">": operator.gt,
                ">=": operator.ge,
                "<=": operator.le,
                "!=": operator.ne,
            }
            if mp[instruction.cond](a, b):
                label = instruction.lab_yes
            else:
                label = instruction.lab_no
            block = instruction.block
            block.remove_instruction(instruction)
            block.add_instruction(ir.Jump(label))
            instruction.delete()
