"""功能说明
========

后端指令选择的第一道工序：把 IR（每个基本块）翻译成"选择 DAG"（selection DAG，有向无环图）。

DAG 表示单个基本块的计算逻辑——数据依赖体现为节点之间的值边（同一 IR 值多处使用时天然共享，
这是 DAG 相对树的优势）；load/store/调用等有副作用的节点用一条"控制链"（token/ctrl）串起先后顺序。
为使用树匹配做指令选择，DAG 随后由 dagsplit.py 拆成一系列树（"树的森林"）。

在前端 → 中端 → 后端流水线中的位置::

    ir.Function ──（本模块 SelectionGraphBuilder.build）──▶ SelectionGraph
                  ──（dagsplit.DagSplitter）──▶ 树 Forest ──（instructionselector）──▶ 抽象机器指令

关键类/函数：
- SelectionGraphBuilder：主体；@make_map 装饰器把 do_xxx 方法自动注册进 f_map，按 IR 指令类型分发；
- prepare_function_info：铺好标签、Phi 虚拟寄存器、参数与返回值位置（与 frame 共享 vreg）；
- FunctionInfo：每个函数的"全局工作台"（value_map / label_map / phi_map / block_tails）；
- chain()：控制链核心机制；copy_phis_of_successors()：Phi 的两步并行拷贝。

详见 docs/irdag.py.md

IR to DAG

The process of instruction selection is preceeded by the creation of
a selection DAG (directed acyclic graph). The dagger take ir-code as
input and produces such a dag for instruction selection.

A DAG represents the logic (computation) of a single basic block.

To do selection with tree matching, the DAG is then splitted into a
series of tree patterns. This is often referred to as a forest of trees.

"""

import itertools
import logging

from qcc.midend import ir
from qcc.backend.arch.generic_instructions import Label
from qcc.backend.arch.stack import StackLocation
from qcc.backend.binutils.debuginfo import FpOffsetAddress
from qcc.backend.codegen.selectiongraph import SelectionGraph, SGNode, SGValue


def prepare_function_info(arch, function_info, ir_function):
    """预备函数级信息：基本块标签、Phi 的虚拟寄存器、参数与返回值位置。

    Fill function info with labels for all basic blocks"""
    # First define labels and phis:

    # 统一的收尾标签：所有 return / exit 都跳到它
    function_info.epilog_label = Label(ir_function.name + "_epilog")

    for ir_block in ir_function:
        # Put label into map:
        function_info.label_map[ir_block] = Label(ir_block.name)

        # Create virtual registers for phi-nodes:
        # SSA 的 Phi 在机器层就是"各前驱把值 copy 进同一个 vreg"，这里先分配好该 vreg
        for phi in ir_block.phis:
            vreg = function_info.frame.new_reg(
                arch.get_reg_class(ty=phi.ty), twain=phi.name
            )
            function_info.phi_map[phi] = vreg

    function_info.arg_vregs = []
    function_info.arg_types = [a.ty for a in ir_function.arguments]
    # 参数位置：架构实现 determine_arg_locations 时按调用约定排（如 x86_64 的 RDI/RSI…），否则按序号
    if hasattr(arch, "determine_arg_locations"):
        arg_locs = arch.determine_arg_locations(function_info.arg_types)
    else:
        arg_locs = range(len(ir_function.arguments))

    for arg, phys_loc in zip(ir_function.arguments, arg_locs):
        if arg.ty in arch.info.value_classes:
            # New vreg:
            vreg = function_info.frame.new_reg(
                arch.info.value_classes[arg.ty], twain=arg.name
            )
        else:
            # Allocate space on stack for this argument:
            # vreg = function_info.frame.alloc(arg.ty.size, 1)
            # print(phys_loc)
            # 非标量（blob）类型不走寄存器，直接使用栈上传参的物理位置
            vreg = phys_loc
        function_info.arg_vregs.append(vreg)

    if isinstance(ir_function, ir.Function):
        if ir_function.return_ty in arch.info.value_classes:
            function_info.rv_vreg = function_info.frame.new_reg(
                arch.get_reg_class(ty=ir_function.return_ty), twain="retval"
            )
        else:
            function_info.rv_vreg = None


