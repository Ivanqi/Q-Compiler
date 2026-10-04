"""局部公共子表达式消除（CSE）pass。

功能说明：
    本文件实现基本块内的公共子表达式消除：同一块里重复计算的相同表达式
    （如两次 add a, b，或两个相同的 const 3）只保留第一次的结果，后出现的
    指令用 replace_by 直接指向先出现的结果，省掉重复计算。

    在 前端 → 中端 → 后端 流水线中的位置：
    属于中端优化阶段，由 api.optimize() 调用，排在 mem2reg（SSA 化）与
    ConstantFolder 之后、TailCallOptimization/LoadAfterStorePass 之前，
    它制造的"孤儿指令"由流水线中更靠后的 DeleteUnusedInstructionsPass 清理。

    关键类/函数：
    - CommonSubexpressionEliminationPass：继承 BlockPass（作用域为单个基本块，
      因此是局部 CSE，不跨块）；on_block 用一张"表达式 → 最早结果"的哈希表
      完成替换。Binop 的键为 (操作数a, 运算符, 操作数b, 结果类型)，
      Const 的键为 (值, 类型)——带类型是为了防止 i32/i64 常量被错误合并。

    详见 docs/cse.py.md。
"""

from qcc.midend import ir
from qcc.midend.opt.transform import BlockPass


class CommonSubexpressionEliminationPass(BlockPass):
    """在单个基本块内消除公共子表达式（把后出现的同键指令指向先出现者）。

    Replace common sub expressions (cse) with the previously defined one.
    """

    def on_block(self, block):
        # ins_map 每次进入块时新建——这就是"局部 CSE"的由来：
        # 块 A 里见过的表达式不会带到块 B（跨块消重需要 GVN）
        ins_map = {}
        stats = 0
        for i in block:
            # 键中放的是 Value 对象本身（默认按身份比较）与类型：
            # SSA 下同一 Value 不可变，身份相等 ⇒ 值必然相等，保守且正确
            if isinstance(i, ir.Binop):
                k = (i.a, i.operation, i.b, i.ty)
            elif isinstance(i, ir.Const):
                k = (i.value, i.ty)
            else:  # pragma: no cover
                # This branch is actually covered, but is optimized by
                # the python peep-hole optimizer!
                # 其他指令（load/call/phi/cast 等）可能有副作用或地址敏感，
                # 一律不碰；该分支被 Python 窥孔优化器优化掉，故覆盖率工具误报
                continue
            if k in ins_map:
                ins_new = ins_map[k]
                i.replace_by(ins_new)
                stats += 1
            else:
                ins_map[k] = i
        if stats > 0:
            self.logger.debug("Replaced %i instructions", stats)
