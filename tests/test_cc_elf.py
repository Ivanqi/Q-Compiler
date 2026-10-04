"""端到端测试：C 源码 → 优化 → 机器码 → ELF 目标文件（需求 2）。

覆盖需求 3 的完整流水线：
前端 frontend（lexer/preprocessor/parser/semantics/codegenerator）
→ 中端 midend（ir + opt 优化 pass）
→ 后端 backend（codegen 指令选择/寄存器分配 → arch 机器码）
→ ELF 输出（backend.format.elf），对 arm / riscv / x86_64 三个架构各跑一遍。
"""
import io
import os
import tempfile
import unittest
from pathlib import Path

from qcc.api import cc, ir_to_object, link, objcopy, optimize
from qcc.backend.format.elf import read_elf

C_SRC = """
int add(int a, int b) {
    return a + b;
}

int fib(int n) {
    if (n < 2) {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

int main(void) {
    return add(fib(10), 42);
}
"""


class CompileToElfTestCase(unittest.TestCase):
    """每个目标架构：C → 目标文件 → 链接 → ELF → 回读校验"""

    def _roundtrip(self, march):
        # ① 前端+中端：cc 内部完成 c_to_ir + optimize
        obj = cc(io.StringIO(C_SRC), march, opt_level=2)
        self.assertGreater(len(obj.get_section("code").data), 0)

        # ② 后端链接：得到可执行映像
        layout_path = Path(__file__).parent / "data" / "layout.mmap"
        exe = link([obj], layout=open(layout_path))

        # ③ 输出 ELF 并回读校验
        with tempfile.TemporaryDirectory() as tmp:
            elf_path = os.path.join(tmp, "test.elf")
            objcopy(exe, "code", "elf", elf_path)
            with open(elf_path, "rb") as f:
                elf = read_elf(f)
            section_names = [s.name for s in elf.sections]
            self.assertIn("code", section_names)
            self.assertIn(".symtab", section_names)
            # hex 与 bin 输出也能生成
            objcopy(exe, "code", "hex", os.path.join(tmp, "test.hex"))
            objcopy(exe, "code", "bin", os.path.join(tmp, "test.bin"))
            self.assertGreater(
                os.path.getsize(os.path.join(tmp, "test.bin")), 0
            )

    def test_x86_64_elf(self):
        self._roundtrip("x86_64")

    def test_riscv_elf(self):
        self._roundtrip("riscv")

    def test_arm_elf(self):
        self._roundtrip("arm")

    def test_optimize_levels(self):
        """各优化档位（0/1/2/s）都应产出合法目标文件"""
        for level in ("0", "1", "2", "s"):
            with self.subTest(level=level):
                obj = cc(
                    io.StringIO(C_SRC), "x86_64", opt_level=level
                )
                self.assertGreater(len(obj.get_section("code").data), 0)

    def test_ir_object_entry(self):
        """ir_to_object 直接编译 IR 模块的入口可用"""
        from qcc.frontend.c import c_to_ir

        ir_module = c_to_ir(io.StringIO(C_SRC), "riscv")
        optimize(ir_module, level=2)
        obj = ir_to_object([ir_module], "riscv")
        self.assertGreater(len(obj.get_section("code").data), 0)


if __name__ == "__main__":
    unittest.main()
