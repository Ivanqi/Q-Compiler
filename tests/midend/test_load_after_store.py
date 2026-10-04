"""Load-after-store 优化（LoadAfterStorePass）测试。

对应 src/qcc/midend/opt/load_after_store.py（需求4 列出的迁移模块之一）。
ppci 原测试套件未直接覆盖本 pass，这里补充。
"""
import unittest

from qcc.backend.binutils.debuginfo import DebugDb
from qcc.midend import ir, irutils
from qcc.midend.irutils import verify_module
from qcc.midend.opt.load_after_store import LoadAfterStorePass


class LoadAfterStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.debug_db = DebugDb()
        self.builder = irutils.Builder()
        self.module = ir.Module("test", debug_db=self.debug_db)
        self.builder.set_module(self.module)
        self.function = self.builder.new_procedure(
            "testfunction", ir.Binding.GLOBAL
        )
        self.builder.set_function(self.function)
        entry = self.builder.new_block()
        self.function.entry = entry
        self.builder.set_block(entry)

    def tearDown(self):
        verify_module(self.module)

    def _make_alloc(self):
        """构造 alloc + addressof 的常见模式"""
        alloc = self.builder.emit(ir.Alloc("A", 4, 4))
        return alloc, self.builder.emit(ir.AddressOf(alloc, "addr"))

    def test_replace_load_after_store(self):
        """store 后紧跟同一地址的 load：load 的使用者直接拿到所存的值"""
        _, addr = self._make_alloc()
        cnst = self.builder.emit(ir.Const(7, "cnst", ir.i32))
        self.builder.emit(ir.Store(cnst, addr))
        load = self.builder.emit(ir.Load(addr, "Ld", ir.i32))
        s = self.builder.emit(ir.add(load, cnst, "s", ir.i32))
        self.builder.emit(ir.Exit())

        LoadAfterStorePass().run(self.module)

        # load 的使用者被重写为所存的值
        self.assertFalse(load.is_used)
        self.assertIs(s.a, cnst)
        self.assertIs(s.b, cnst)

    def test_no_replace_load_from_other_address(self):
        """不同地址的 load 不受影响"""
        _, addr1 = self._make_alloc()
        _, addr2 = self._make_alloc()
        cnst = self.builder.emit(ir.Const(7, "cnst", ir.i32))
        self.builder.emit(ir.Store(cnst, addr1))
        load = self.builder.emit(ir.Load(addr2, "Ld", ir.i32))
        self.builder.emit(ir.Exit())

        LoadAfterStorePass().run(self.module)

        self.assertIn(load, self.function.entry.instructions)

    def test_volatile_load_not_replaced(self):
        """volatile load 不可被优化掉"""
        _, addr = self._make_alloc()
        cnst = self.builder.emit(ir.Const(7, "cnst", ir.i32))
        self.builder.emit(ir.Store(cnst, addr))
        load = self.builder.emit(ir.Load(addr, "Ld", ir.i32, volatile=True))
        self.builder.emit(ir.Exit())

        LoadAfterStorePass().run(self.module)

        self.assertIn(load, self.function.entry.instructions)

    def test_redundant_store_removed(self):
        """同一地址连续两次 store：前一次是多余的，被删除"""
        _, addr = self._make_alloc()
        c1 = self.builder.emit(ir.Const(1, "c1", ir.i32))
        c2 = self.builder.emit(ir.Const(2, "c2", ir.i32))
        s1 = self.builder.emit(ir.Store(c1, addr))
        self.builder.emit(ir.Store(c2, addr))
        self.builder.emit(ir.Exit())

        LoadAfterStorePass().run(self.module)

        self.assertNotIn(s1, self.function.entry.instructions)


if __name__ == "__main__":
    unittest.main()
