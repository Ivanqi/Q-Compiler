# -*- coding: utf-8 -*-
"""M1：按 manifest 把 ppci/ppci 中的文件复制到 qcc 新布局。

用法：python3 tools/migrate_copy.py
只做复制（含数据文件 burg.grammar）与新建 4 个包 __init__.py；
不动 ppci 源目录。
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifest import FILE_MAP  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_ROOT = os.path.join(REPO, "ppci", "ppci")
DST_ROOT = os.path.join(REPO, "qcc")

# 新建的包 __init__.py（原 ppci 无对应文件）
NEW_INITS = {
    "qcc/__init__.py": '''# -*- coding: utf-8 -*-
"""Q-Compiler：从 ppci 迁移而来的自包含 C 编译器。

流水线（需求 3）：C 源码 → frontend（前端）→ midend（中端：IR+优化）
→ backend（后端：指令选择/寄存器分配 → arm/risc-v/x86_64 机器码）
→ ELF/hex 目标文件（需求 2）。

子包：
- qcc.frontend  ① C 语言前端（lexer/preprocessor/parser/semantics/codegenerator）
- qcc.midend   ② 中间表示 ir.py 与优化 pass（opt/）
- qcc.backend  ③ 代码生成（codegen/）、目标架构（arch/）、
                 二进制工具（binutils/）与输出格式（format/，含 ELF）
- qcc.utils       公共工具
- qcc.api         总入口：cc()/optimize()/ir_to_object()/link()/objcopy()
"""

__version_info__ = (0, 5, 9)
__version__ = ".".join(map(str, __version_info__))
''',
    "qcc/frontend/__init__.py": '''# -*- coding: utf-8 -*-
"""① 前端：把 C 语言源码翻译为中间表示（midend.ir）。

- tools/  词法/语法分析工具基础（handlexer、recursivedescent、yacc…）
- c/      C 前端：lexer → preprocessor → parser → semantics →
           builder → codegenerator（AST → IR）
- common.py 跨语言公共设施（SourceLocation、Token）
"""
''',
    "qcc/midend/__init__.py": '''# -*- coding: utf-8 -*-
"""② 中端：中间表示（IR）与优化。

- ir.py    统一 IR：Module → Function → Block → Instruction
- irutils/ IR 校验、Builder、文本读写
- opt/     优化 pass：mem2reg、常量折叠、CSE、尾调用、DCE、CleanPass…
- graph/   图算法基础：流图、支配树、干涉图用图结构
"""
''',
    "qcc/backend/__init__.py": '''# -*- coding: utf-8 -*-
"""③ 后端：IR → 机器指令 → 机器码/目标文件。

- codegen/   代码生成流水线：SelectionDAG 构建 → BURG 树匹配指令选择
             → 指令调度 → 图着色寄存器分配 → 栈帧发射
- arch/      目标架构：arm、riscv、x86_64（另有 example 教学架构）
- binutils/  目标文件（ObjectFile）、汇编器、链接器（linker）
- format/    输出格式：ELF（需求要求）、Intel HEX
"""
''',
}


def to_module(rel):
    """相对路径 → 模块名（__init__.py 归到包本身）。"""
    rel = rel[:-3] if rel.endswith(".py") else rel
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def main():
    missing, copied = [], 0
    for old_rel, new_rel in FILE_MAP.items():
        src = os.path.join(SRC_ROOT, old_rel)
        dst = os.path.join(DST_ROOT, new_rel)
        if not os.path.isfile(src):
            missing.append(old_rel)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        copied += 1
    if missing:
        print("缺失源文件:")
        for m in missing:
            print("  ", m)
        return 1
    for new_rel, content in NEW_INITS.items():
        dst = os.path.join(REPO, new_rel)
        if not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(content)
    print(f"复制完成：{copied} 个文件 + {len(NEW_INITS)} 个新建 __init__.py")
    # 统计总行数
    total = 0
    for root, _, files in os.walk(DST_ROOT):
        for fn in files:
            if fn.endswith((".py", ".grammar")):
                total += sum(
                    1 for _ in open(os.path.join(root, fn), encoding="utf-8")
                )
    print(f"qcc 总行数：{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
