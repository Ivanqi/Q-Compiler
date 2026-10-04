"""RISC-V 汇编打印器：把指令对象渲染成 GNU 风格的 RISC-V 汇编文本。

RiscvAsmPrinter 继承通用的 AsmPrinter，供 RiscvArch.asm_printer 使用（见 arch.py），
也用于 objdump 之类的"从指令对象反查汇编"场景。每条指令的助记符与操作数形态来自
instructions.py 等文件中指令类自身的 Syntax 定义（str(instruction) 即汇编文本），
本类只额外处理通用伪指令 SectionInstruction（打印为 .section 指示字），
是"机器码/指令对象 → 可读汇编"这一环的终点。

"""

from qcc.backend.arch.asm_printer import AsmPrinter
from qcc.backend.arch.generic_instructions import SectionInstruction


class RiscvAsmPrinter(AsmPrinter):
    """RISC-V 专用汇编打印器。

    Riscv specific assembly printer"""

    def print_instruction(self, instruction):
        """打印单条指令：section 伪指令特判，其余直接取其语法文本。"""
        if isinstance(instruction, SectionInstruction):
            return f".section {instruction.name}"
        else:
            return str(instruction)
