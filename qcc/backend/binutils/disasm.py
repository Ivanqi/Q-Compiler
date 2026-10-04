"""反汇编器基类（Disassembler）。

api.disasm() 用它把二进制数据按架构的指令解码规则还原成文本，
便于人工检查生成的机器码。Contains disassembler stuff."""

from qcc.backend.arch.data_instructions import DByte


class Disassembler:
    """Base disassembler for some architecture"""

    def __init__(self, arch):
        self.arch = arch

    def disasm(self, data, outs, address=0):
        """Disassemble data into an instruction stream"""
        # TODO: implement this!

        # The trial and error method, will be slow as a snail:
        # for instruction in self.arch.isa.instructions:
        #    for size in instruction.sizes():
        #        part = data[:size]
        #        try:
        #            print(instruction, part, size)
        #            i = instruction.decode(part)
        #            print(i)
        #        except ValueError:
        #            pass

        # For now, all is bytes!
        for byte in data:
            ins = DByte(byte)
            ins.address = address
            outs.emit(ins)
            address += len(ins.encode())

    def take_one(self):
        pass
