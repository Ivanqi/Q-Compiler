"""功能说明
========

后端代码生成的总指挥（机器码生成器），位于 前端（C → IR）→ 中端（IR 优化）→ 后端（本模块）
流水线的末端：把优化后的 ir.Module 变成可写入目标文件的真实机器指令。
目标架构（Architecture）在创建生成器时提供。

后端流水线四阶段（在 __init__ 中把各"专家"请进来）::

    ir.Module（来自前端 + 中端优化）
      ↓ ① 指令选择 InstructionSelector1          —— IR 指令 → 架构抽象指令（虚拟寄存器）
      ↓ ② 指令调度 InstructionScheduler          —— 图形方法未启用
      ↓ ③ 寄存器分配 GraphColoringRegisterAllocator —— 虚拟寄存器 → 真实寄存器/栈槽
      ↓ ④ 帧发射 emit_frame_to_stream            —— 加 prologue/epilogue，经 PeepHoleStream 输出

关键类/函数：
- CodeGenerator：总入口；generate() 依次处理外部符号、全局变量与每个函数；
- generate_function()：单函数完整流程（建帧 → 选择 → 分配 → 窥孔 → 发射）；
- select_and_schedule() / emit_frame_to_stream()：指令选择与最终指令流输出。

详见 docs/codegen.py.md

Machine code generator.

The architecture is provided when the generator is created.
"""

import logging

from qcc.midend import ir
from qcc.backend.arch import data_instructions
from qcc.backend.arch.arch import Architecture
from qcc.backend.arch.arch_info import Endianness
from qcc.backend.arch.data_instructions import DByte, DZero
from qcc.backend.arch.encoding import Instruction
from qcc.backend.arch.generic_instructions import (
    Alignment,
    ArtificialInstruction,
    Comment,
    DebugData,
    Global,
    InlineAssembly,
    Label,
    RegisterUseDef,
    SetSymbolType,
    VirtualInstruction,
)
from qcc.backend.binutils.debuginfo import DebugDb, DebugLocation, DebugType
from qcc.backend.binutils.outstream import FunctionOutputStream, MasterOutputStream
from qcc.midend.irutils import Verifier, split_block
from qcc.backend.codegen.instructionscheduler import InstructionScheduler
from qcc.backend.codegen.instructionselector import InstructionSelector1
from qcc.backend.codegen.irdag import SelectionGraphBuilder
from qcc.backend.codegen.peephole import PeepHoleStream
from qcc.backend.codegen.registerallocator import GraphColoringRegisterAllocator


