"""尾调用优化（TCO）pass。

功能说明：
    本文件实现自尾递归的尾调用优化：当函数最后做的一件事是"调用自身并把结果
    直接 return"时，这次调用不必保留当前栈帧——把 "return call f(...)" 改写为
    "跳回函数开头"，递归参数通过 Phi 节点沿 CFG 回边传递，递归因此变成循环，
    栈帧不再随递归深度增长（fib(10000) 从爆栈变成跑循环）。

    在 前端 → 中端 → 后端 流水线中的位置：
    属于中端优化阶段，由 api.optimize() 调用，排在 mem2reg（Phi 语义就绪）与
    CSE 之后、LoadAfterStorePass 之前；它制造的新入口块（只含一条 Jump）由流水线
    末尾的 CleanPass 做 CFG 整形消化。

    关键类/函数：
    - TailCallOptimization：继承 FunctionPass 的 pass 主体；
    - on_function：用五条精确条件检测尾调用（块以 Return 收尾、倒数第二条是
      FunctionCall、return 的正是调用结果、且 callee 就是函数自身）；
    - _replace_entry：新建入口块，为每个参数在旧入口放 Phi（循环变量）；
    - rewrite_tailcalls：把每个 "call + return" 改写成 "jump 回循环头 + 给 Phi 添回边"。

    局限性：因 callee is function 的对象身份检查，只优化直接自尾递归，
    互递归与函数指针间接调用被保守放过。

    详见 docs/tailcall.py.md。
"""

from qcc.midend import ir
from qcc.midend.opt.transform import FunctionPass


class TailCallOptimization(FunctionPass):
    """尾调用优化：把对函数自身的尾调用改写为跳转（递归变循环）。

    Tail call optimization.

    This optimization replaces calls to the function
    itself with jumps.

    For example, the following function contains a tail call:

    i32 fib(i32 n)
    {
      block0: {
        cjmp n > 0 ? block1 : block2
      }

      block1: {
        return call fib(n - 2)
      }

      block2: {
        i32 c = 1;
        return c;
      }
    }

    It could also become this:

    i32 fib(i32 n)
    {
      block3: {  // entry
        jmp block0;
      }

      block0: {
        i32 n_phi = {block3: n, block1: n2}
        cjmp n_phi > 0 ? block1 : block2
      }

      block1: {
        i32 n2 = n_phi - 2;
        jmp block0;
      }

      block2: {
        i32 c = 1;
        return c;
      }
    }

    In the latter case, the return call combination is replaced
    with a jump to the start of the function.
    """

    def on_function(self, function):
        """第一步：扫描所有块，收集满足五条条件的自尾调用（块尾 call + return）。"""
        # Check if there are any tail calls. If not, we are done.
        # 五条条件：块至少两条指令；末条是 Return；倒数第二条是 FunctionCall；
        # return 的正是该调用的结果（尾位置的本质）；被调者就是本函数自身
        tail_calls = []
        for block in function:
            if (
                len(block) >= 2
                and isinstance(block[-1], ir.Return)
                and isinstance(block[-2], ir.FunctionCall)
                and block[-2] is block[-1].result
                and block[-2].callee is function
            ):
                tail_calls.append((block[-1], block[-2]))

        # 没有尾调用就什么都不做，避免无谓改动
        if tail_calls:
            self.rewrite_tailcalls(function, tail_calls)

    def _replace_entry(self, function):
        """第二步：改造函数入口为"循环头"（新入口 + 每个参数的 Phi）。

        Replace tail calls by jumps to the old entry of this function."""
        z = []
        z.append((function.entry, function.arguments))
        # 新入口块插到块列表最前，保持"入口块排第一"的布局约定
        new_entry = ir.Block("new_entry")
        function.add_block(new_entry)
        function.blocks.insert(0, function.blocks.pop())
        old_entry = function.entry
        function.entry = new_entry
        new_entry.add_instruction(ir.Jump(old_entry))

        # Insert phi nodes for each argument:
        # 参数改用 Phi：Phi 初值来自新入口，后续迭代值来自各尾调用点
        arg_phis = []
        for argument in function.arguments:
            arg_phi = ir.Phi(argument.name, argument.ty)
            old_entry.insert_instruction(arg_phi)
            argument.replace_by(arg_phi)
            arg_phis.append(arg_phi)

            # Add the trivial input branch for the phi node from entry:
            arg_phi.set_incoming(new_entry, argument)
        return old_entry, arg_phis

    def rewrite_tailcalls(self, function, tail_calls):
        """第三步：把每个 "call + return" 改写成 "jump 回循环头 + 给 Phi 添回边"。

        Change all recursive tail calls into jumps."""
        old_entry, arg_phis = self._replace_entry(function)

        # Replace tail calls by call to new block
        for tail, tail_call in tail_calls:
            assert isinstance(tail_call, ir.FunctionCall)
            block = tail.block

            # Replace tail call by jump:
            # 实参都是本块内已算好的纯值（如 n2 = sub ... 仍在 Jump 之前），
            # 删掉 call 不丢计算，只是把"传参开新帧"改成"沿 Phi 边流回循环头"
            tail.remove_from_block()
            tail_call.remove_from_block()
            block.add_instruction(ir.Jump(old_entry))

            # Add input for all arguments to the argument phis:
            # 回边入口：跳回 old_entry 时，Phi 选中的是本次"调用"传的新参数
            for arg_phi, arg_value in zip(arg_phis, tail_call.arguments):
                arg_phi.set_incoming(block, arg_value)
