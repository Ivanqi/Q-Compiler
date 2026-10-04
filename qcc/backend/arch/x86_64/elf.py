"""x86_64 ELF 目标文件重定位常量与映射表。

定义 System V ABI 规定的 x86-64 重定位类型编号（R_X86_64_*）：S=符号值、A=加数、
P=重定位字段自身的地址，各常量后的算式就是链接器计算补丁值的公式。
elf_reloc_mapping 把后端内部使用的重定位名（rel32/abs64/abs32/absaddr64）翻译成
ELF 类型号：isa 里注册的重定位类（如 Rel32JmpRelocation/Abs64Relocation）在编码时
记录重定位名，链接器写 .rela 节、执行重定位时按此表查号并回填。

ELF format support code."""

# TODO: this must be placed in arch specific file.

# x86-64 ELF 重定位类型编号（取自 System V ABI）
R_X86_64_NONE = 0
R_X86_64_64 = 1  # S + A
R_X86_64_PC32 = 2  # S + A - P
R_X86_64_GOT32 = 3
R_X86_64_PLT32 = 4
R_X86_64_COPY = 5
R_X86_64_GLOB_DAT = 6
R_X86_64_JUMP_SLOT = 7
R_X86_64_RELATIVE = 8
R_X86_64_GOTPCREL = 9
R_X86_64_32 = 10
R_X86_64_32S = 11
R_X86_64_16 = 12
R_X86_64_PC16 = 13
R_X86_64_8 = 14
R_X86_64_PC8 = 15
R_X86_64_DTPMOD64 = 16
R_X86_64_DTPOFF64 = 17
R_X86_64_TPOFF64 = 18
R_X86_64_TLSGD = 19
R_X86_64_TLSLD = 20
R_X86_64_DTPOFF32 = 21
R_X86_64_GOTTPOFF = 22
R_X86_64_TPOFF32 = 23
R_X86_64_PC64 = 24
R_X86_64_GOTOFF64 = 25
R_X86_64_GOTPC32 = 26
R_X86_64_SIZE32 = 32
R_X86_64_SIZE64 = 33
R_X86_64_GOTPC32_TLSDESC = 34
R_X86_64_TLSDESC_CALL = 35
R_X86_64_TLSDESC = 36
R_X86_64_IRELATIVE = 37

# 后端重定位名 -> ELF 重定位类型号（链接器写目标文件、执行重定位时查此表）
elf_reloc_mapping = {
    "rel32": R_X86_64_PC32,
    "abs64": R_X86_64_64,
    "abs32": R_X86_64_32,
    "absaddr64": R_X86_64_64,
}
