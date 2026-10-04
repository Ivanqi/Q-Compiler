# -*- coding: utf-8 -*-
"""C 测试程序套件（需求：测试函数功能、类型系统、SSA优化、动静态文件
链接、逻辑控制、代码封装、标准库、指针、数组、文件IO、网络IO）。

每个测试程序（tests/c/*.c）在 x86_64 上走完整流水线并真机执行：
    编译（qcc.api.cc，含 libc 运行时）→ 汇编启动代码（asm）→
    链接（link + layout）→ 写 ELF 可执行文件 → subprocess 运行 →
    比对 stdout 与退出码。
同一份源码还交叉编译到 riscv / arm，验证后端可用性（编译通过性）。
另外直接检查优化后的 IR，验证 SSA 构造（phi 节点）与 mem2reg。
"""
import argparse
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from qcc.api import asm, cc, ir_to_object, link, optimize
from qcc.backend.format.elf import write_elf
from qcc.frontend.c import COptions, c_to_ir
from qcc.midend import ir

from qcc.__main__ import LIBRT, RUNTIME_SOURCES

REPO = Path(__file__).resolve().parents[1]
C_DIR = Path(__file__).resolve().parent / "c"

# 各测试程序的预期输出（真机运行结果）
EXPECTED = {
    "functions.c": (
        "add=7\nfib=55\napply_add=11\napply_mul=30\npoly=11\npoly=1\n"
    ),
    "types.c": (
        "ch=A\ns=-123\ni=123456\nl=123456789\nu=-1\nfrange=ok\nsum=3\n"
        "cast=2\np=3,4\nsizeof(Point)=8\nv.i=42\nv.c=Z\nenum=2\n"
        "bitfield=1,5,9\nsizes=1,2,4,8\n"
    ),
    "ssa.c": "sum_to=5050\nsum_to=0\nabs=7\nabs=7\nnested=24\n",
    "control.c": (
        "classify=10,20,99\nshort_circuit=0\n"
        "odd=1\nodd=3\nodd=5\nodd=7\nodd=9\n"
        "dowhile=3\nforsum=10\nternary=111\ngoto_loop=45\n"
    ),
    "pointers.c": (
        "sum_array=15\nsum_arith=15\np2=3\npp=1\npp3=4\nswap=9,7\n"
        "strlen=7\nnull=ok\nvoidptr=9\nfnptr=15\nptrdiff=4\n"
    ),
    "arrays.c": (
        "max=9\ntrace=15\nm[2][1]=8\nword=array\nnames=alpha,beta\n"
        "partial=10,20,0\nsum=24\ndecay=7\n"
    ),
    "stdlib.c": (
        "fmt=-42,42,ff,Q,libc\nhex=ff\ndec=-123\nstrlen=11\n"
        "memcpy=copy!\nheap_sum=60\n"
    ),
    "encapsulation.c": "counter=3\nglobal_counter=1\nlen_sq=25\n",
    "fileio.c": "file=<Q-Compiler file IO test\n>\n",
    "netio.c": "socket=ok\nbind=ok\nsendto=ok\nrecvfrom=ok\nudp_echo=ok\n",
}

# 需要 cwd=tests/c 的程序（读取相对路径文件）
NEED_C_DIR_CWD = {"fileio.c"}

# 交叉编译排除表（文档化的后端限制）：
# - netio.c：内联汇编 syscall 仅支持 x86_64
# - types.c：arm 后端未实现浮点指令模式（vfp 骨架未完成，riscv 支持浮点）
CROSS_COMPILE_EXCLUDE = {
    "netio.c": {"arm", "riscv"},
    "types.c": {"arm"},
}


def make_coptions(march="x86_64"):
    coptions = COptions()
    coptions.add_include_path(str(LIBRT / "include"))
    coptions.add_define(f"__{march}__", "1")
    if march == "x86_64":
        coptions.add_define("__LP64__", "1")
    return coptions


