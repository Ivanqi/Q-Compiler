# -*- coding: utf-8 -*-
"""测试辅助函数（由 ppci/test/helper_util.py 精简而来）。

只保留迁移后测试实际用到的功能；qemu/iverilog 等外部模拟器
相关辅助已被移除。
"""
import shutil
import subprocess
import unittest
from pathlib import Path

test_path = Path(__file__).resolve().parent


def has_gnu_asm(tool_name: str) -> bool:
    """检查系统中是否存在 GNU 汇编器工具（如 arm-none-eabi-as）。"""
    return shutil.which(tool_name) is not None


def gnu_assemble(source, as_args=(), prefix="arm-none-eabi"):
    """用 GNU 汇编器汇编源码，用于对照验证 qcc 汇编器的输出。

    工具缺失时跳过测试（SkipTest），而不是失败。
    """
    as_bin = shutil.which(f"{prefix}-as")
    if as_bin is None:
        raise unittest.SkipTest(f"{prefix}-as not available")

    # Perform the actual assemble:
    cmd = [as_bin, *as_args, "-"]
    p = subprocess.run(
        cmd, input=source.encode("utf8"), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if p.returncode:
        raise RuntimeError(f"GNU as failed: {p.stderr.decode('utf8')}")
    return p.stdout
