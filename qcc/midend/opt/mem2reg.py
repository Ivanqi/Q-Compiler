"""内存到寄存器提升（mem2reg，即 SSA 构造）pass。

功能说明：
    本文件实现中端优化器里最重要的 pass——内存到寄存器提升。
    C 前端（codegenerator）故意生成"笨 IR"：每个局部变量都 alloca 一块栈空间，
    写变量用 store、读变量用 load；本 pass 把"只被 store/load 使用"的内存位置
    提升为纯 SSA 值（必要时在支配边界插入 Phi 节点），消掉全部局部内存访问，
    之后才谈得上常量折叠、CSE、死代码删除等进一步优化。

    在 前端 → 中端 → 后端 流水线中的位置：
    属于中端优化阶段，由 api.optimize() 调用；排在优化 pass 列表第一位，
    必须先于 ConstantFolder / LoadAfterStorePass / CleanPass 运行（它们都期待
    SSA 形式）；整个 pass 列表连跑三遍，以处理 CFG 变化后新暴露的提升机会。

    关键类/函数：
    - is_alloc_promotable：一组安全检查，判定某个 alloc 是否可以提升；
    - Mem2RegPromotor：pass 主体（FunctionPass），实现经典 Cytron 两步算法——
      place_phi_nodes 用迭代支配边界在会合点放置 Phi，
      rename 沿支配树用"到达值栈"重命名（替换 load、填 Phi 入口），
      promote 负责编排单个 alloc 的提升与善后清理。

    详见 docs/mem2reg.py.md。

This file implements memory to register promotion.

When a memory location is only used by store and load, the stored value can
also be stored into a register, to improve performance.
"""

from qcc.midend import ir
from qcc.midend.graph.domtree import CfgInfo
from qcc.midend.opt.transform import FunctionPass


def is_alloc_promotable(alloc_inst: ir.Alloc):
    """检查 alloc 是否只被取地址 + load/store 使用（可提升的前提）。

    Check if alloc value is only used by load and store operations."""
    assert isinstance(alloc_inst, ir.Alloc)
    if len(alloc_inst.used_by) != 1:
        return False

    addr_inst = list(alloc_inst.used_by)[0]
    if not isinstance(addr_inst, ir.AddressOf):
        return False

    if not addr_inst.used_by:
        return False

    # Check if alloc is only used by load and store instructions:
    # 地址一旦被传出（作为实参、参与指针运算等），提升就不安全，必须保守拒绝
    if not all(
        isinstance(use, (ir.Load, ir.Store)) for use in addr_inst.used_by
    ):
        return False

    # Extract loads and stores:
    loads = [i for i in addr_inst.used_by if isinstance(i, ir.Load)]
    stores = [i for i in addr_inst.used_by if isinstance(i, ir.Store)]

    # Check if the alloc is used as a value instead of an address:
    if any(store.value is addr_inst for store in stores):
        return False

    # Check for volatile:
    if any(mem_op.volatile for mem_op in stores + loads):
        return False

    # Check for types:
    load_types = [load.ty for load in loads]
    store_types = [store.value.ty for store in stores]
    all_types = load_types + store_types
    assert all_types
    return all(all_types[0] is ty for ty in all_types)

    # Check that the alloc has the right amount of bytes:
    # phi_type = all_types[0]
    # TODO: re-enable this check, but it requires target knowledge?
    # if alloc_inst.amount != phi_type.byte_size:
    #    return False