class CodeGenerator:
    """机器码生成器：组装备有指令选择器、指令调度器与图着色寄存器分配器的后端流水线。

    Machine code generator"""

    logger = logging.getLogger("codegen")

    def __init__(self, arch, reporter, optimize_for="size"):
        """创建后端生成器：按优化目标选定指令选择权重，并实例化三个流水线组件。"""
        assert isinstance(arch, Architecture), arch
        self.arch = arch
        self.reporter = reporter
        self.verifier = Verifier()
        self.sgraph_builder = SelectionGraphBuilder(arch)
        # 优化目标 → (尺寸权重, 执行周期权重, 能耗权重)，供 BURG 选择模式时加权评分
        weights_map = {
            "size": (10, 1, 1),
            "speed": (3, 10, 1),
            "co2": (1, 2, 10),
            "awesome": (13, 13, 13),
        }
        selection_weights = weights_map.get(optimize_for, (1, 1, 1))
        self.instruction_selector = InstructionSelector1(
            arch, self.sgraph_builder, reporter, weights=selection_weights
        )
        self.instruction_scheduler = InstructionScheduler()
        self.register_allocator = GraphColoringRegisterAllocator(
            arch, self.instruction_selector, reporter
        )

    def generate(self, ircode: ir.Module, output_stream, debug=False):
        """总入口：把一个 IR 模块（外部符号 + 全局变量 + 全部函数）生成为输出流中的机器码。

        Generate machine code from ir-code into output stream"""
        assert isinstance(ircode, ir.Module)
        if ircode.debug_db:
            self.debug_db = ircode.debug_db
        else:
            self.debug_db = DebugDb()

        self.logger.info(
            "Generating %s code for module %s", str(self.arch), ircode.name
        )

        # Declare externals:
        output_stream.select_section("data")
        for external in ircode.externals:
            self._mark_global(output_stream, external)
            if isinstance(external, ir.ExternalSubRoutine):
                output_stream.emit(SetSymbolType(external.name, "func"))

        # Generate code for global variables:
        output_stream.select_section("data")
        for var in ircode.variables:
            self.generate_global(var, output_stream, debug)

        # Generate code for functions:
        # Munch program into a bunch of frames. One frame per function.
        # Each frame has a flat list of abstract instructions.
        output_stream.select_section("code")
        for function in ircode.functions:
            self.generate_function(function, output_stream, debug=debug)

        # Output debug type data:
        if debug:
            for di in self.debug_db.infos:
                if isinstance(di, DebugType):
                    # TODO: prevent this from being emitted twice in some way?
                    output_stream.emit(DebugData(di))

    def generate_global(self, var, output_stream, debug):
        """生成全局变量的数据映像：对齐、符号标记、标签，以及字节数据或标签引用。

        Generate code for a global variable"""
        alignment = Alignment(var.alignment)
        output_stream.emit(alignment)
        self._mark_global(output_stream, var)
        label = Label(var.name)
        output_stream.emit(label)
        if var.amount == 0 and var.value is None and not var.used_by:
            pass  # E.g. empty WASM func_table
        elif var.amount > 0:
            if var.value:
                assert isinstance(var.value, tuple)
                for part in var.value:
                    if isinstance(part, bytes):
                        # Emit plain byte data:
                        for byte in part:
                            output_stream.emit(DByte(byte))
                    elif isinstance(part, tuple) and part[0] is ir.ptr:
                        # Emit reference to a label:
                        assert isinstance(part[1], str)
                        labels_refs = {
                            (2, Endianness.LITTLE): data_instructions.Dw2,
                            (4, Endianness.LITTLE): data_instructions.Dcd2,
                            (8, Endianness.LITTLE): data_instructions.Dq2,
                        }
                        key = (
                            self.arch.info.get_size(part[0]),
                            self.arch.info.endianness,
                        )
                        op_cls = labels_refs[key]
                        output_stream.emit(op_cls(part[1]))
                    else:
                        raise NotImplementedError(str(part))
            else:
                output_stream.emit(DZero(var.amount))
        else:  # pragma: no cover
            raise NotImplementedError()
        self.debug_db.map(var, label)
        if self.debug_db.contains(label) and debug:
            dv = self.debug_db.get(label)
            dv.address = label.name
            output_stream.emit(DebugData(dv))

    def generate_function(self, ir_function, output_stream, debug=False):
        """生成单个函数：切分超大基本块 → 建帧 → 指令选择 → 寄存器分配 → 窥孔 → 发射。

        Generate code for one function into a frame"""
        self.logger.info(
            "Generating %s code for function %s",
            str(self.arch),
            ir_function.name,
        )

        self.reporter.heading(3, f"Log for {ir_function}")
        self.reporter.dump_ir(ir_function)

        # Split too large basic blocks in smaller chunks (for literal pools):
        # TODO: fix arbitrary number of 500. This works for arm and thumb..
        # 为字面量池可达性（ARM/Thumb 的取指偏移有限）而切块；200 是经验值
        split_block_nr = 1
        for block in ir_function:
            max_block_len = 200
            while len(block) > max_block_len:
                self.logger.debug("%s too large, splitting up", str(block))
                newname = f"{ir_function.name}_splitted_block_{split_block_nr}"
                split_block_nr += 1
                _, block = split_block(
                    block, pos=max_block_len, newname=newname
                )

        self._mark_global(output_stream, ir_function)
        output_stream.emit(SetSymbolType(ir_function.name, "func"))

        # Create a frame for this function:
        frame_name = ir_function.name
        frame = self.arch.new_frame(frame_name, ir_function)
        frame.debug_db = self.debug_db  # Attach debug info
        self.debug_db.map(ir_function, frame)

        # Select instructions and schedule them:
        self.select_and_schedule(ir_function, frame)

        self.reporter.dump_frame(frame)

        # Do register allocation:
        # ③ 图着色寄存器分配：给虚拟寄存器指派真实寄存器，放不下的溢出到栈槽
        self.register_allocator.alloc_frame(frame)

        # TODO: Peep-hole here?
        # frame.instructions = [i for i in frame.instructions]
        # 架构自定义的窥孔钩子（架构相关的相邻指令优化）
        if hasattr(self.arch, "peephole"):
            frame.instructions = self.arch.peephole(frame)

        self.reporter.dump_frame(frame)

        # Add label and return and stack adjustment:
        instruction_list = []
        output_stream = MasterOutputStream(
            [FunctionOutputStream(instruction_list.append), output_stream]
        )
        peep_hole_stream = PeepHoleStream(output_stream)
        self.emit_frame_to_stream(frame, peep_hole_stream, debug=debug)
        peep_hole_stream.flush()

        # Emit function debug info:
        if self.debug_db.contains(frame) and debug:
            func_end_label = self.debug_db.new_label()
            output_stream.emit(Label(func_end_label))
            d = self.debug_db.get(frame)
            d.begin = frame_name
            d.end = func_end_label
            dd = DebugData(d)
            output_stream.emit(dd)

        self.reporter.dump_instructions(instruction_list, self.arch)

    def select_and_schedule(self, ir_function, frame):
        """执行指令选择与调度（当前只有树覆盖/BURG 路径可用）。

        Perform instruction selection and scheduling"""
        self.logger.debug("Selecting instructions")

        # 树覆盖法（选择 DAG → 树森林 → BURG 匹配）；图式方法尚未实现（见下方分支）
        tree_method = True
        if tree_method:
            self.instruction_selector.select(ir_function, frame)
        else:  # pragma: no cover
            raise NotImplementedError("TODO")
            # Build a graph:
            # self.sgraph_builder.build(ir_function, function_info)
            # reporter.message('Selection graph')
            # reporter.dump_sgraph(sgraph)

            # Schedule instructions:
            # self.instruction_scheduler.schedule(sgraph, frame)

    def emit_frame_to_stream(self, frame, output_stream, debug=False):
        """帧的最终发射：加入 prologue/epilogue，逐条输出真实指令（此时栈空间与待保存寄存器已确定）。

        Add code for the prologue and the epilogue. Add a label, the
        return instruction and the stack pointer adjustment for the frame.
        At this point we know how much stack space must be reserved for
        locals and what registers should be saved.
        """
        # Materialize the register allocated instructions into a stream of
        # real instructions.
        self.logger.debug("Emitting instructions")

        debug_data = []

        # Prefix code:
        output_stream.emit_all(self.arch.gen_prologue(frame))

        for instruction in frame.instructions:
            assert isinstance(instruction, Instruction), str(instruction)

            # If the instruction has debug location, emit it here:
            if self.debug_db.contains(instruction) and debug:
                d = self.debug_db.get(instruction)
                assert isinstance(d, DebugLocation)
                if not d.address:
                    label_name = self.debug_db.new_label()
                    d.address = label_name
                    source_line = d.loc.get_source_line()
                    output_stream.emit(Comment(source_line))
                    output_stream.emit(Label(label_name))
                    debug_data.append(DebugData(d))

            if isinstance(instruction, VirtualInstruction):
                # Process virtual instructions
                if isinstance(instruction, RegisterUseDef):
                    pass
                elif isinstance(instruction, ArtificialInstruction):
                    output_stream.emit(instruction)
                elif isinstance(instruction, InlineAssembly):
                    self._generate_inline_assembly(
                        instruction.template,
                        instruction.output_registers,
                        instruction.input_registers,
                        output_stream,
                    )
                else:  # pragma: no cover
                    raise NotImplementedError(str(instruction))
            else:
                # Real instructions:
                # 走到这里说明寄存器分配已完成：所有寄存器都必须已着色（有物理寄存器）
                assert all(r.is_colored for r in instruction.registers)
                output_stream.emit(instruction)

        # Postfix code, like register restore and stack adjust:
        output_stream.emit_all(self.arch.gen_epilogue(frame))

        # Last but not least, emit debug infos:
        for dd in debug_data:
            output_stream.emit(dd)

        # Check if we know what variables are live
        for tmp in frame.ig.temp_map:
            if self.debug_db.contains(tmp):
                self.debug_db.get(tmp)
                # print(tmp, di)
                # frame.live_ranges(tmp)
                # print('live ranges:', lr)

    def _generate_inline_assembly(
        self, assembly_source, output_registers, input_registers, ostream
    ):
        """把内联汇编模板里的 %0/%1 占位符替换为分配后的真实寄存器，再交给架构汇编器汇编。

        Emit inline assembly template to outstream."""
        from qcc.common import DiagnosticsManager

        # poor mans assembly api copied from api.py

        # Replace template variables with actual registers:
        mapping = {
            f"%{index}": str(register.get_real())
            for index, register in enumerate(
                output_registers + input_registers
            )
        }

        for k, v in mapping.items():
            assembly_source = assembly_source.replace(k, v)

        diag = DiagnosticsManager()
        assembler = self.arch.assembler
        assembler.prepare()
        assembler.assemble(assembly_source, ostream, diag)
        # TODO: this flush action might be troublesome, since it might emit
        # a literal pool on ARM.
        assembler.flush()

    def _mark_global(self, output_stream, value):
        """对绑定为 GLOBAL 的符号发射 Global 指令（导出符号；static 符号不发）。"""
        # Indicate static or global variable.
        assert isinstance(value, ir.GlobalValue)

        if value.binding == ir.Binding.GLOBAL:
            output_stream.emit(Global(value.name))
