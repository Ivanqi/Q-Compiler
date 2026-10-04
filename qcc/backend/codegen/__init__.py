"""后端代码生成包：IR → 机器指令流水线。

需求3"后端"的核心：irdag 建图 → burg 树匹配 → 指令选择 →
调度 → 图着色寄存器分配 → 栈帧发射（codegen.CodeGenerator）。"""
from qcc.backend.codegen.codegen import CodeGenerator

__all__ = ["CodeGenerator"]
