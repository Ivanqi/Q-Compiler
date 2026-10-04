# -*- coding: utf-8 -*-
"""③ 后端：IR → 机器指令 → 机器码/目标文件。

- codegen/   代码生成流水线：SelectionDAG 构建 → BURG 树匹配指令选择
             → 指令调度 → 图着色寄存器分配 → 栈帧发射
- arch/      目标架构：arm、riscv、x86_64（另有 example 教学架构）
- binutils/  目标文件（ObjectFile）、汇编器、链接器（linker）
- format/    输出格式：ELF（需求要求）、Intel HEX
"""
