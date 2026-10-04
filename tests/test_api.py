"""api.py 接口测试（由 ppci/test/test_api.py 裁剪而来）。

已移除依赖 build 构建系统的 RecipeTestCase（construct 已随裁剪删除），
保留 disasm / link / objcopy 用例，并补充 cc+link+objcopy 的实际输出测试。
"""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qcc.api import cc, disasm, link, objcopy
from qcc.common import TaskError


class ApiTestCase(unittest.TestCase):
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_disasm(self, mock_stdout):
        binary_file = io.BytesIO(bytes(range(10)))
        disasm(binary_file, "riscv")

    def test_link_without_arguments(self):
        with self.assertRaises(ValueError):
            link([])


class ObjcopyTestCase(unittest.TestCase):
    def test_wrong_format(self):
        with self.assertRaises(TaskError):
            objcopy(None, None, "invalid_format", None)

    def test_objcopy_bin(self):
        """cc → link → objcopy bin 输出裸二进制"""
        src = io.StringIO("int main(void) { return 42; }")
        obj = cc(src, "arm")
        layout_path = Path(__file__).parent / "data" / "layout.mmap"
        exe = link([obj], layout=open(layout_path))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.bin")
            objcopy(exe, "code", "bin", out)
            self.assertGreater(os.path.getsize(out), 0)


if __name__ == "__main__":
    unittest.main()
