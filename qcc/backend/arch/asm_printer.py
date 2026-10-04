"""汇编打印器模块：定义 AsmPrinter 基类，供各后端继承以定制指令的汇编文本
渲染（默认 print_instruction 直接返回 str(instruction)）；Architecture
实例在初始化时持有一个 AsmPrinter 用于输出汇编代码。
"""


class AsmPrinter:
    """汇编打印器基类：重写 print_instruction 以自定义汇编代码的正确渲染。

    Subclass this class to create render assembly code correctly"""

    def print_instruction(self, instruction):
        return str(instruction)