class Mem2RegPromotor(FunctionPass):
    """把"仅被 load/store 使用"的 alloc 指令提升为 SSA 值与 Phi 节点。

    Tries to find alloc instructions only used by load and store
    instructions and replace them with values and phi nodes"""

    def place_phi_nodes(self, stores, phi_ty, name, cfg_info):
        """算法第一步：在需要的地方放置 Phi 函数（迭代支配边界）。

        变量在块 x 中有定义，则 x 的支配边界 df(x) 中每个块都需要一个 Phi；
        新插入的 Phi 本身也是"定义"，所以要用工作列表迭代到不动点。

        Step 1: place phi-functions where required:
        Each node in the df(x) requires a phi function,
        where x is a block where the variable is defined.
        """
        defining_blocks = {st.block for st in stores}

        # Create worklist:
        block_backlog = set(defining_blocks)

        has_phi = set()

        phis = []
        idx = 0
        while block_backlog:
            defining_block = block_backlog.pop()
            for frontier_block in cfg_info.df[defining_block]:
                if frontier_block not in has_phi:
                    has_phi.add(frontier_block)
                    block_backlog.add(frontier_block)
                    phi_name = f"phi_{name}_{idx}"
                    idx += 1
                    phi = ir.Phi(phi_name, phi_ty)
                    phis.append(phi)
                    frontier_block.insert_instruction(phi)
        return phis

    def rename(self, initial_value, phis, loads, stores, cfg_info):
        """算法第二步：沿支配树自顶向下重命名（用"到达值栈"传播当前值）。

        Step 2: renaming:

        Start a top down sweep over the dominator tree to visit all
        statements
        """
        # 到达值栈：栈顶即"变量在当前程序点必然持有的值"
        stack = [initial_value]

        def search(tree_node):
            # Get the cfg node and block from the dominator tree node
            cfg_node = tree_node.node
            if not cfg_info.has_block(cfg_node):
                return

            block = cfg_info.get_block(cfg_node)

            # Crawl down block:
            defs = 0
            for instruction in block:
                # Phi 与 store 都是"定义"，把新值压栈；
                # load 则被替换成当前栈顶值（重命名的核心动作）。
                if instruction in phis:
                    stack.append(instruction)
                    defs += 1

                if instruction in stores:
                    stack.append(instruction.value)
                    defs += 1

                if instruction in loads:
                    # Replace all uses of a with cur_V
                    instruction.replace_by(stack[-1])
                    alloc = instruction.address
                    assert isinstance(alloc, ir.AddressOf)
                    # self.debug_db.map(aloc, stack[-1])

            # At the end of the block
            # For all successors with phi functions, insert the proper
            # variable:
            # 块尾：给每个后继块中本变量的 Phi 设置"从本块过来时的值"
            for successor_node in cfg_info.cfg.successors(cfg_node):
                if not cfg_info.has_block(successor_node):
                    continue
                successor_block = cfg_info.get_block(successor_node)
                for phi in (p for p in phis if p.block == successor_block):
                    phi.set_incoming(block, stack[-1])

            # Recurse into children:
            # 递归进入支配树子节点：先父后子的顺序保证子块继承父块的"到达值"
            for child_tree_node in tree_node.children:
                search(child_tree_node)

            # Cleanup stack:
            # 退出本块：弹出本块压入的定义，恢复父块视角
            for _ in range(defs):
                stack.pop(-1)

        search(cfg_info.cfg.root_tree)

    def promote(self, alloc: ir.Alloc, cfg_info):
        """提升单个 alloc 指令：把 load 替换为赋值并清理内存操作。

        Promote a single alloc instruction.

        Find load operations and replace them with assignments.
        """
        name = alloc.name
        addr = list(alloc.used_by)[0]

        loads = [i for i in addr.used_by if isinstance(i, ir.Load)]
        stores = [i for i in addr.used_by if isinstance(i, ir.Store)]

        self.logger.debug(
            "Promoting alloc %s used by %s load and %s stores",
            alloc,
            len(loads),
            len(stores),
        )

        # Determine the type of the phi node:
        load_types = [load.ty for load in loads]
        store_types = [store.value.ty for store in stores]
        all_types = load_types + store_types
        assert all_types
        phi_ty = all_types[0]

        # If loads are found, we need phi nodes:
        if loads:
            phis = self.place_phi_nodes(stores, phi_ty, name, cfg_info)

            # Preserve debug info:
            for phi in phis:
                self.debug_db.map(alloc, phi)

            # Create undefined value at start:
            # 入口处放 undef 占位：给"某些路径从未给变量赋值"的情况一个初值，
            # 保证 SSA 形式完整（C 允许使用未初始化变量）
            initial_value = ir.Undefined(f"und_{name}", phi_ty)
            alloc.function.entry.insert_instruction(initial_value)

            self.rename(initial_value, phis, loads, stores, cfg_info)

            # Check that all phis have the proper number of inputs.
            for phi in phis:
                assert len(phi.inputs) == len(
                    cfg_info.cfg.predecessors(cfg_info.get_node(phi.block))
                )

            # Remove unused instructions:
            # 反复迭代删除没人使用的 undef/Phi，直到不动点
            # （删掉一个 Phi 可能让另一个 Phi 变成无用）
            new_instructions = [initial_value] + phis
            while True:
                change = False
                for i in new_instructions:
                    if not i.is_used:
                        i.remove_from_block()
                        new_instructions.remove(i)
                        change = True
                if not change:
                    break

        # Each store instruction can be removed.
        for store in stores:
            store.remove_from_block()

        # Remove all load instructions:
        for load in loads:
            assert not load.is_used, str(load.used_by) + str(load)
            load.remove_from_block()

        # Finally the addr instruction can be deleted:
        assert not addr.is_used
        addr.remove_from_block()

        # Remove alloc from block:
        assert not alloc.is_used
        alloc.remove_from_block()

    def on_function(self, function):
        """对单个函数：先算 CFG/支配信息，再逐个尝试提升块内的 alloc。"""
        cfg_info = CfgInfo(function)
        for block in function.blocks:
            allocs = [i for i in block if isinstance(i, ir.Alloc)]
            for alloc in allocs:
                if is_alloc_promotable(alloc):
                    self.promote(alloc, cfg_info)
