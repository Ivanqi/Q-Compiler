"""ELF 字符串表（.strtab）解析。

节名、符号名在 ELF 里存为字符串表的偏移；本模块提供按偏移
取字符串的辅助。"""
class StringTable:
    def __init__(self):
        self.strtab = bytes([0])
        self.names = {}

    def get_name(self, name):
        if name not in self.names:
            self.names[name] = len(self.strtab)
            self.strtab += name.encode("ascii") + bytes([0])
        return self.names[name]


def elf_hash(name):
    """ELF hashing function.

    See figure 2-15 in the ELF format PDF document.
    """

    h = 0
    for c in name:
        h = (h << 4) + ord(c)
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g
        assert h >= 0

    return h