class FunctionInfo:
    """单个函数的全局工作台：frame、IR 值 → 图输出的映射（value_map）、标签表、Phi 的 vreg 等。

    Keeps track of global function data when generating code for part
    of a functions."""

    def __init__(self, frame):
        self.frame = frame
        self.value_map = {}  # mapping from ir-value to dag node
        self.label_map = {}
        self.epilog_label = None
        self.phi_map = {}  # mapping from phi node to vreg
        self.block_tails = {}


def depth_first_order(function):
    """按（先进先出的）深度优先序返回基本块——保证块序与支配关系相容，Phi 拷贝时机正确。

    Return blocks in depth first search order"""
    blocks = [function.entry]
    L = [function.entry]
    while L:
        b = L.pop(0)
        for b2 in b.successors:
            if b2 not in blocks:
                blocks.append(b2)
                L.append(b2)
    return blocks


class Operation:
    """节点上的操作标记（操作名 + IR 类型，如 ("ADD", i32)，__str__ 渲染为 ADDI32）。

    A single operation with a type"""

    def __init__(self, op, ty):
        self.op = op
        self.ty = ty
        # assert ty, str(op)+str(ty)
        if op == "MOV" and ty is None:
            raise AssertionError("MOV must not have type none")

    def __str__(self):
        if self.ty is None or self.op in ["LABEL", "CALL"]:
            return self.op.upper()
        else:
            return f"{self.op}{str(self.ty)}".upper()


def make_map(cls):
    """类装饰器：把 do_xxx 方法自动注册进 f_map，键为对应的 IR 类（do_c_jump → ir.CJump）。

    Add an attribute to the class that is a map of ir types to handler
    functions. For example if a function is called do_phi it will be
    registered into f_map under key ir.Phi.
    """
    f_map = getattr(cls, "f_map")
    for name, func in list(cls.__dict__.items()):
        if name.startswith("do_"):
            tp_name = "".join(x.capitalize() for x in name[2:].split("_"))
            ir_class = getattr(ir, tp_name)
            f_map[ir_class] = func
    return cls


