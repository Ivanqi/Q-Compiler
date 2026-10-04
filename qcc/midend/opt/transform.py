"""IR 优化 pass 的框架（基类体系）与两个最简单的示例 pass。

功能说明：
    本文件定义优化 pass 的基类继承链：ModulePass（整体模块）→ FunctionPass
    （按函数遍历）→ BlockPass（按基本块遍历）→ InstructionPass（按指令遍历）。
    这是模板方法模式：pass 作者只需覆盖最细粒度的方法（如 on_instruction），
    "如何遍历整个模块"由框架代劳；所有 pass 的统一入口是 opt_pass.run(ir_module)。

    在 前端 → 中端 → 后端 流水线中的位置：
    位于中端优化阶段的核心，qcc/midend/opt/ 下所有 pass 都继承这里的基类；
    由 api.optimize() 按列表顺序依次调用（mem2reg 之后、常量折叠之前是本文件的
    RemoveAddZeroPass，倒数第二个是负责收尸的 DeleteUnusedInstructionsPass）。

    关键类/函数：
    - ModulePass / FunctionPass / BlockPass / InstructionPass：四级 pass 基类；
    - RemoveAddZeroPass：把 x+0、0+x、x*1 化简为 x 本身；
    - DeleteUnusedInstructionsPass：删除没人使用且无副作用的指令（DCE）。

    详见 docs/transform.py.md。

Transformation to optimize IR-code"""

import abc
import logging

from qcc.midend import ir


class ModulePass(abc.ABC):
    """所有优化 pass 的根基类：统一入口 run(ir_module)，原地修改模块。

    Base class of all optimizing passes.

    Subclass this class to implement your own optimization pass.
    """

    def __init__(self):
        # 每个 pass 实例配一个以类名命名的 logger，便于区分日志来源
        self.logger = logging.getLogger(str(self.__class__.__name__))

    def __repr__(self):
        return self.__class__.__name__

    def prepare(self):
        """钩子方法：子类可在正式运行前初始化状态（默认什么也不做）。"""
        pass

    @abc.abstractmethod
    def run(self, ir_module):  # pragma: no cover
        """在此模块上运行本 pass（抽象方法，子类实现）。

        Run this pass over a module"""
        raise NotImplementedError()


class FunctionPass(ModulePass):
    """按函数遍历的 pass 基类：自动对模块中每个函数调用 on_function。

    Base pass that loops over all functions in a module"""

    def run(self, ir_module: ir.Module):
        """pass 主入口：遍历模块中每个函数并调用 on_function。

        Main entry point for the pass"""
        self.prepare()
        # 暴露模块调试信息供子类使用（如 mem2reg 迁移 alloc 的调试映射）
        self.debug_db = ir_module.debug_db
        assert isinstance(ir_module, ir.Module)
        for function in ir_module.functions:
            self.on_function(function)
        self.debug_db = None

    @abc.abstractmethod
    def on_function(self, function: ir.SubRoutine):  # pragma: no cover
        """子类覆盖此方法，实现"对单个函数做什么"（抽象方法）。

        Override this virtual method"""
        raise NotImplementedError()


class BlockPass(FunctionPass):
    """按基本块遍历的 pass 基类：对函数中每个块调用 on_block。

    Base pass that loops over all blocks"""

    def on_function(self, function):
        """遍历函数中的每个基本块。

        Loops over each block in the function"""
        for block in function.blocks:
            self.on_block(block)

    @abc.abstractmethod
    def on_block(self, block: ir.Block):  # pragma: no cover
        """子类覆盖此方法，实现"对单个基本块做什么"（抽象方法）。

        Override this virtual method"""
        raise NotImplementedError()


class InstructionPass(BlockPass):
    """按指令遍历的 pass 基类：对块中每条指令调用 on_instruction。

    Base pass that loops over all instructions"""

    def on_block(self, block):
        """遍历块中的每条指令。

        Loops over each instruction in the block"""
        for instruction in block:
            self.on_instruction(instruction)

    @abc.abstractmethod
    def on_instruction(self, instruction):  # pragma: no cover
        """子类覆盖此方法，实现"对单条指令做什么"（抽象方法）。

        Override this virtual method"""
        raise NotImplementedError()


class RemoveAddZeroPass(InstructionPass):
    """代数恒等化简：把 x+0、0+x 替换为 x，把 x*1 替换为 x。

    Replace additions with zero with the value itself.
    Replace multiplication by 1 with value itself.
    """

    def on_instruction(self, instruction):
        # 用精确类型匹配（is，而非 isinstance）：只化简标准 Binop/Const，
        # 将来若出现子类（如带快速数学标志的 Binop）不会被误伤
        if type(instruction) is ir.Binop:
            if instruction.operation == "+":
                if (
                    type(instruction.b) is ir.Const
                    and instruction.b.value == 0
                ):
                    instruction.replace_by(instruction.a)
                elif (
                    type(instruction.a) is ir.Const
                    and instruction.a.value == 0
                ):
                    instruction.replace_by(instruction.b)
            elif instruction.operation == "*":
                if (
                    type(instruction.b) is ir.Const
                    and instruction.b.value == 1
                ):
                    instruction.replace_by(instruction.a)


class DeleteUnusedInstructionsPass(BlockPass):
    """死代码删除（DCE）：删掉块内没人使用且无副作用的指令。

    Remove unused variables from a block"""

    def on_block(self, block):
        # FunctionCall 例外：调用可能有副作用，即使返回值没人用也必须保留
        # （C 里的 foo(); 就生成这样的 IR，调用本身不能被优化掉）
        unused_instructions = [
            i
            for i in block
            if (
                isinstance(i, ir.Value)
                and (not isinstance(i, ir.FunctionCall))
                and (not i.is_used)
            )
        ]
        count = len(unused_instructions)
        for instruction in unused_instructions:
            instruction.remove_from_block()
        if count > 0:
            self.logger.debug("Deleted %i unused instructions", count)
