"""RISC-V 架构描述：把指令表、寄存器、重定位与汇编打印组装成一个可编译的目标架构。

核心是 RiscvArch（Architecture 子类）：它按选项（rvc 压缩指令 / rvf 硬件浮点 /
rvfx 浮点直通整数寄存器）拼装 isa（指令选择模式的注册表 isa.pattern 由此进入
BURG 动态规划匹配器），选定访存指令（Sw/Lw 或 CSwsp/CLwsp），指定寄存器类、
调用约定（参数 R12-R17、返回值 R10）、序言/尾声与数据段的生成，并提供软浮点
时所需的运行时例程（get_runtime）。
RiscvAssembler 负责把汇编文本解析成指令流（字面量池 + 伪指令辅助），
gen_prologue/gen_epilogue/gen_call 产出的指令序列随后进入寄存器分配与 encode/重定位。

RISC-V architecture."""

import io

from qcc.midend import ir
from qcc.backend.binutils.assembler import BaseAssembler
from qcc.backend.arch.arch import Architecture
from qcc.backend.arch.arch_info import ArchInfo, TypeInfo
from qcc.backend.arch.data_instructions import DByte, DZero, data_isa
from qcc.backend.arch.generic_instructions import Label, RegisterUseDef
from qcc.backend.arch.stack import FramePointerLocation, StackLocation
from qcc.backend.arch.riscv import instructions
from qcc.backend.arch.riscv.asm_printer import RiscvAsmPrinter
from qcc.backend.arch.riscv.instructions import (
    Addi,
    Align,
    Bl,
    Blr,
    Lb,
    Lw,
    Movr,
    Sb,
    Section,
    Sw,
    dcd,
    isa,
)
from qcc.backend.arch.riscv.registers import (
    F10,
    F12,
    F13,
    F14,
    F15,
    F16,
    F17,
    FP,
    LR,
    PC,
    R0,
    R9,
    R10,
    R11,
    R12,
    R13,
    R14,
    R15,
    R16,
    R17,
    R18,
    R19,
    R20,
    R21,
    R22,
    R23,
    R24,
    R25,
    R26,
    R27,
    SP,
    Register,
    RiscvFRegister,
    RiscvRegister,
    gdb_registers,
    register_classes_hwfp,
    register_classes_swfp,
)
from qcc.backend.arch.riscv.rvc_instructions import (
    CAddi4spn,
    CAddi16sp,
    CBl,
    CBlr,
    CJr,
    CLwsp,
    CMovr,
    CSwsp,
    rvcisa,
)
from qcc.backend.arch.riscv.rvf_instructions import movf, rvfisa
from qcc.backend.arch.riscv.rvfx_instructions import rvfxisa


def isinsrange(bits, val) -> bool:
    """判断 val 是否落在 bits 位（含符号位）有符号立即数的可表示范围内。"""
    msb = 1 << (bits - 1)
    ll = -msb
    return bool(val <= (msb - 1) and (val >= ll))


class RiscvAssembler(BaseAssembler):
    """RISC-V 汇编解析器：把汇编文本翻译成指令对象流。

    额外维护一个字面量池（lit_pool）：遇到 LDR r0, =SOMESYM 这类伪指令时，
    先登记一个标签与 dcd 数据，稍后统一发射（flush），这是 RISC-V 常见的
    "常量池随数据段下发"写法。
    """

    def __init__(self):
        """初始化汇编器，并准备字面量池与计数（_lit_N 标签的编号来源）。"""
        super().__init__()
        self.lit_pool = []
        self.lit_counter = 0

    def flush(self):
        """把字面量池中累积的标签/数据依次发射出去；宏展开期间不允许调用。"""
        if self.in_macro:
            raise Exception()
        while self.lit_pool:
            i = self.lit_pool.pop(0)
            self.emit(i)

    def add_literal(self, v):
        """登记一个字符串字面量，生成 _lit_N 标签，返回标签名供调用处引用。

        For use in the pseudo instruction LDR r0, =SOMESYM"""
        # Invent some label for the literal and store it.
        assert type(v) is str
        self.lit_counter += 1
        label_name = f"_lit_{self.lit_counter}"
        self.lit_pool.append(Label(label_name))
        self.lit_pool.append(dcd(v))
        return label_name


