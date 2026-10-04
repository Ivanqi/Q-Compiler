"""自包含性守卫测试（需求 1：迁移而非依赖整个 ppci 包）。

在子进程中屏蔽 ppci 后导入 qcc 并完整编译一次：
证明根目录下的 qcc 不（静默）依赖仓库里的旧 ppci 源码。
"""
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

GUARD_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
# 守卫：使 `import ppci` 必然失败
sys.modules["ppci"] = None

import qcc
import qcc.api
from qcc.api import cc, link, objcopy
import io

# 若 qcc 内部任何模块偷偷 import ppci，这一步会抛 ImportError
obj = cc(io.StringIO("int add(int a, int b) {{ return a + b; }}"), "x86_64")

# 确认加载的 qcc 来自仓库根目录而非别处
assert qcc.__file__.startswith({root!r}), qcc.__file__

# 确认没有 ppci 子模块混入 sys.modules
# （sys.modules["ppci"] 本身是守卫设置的 None，不算泄漏）
leaks = [m for m in sys.modules if m.startswith("ppci.")]
assert not leaks, leaks
print("GUARD OK")
"""


class NoPpciDependencyTestCase(unittest.TestCase):
    def test_qcc_does_not_import_ppci(self):
        """屏蔽 ppci 后 qcc 仍能完成一次 C 编译"""
        script = GUARD_SCRIPT.format(root=str(REPO))
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"子进程失败\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("GUARD OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