@make_map
class SelectionGraphBuilder:
    """主体：把一个函数翻译成选择 DAG（按基本块分组），供指令选择器拆树匹配。

    Create a selectiongraph from a function for instruction selection"""

    logger = logging.getLogger("selection-graph-builder")
    f_map = {}

    def __init__(self, arch):
        self.arch = arch
        # size_map = {8: ir.i8, 16: ir.i16, 32: ir.i32, 64: ir.i64}
        self.ptr_ty = arch.info.type_infos["ptr"]

    def build(self, ir_function: ir.SubRoutine, function_info, debug_db):
        """总入口：为给定函数创建选择 DAG（按基本块分组）。

        Create a selection graph for the given function.

        Selection graph is divided into groups for each basic block.
        """
        self.debug_db = debug_db
        self.sgraph = SelectionGraph()
        self.function_info = function_info

        # TODO: fix this total mess with vreg, block and chains:
        self.current_block = None

        # Create maps for global variables:
        # 全局变量/函数/外部符号各建一个 LABEL 节点并映射（名字即符号名）
        for variable in itertools.chain(
            ir_function.module.variables,
            ir_function.module.functions,
            ir_function.module.externals,
        ):
            val = self.new_node("LABEL", ir.ptr)
            val.value = variable.name
            self.add_map(variable, val.new_output(variable.name))

        # 控制链的起点：ENTRY 节点产出的控制 token
        self.current_token = self.new_node("ENTRY", None).new_output(
            "token", kind=SGValue.CONTROL
        )

        # Create temporary registers for aruments:
        for arg, vreg in zip(ir_function.arguments, function_info.arg_vregs):
            if isinstance(vreg, StackLocation):
                param_node = self.new_node("FPREL", ir.ptr, value=vreg)
                output = param_node.new_output(arg.name)
                output.wants_vreg = False
            else:
                param_node = self.new_node("REG", arg.ty, value=vreg)
                output = param_node.new_output(arg.name)
                output.vreg = vreg

            # When refering the paramater, use the copied value:
            self.add_map(arg, output)

            self.chain(param_node)

        # Generate nodes for all blocks:
        for ir_block in depth_first_order(ir_function):
            self.block_to_sgraph(ir_block, function_info)

        # 自检：所有边的源/目的节点都必须在图中
        self.sgraph.check()
        return self.sgraph

    def block_to_sgraph(self, ir_block: ir.Block, function_info):
        """把单个基本块翻译成 DAG：逐条指令经 f_map 分发到 do_xxx 处理器。

        Create dag (directed acyclic graph) from a basic block.

        The resulting dag can be used for instruction selection.
        """
        assert isinstance(ir_block, ir.Block)

        self.current_block = ir_block

        # Create start node:
        entry_node = self.new_node("ENTRY", None)
        entry_node.value = ir_block
        self.current_token = entry_node.new_output(
            "token", kind=SGValue.CONTROL
        )

        # Generate series of trees:
        for instruction in ir_block:
            # In case of last statement, first perform phi-lifting:
            # 终止符之前必须先把后继块的 Phi 输入拷贝好（Phi 的并行语义）
            if instruction.is_terminator:
                self.copy_phis_of_successors(ir_block)

            # Dispatch the handler depending on type:
            # 按 IR 指令的具体类型查表分发（表由 @make_map 装饰器自动建立）
            self.f_map[type(instruction)](self, instruction)

        # Save tail node of this block:
        function_info.block_tails[ir_block] = self.current_token.node

        # Create end node:
        sgnode = self.new_node("EXIT", None)
        sgnode.add_input(self.current_token)

    def do_jump(self, node):
        """处理无条件跳转：建 JMP 节点（目标标签存入 value）并接入控制链。"""
        sgnode = self.new_node("JMP", None)
        sgnode.value = self.function_info.label_map[node.target]
        self.debug_db.map(node, sgnode)
        self.chain(sgnode)

    def chain(self, sgnode):
        """控制链核心：把当前控制 token 连进新节点，新节点的控制输出成为新的当前 token。

        副作用节点（load/store/call/跳转等）借此保证先后顺序；纯运算不进链，保留重排自由。
        """
        if self.current_token is not None:
            sgnode.add_input(self.current_token)
        self.current_token = sgnode.new_output("ctrl", kind=SGValue.CONTROL)

    def new_node(self, name, ty, *args, value=None):
        """创建并登记一个新的选择图节点（ir.ptr 统一替换为架构的指针类型）。

        Create a new selection graph node, and add it to the graph"""
        assert isinstance(name, str)
        assert isinstance(ty, ir.Typ) or ty is None
        # assert isinstance(name, Operation)
        if ty is ir.ptr:
            ty = self.ptr_ty
        sgnode = SGNode(Operation(name, ty))
        sgnode.add_inputs(*args)
        sgnode.value = value
        sgnode.group = self.current_block
        self.sgraph.add_node(sgnode)
        return sgnode

    def new_vreg(self, ty):
        """按类型从 frame 新开一个虚拟寄存器（寄存器分配阶段统一着色）。

        Generate a new temporary fitting for the given type"""
        return self.function_info.frame.new_reg(
            self.arch.info.value_classes[ty]
        )

    def add_map(self, node, sgvalue):
        """登记"IR 值 → 图输出"的映射（value_map 是整张图的翻译记忆）。"""
        assert isinstance(node, ir.Value)
        assert isinstance(sgvalue, SGValue)
        self.function_info.value_map[node] = sgvalue

    def get_value(self, node):
        """查询某个 IR 值对应的图输出（SGValue）。"""
        return self.function_info.value_map[node]

    def do_return(self, node):
        """return 拆成两步：MOV 把返回值搬进返回寄存器，再 JMP 到 epilog 标签。

        Move result into result register and jump to epilog"""
        res = self.get_value(node.result)
        vreg = self.function_info.rv_vreg
        if vreg:
            mov_node = self.new_node("MOV", node.result.ty, res, value=vreg)
            self.chain(mov_node)
        else:  # pragma: no cover
            raise NotImplementedError("Pass pointer as first arg instead")

        # Jump to epilog:
        sgnode = self.new_node("JMP", None)
        sgnode.value = self.function_info.epilog_label
        self.chain(sgnode)

    def do_c_jump(self, node):
        """条件跳转：建 CJMP 节点，value 携带 (条件, yes 标签, no 标签)。

        Process conditional jump into dag"""
        lhs = self.get_value(node.a)
        rhs = self.get_value(node.b)
        assert node.a.ty is node.b.ty
        cond = node.cond
        sgnode = self.new_node("CJMP", node.a.ty, lhs, rhs)
        sgnode.value = (
            cond,
            self.function_info.label_map[node.lab_yes],
            self.function_info.label_map[node.lab_no],
        )
        self.chain(sgnode)
        self.debug_db.map(node, sgnode)

    def do_exit(self, node):
        """exit 指令：直接 JMP 到 epilog 标签。"""
        # Jump to epilog:
        sgnode = self.new_node("JMP", None)
        sgnode.value = self.function_info.epilog_label
        self.chain(sgnode)

    def do_address_of(self, node):
        """取地址：DAG 中直接复用被取地址值的映射（AddressOf 等价于原值）。

        Process ir.AddressOf instruction"""
        address = self.get_value(node.src)
        self.add_map(node, address)
        return address

    def do_alloc(self, node):
        """栈分配：frame.alloc 分一个栈槽，建 FPREL 节点（帧指针 + 偏移寻址）。

        Process the alloc instruction"""
        # TODO: check alignment?
        # fp = self.new_node("REG", ir.ptr, value=self.arch.fp)
        # fp_output = fp.new_output('fp')
        # fp_output.wants_vreg = False
        # offset = self.new_node("CONST", ir.ptr)
        slot = self.function_info.frame.alloc(node.amount, node.alignment)
        # offset_output = offset.new_output('offset')
        # offset_output.wants_vreg = False
        sgnode = self.new_node("FPREL", ir.ptr, value=slot)

        output = sgnode.new_output("alloc")
        # 地址不占寄存器：无需为它物化一个 vreg
        output.wants_vreg = False
        self.add_map(node, output)
        if self.debug_db.contains(node):
            dbg_var = self.debug_db.get(node)
            dbg_var.address = FpOffsetAddress(slot)
        # self.debug_db.map(node, sgnode)

    def do_copy_blob(self, node):
        """大块数据拷贝：建 MOVB 节点（由架构模式展开成 memcpy 序列）。

        Create a memcpy node."""
        dst = self.get_address(node.dst)
        src = self.get_address(node.src)
        sgnode = self.new_node("MOVB", None, dst, src, value=node.amount)
        self.chain(sgnode)

    def get_address(self, ir_address):
        """求 load/store 的地址：全局符号新建 LABEL 节点，普通地址查 value_map。

        Determine address for load or store."""
        if isinstance(ir_address, ir.GlobalValue):
            # A global variable may be contained in another module
            # That is why it is created here, and not in the prepare step
            sgnode = self.new_node("LABEL", ir.ptr)
            sgnode.value = ir_address.name
            address = sgnode.new_output("address")
        else:
            address = self.get_value(ir_address)
        return address

    def do_load(self, node):
        """读内存：建 LDR 节点并接入控制链（必须排在更早的 store/调用之后）。

        Create dag node for load operation"""
        address = self.get_address(node.address)
        sgnode = self.new_node("LDR", node.ty, address)
        # Make sure a data dependence is added to this node
        self.debug_db.map(node, sgnode)
        self.chain(sgnode)
        self.add_map(node, sgnode.new_output(node.name))

    def do_store(self, node):
        """写内存：建 STR 节点（blob 类型改发 MOVB）并接入控制链。

        Create a DAG node for the store operation"""
        address = self.get_address(node.address)
        value = self.get_value(node.value)
        if node.value.ty.is_blob:
            size = node.value.ty.size
            sgnode = self.new_node("MOVB", None, address, value, value=size)
        else:
            sgnode = self.new_node("STR", node.value.ty, address, value)
        self.chain(sgnode)
        self.debug_db.map(node, sgnode)

    def do_inline_asm(self, node):
        """内联汇编：输入值先 MOV 进新 vreg，建 ASM 节点携带 (模板, 输出寄存器, 输入寄存器, clobbers)。

        Create selection graph node for inline asm code.

        This is a little weird, as we really do not need to select
        any instructions, but this special node will be filtered later
        on.
        """

        input_registers = []
        for input_value in node.input_values:
            arg_val = self.get_value(input_value)
            reg_loc = self.new_vreg(input_value.ty)

            mov_sgnode = self.new_node(
                "MOV", input_value.ty, arg_val, value=reg_loc
            )

            self.chain(mov_sgnode)
            input_registers.append(reg_loc)

        if len(node.output_values) > 0:
            issue = (
                "Output registers on asm cannot be greater than "
                "the number of input"
            )
            assert len(node.output_values) <= len(node.input_values), issue

        output_registers = []

        sgnode = self.new_node(
            "ASM",
            None,
            value=(
                node.template,
                output_registers,
                input_registers,
                node.clobbers,
            ),
        )
        self.chain(sgnode)
        self.debug_db.map(node, sgnode)

        for i, (reg, addr) in enumerate(
            zip(node.clobbers, node.output_values)
        ):
            address = self.get_address(addr)

            param_node = self.new_node("REG", address.ty, value=reg)
            output = param_node.new_output("ret_" + str(i))
            output.wants_vreg = False

            sgnode = self.new_node("STR", address.ty, address, output)

            self.chain(sgnode)

    def do_const(self, node):
        """常量：建 CONST 节点且 wants_vreg=False——常量直接作指令立即数，不占寄存器。

        Process constant instruction"""
        if isinstance(node.value, (int, float)):
            value = node.value
        else:  # pragma: no cover
            raise NotImplementedError(str(type(node.value)))
        sgnode = self.new_node("CONST", node.ty)
        self.debug_db.map(node, sgnode)
        sgnode.value = value
        output = sgnode.new_output(node.name)
        output.wants_vreg = False
        self.add_map(node, output)

    def do_literal_data(self, node):
        """字面量数据（字符串等）存入常量池，图上只留一个 LABEL 引用。

        Literal data is stored after a label"""
        label = self.function_info.frame.add_constant(node.data)
        sgnode = self.new_node("LABEL", ir.ptr, value=label)
        self.add_map(node, sgnode.new_output(node.name))

    def do_unop(self, node):
        """一元运算：- → NEG、~ → INV。

        Visit an unary operator and create a DAG node"""
        names = {"-": "NEG", "~": "INV"}
        op = names[node.operation]
        a = self.get_value(node.a)
        sgnode = self.new_node(op, node.ty, a)
        self.debug_db.map(node, sgnode)
        self.add_map(node, sgnode.new_output(node.name))

    def do_binop(self, node):
        """二元运算：IR 运算符翻译成架构无关的操作名（+ → ADD、* → MUL 等）。

        Visit a binary operator and create a DAG node"""
        names = {
            "+": "ADD",
            "-": "SUB",
            "|": "OR",
            "<<": "SHL",
            "*": "MUL",
            "&": "AND",
            ">>": "SHR",
            "/": "DIV",
            "%": "REM",
            "^": "XOR",
        }
        op = names[node.operation]
        a = self.get_value(node.a)
        b = self.get_value(node.b)
        sgnode = self.new_node(op, node.ty, a, b)
        self.debug_db.map(node, sgnode)
        self.add_map(node, sgnode.new_output(node.name))

    def do_cast(self, node):
        """类型转换：同位数且同寄存器类（如 u32↔i32 重解释）时直接共享原值，否则建 I32TO 这类转换节点。

        Create a cast of type"""
        from_ty = node.src.ty
        if from_ty is ir.ptr:
            from_ty = self.ptr_ty

        to_ty = node.ty
        if to_ty is ir.ptr:
            to_ty = self.ptr_ty

        if (
            from_ty.is_integer
            and to_ty.is_integer
            and from_ty.bits == to_ty.bits
            and (
                self.arch.get_reg_class(ty=from_ty)
                is self.arch.get_reg_class(ty=to_ty)
            )
        ):
            # No cast required if:
            # - both types are integer
            # - the integer is the same size
            # - both types use the same register class.
            src_value = self.get_value(node.src)
            self.add_map(node, src_value)
        else:
            # Determine if cast is required.
            op = f"{str(from_ty).upper()}TO"
            a = self.get_value(node.src)
            sgnode = self.new_node(op, node.ty, a)
            self.add_map(node, sgnode.new_output(node.name))

    def do_undefined(self, node):
        """未定义值（mem2reg 留下的 UND）：建 UND 节点，由指令选择器分配一个未初始化寄存器。

        Create node for undefined value."""
        op = "UND"
        sgnode = self.new_node(op, node.ty)
        self.debug_db.map(node, sgnode)
        self.add_map(node, sgnode.new_output(node.name))

    def _prep_call_arguments(self, node):
        """把实参逐个搬到新 vreg：实参随后要按调用约定落到参数寄存器，不能继续占用计算寄存器。

        Prepare call arguments into proper locations"""
        # This is the moment to move all parameters to new temp registers.
        args = []
        for argument in node.arguments:
            arg_val = self.get_value(argument)
            if argument.ty.is_blob:
                args.append((argument.ty, arg_val.node.value))
            else:
                loc = self.new_vreg(argument.ty)
                args.append((argument.ty, loc))
                arg_sgnode = self.new_node(
                    "MOV", argument.ty, arg_val, value=loc
                )
                self.chain(arg_sgnode)
            # reg_out = arg_sgnode.new_output('arg')
            # reg_out.vreg = loc
            # regouts.append(reg_out)
            # inputs.append(arg_sgnode.new_output('x'))
        return args

    def _make_call(self, node, args, rv):
        """建 CALL 节点（value 携带 (目标, 实参, 返回值位置)）并接入控制链。

        直接调用以函数名为目标；间接调用先把函数指针 MOV 进 vreg。
        """
        if isinstance(node.callee, (ir.SubRoutine, ir.ExternalSubRoutine)):
            call_target = node.callee.name
        else:
            fptr = self.get_value(node.callee)

            fptr_vreg = self.new_vreg(ir.ptr)
            fptr_sgnode = self.new_node("MOV", ir.ptr, fptr, value=fptr_vreg)
            self.chain(fptr_sgnode)
            call_target = fptr_vreg

        # Perform the actual call:
        sgnode = self.new_node("CALL", None)
        sgnode.value = (call_target, args, rv)
        self.debug_db.map(node, sgnode)
        # for i in inputs:
        #    sgnode.add_input(i)
        self.chain(sgnode)

    def do_procedure_call(self, node):
        """过程调用（无返回值）：准备实参后直接发 CALL。

        Transform a procedure call"""
        args = self._prep_call_arguments(node)
        self._make_call(node, args, None)

    def do_function_call(self, node):
        """函数调用（有返回值）：实参就位后发 CALL，并用 REG 节点映射结果 vreg 供表达式使用。

        Transform a function call"""
        args = self._prep_call_arguments(node)

        # New register for copy of result:
        ret_val = self.function_info.frame.new_reg(
            self.arch.info.value_classes[node.ty],
            f"{node.name}_result",
        )

        rv = (node.ty, ret_val)
        self._make_call(node, args, rv)

        # When using the call as an expression, use the return value vreg:
        sgnode = self.new_node("REG", node.ty, value=ret_val)
        output = sgnode.new_output("res")
        output.vreg = ret_val
        self.add_map(node, output)

    def do_phi(self, node):
        """Phi 在机器层就是 prepare_function_info 预分配的那个 vreg，建 REG 节点引用它。

        Refer to the correct copy of the phi node"""
        vreg = self.function_info.phi_map[node]
        sgnode = self.new_node("REG", node.ty, value=vreg)
        output = sgnode.new_output(node.name)
        output.vreg = vreg
        self.add_map(node, output)
        self.debug_db.map(node, vreg)

    def copy_phis_of_successors(self, ir_block):
        """When a terminator instruction is encountered, handle the copy
        of phi values into the expected virtual register"""
        # Copy values to phi nodes in other blocks:
        # step 1: create a new temporary that contains the value of the phi
        # node. Do this because the calculation of the value can involve the
        # phi vreg itself.
        val_map = {}
        for succ_block in ir_block.successors:
            for phi in succ_block.phis:
                from_val = phi.get_value(ir_block)
                val = self.get_value(from_val)
                vreg1 = self.new_vreg(phi.ty)
                sgnode = self.new_node("MOV", phi.ty, val, value=vreg1)
                self.chain(sgnode)
                val_map[from_val] = vreg1

        # Step 2: copy the temporary value to the phi register:
        for succ_block in ir_block.successors:
            for phi in succ_block.phis:
                vreg = self.function_info.phi_map[phi]
                from_val = phi.get_value(ir_block)
                vreg1 = val_map[from_val]

                # TODO: ensure this is valid:
                # In case phi input is phi itself, do not copy value:
                # if vreg is vreg1:
                #    continue

                # Create reg node:
                sgnode1 = self.new_node("REG", phi.ty, value=vreg1)
                val = sgnode1.new_output(vreg1.name)

                # Create move node:
                sgnode = self.new_node("MOV", phi.ty, val, value=vreg)
                self.chain(sgnode)