def build_executable(user_files, opt_level="2"):
    """完整构建：启动代码 + libc 运行时 + 用户程序 → 链接 → 目标文件。"""
    objects = []
    with open(LIBRT / "start.asm", "r", encoding="utf8") as f:
        objects.append(asm(f, "x86_64"))
    coptions = make_coptions()
    for rel in RUNTIME_SOURCES:
        with open(LIBRT / rel, "r", encoding="utf8") as f:
            objects.append(cc(f, "x86_64", coptions=coptions,
                              opt_level=opt_level))
    for path in user_files:
        with open(path, "r", encoding="utf8") as f:
            objects.append(cc(f, "x86_64", coptions=coptions,
                              opt_level=opt_level))
    with open(LIBRT / "layout.mmap") as layout:
        return link(objects, layout=layout)


def run_elf(obj, cwd=None):
    """把链接产物写成 ELF 可执行文件并真机运行，返回 (stdout, exit_code)。"""
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "test.elf"
        with open(exe, "wb") as f:
            write_elf(obj, f, type="executable")
        exe.chmod(exe.stat().st_mode | 0o111)
        proc = subprocess.run(
            [str(exe)], cwd=cwd, capture_output=True, text=True, timeout=60
        )
        return proc.stdout, proc.returncode


class CSingleFileTestCase(unittest.TestCase):
    """单个 C 源文件：x86_64 真机运行 + riscv/arm 交叉编译"""

    def _test_program(self, name):
        src = C_DIR / name
        cwd = str(C_DIR) if name in NEED_C_DIR_CWD else None
        obj = build_executable([src])
        stdout, exit_code = run_elf(obj, cwd=cwd)
        self.assertEqual(exit_code, 0, f"exit code {exit_code}")
        self.assertEqual(stdout, EXPECTED[name],
                         f"stdout mismatch for {name}:\n{stdout}")

    def _test_cross_compile(self, name):
        """同一源码能交叉编译到 riscv / arm（后端可用性）。

        CROSS_COMPILE_EXCLUDE 记录文档化的后端限制。
        """
        for march in ("riscv", "arm"):
            if march in CROSS_COMPILE_EXCLUDE.get(name, ()):
                continue
            with self.subTest(march=march):
                with open(C_DIR / name, "r", encoding="utf8") as f:
                    obj = cc(f, march, coptions=make_coptions(march),
                             opt_level="2")
                self.assertGreater(len(obj.get_section("code").data), 0)

    def test_functions(self):
        self._test_program("functions.c")

    def test_types(self):
        self._test_program("types.c")

    def test_ssa(self):
        self._test_program("ssa.c")

    def test_control(self):
        self._test_program("control.c")

    def test_pointers(self):
        self._test_program("pointers.c")

    def test_arrays(self):
        self._test_program("arrays.c")

    def test_stdlib(self):
        self._test_program("stdlib.c")

    def test_encapsulation(self):
        self._test_program("encapsulation.c")

    def test_fileio(self):
        self._test_program("fileio.c")

    def test_netio(self):
        self._test_program("netio.c")

    def test_cross_compile_all(self):
        for name in EXPECTED:
            self._test_cross_compile(name)


class LinkingTestCase(unittest.TestCase):
    """动静态链接：两个编译单元分别编译成目标文件后再链接。

    - 静态（static）：同名 static 函数/变量在各单元内隔离，互不冲突；
    - 动态（链接期解析）：extern 声明的符号在链接时跨对象解析。
    """

    def test_multi_file_link_and_run(self):
        main_c = C_DIR / "linking" / "main.c"
        math_c = C_DIR / "linking" / "math.c"
        objects = []
        with open(LIBRT / "start.asm", "r", encoding="utf8") as f:
            objects.append(asm(f, "x86_64"))
        coptions = make_coptions()
        for rel in RUNTIME_SOURCES:
            with open(LIBRT / rel, "r", encoding="utf8") as f:
                objects.append(cc(f, "x86_64", coptions=coptions))
        # 两个编译单元分别编译（模拟多文件项目）
        with open(math_c, "r", encoding="utf8") as f:
            objects.append(cc(f, "x86_64", coptions=coptions))
        with open(main_c, "r", encoding="utf8") as f:
            objects.append(cc(f, "x86_64", coptions=coptions))
        with open(LIBRT / "layout.mmap") as layout:
            obj = link(objects, layout=layout)
        stdout, exit_code = run_elf(obj)
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            stdout,
            "squares=41\ncounted=30\ncounted=3\ncalls=2\n"
            "local=3\nhelper=40\n",
        )


