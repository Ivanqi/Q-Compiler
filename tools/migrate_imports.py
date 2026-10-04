# -*- coding: utf-8 -*-
"""M2：重写 qcc 中的 import 语句（ppci.* / 相对导入 → qcc.* 绝对导入）。

原理：
1. 由 manifest.FILE_MAP 推导 旧模块名 → 新模块名 映射（含所有包前缀）。
2. 用 ast 解析每个文件，找出 Import / ImportFrom：
   - 绝对导入 ppci.x → 前缀映射；
   - 相对导入（level>0）→ 先按 Python 语义解析为旧绝对模块路径，再映射。
3. 每个被导入名字区分「子模块」（旧模块路径在映射表中）与「属性」，
   按父模块分组重新生成 `from qcc.xxx import a, b` 语句。
4. 无法映射的旧模块 → 报告错误（说明 manifest 不完整）。

用法：
  python3 tools/migrate_imports.py --dry-run    # 只报告
  python3 tools/migrate_imports.py --apply      # 实际改写
"""
import argparse
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifest import FILE_MAP, PREFIX_MAP  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DST_ROOT = os.path.join(REPO, "qcc")


def to_module(rel):
    rel = rel[:-3] if rel.endswith(".py") else rel
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def build_module_map():
    """由 FILE_MAP 推导 旧模块名(ppci.*) → 新模块名(qcc.*) 完整映射。

    除文件本身外，把所有包目录前缀也纳入映射。注意不能按相同下标
    切片：新布局在包根部插入了 frontend/midend/backend 段
    （如 ppci.arch.arm → qcc.backend.arch.arm，多一段）。
    正确做法：旧目录前缀未包含的尾段在新路径中同名保留，
    据此推算新前缀长度。
    """
    old2new = {"ppci": "qcc"}
    for old_rel, new_rel in FILE_MAP.items():
        if not old_rel.endswith((".py", ".grammar")):
            continue
        old_mod, new_mod = "ppci." + to_module(old_rel), "qcc." + to_module(new_rel)
        old2new[old_mod] = new_mod
        old_dirs = old_rel.split("/")[:-1]
        new_dirs = new_rel.split("/")[:-1]
        for k in range(1, len(old_dirs) + 1):
            tail = len(old_dirs) - k
            # 未包含的旧尾段在新路径中必须同名保留（本清单满足）
            assert tail == 0 or old_dirs[k:] == new_dirs[-tail:], old_rel
            m = len(new_dirs) - tail
            old_key = "ppci." + ".".join(old_dirs[:k])
            old2new[old_key] = "qcc." + ".".join(new_dirs[:m]) if m else "qcc"
    return old2new


OLD2NEW = build_module_map()


def map_module(old_mod):
    """旧绝对模块名 → 新绝对模块名；无法映射返回 None。"""
    if old_mod in OLD2NEW:
        return OLD2NEW[old_mod]
    # 点号边界最长前缀匹配
    best = None
    for p, v in OLD2NEW.items():
        if old_mod.startswith(p + ".") and (best is None or len(p) > len(best[0])):
            best = (p, v)
    if best:
        return best[1] + old_mod[len(best[0]):]
    return None


def rel_import_to_old(file_rel, level, module):
    """把文件中的相对导入解析为旧绝对模块路径。

    file_rel: qcc 内新相对路径；level/module: ImportFrom 的参数。
    旧路径推导按「旧布局中对应位置」进行：新路径先映射回旧路径。
    """
    # 新相对路径 → 旧相对路径
    rev = {v: k for k, v in FILE_MAP.items() if k.endswith((".py", ".grammar"))}
    # 文件可能不在 FILE_MAP（新建 __init__.py），跳过即可（不会出现 import）
    if file_rel not in rev:
        return None
    old_rel = rev[file_rel]
    old_rel = old_rel[:-3] if old_rel.endswith(".py") else old_rel
    parts = old_rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    elif parts:
        parts = parts[:-1]  # 去掉文件名
    # Python 语义：level 1 = 当前包，2 = 父包，...
    keep = len(parts) - (level - 1)
    base_parts = parts[:keep] if keep >= 0 else []
    base_parts = base_parts + (module.split(".") if module else [])
    return "ppci." + ".".join(base_parts) if base_parts else "ppci"


