"""RISC-V 后端包入口：对外导出该目标架构的顶层对象 RiscvArch。

本包是 RISC-V（RV32）目标的后端实现集合：arch.py 给出架构描述（RiscvArch：指令选择
模式注册、寄存器分配所需的寄存器类、函数序言/尾声生成），instructions.py 与
rvc/rvf/rvfx_instructions.py 定义各扩展的机器指令、encode 编码与 isa.pattern 指令选择
模式，registers.py 定义物理寄存器与寄存器类，relocations.py 与 rvc_relocations.py 提供
链接期重定位，tokens.py 描述 32/16 位指令字的位域布局，asm_printer.py 负责汇编打印。
使用方（目标注册表、api 层）通常只需 from qcc.backend.arch.riscv import RiscvArch。

See also: http://riscv.org

Contributed by Michael.

"""

from qcc.backend.arch.riscv.arch import RiscvArch

# 本包唯一对外公开的名字：架构描述类
__all__ = ["RiscvArch"]
