# -*- coding: utf-8 -*-
"""M5：把 ppci 相关测试复制到 tests/ 并改写 import（ppci.* → qcc.*）。

用法：python3 tools/migrate_tests.py
"""
import os
import re
import shutil
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(REPO, "ppci", "test")
DST = os.path.join(REPO, "tests")

TEST_MAP = {
    # 前端
    "lang/c/test_c.py": "frontend/test_c.py",
    "lang/c/test_lexer.py": "frontend/test_lexer.py",
    "lang/c/test_parser.py": "frontend/test_parser.py",
    "lang/c/test_cpreprocessor.py": "frontend/test_cpreprocessor.py",
    "lang/c/test_synthesis.py": "frontend/test_synthesis.py",
    "lang/c/test_context.py": "frontend/test_context.py",
    "lang/c/test_utils.py": "frontend/test_utils.py",
    # 中端
    "test_ir.py": "midend/test_ir.py",
    "test_opt.py": "midend/test_opt.py",
    "graph/test_graph.py": "midend/graph/test_graph.py",
    # 后端
    "test_asm.py": "backend/test_asm.py",
    "test_bintools.py": "backend/test_bintools.py",
    "arch/test_armasm.py": "backend/arch/test_armasm.py",
    "arch/test_thumbasm.py": "backend/arch/test_thumbasm.py",
    "arch/test_riscvasm.py": "backend/arch/test_riscvasm.py",
    "arch/test_riscvrvcasm.py": "backend/arch/test_riscvrvcasm.py",
    "arch/test_x86asm.py": "backend/arch/test_x86asm.py",
    "arch/test_x86_sse.py": "backend/arch/test_x86_sse.py",
    "codegen/test_burm.py": "backend/codegen/test_burm.py",
    "codegen/test_codegen.py": "backend/codegen/test_codegen.py",
    "codegen/test_register_allocator.py": (
        "backend/codegen/test_register_allocator.py"
    ),
    # 格式与接口
    "format/test_elf.py": "format/test_elf.py",
    "format/test_header.py": "format/test_header.py",
    "format/test_hexfile.py": "format/test_hexfile.py",
    # 数据
    "data/add.pi": "data/add.pi",
}

# 需为相对导入创建的包标记
PKG_INITS = [
    "__init__.py",
    "frontend/__init__.py",
    "midend/__init__.py",
    "midend/graph/__init__.py",
    "backend/__init__.py",
    "backend/arch/__init__.py",
    "backend/codegen/__init__.py",
    "format/__init__.py",
    "data/__init__.py",
]


def rewrite(s: str) -> str:
    # 绝对导入与注释中的包名：ppci → qcc
    s = re.sub(r"\bppci\.", "qcc.", s)
    s = re.sub(r"\bppci\b", "qcc", s)
    # qcc 内部布局调整：中端/后端子包下沉到 midend/backend 等目录
    module_map = [
        ("qcc.binutils", "qcc.backend.binutils"),
        ("qcc.arch", "qcc.backend.arch"),
        ("qcc.format", "qcc.backend.format"),
        ("qcc.codegen", "qcc.backend.codegen"),
        ("qcc.lang", "qcc.frontend"),
        ("qcc.opt", "qcc.midend.opt"),
        ("qcc.graph", "qcc.midend.graph"),
        ("qcc.irutils", "qcc.midend.irutils"),
        ("qcc.ir", "qcc.midend.ir"),
    ]
    for old, new in module_map:
        s = re.sub(r"\b" + re.escape(old) + r"\b", new, s)

    # `from qcc import ir[, irutils...]` 形式：把子模块名归到对应目录
    dir_of = {
        "ir": "midend", "irutils": "midend", "opt": "midend",
        "graph": "midend", "codegen": "backend", "arch": "backend",
        "binutils": "backend", "format": "backend", "lang": "frontend",
    }

    def sub_from_qcc(m):
        names = [n.strip() for n in m.group(1).split(",")]
        dirs = {dir_of.get(n.split(" as ")[0].split(".")[0]) for n in names}
        if len(dirs) == 1 and dirs != {None}:
            return f"from qcc.{dirs.pop()} import {', '.join(names)}"
        return m.group(0)

    s = re.sub(r"from qcc import ([^\n]+)", sub_from_qcc, s)
    # 子目录测试里的 .helper_util → ..helper_util（helper 在 tests/ 根）
    s = s.replace("from .helper_util import", "from ..helper_util import")
    return s


def main():
    for old_rel, new_rel in TEST_MAP.items():
        src = os.path.join(SRC, old_rel)
        dst = os.path.join(DST, new_rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if old_rel.endswith(".py"):
            text = open(src, encoding="utf-8").read()
            open(dst, "w", encoding="utf-8").write(rewrite(text))
        else:
            shutil.copyfile(src, dst)
    for init in PKG_INITS:
        path = os.path.join(DST, init)
        if not os.path.exists(path):
            open(path, "w", encoding="utf-8").write("")
    print(f"复制并改写 {len(TEST_MAP)} 个测试文件")


if __name__ == "__main__":
    sys.exit(main())
