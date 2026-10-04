# -*- coding: utf-8 -*-
"""M7 终验脚本：编译检查 + 审计 + 端到端冒烟。

用法：python3 tools/verify_all.py
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

checks = []


def run(name, cmd, cwd=REPO):
    print(f"\n=== {name} ===")
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    ok = result.returncode == 0
    checks.append((name, ok, result.stdout + result.stderr))
    if ok:
        print("OK")
    else:
        print("FAILED:\n", result.stdout[-2000:], result.stderr[-2000:])
    return ok


def main():
    # 1. 语法检查
    run("compileall qcc", [sys.executable, "-m", "compileall", "-q",
                               "qcc"])
    # 2. 无残留 ppci 导入
    grep = subprocess.run(
        ["grep", "-rn", "-E", "^\\s*(from|import) ppci", "qcc",
         "--include=*.py"],
        cwd=str(REPO), capture_output=True, text=True)
    ok = grep.returncode == 1  # grep 无匹配返回 1
    checks.append(("审计: 无 ppci 导入", ok, grep.stdout))
    print(f"\n=== 审计: 无 ppci 导入 ===\n{'OK' if ok else 'FAILED: ' + grep.stdout}")
    # 3. 导入守卫
    run("导入守卫 (ppci 屏蔽下 import qcc)", [
        sys.executable, "-c",
        "import sys; sys.path.insert(0, '.'); sys.modules['ppci'] = None; "
        "import qcc.api; "
        "assert all(qcc.api.get_arch(m) for m in "
        "['x86_64', 'riscv', 'arm', 'example']); print('import OK')",
    ])
    # 4. 端到端：C → ELF（三个架构）
    run("端到端 C → ELF (x86_64/riscv/arm)", [
        sys.executable, "-c",
        """
import io, sys, tempfile, os
sys.path.insert(0, '.')
sys.modules['ppci'] = None
from qcc.api import cc, link, objcopy
from qcc.backend.format.elf import read_elf
for march in ['x86_64', 'riscv', 'arm']:
    src = io.StringIO('int add(int a, int b) { return a + b; } '
                      'int main(void) { return add(2, 3); }')
    obj = cc(src, march, opt_level=2)
    with open('tests/data/layout.mmap') as layout:
        exe = link([obj], layout=layout)
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 't.elf')
        objcopy(exe, 'code', 'elf', out)
        elf = read_elf(open(out, 'rb'))
        assert 'code' in [s.name for s in elf.sections], march
print('E2E OK')
""",
    ])
    # 5. 全量测试
    run("pytest tests/", [sys.executable, "-m", "pytest", "tests/", "-q"])

    failed = [c for c in checks if not c[1]]
    print("\n===== 结果 =====")
    for name, ok, _ in checks:
        print(("PASS " if ok else "FAIL ") + name)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
