"""目标架构信息载体（ArchInfo）：保存某具体目标的字节序、各 IR 类型的大小
与对齐（TypeInfo）、寄存器类与寄存器别名关系。语言前端与后端都通过它查询
类型布局、按名取寄存器，是架构无关代码与具体目标之间的信息桥梁。

Architecture information carriers.

This module contains classes with information about a specific target.

The information present:
- endianness
- type sizes and alignment
- int size for the machine

"""

import enum
from collections import defaultdict

from qcc.midend import ir
from qcc.utils.collections import OrderedSet


class Endianness(enum.Enum):
    """字节序枚举：LITTLE（小端）或 BIG（大端）。

    Define endianness as little or big"""

    LITTLE = 1
    BIG = 1000


class TypeInfo:
    """单个类型的目标相关信息：大小与对齐字节数。

    Target specific type information"""

    def __init__(self, size, alignment):
        self.size = size
        self.alignment = alignment


class ArchInfo:
    """架构信息集合：类型信息表、字节序、寄存器类，并据此建立寄存器名索引
    与别名映射，供前端和后端查询。

    A collection of information for language frontends"""

    def __init__(
        self,
        type_infos=None,
        endianness=Endianness.LITTLE,
        register_classes=(),
    ):
        self.type_infos = type_infos
        assert isinstance(endianness, Endianness)
        self.endianness = endianness
        self.register_classes = register_classes
        self._registers_by_name = {}

        mapping = {}
        for register_class in self.register_classes:
            for ty in register_class.ir_types:
                if ty in mapping:
                    raise ValueError(f"Duplicate type assignment {ty}")
                mapping[ty] = register_class.typ
            if register_class.registers:
                for register in register_class.registers:
                    self._registers_by_name[register.name] = register
        self.value_classes = mapping

        self.calc_alias()

    def get_register(self, name):
        """按名称取出机器寄存器对象。

        Retrieve the machine register by name."""
        return self._registers_by_name[name]

    def has_register(self, name):
        """判断本架构是否存在指定名称的寄存器。

        Test if this architecture has a register with the given name."""
        return name in self._registers_by_name

    def get_type_info(self, typ):
        """取出给定类型（ir.Typ 或类型名）的 TypeInfo。

        Retrieve type information for the given type"""
        if isinstance(typ, str):
            typ = self.type_infos[typ]
        assert isinstance(typ, ir.Typ)
        return self.type_infos[typ]

    def get_size(self, typ):
        """取给定类型的字节大小。

        Get the size (in bytes) of the given type"""
        return self.get_type_info(typ).size

    def get_alignment(self, typ):
        """取给定类型的对齐字节数。

        Get the alignment for the given type"""
        return self.get_type_info(typ).alignment

    def calc_alias(self):
        """计算完整的寄存器别名总览表（register -> 其自身及所有别名集合）。

        Calculate a complete overview of register aliasing.

        This uses the alias attribute when a register is
        defined.

        For example on x86_64, `rax` aliases with `eax`, `eax` aliases `ax`,
        and `ax` aliases `al`.

        This function creates a map from `al` to `rax` and vice versa.
        """
        alias = defaultdict(OrderedSet)

        for reg_class in self.register_classes:
            if reg_class.registers:
                for register in reg_class.registers:
                    # The trivial alias: itself!
                    alias[register].add(register)
                    for r2 in dfs_alias(register):
                        alias[register].add(r2)
                        alias[r2].add(register)

        self.alias = dict(alias)


def dfs_alias(register):
    """对 register.aliases 做深度优先搜索，递归展开别名的别名。

    Do a depth first search on the aliases member.

    This can be used to find aliases of aliases.
    """
    for r2 in register.aliases:
        yield from dfs_alias(r2)
        yield r2
