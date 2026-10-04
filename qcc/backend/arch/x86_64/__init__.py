"""
x86_64 后端包入口：对外仅导出架构类 X86_64Arch。

包内汇聚 x86-64 后端的全部架构描述：指令字典与指令选择模式（instructions.py）、
SSE/SSE2 标量浮点指令（sse2_instructions.py）、x87 协处理器指令（x87_instructions.py）、
寄存器与寄存器类定义（registers.py）、ELF 重定位常量（elf.py）。

X86_64Arch 汇总这些部件后向编译流水线提供：寄存器分配所需的 register_classes、
调用约定与栈帧生成（gen_prologue/gen_epilogue）、编码与重定位钩子（get_reloc_type，
配合 elf.py 的 ELF 重定位编号写目标文件）。

For a good list of op codes, checkout:

http://ref.x86asm.net/coder64.html

For an online assembler, checkout:

https://defuse.ca/online-x86-assembler.htm

Linux
~~~~~

For a good list of linux system calls, refer:

http://blog.rchapman.org/post/36801038863/linux-system-call-table-for-x86-64

"""

from qcc.backend.arch.x86_64.arch import X86_64Arch

# 本包的公共接口：只暴露 x86_64 架构类
__all__ = ["X86_64Arch"]
