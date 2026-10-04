"""基本块内的 store/load 转发与冗余存储消除 pass。

功能说明：
    mem2reg 只能提升"纯局部变量"（alloc 仅被 load/store 使用的那种），全局变量、
    数组元素、结构体字段、指针指向的内存的访问仍以 store/load 形式留在 IR 中，
    本 pass 专门收拾这些残留：单块内做两类化简——① load-after-store 转发：
    刚 store 过的地址立刻 load，直接把 load 替换成存进去的值；② 冗余 store
    消除（死存储消除）：同一地址连续两次 store 且中间无人读，删掉第一次。

    在 前端 → 中端 → 后端 流水线中的位置：
    属于中端优化阶段，由 api.optimize() 调用，排在 mem2reg 与 CSE 之后（CSE 先把
    计算同一地址的重复指针算术合并，本 pass 才看得到"同地址"）、
    DeleteUnusedInstructionsPass 之前（转发产生的孤儿 load 需要 DCE 收尸）。

    关键类/函数：
    - LoadAfterStorePass：继承 BlockPass（作用域为单个基本块）；
    - find_store_backwards：核心查找，从某条指令向前找"最近一次同地址 store"，
      遇到不同地址的 store 或函数调用就保守停下（无别名分析）；
    - replace_load_after_store / remove_redundant_stores：两个阶段。
    注意：volatile 访问一律不碰（volatile 语义要求每次真实访存）。

    详见 docs/load_after_store.py.md。
"""

from qcc.midend import ir
from qcc.midend.opt.transform import BlockPass


class LoadAfterStorePass(BlockPass):
    """块内优化：把紧跟 store 的 load 转发为存进去的值，并删除冗余 store。

    Remove load after store to the same location.

    .. code::

        [x] = a
        b = [x]
        c = b + 2

    transforms into:

    .. code::

        [x] = a
        c = a + 2
    """

    def find_store_backwards(
        self, i, ty, stop_on=(ir.FunctionCall, ir.ProcedureCall, ir.Store)
    ):
        """从指令 i 向前（块首方向）查找"最近一次同地址 store"。

        Go back from this instruction to beginning"""
        block = i.block
        instructions = block.instructions
        pos = instructions.index(i)
        for x in range(pos - 1, 0, -1):
            i2 = instructions[x]
            if isinstance(i2, ir.Store) and ty is i2.value.ty:
                # Got first store!
                # 地址相同 → 找到最近写同地址的 store；地址不同 → 保守放弃：
                # 中间隔着"可能别名的写"（无别名分析，p/q 是否同址无法证明），
                # 跨过它做转发可能读到错值
                if i2.address is i.address:
                    return i2
                else:
                    return None
            elif isinstance(i2, stop_on):
                # A call can change memory, store not found..
                # 调用能写任意内存，它之前的 store 状态不可信，保守停下
                return None
        return None

    def on_block(self, block):
        """对单块的入口：先做 load 转发，再做冗余 store 消除。"""
        self.replace_load_after_store(block)
        self.remove_redundant_stores(block)

    def replace_load_after_store(self, block):
        """阶段一：把"刚 store 过就 load"的 load 替换成存进去的值。

        Replace load after store with the value of the store"""
        load_instructions = [
            ins
            for ins in block
            if isinstance(ins, ir.Load) and not ins.volatile
        ]

        # Replace loads after store of same address by the stored value:
        count = 0
        for load in load_instructions:
            # Find store instruction preceeding this load:
            store = self.find_store_backwards(load, load.ty)
            if store is not None:
                # Assert type equivalence:
                assert load.ty is store.value.ty
                load.replace_by(store.value)
                count += 1
                # TODO: after one try, the instructions are different
                # reload of instructions required?
        if count > 0:
            self.logger.debug("Replaced %s loads after store", count)

    def remove_redundant_stores(self, block):
        """阶段二：两次 store 同一地址且中间无人读时，删除前一次 store。

        From two stores to the same address remove the previous one"""
        store_instructions = [
            i for i in block if isinstance(i, ir.Store) and not i.volatile
        ]

        count = 0
        # TODO: assume volatile memory stores always!
        # Replace stores to the same location:
        # stop_on 比起阶段一多了 ir.Load：两次 store 之间若夹着 load，
        # 前一次 store 的值可能已被读走，不能删（不变量：中间无 load 无调用
        # ⟺ 第一次 store 的值没有任何观察者 ⟹ 删除安全）
        for store in store_instructions:
            store_prev = self.find_store_backwards(
                store,
                store.value.ty,
                stop_on=(ir.FunctionCall, ir.ProcedureCall, ir.Store, ir.Load),
            )
            if store_prev is not None and not store_prev.volatile:
                store_prev.remove_from_block()

        if count > 0:
            self.logger.debug("Replaced %s redundant stores", count)
