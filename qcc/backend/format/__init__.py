"""输出格式包：ELF（需求2 要求支持）与 Intel HEX。

compile → link → objcopy 的最后一站：把 ObjectFile 落成
elf/bin/hex 等具体文件格式。Package for different file formats"""

from qcc.backend.format.elf import ElfFile
from qcc.backend.format.hexfile import HexFile

__all__ = ("HexFile", "ElfFile")
