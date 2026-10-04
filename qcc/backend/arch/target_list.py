"""Contains a list of instantiated targets.

（迁移说明：由 ppci/arch/target_list.py 裁剪而来，仅保留
需求要求的 arm / riscv / x86_64 三个后端，外加 example 教学架构。）
"""

from functools import lru_cache

from qcc.backend.arch.arm import ArmArch
from qcc.backend.arch.example import ExampleArch
from qcc.backend.arch.riscv import RiscvArch
from qcc.backend.arch.x86_64 import X86_64Arch

target_classes = [
    ArmArch,
    ExampleArch,
    RiscvArch,
    X86_64Arch,
]


target_class_map = {t.name: t for t in target_classes}
target_names = tuple(sorted(target_class_map.keys()))


@lru_cache(maxsize=30)
def create_arch(name, options=None):
    """Get a target architecture by its name. Possibly arch options can be
    given.
    """
    # Create the instance!
    target = target_class_map[name](options=options)
    return target
