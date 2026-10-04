# -*- coding: utf-8 -*-
"""Q-Compiler 命令行入口：`python3 -m qcc [选项] <源文件.c...>`。

流水线（需求3）：C 源码 → frontend（前端）→ midend（中端：IR+优化）
→ backend（后端：arm/riscv/x86_64 机器码）→ ELF/hex/bin 目标文件（需求2）。

用法示例：
    # 编译 C 源码为目标文件（默认输出 a.elf，可重定位 ELF）
    python3 -m qcc hello.c -o hello.elf

    # 输出优化后的汇编文本
    python3 -m qcc hello.c -S -O2

    # 交叉编译到 riscv / arm
    python3 -m qcc hello.c -m riscv -o hello_riscv.elf

    # 编译 + 链接 libc 运行时 + 在真机（x86_64）上执行
    python3 -m qcc --run hello.c

    # 多文件链接
    python3 -m qcc --link main.elf math.elf -o app.elf --entry main
"""
import argparse
import io
import os
import subprocess
import sys
from pathlib import Path

# 允许从仓库任意位置以 `python3 -m qcc` 运行
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from qcc.api import (  # noqa: E402
    asm,
    cc,
    chmod_x,
    ir_to_assembly,
    ir_to_object,
    link,
    objcopy,
    optimize,
)
from qcc.backend.format.elf import write_elf  # noqa: E402
from qcc.common import CompilerError, DiagnosticsManager  # noqa: E402
from qcc.frontend.c import COptions, c_to_ir  # noqa: E402

LIBRT = _REPO / "librt"

# --run 模式需要链接的 libc 运行时源文件（相对 librt/）
RUNTIME_SOURCES = [
    "bsp.c",                        # bsp_putc / bsp_exit（→ start.asm 的 syscall 桥）
    "lib.c",                        # printf / itoa（经 bsp_putc 输出）
    "src/syscall/syscall.c",        # 内联汇编 syscall()
    "src/syscall/brk.c",            # brk / sbrk（malloc 的堆基元）
    "src/malloc.c",                 # malloc / free / memcpy / memmove
    "src/unistd.c",                 # read / write / close（文件 IO）
    "src/fcntl.c",                  # open（文件 IO）
    "src/stat.c",                   # fstat / f_size
    # 注：src/string/string.c 与 lib.c 重复定义 reverse/itoa，不纳入
    "src/string/strlen.c",
]


def make_coptions(args, march):
    coptions = COptions()
    if args.freestanding:
        coptions.enable("freestanding")
    for path in args.include:
        coptions.add_include_path(Path(path))
    for macro in args.define:
        if "=" in macro:
            name, value = macro.split("=", 1)
        else:
            name, value = macro, "1"
        coptions.add_define(name, value)
    # 架构宏：lib.c 的 %f 格式化等按架构开关
    coptions.add_define(f"__{march}__", "1")
    if march == "x86_64":
        coptions.add_define("__LP64__", "1")
    return coptions


def compile_to_objects(filenames, march, args):
    """前端+中端+后端：C 源码 → ObjectFile 列表。"""
    objects = []
    for filename in filenames:
        with open(filename, "r", encoding="utf8") as f:
            obj = cc(
                f,
                march,
                coptions=make_coptions(args, march),
                opt_level=args.opt_level,
            )
        print(f"编译 {filename} → {obj}")
        objects.append(obj)
    return objects


def emit_assembly(filenames, march, args):
    """-S：输出每个源文件优化后的汇编文本。"""
    texts = []
    for filename in filenames:
        with open(filename, "r", encoding="utf8") as f:
            ir_module = c_to_ir(f, march, coptions=make_coptions(args, march))
        optimize(ir_module, level=args.opt_level)
        texts.append(f"# {filename}\n{ir_to_assembly([ir_module], march)}")
    return "\n".join(texts)


def build_executable(user_files, march, args):
    """--run：启动代码 + libc 运行时 + 用户程序 → 链接 → ELF 可执行。"""
    objects = []
    # 1. 启动代码（start / bsp_syscall）
    with open(LIBRT / "start.asm", "r", encoding="utf8") as f:
        objects.append(asm(f, march))
    # 2. libc 运行时（含 bsp.c，include 路径指向 librt/include）
    run_args = argparse.Namespace(
        **{**vars(args), "include": args.include + [str(LIBRT / "include")]}
    )
    for rel in RUNTIME_SOURCES:
        with open(LIBRT / rel, "r", encoding="utf8") as f:
            objects.append(
                cc(f, march, coptions=make_coptions(run_args, march),
                   opt_level=args.opt_level)
            )
    # 3. 用户程序
    objects.extend(compile_to_objects(user_files, march, run_args))
    # 4. 链接
    layout = open(args.layout or (LIBRT / "layout.mmap"))
    return link(objects, layout=layout, entry=args.entry)