class SsaOptimizationTestCase(unittest.TestCase):
    """SSA 优化（需求中的"SSA优化"）：mem2reg 提升栈变量为 SSA 值。

    检查优化后 IR：
    - abs_v：分支变量 r 在合并点产生 phi 节点；
    - sum_to：循环变量全部提升，alloc/load/store 消失；
    - O0 与 O2 的运行结果一致（优化保持语义）。
    """

    def _compile_ir(self, opt_level):
        with open(C_DIR / "ssa.c", "r", encoding="utf8") as f:
            ir_module = c_to_ir(f, "x86_64", coptions=make_coptions())
        optimize(ir_module, level=opt_level)
        return ir_module

    def _function(self, ir_module, name):
        for fn in ir_module.functions:
            if fn.name == name:
                return fn
        raise AssertionError(f"function {name} not found")

    def test_phi_nodes_after_mem2reg(self):
        """分支合并点应有 phi 节点"""
        ir_module = self._compile_ir("2")
        abs_v = self._function(ir_module, "abs_v")
        phis = [
            ins for block in abs_v.blocks for ins in block
            if isinstance(ins, ir.Phi)
        ]
        self.assertEqual(len(phis), 1, f"expected 1 phi, got {len(phis)}")

    def test_allocs_promoted(self):
        """循环累加器的 alloc/load/store 全部被提升消除"""
        ir_module = self._compile_ir("2")
        sum_to = self._function(ir_module, "sum_to")
        instructions = [ins for b in sum_to.blocks for ins in b]
        self.assertFalse(
            any(isinstance(ins, ir.Alloc) for ins in instructions),
            "alloc 应被 mem2reg 消除",
        )
        self.assertFalse(
            any(isinstance(ins, (ir.Load, ir.Store)) for ins in instructions),
            "load/store 应被 mem2reg 消除",
        )

    def test_optimization_preserves_semantics(self):
        """O0 与 O2 真机运行结果一致"""
        out0, code0 = run_elf(build_executable([C_DIR / "ssa.c"],
                                               opt_level="0"))
        out2, code2 = run_elf(build_executable([C_DIR / "ssa.c"],
                                               opt_level="2"))
        self.assertEqual(code0, 0)
        self.assertEqual(code2, 0)
        self.assertEqual(out0, out2)
        self.assertEqual(out0, EXPECTED["ssa.c"])


class CliEntryTestCase(unittest.TestCase):
    """编译器入口（python3 -m qcc）可用性"""

    def _qcc(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "qcc", *args],
            cwd=str(REPO), capture_output=True, text=True, timeout=120,
        )

    def test_cli_run(self):
        result = self._qcc("--run", "tests/c/functions.c", "-O2",
                           "-o", "/tmp/qcc_cli_test.elf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("add=7", result.stdout)
        self.assertIn("fib=55", result.stdout)

    def test_cli_assembly(self):
        result = self._qcc("tests/c/control.c", "-S", "-O2",
                           "-o", "/tmp/qcc_cli_test.S")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = open("/tmp/qcc_cli_test.S", encoding="utf8").read()
        self.assertIn("section code", text)

    def test_cli_cross_compile(self):
        for march in ("riscv", "arm"):
            with self.subTest(march=march):
                result = self._qcc("tests/c/pointers.c", "-m", march,
                                   "-O2", "-o", f"/tmp/qcc_cli_{march}.elf")
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
