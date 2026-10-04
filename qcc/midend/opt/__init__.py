"""中端优化 pass 包的导出入口。

功能说明：
    统一导出 qcc/midend/opt/ 下的各个优化 pass 与 pass 基类，供 api.optimize()
    等调用方按名字使用；实际的 pass 实现分散在同目录各模块中（mem2reg、cse、
    clean、constantfolding、transform、load_after_store、tailcall、cjmp）。
"""

from qcc.midend.opt.clean import CleanPass
from qcc.midend.opt.constantfolding import ConstantFolder
from qcc.midend.opt.cse import CommonSubexpressionEliminationPass
from qcc.midend.opt.load_after_store import LoadAfterStorePass
from qcc.midend.opt.mem2reg import Mem2RegPromotor
from qcc.midend.opt.transform import (
    BlockPass,
    DeleteUnusedInstructionsPass,
    FunctionPass,
    InstructionPass,
    ModulePass,
    RemoveAddZeroPass,
)

__all__ = [
    "ModulePass",
    "FunctionPass",
    "BlockPass",
    "InstructionPass",
    "CleanPass",
    "CommonSubexpressionEliminationPass",
    "ConstantFolder",
    "DeleteUnusedInstructionsPass",
    "LoadAfterStorePass",
    "Mem2RegPromotor",
    "RemoveAddZeroPass",
]
