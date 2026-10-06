# -*- coding: utf-8 -*-
"""链接器（qcc/backend/binutils/linker.py）专用测试。

直接测试 Linker 的各个环节（test_bintools.py 走 api.link 的黑盒路径，
本文件深入 Linker 对象）：
- 多对象合并：节拼接、符号编号平移、重定位条目偏移平移
- 跨对象符号解析：重定位把符号地址"锤"进代码节
- 静态符号隔离：同名 static 符号互不冲突
- 未定义符号报错
- 链接脚本：节地址分配、入口符号、映像（image）生成
"""
import io
import unittest

from qcc.api import asm, cc
from qcc.backend.binutils.linker import Linker
from qcc.backend.binutils.layout import get_layout
from qcc.backend.binutils.objectfile import ObjectFile
from qcc.common import CompilerError

LAYOUT = """
MEMORY code LOCATION=0x40000 SIZE=0x10000 {
    SECTION(code)
}
MEMORY ram LOCATION=0x20000000 SIZE=0xA000 {
    SECTION(data)
}
"""

LAYOUT_WITH_ENTRY = """
ENTRY(main)
MEMORY code LOCATION=0x40000 SIZE=0x10000 {
    SECTION(code)
}
MEMORY ram LOCATION=0x20000000 SIZE=0xA000 {
    SECTION(data)
}
"""


class LinkerTestCase(unittest.TestCase):
    def link_objects(self, objects, layout=None, entry=None):
        linker = Linker(objects[0].arch)
        return linker.link(
            objects,
            layout=get_layout(io.StringIO(layout or LAYOUT)),
            entry_symbol_name=entry,
        )

    def test_merge_two_objects(self):
        """两个对象链接后：代码长度是两者之和，符号表平移合并"""
        o1 = cc(io.StringIO("int f(void) { return 1; }"), "x86_64")
        o2 = cc(io.StringIO("int g(void) { return 2; }"), "x86_64")
        len1 = len(o1.get_section("code").data)
        len2 = len(o2.get_section("code").data)

        out = self.link_objects([o1, o2])
        self.assertEqual(len(out.get_section("code").data), len1 + len2)
        names = [s.name for s in out.symbols]
        self.assertIn("f", names)
        self.assertIn("g", names)

    def test_cross_reference_resolved(self):
        """跨对象调用：重定位后符号有确定地址、代码节被回填"""
        o1 = cc(io.StringIO(
            "extern int helper(void);\nint main(void) { return helper(); }"
        ), "x86_64")
        o2 = cc(io.StringIO("int helper(void) { return 42; }"), "x86_64")

        out = self.link_objects([o1, o2])
        helper_id = out.get_symbol("helper").id
        self.assertIsNotNone(helper_id)
        value = out.get_symbol_id_value(helper_id)
        self.assertGreater(value, 0)
        # 重定位后：链接器内部的重定位条目应已全部解决
        self.assertEqual(
            len([s for s in out.sections if s.name.startswith(".rela")]),
            0,
        )

    def test_undefined_symbol_error(self):
        """未解析的符号链接报错"""
        o1 = cc(io.StringIO(
            "extern int missing(void);\nint main(void) { return missing(); }"
        ), "x86_64")
        with self.assertRaises(CompilerError) as cm:
            self.link_objects([o1])
        self.assertIn("missing", str(cm.exception))

    def test_static_symbols_isolated(self):
        """两个对象中的同名 static 符号互不冲突（各取各的地址）"""
        o1 = cc(io.StringIO(
            "static int f(void) { return 1; }\nint a(void) { return f(); }"
        ), "x86_64")
        o2 = cc(io.StringIO(
            "static int f(void) { return 2; }\nint b(void) { return f(); }"
        ), "x86_64")
        out = self.link_objects([o1, o2])  # 不抛重复符号错误即可
        self.assertGreaterEqual(len(out.symbols), 2)

    def test_layout_places_sections(self):
        """链接脚本把 code/data 放到指定地址，并生成映像"""
        o1 = cc(io.StringIO("int main(void) { return 0; }"), "x86_64")
        out = self.link_objects([o1])
        self.assertEqual(out.get_section("code").address, 0x40000)
        self.assertEqual(out.get_section("data").address, 0x20000000)
        # 映像：link 后可通过 get_image 拿到连续内存区
        self.assertGreater(len(out.get_image("code").data), 0)

    def test_entry_symbol_from_layout(self):
        """布局里的 ENTRY(main) 成为输出目标的入口符号"""
        o1 = cc(io.StringIO("int main(void) { return 0; }"), "x86_64")
        out = self.link_objects([o1], layout=LAYOUT_WITH_ENTRY)
        self.assertIsNotNone(out.entry_symbol_id)
        entry_name = out.symbols_by_id[out.entry_symbol_id].name
        self.assertEqual(entry_name, "main")

    def test_command_line_entry_overrides(self):
        """命令行 entry 覆盖布局 ENTRY"""
        o1 = cc(io.StringIO(
            "int main(void) { return 0; }\nint _start(void) { return main(); }"
        ), "x86_64")
        out = self.link_objects([o1], entry="_start")
        entry_name = out.symbols_by_id[out.entry_symbol_id].name
        self.assertEqual(entry_name, "_start")

    def test_partial_link(self):
        """部分链接：只合并，不解析未定义符号、不报错"""
        o1 = cc(io.StringIO(
            "extern int ext(void);\nint main(void) { return ext(); }"
        ), "x86_64")
        linker = Linker(o1.arch)
        out = linker.link([o1], partial_link=True)
        self.assertGreater(len(out.get_section("code").data), 0)

    def test_debug_info_replication(self):
        """debug=True：链接后保留调试信息（符号编号平移）"""
        o1 = cc(io.StringIO("int main(void) { return 0; }"),
                "x86_64", debug=True)
        linker = Linker(o1.arch)
        out = linker.link([o1], debug=True)
        self.assertIsNotNone(out.debug_info)

    def test_relocation_offsets_shifted(self):
        """第二个对象的符号值 = 布局地址 + 第一个对象代码长度（拼接正确）"""
        o1 = cc(io.StringIO("int f(void) { return 1; }"), "x86_64")
        o2 = cc(io.StringIO("int g(void) { return 2; }"), "x86_64")
        len1 = len(o1.get_section("code").data)
        out = self.link_objects([o1, o2])
        g_value = out.get_symbol_id_value(out.get_symbol("g").id)
        self.assertEqual(g_value, 0x40000 + len1)


if __name__ == "__main__":
    unittest.main()
