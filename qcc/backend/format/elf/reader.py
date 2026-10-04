"""ELF 读取入口：read_elf(f) → ElfFile。

解析 ELF 头、节表与符号表，把文件字节还原成 ElfFile 对象
（file.py）。用于验证我们写出的 ELF 是否合法。Support to process an ELF file."""

from qcc.backend.format.elf.file import ElfFile

# TODO: move some parts from ElfFile to this file.


def read_elf(f):
    """Read an ELF file"""
    return ElfFile.load(f)
