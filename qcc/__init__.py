# -*- coding: utf-8 -*-
"""Q-Compiler：从 ppci 迁移而来的自包含 C 编译器。

流水线（需求 3）：C 源码 → frontend（前端）→ midend（中端：IR+优化）
→ backend（后端：指令选择/寄存器分配 → arm/risc-v/x86_64 机器码）
→ ELF/hex 目标文件（需求 2）。

子包：
- qcc.frontend  ① C 语言前端（lexer/preprocessor/parser/semantics/codegenerator）
- qcc.midend   ② 中间表示 ir.py 与优化 pass（opt/）
- qcc.backend  ③ 代码生成（codegen/）、目标架构（arch/）、
                 二进制工具（binutils/）与输出格式（format/，含 ELF）
- qcc.utils       公共工具
- qcc.api         总入口：cc()/optimize()/ir_to_object()/link()/objcopy()
"""

__version_info__ = (0, 5, 9)
__version__ = ".".join(map(str, __version_info__))
