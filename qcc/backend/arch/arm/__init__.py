"""ARM 后端包入口。

汇集 ARM 架构的全部模块：arch.py（ArmArch 架构实现与两套汇编器）、
arm_instructions.py 与 thumb_instructions.py（ARM / Thumb 指令定义及
@isa.pattern 指令选择模式）、registers.py（寄存器定义）、isa.py（Isa 容器与
机器码位域 Token）、arm_relocations.py 与 thumb_relocations.py（链接期重定位）。
对外只导出 ArmArch；实例化时通过 options 里的 "thumb" 开关在 ARM 与 Thumb
双模式间切换（指令集、汇编器、帧指针、寄存器类别随之不同）。

Arm machine specifics. The arm target has several options:

* thumb: enable thumb mode, emits thumb code

"""

from qcc.backend.arch.arm.arch import ArmArch

__all__ = ["ArmArch"]