class RiscvArch(Architecture):
    """RISC-V（RV32）架构描述：本后端对外的总入口。

    负责按选项组装指令集容器 isa（rvc 压缩指令 / rvf 硬件浮点 / rvfx 浮点走整数
    寄存器）、选择寄存器类与访存指令，并实现调用约定、序言/尾声、常量池与运行时
    例程等回调，供 codegen/寄存器分配/编码各环节调用。
    """

    name = "riscv"
    option_names = ("rvc", "rvf", "rvfx")

    def __init__(self, options=None):
        """按选项拼装 isa（基础指令 + 扩展 + 数据指令）并确定访存指令与寄存器类。"""
        super().__init__(options=options)
        if self.has_option("rvc"):
            self.isa = isa + rvcisa + data_isa
            self.store = CSwsp
            self.load = CLwsp
            self.regclass = register_classes_swfp
        elif self.has_option("rvfx"):
            self.isa = isa + rvfxisa + data_isa
            self.store = Sw
            self.load = Lw
            self.regclass = register_classes_swfp
        elif self.has_option("rvf"):
            self.isa = isa + rvfisa + data_isa
            self.store = Sw
            self.load = Lw
            self.regclass = register_classes_hwfp
        else:
            self.isa = isa + data_isa
            self.store = Sw
            self.load = Lw
            self.regclass = register_classes_swfp
        self.fp_location = FramePointerLocation.TOP
        self.isa.sectinst = Section
        self.isa.dbinst = DByte
        self.isa.dsinst = DZero
        self.gdb_registers = gdb_registers
        self.gdb_pc = PC
        self.asm_printer = RiscvAsmPrinter()
        self.assembler = RiscvAssembler()
        self.assembler.gen_asm_parser(self.isa)

        self.info = ArchInfo(
            type_infos={
                ir.i8: TypeInfo(1, 1),
                ir.u8: TypeInfo(1, 1),
                ir.i16: TypeInfo(2, 2),
                ir.u16: TypeInfo(2, 2),
                ir.i32: TypeInfo(4, 4),
                ir.u32: TypeInfo(4, 4),
                ir.f32: TypeInfo(4, 4),
                ir.f64: TypeInfo(4, 4),
                "int": ir.i32,
                "long": ir.i32,
                "ptr": ir.u32,
                ir.ptr: ir.u32,
            },
            register_classes=self.regclass,
        )

        self.fp = FP
        self.callee_save = (
            R9,
            R18,
            R19,
            R20,
            R21,
            R22,
            R23,
            R24,
            R25,
            R26,
            R27,
        )
        self.caller_save = (R10, R11, R12, R13, R14, R15, R16, R17)
        # (LR, FP, R9, R18, R19, R20, R21 ,R22, R23 ,R24, R25, R26, R27)

    def branch(self, reg, lab):
        """生成一条跳转/调用指令：lab 为寄存器时是间接跳转（jalr），否则是直接跳转。

        rvc 选项下优先用 16 位压缩形态（CBlr/CBl），否则用完整 32 位形态。
        """
        if self.has_option("rvc"):
            if isinstance(lab, RiscvRegister):
                return CBlr(reg, lab, 0, clobbers=self.caller_save)
            else:
                return CBl(reg, lab, clobbers=self.caller_save)
        else:
            if isinstance(lab, RiscvRegister):
                return Blr(reg, lab, 0, clobbers=self.caller_save)
            else:
                return Bl(reg, lab, clobbers=self.caller_save)


    def get_reloc_type(self, reloc_type, symbol):
        """Get the reloc type for ELF format（迁移增强：原 ppci 未实现，
        导致 riscv 无法写出可重定位 ELF 目标文件）。

        RISC-V ELF psABI 重定位编号：BRANCH=16, JAL=17, CALL=18,
        PCREL_HI20=23, PCREL_LO12_I=24, HI20=26, LO12_I=27, 32=1。
        """
        mapping = {
            "b_imm12": 16,       # B 型分支偏移 → R_RISCV_BRANCH
            "b_imm20": 17,       # jal 偏移 → R_RISCV_JAL
            "abs32_imm20": 26,   # lui %hi(sym) → R_RISCV_HI20
            "rel_imm20": 23,     # auipc %pcrel_hi → R_RISCV_PCREL_HI20
            "abs32_imm12": 27,   # %lo(sym) → R_RISCV_LO12_I
            "rel_imm12": 24,     # %pcrel_lo → R_RISCV_PCREL_LO12_I
            "absaddr32": 1,      # 32 位绝对地址 → R_RISCV_32
            "cb_imm11": 16,      # 压缩分支 → R_RISCV_BRANCH
            "cbl_imm11": 17,     # 压缩跳转 → R_RISCV_JAL
            "bc_imm11": 16,
            "bc_imm8": 16,
        }
        return mapping[reloc_type]

    def get_runtime(self):
        """返回编译器运行时例程（汇编源码），如软浮点/除法辅助函数。

        Implement compiler runtime functions"""
        from qcc.api import asm

        asm_src = """
        __sdiv:
        ; Divide x12 by x13
        ; x14 is a work register.
        ; x10 is the quotient

        mv x10, x0     ; Initialize the result
        li x14, 1      ; mov divisor into temporary register.

        ; Blow up part: blow up divisor until it is larger than the divident.
        __shiftl:
        bge x13, x12, __cont1
        slli x13, x13, 1
        slli x14, x14, 1
        j __shiftl

        ; Repeatedly substract shifted versions of divisor
        __cont1:
        beq x14, x0, __exit
        blt x12, x13, __skip
        sub x12, x12, x13
        or x10, x10, x14
        __skip:
        srli x13, x13, 1
        srli x14, x14, 1
        j __cont1

        __exit:
        jalr x0,ra,0
        """
        return asm(io.StringIO(asm_src), self)

    def move(self, dst, src):
        """在 dst、src 之间生成一条搬运指令（rvc 用 c.mv，rvf 浮点用 fsgnj.s）。

        Generate a move from src to dst"""
        if self.has_option("rvc"):
            return CMovr(dst, src, ismove=True)
        else:
            if (
                isinstance(dst, RiscvFRegister)
                and isinstance(src, RiscvFRegister)
                and self.has_option("rvf")
            ):
                return movf(dst, src)
            else:
                return Movr(dst, src, ismove=True)

    def gen_riscv_memcpy(self, dst, src, tmp, size):
        """用逐字节 Lb/Sb 的方式拷贝 size 字节（寄存器分配前调用，实现从简）。

        逐字节搬运效率不高，是待优化的朴素实现。
        """
        # Called before register allocation
        # Major crappy memcpy, can be improved!
        for idx in range(size):
            yield Lb(tmp, idx, src)
            yield Sb(tmp, idx, dst)

    def peephole(self, frame):
        """窥孔优化：修正栈帧相对（fprel）访存的偏移，使其跨过被压栈的 LR/FP。

        所有带 fprel 标记的访存指令（未直接给出栈指针偏移者）统一加上
        round_up(stacksize + 8) - 8 的补偿量，返回新的指令列表。
        """
        newinstructions = []
        for ins in frame.instructions:
            if hasattr(ins, "fprel") and ins.fprel:
                ins.offset += round_up(frame.stacksize + 8) - 8
            newinstructions.append(ins)
        return newinstructions

    def gen_call(self, frame, label, args, rv):
        """生成实际调用序列：实参搬运、栈参数拷贝、寄存器使用声明、跳转与返回值回搬。

        Implement actual call and save / restore live registers"""

        arg_types = [a[0] for a in args]
        arg_locs = self.determine_arg_locations(arg_types)
        stack_size = 0
        # Setup parameters:
        for arg_loc, arg2 in zip(arg_locs, args):
            arg = arg2[1]
            if isinstance(arg_loc, (RiscvRegister, RiscvFRegister)):
                yield self.move(arg_loc, arg)
            elif isinstance(arg_loc, StackLocation):
                stack_size += arg_loc.size
                if isinstance(arg, RiscvRegister):
                    yield Sw(arg, arg_loc.offset, SP)
                elif isinstance(arg, StackLocation):
                    p1 = frame.new_reg(RiscvRegister)
                    p2 = frame.new_reg(RiscvRegister)
                    v3 = frame.new_reg(RiscvRegister)

                    # Destination location:
                    # Remember that the LR and FP are pushed in between
                    # So hence -8:
                    yield instructions.Addi(p1, SP, arg_loc.offset)
                    # Source location:
                    yield instructions.Addi(
                        p2,
                        self.fp,
                        arg.offset + round_up(frame.stacksize + 8) - 8,
                    )
                    yield from self.gen_riscv_memcpy(p1, p2, v3, arg.size)
            else:  # pragma: no cover
                raise NotImplementedError("Parameters in memory not impl")

        # Record that certain amount of stack is required:
        frame.add_out_call(stack_size)

        arg_regs = {
            arg_loc for arg_loc in arg_locs if isinstance(arg_loc, Register)
        }
        yield RegisterUseDef(uses=arg_regs)

        yield self.branch(LR, label)

        if rv:
            retval_loc = self.determine_rv_location(rv[0])
            yield RegisterUseDef(defs=(retval_loc,))
            yield self.move(rv[1], retval_loc)

    def gen_function_enter(self, args):
        """生成函数入口代码：把调用者按约定放在寄存器/栈上的实参搬到分配后的位置。"""
        arg_types = [a[0] for a in args]
        arg_locs = self.determine_arg_locations(arg_types)

        arg_regs = {
            arg_loc for arg_loc in arg_locs if isinstance(arg_loc, Register)
        }
        yield RegisterUseDef(defs=arg_regs)

        for arg_loc, arg2 in zip(arg_locs, args):
            arg = arg2[1]
            if isinstance(arg_loc, Register):
                yield self.move(arg, arg_loc)
            elif isinstance(arg_loc, StackLocation):
                if isinstance(arg, RiscvRegister):
                    Code = Lw(arg, arg_loc.offset, FP)
                    Code.fprel = True
                    yield Code
                else:
                    pass
            else:  # pragma: no cover
                raise NotImplementedError("Parameters in memory not impl")

    def gen_function_exit(self, rv):
        """生成函数出口代码：把返回值搬到约定寄存器（R10 或 F10）并声明其活跃。"""
        live_out = set()
        if rv:
            retval_loc = self.determine_rv_location(rv[0])
            yield self.move(retval_loc, rv[1])
            live_out.add(retval_loc)
        yield RegisterUseDef(uses=live_out)

    def determine_arg_locations(self, arg_types):
        """按调用约定为每个实参选择落点：优先整型寄存器 R12-R17 / 浮点寄存器 F12-F17，
        放不下的（含 blob 类型）依次落到栈上 StackLocation。

        Given a set of argument types, determine location for argument
        ABI:
        pass args in R12-R17
        return values in R10
        """
        locations = []
        regs = [R12, R13, R14, R15, R16, R17]
        fregs = [F12, F13, F14, F15, F16, F17]

        offset = 0
        for a in arg_types:
            if a.is_blob:
                r = StackLocation(offset, a.size)
                offset += a.size
            else:
                if a in [ir.f32, ir.f64] and self.has_option("rvf"):
                    if fregs:
                        r = fregs.pop(0)
                    else:
                        arg_size = self.info.get_size(a)
                        r = StackLocation(offset, a.size)
                        offset += arg_size
                else:
                    if regs:
                        r = regs.pop(0)
                    else:
                        arg_size = self.info.get_size(a)
                        r = StackLocation(offset, arg_size)
                        offset += arg_size
            locations.append(r)
        return locations

    def determine_rv_location(self, ret_type):
        """确定返回值寄存器：硬件浮点的浮点返回用 F10，其余一律用 R10。"""
        if ret_type in [ir.f32, ir.f64] and self.has_option("rvf"):
            rv = F10
        else:
            rv = R10
        return rv

    def gen_prologue(self, frame):
        """生成函数序言：开栈、保存 LR/FP 与 callee-saved 寄存器、设置帧指针。

        rvc 选项下用 c.addi16sp/c.swsp 等压缩指令，否则用 addi/sw。

        Returns prologue instruction sequence"""
        # Label indication function:
        yield Label(frame.name)
        ssize = round_up(frame.stacksize + 8)
        if self.has_option("rvc") and isinsrange(10, -ssize):
            yield CAddi16sp(-ssize)  # Reserve stack space
        else:
            yield Addi(SP, SP, -ssize)  # Reserve stack space

        if self.has_option("rvc"):
            yield CSwsp(LR, 4)
            yield CSwsp(FP, 0)
        else:
            yield Sw(LR, 4, SP)
            yield Sw(FP, 0, SP)

        if self.has_option("rvc"):
            yield CAddi4spn(FP, 8)  # Setup frame pointer
        else:
            yield Addi(FP, SP, 8)  # Setup frame pointer
        # yield Addi(FP, SP, 8)  # Setup frame pointer

        saved_registers = self.get_callee_saved(frame)
        rsize = 4 * len(saved_registers)
        rsize = round_up(rsize)

        if self.has_option("rvc") and isinsrange(10, rsize):
            yield CAddi16sp(-rsize)  # Reserve stack space
        else:
            yield Addi(SP, SP, -rsize)  # Reserve stack space

        i = 0
        for register in saved_registers:
            i -= 4
            if self.has_option("rvc"):
                yield CSwsp(register, i + rsize)
            else:
                yield Sw(register, i + rsize, SP)

        # Allocate space for outgoing calls:
        extras = max(frame.out_calls) if frame.out_calls else 0
        if extras:
            ssize = round_up(extras)
            if self.has_option("rvc") and isinsrange(10, ssize):
                yield CAddi16sp(-ssize)  # Reserve stack space
            else:
                yield Addi(SP, SP, -ssize)  # Reserve stack space

    def litpool(self, frame):
        """生成当前字面量池：切到 data 段，按 4 字节对齐发射 dcd/字节数据，再切回 code。

        Generate instruction for the current literals"""
        yield Section("data")
        # Align at 4 byte
        if frame.constants:
            yield Align(4)

        # Add constant literals:
        while frame.constants:
            label, value = frame.constants.pop(0)
            yield Label(label)
            if isinstance(value, (int, str)):
                yield dcd(value)
            elif isinstance(value, bytes):
                for byte in value:
                    yield DByte(byte)
                yield Align(4)  # Align at 4 bytes
            else:  # pragma: no cover
                raise NotImplementedError(f"Constant of type {value}")

        yield Section("code")

    def between_blocks(self, frame):
        """基本块之间的钩子：把字面量池安插到此处（常量放中间便于寻址）。"""
        yield from self.litpool(frame)

    def gen_epilogue(self, frame):
        """生成函数尾声：恢复 callee-saved 寄存器与 LR/FP、释放栈空间、返回并发射常量池。

        Return epilogue sequence for a frame. Adjust frame pointer
        and add constant pool
        """
        # Free space for outgoing calls:
        extras = max(frame.out_calls) if frame.out_calls else 0
        if extras:
            ssize = round_up(extras)
            if self.has_option("rvc") and isinsrange(10, ssize):
                yield CAddi16sp(ssize)  # Reserve stack space
            else:
                yield Addi(SP, SP, ssize)  # Reserve stack space

        # Callee saved registers:
        saved_registers = self.get_callee_saved(frame)
        rsize = 4 * len(saved_registers)
        rsize = round_up(rsize)

        i = 0
        for register in saved_registers:
            i -= 4
            if self.has_option("rvc"):
                yield CLwsp(register, i + rsize)
            else:
                yield Lw(register, i + rsize, SP)

        if self.has_option("rvc") and isinsrange(10, rsize):
            yield CAddi16sp(rsize)  # Reserve stack space
        else:
            yield Addi(SP, SP, rsize)  # Reserve stack space

        if self.has_option("rvc"):
            yield CLwsp(LR, 4)
            yield CLwsp(FP, 0)
        else:
            yield Lw(LR, 4, SP)
            yield Lw(FP, 0, SP)

        ssize = round_up(frame.stacksize + 8)
        if self.has_option("rvc") and isinsrange(10, ssize):
            yield CAddi16sp(ssize)  # Free stack space
        else:
            yield Addi(SP, SP, ssize)  # Free stack space

        # Return
        if self.has_option("rvc"):
            yield CJr(LR)
        else:
            yield Blr(R0, LR, 0)

        # Add final literal pool:
        yield from self.litpool(frame)
        yield Align(4)  # Align at 4 bytes

    def get_callee_saved(self, frame):
        """返回本函数实际用到、因而需要保存/恢复的被调用者保存寄存器列表。"""
        saved_registers = []
        for register in self.callee_save:
            if frame.is_used(register, self.info.alias):
                saved_registers.append(register)
        return saved_registers


def round_up(s):
    """把字节数按 16 字节（栈对齐要求）向上取整；已对齐时多加 16 也无妨。"""
    return s + (16 - s % 16)
