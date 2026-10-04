"""

本模块是后端架构抽象层的包入口：汇总导出架构抽象基类 Architecture/Frame 与
指令集容器 Isa，并提供 get_arch / get_current_arch 两个工厂函数——按名称
（如 "x86_64"、"riscv"）或当前平台返回目标架构实例。导入子模块时各后端的
指令类会经元类自动注册到各自的 Isa 中。

.. autoclass:: qcc.backend.arch.arch.Architecture
    :members:

.. autoclass:: qcc.backend.arch.arch_info.ArchInfo
    :members:


.. autoclass:: qcc.backend.arch.arch.Frame
    :members:

.. autoclass:: qcc.backend.arch.isa.Isa
    :members:


.. autoclass:: qcc.backend.arch.registers.Register
    :members: is_colored


.. autoclass:: qcc.backend.arch.encoding.Instruction
    :members:

"""

import platform
import sys

from qcc.backend.arch.arch import Architecture, Frame
from qcc.backend.arch.isa import Isa


def get_current_arch():
    """返回当前平台对应的架构实例（Windows 下为 x86_64:wincc，
    Linux/macOS 64 位下为 x86_64），无法识别时返回 None。

    Try to get the architecture for the current platform"""
    if sys.platform.startswith("win"):
        machine = platform.machine()
        if machine == "AMD64":
            return get_arch("x86_64:wincc")
    elif sys.platform in ("linux", "darwin"):
        if platform.architecture()[0] == "64bit":
            return get_arch("x86_64")


def get_arch(arch):
    """按名称获取目标架构实例：arch 既可以是 Architecture 实例，也可以是
    "arch:option1:option2" 形式的字符串（冒号后为架构选项，转交
    target_list.create_arch 创建），否则抛 ValueError。

    Try to return an architecture instance.

    Args:
        arch: can be a string in the form of arch:option1:option2

    .. doctest::

        >>> from qcc.api import get_arch
        >>> arch = get_arch('x86_64')
        >>> arch
        x86_64-arch
        >>> type(arch)
        <class 'qcc.backend.arch.x86_64.arch.X86_64Arch'>
    """
    if isinstance(arch, Architecture):
        return arch
    elif isinstance(arch, str):
        # Horrific import cycle created. TODO: restructure this
        from qcc.backend.arch.target_list import create_arch

        if ":" in arch:
            # We have target with options attached
            parts = arch.split(":")
            return create_arch(parts[0], options=tuple(parts[1:]))
        else:
            return create_arch(arch)
    raise ValueError(f"Invalid architecture {arch}")


__all__ = ["Architecture", "Frame", "Isa", "get_arch", "get_current_arch"]