def run_executable(obj, output_name):
    """把链接产物写成 ELF 可执行文件并在真机上运行。"""
    with open(output_name, "wb") as f:
        write_elf(obj, f, type="executable")
    chmod_x(output_name)
    print(f"运行 {output_name} ...")
    proc = subprocess.run([output_name], capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def write_output(obj, output_name, args):
    """按 --format 输出：elf（可重定位/可执行）/ bin / hex。"""
    fmt = args.format
    if fmt == "elf":
        elf_type = "executable" if obj.is_executable else "relocatable"
        try:
            with open(output_name, "wb") as f:
                write_elf(obj, f, type=elf_type)
        except NotImplementedError as ex:
            # riscv/arm 未实现重定位表生成（x86_64 已实现）：
            # 退化为"链接成可执行文件"再输出 ELF
            if obj.is_executable:
                raise
            print(f"提示：{args.march} 不支持可重定位 ELF，"
                  "改为链接后输出可执行 ELF ...")
            layout = open(args.layout or (LIBRT / "layout.mmap"))
            exe = link([obj], layout=layout, entry=args.entry)
            with open(output_name, "wb") as f:
                write_elf(exe, f, type="executable")
            elf_type = "executable"
        if elf_type == "executable":
            chmod_x(output_name)
    elif fmt in ("bin", "hex"):
        # bin/hex 需要链接后的映像（image）
        if not obj.image_map:
            print("警告：bin/hex 需要先链接（--link 或 --run）", file=sys.stderr)
            return False
        objcopy(obj, "code", fmt, output_name)
    else:  # pragma: no cover
        raise NotImplementedError(f"未知格式 {fmt}")
    print(f"输出 {output_name}")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="qcc",
        description="Q-Compiler：C → IR → 优化 → arm/riscv/x86_64 机器码",
        epilog="示例：python3 -m qcc hello.c -O2 -o hello.elf；"
               "python3 -m qcc --run hello.c",
    )
    parser.add_argument("sources", nargs="+", help="C 源文件")
    parser.add_argument("-m", "--march", default="x86_64",
                        choices=["x86_64", "riscv", "arm"],
                        help="目标架构（默认 x86_64）")
    parser.add_argument("-O", "--opt-level", default="0",
                        choices=["0", "1", "2", "s"],
                        help="IR 优化级别（默认 0）")
    parser.add_argument("-o", "--output", default=None,
                        help="输出文件名（默认 a.elf / a.bin / a.hex）")
    parser.add_argument("-S", "--assembly", action="store_true",
                        help="输出优化后的汇编文本而不是目标文件")
    parser.add_argument("--format", default="elf",
                        choices=["elf", "bin", "hex"],
                        help="输出格式（默认 elf）")
    parser.add_argument("--run", action="store_true",
                        help="链接 libc 运行时并真机执行（仅 x86_64）")
    parser.add_argument("--link", action="store_true",
                        help="把输入（.c 或 .elf 目标文件）链接为一个可执行文件")
    parser.add_argument("--entry", default=None,
                        help="链接入口符号（默认取链接脚本 ENTRY）")
    parser.add_argument("--layout", default=None,
                        help="链接脚本 .mmap 文件（默认 librt/layout.mmap）")
    parser.add_argument("--freestanding", action="store_true",
                        help="独立环境编译（不假定宿主 C 库）")
    parser.add_argument("-I", "--include", action="append", default=[],
                        help="头文件搜索路径（可多次指定）")
    parser.add_argument("-D", "--define", action="append", default=[],
                        help="预定义宏（NAME 或 NAME=VALUE，可多次指定）")
    args = parser.parse_args(argv)

    # 默认带上自带的 libc 头文件路径（可用 -I 追加更多）
    if str(LIBRT / "include") not in args.include:
        args.include.insert(0, str(LIBRT / "include"))

    if args.output is None:
        ext = ".S" if args.assembly else (
            {"bin": ".bin", "hex": ".hex"}.get(args.format, ".elf")
        )
        args.output = "a" + ext

    if args.run and args.march != "x86_64":
        parser.error("--run 只能在 x86_64 上真机执行（riscv/arm 请用 -m 交叉编译）")

    try:
        if args.assembly:
            text = emit_assembly(args.sources, args.march, args)
            with open(args.output, "w", encoding="utf8") as f:
                f.write(text)
            print(f"输出 {args.output}")
            return 0

        if args.run:
            obj = build_executable(args.sources, args.march, args)
            return run_executable(obj, args.output)

        if args.link:
            objects = []
            for src in args.sources:
                if src.endswith(".c"):
                    objects.extend(
                        compile_to_objects([src], args.march, args)
                    )
                else:
                    # .elf/.o 目标文件：直接读回
                    from qcc.backend.binutils.objectfile import get_object
                    objects.append(get_object(open(src, "rb")))
            layout = open(args.layout or (LIBRT / "layout.mmap"))
            obj = link(objects, layout=layout, entry=args.entry)
            return 0 if write_output(obj, args.output, args) else 1

        # 默认：编译（单文件 → 目标文件；多文件 → 链接）
        objects = compile_to_objects(args.sources, args.march, args)
        if len(objects) == 1 and not args.layout:
            obj = objects[0]
        else:
            layout = open(args.layout or (LIBRT / "layout.mmap"))
            obj = link(objects, layout=layout, entry=args.entry)
        return 0 if write_output(obj, args.output, args) else 1
    except CompilerError as ex:
        ex.print()
        return 1
    except FileNotFoundError as ex:
        print(f"错误：{ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