class ImportRewriter:
    def __init__(self):
        self.errors = []   # (file, lineno, msg)
        self.changes = []  # (file, count)

    def rewrite_node(self, file_rel, node):
        """返回替换文本列表（每个 import 节点可能展开为多条语句）。"""
        lines = []

        if isinstance(node, ast.Import):
            # import ppci.x.y [as z]；非 ppci 的（stdlib）保持不变
            for alias in node.names:
                old_mod = alias.name
                if not old_mod.startswith("ppci"):
                    continue
                new_mod = map_module(old_mod)
                if new_mod is None:
                    self.errors.append((file_rel, node.lineno,
                                        f"无法映射模块 {old_mod}"))
                    continue
                lines.append(f"import {new_mod}"
                             + (f" as {alias.asname}" if alias.asname else ""))
            return lines

        # ImportFrom
        assert isinstance(node, ast.ImportFrom)
        if node.level == 0:
            if not (node.module or "").startswith("ppci"):
                return lines  # stdlib 导入，不动
            old_base = node.module or ""
        else:
            old_base = rel_import_to_old(file_rel, node.level, node.module)
            if old_base is None:
                self.errors.append((file_rel, node.lineno,
                                    "相对导入无法回溯到旧路径"))
                return lines
        new_base = map_module(old_base)
        if new_base is None:
            self.errors.append((file_rel, node.lineno,
                                f"无法映射模块 {old_base}"))
            return lines

        # 按「名字所属父模块」分组
        groups = {}  # parent_mod -> list of (name, asname)
        for alias in node.names:
            name = alias.name
            if name == "*":
                lines.append(f"from {new_base} import *")
                continue
            sub_old = old_base + "." + name if old_base else name
            sub_new = map_module(sub_old)
            if sub_new is not None and sub_new != new_base:
                # 名字是子模块 → 从其新父模块导入
                parent = sub_new.rsplit(".", 1)[0]
                groups.setdefault(parent, []).append((name, alias.asname))
            else:
                # 名字是属性（类/函数/变量）
                groups.setdefault(new_base, []).append((name, alias.asname))
        for parent, names in groups.items():
            parts = []
            for name, asname in names:
                parts.append(name + (f" as {asname}" if asname else ""))
            line = f"from {parent} import {', '.join(parts)}"
            if len(line) > 88 and len(parts) > 1:
                # 过长则折行（括号形式），保持可读性
                line = (
                    f"from {parent} import (\n"
                    + ",\n".join("    " + p for p in parts)
                    + ",\n)"
                )
            lines.append(line)
        return lines

    def rewrite_file(self, file_rel, apply=False):
        path = os.path.join(DST_ROOT, file_rel)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        repls = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                new_lines = self.rewrite_node(file_rel, node)
                if not new_lines:
                    continue
                indent = " " * node.col_offset
                new_text = "\n".join(indent + ln for ln in new_lines)
                old_text = ast.get_source_segment(src, node)
                if old_text != new_text:
                    repls.append((node.lineno, node.col_offset,
                                  node.end_lineno, node.end_col_offset,
                                  new_text))
        if repls:
            self.changes.append((file_rel, len(repls)))
            if not apply:
                return
            lines = src.split("\n")
            # 从后往前替换，避免行号漂移
            for lineno, col, end_lineno, end_col, new_text in sorted(
                    repls, key=lambda r: r[0], reverse=True):
                # 拼出新源码：把被替换区间裁掉、插入新文本行。
                # 注意不引入多余空行：col 之前的文本若只是缩进（新文本已含）
                # 就丢弃，否则合并到新文本首行；区间末行剩余文本合并到末行。
                new_lines = new_text.split("\n")
                before_frag = lines[lineno - 1][:col]
                after_frag = lines[end_lineno - 1][end_col:]
                if before_frag.strip():
                    new_lines[0] = before_frag + new_lines[0]
                if after_frag:
                    new_lines[-1] = new_lines[-1] + after_frag
                lines = lines[:lineno - 1] + new_lines + lines[end_lineno:]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))


def walk_py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.relpath(os.path.join(dirpath, fn), root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际改写文件")
    args = ap.parse_args()

    rw = ImportRewriter()
    for file_rel in walk_py_files(DST_ROOT):
        rw.rewrite_file(file_rel, apply=args.apply)

    if rw.errors:
        print("无法映射的导入：")
        for f, ln, msg in rw.errors:
            print(f"  {f}:{ln}: {msg}")
        return 1

    if args.apply:
        print(f"改写 {len(rw.changes)} 个文件：")
        for f, n in rw.changes:
            print(f"  {f}: {n} 处")
    else:
        print(f"[dry-run] 将改写 {len(rw.changes)} 个文件，"
              f"共 {sum(n for _, n in rw.changes)} 处 import；"
              "无未映射导入。")
        print("确认后用 --apply 实际改写。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
