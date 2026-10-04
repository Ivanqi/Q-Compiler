"""ARM 架构（Architecture 后端）实现。

ArmArch 把 ARM / Thumb 两套指令集接入编译流水线：按 options 中的 "thumb" 选项
选择 isa（arm_isa 或 thumb_isa）+ 对应汇编器（ArmAssembler / ThumbAssembler），
并决定帧指针（R11 或 R7）、被调用者保存寄存器以及寄存器分配器可用的
RegisterClass（Thumb 只能用 R0–R7）；gen_prologue / gen_epilogue 生成函数
序言与尾声（保存 LR、FP，调整 SP，Thumb 下 SubSp 需分多次循环），gen_call /
gen_function_enter 按调用约定在 R1–R4 传参、R0 返回，litpool 在代码中穿插输出
常量字面量池。ArmAssembler / ThumbAssembler 通过 add_extra_rules 扩展语法
（"{}" 寄存器列表、"ldr r0, =sym" 伪指令），供 gen_asm_parser 生成汇编解析器；
ARM_ASM_RT 是 ARM 模式下用汇编写的运行时（__sdiv 软件除法）。

ARM architecture definition."""

import io

from qcc.midend import ir
from qcc.backend.binutils.assembler import BaseAssembler
from qcc.backend.arch.arch import Architecture
from qcc.backend.arch.arch_info import ArchInfo, TypeInfo
from qcc.backend.arch.data_instructions import Db, Dcd2, Dd, data_isa
from qcc.backend.arch.generic_instructions import Alignment, Label, RegisterUseDef
from qcc.backend.arch.registers import RegisterClass
from qcc.backend.arch.stack import StackLocation
from qcc.backend.arch.arm import arm_instructions, thumb_instructions
from qcc.backend.arch.arm.arm_instructions import LdrPseudo, arm_isa
from qcc.backend.arch.arm.registers import (
    LR,
    PC,
    R0,
    R1,
    R2,
    R3,
    R4,
    R5,
    R6,
    R7,
    R8,
    R9,
    R10,
    R11,
    SP,
    ArmRegister,
    LowArmRegister,
    RegisterSet,
    all_registers,
    register_range,
)
from qcc.backend.arch.arm.thumb_instructions import thumb_isa


class ArmCallingConvention:
    """ARM 调用约定的占位类（具体寄存器约定目前直接写在 ArmArch 中）。"""

    pass


class ArmArch(Architecture):
    """ARM 架构类：ARM / Thumb 双模式的后端描述。

    Arm machine class."""

    name = "arm"
    option_names = ("thumb", "jazelle", "neon", "vfpv1", "vfpv2")

    def __init__(self, options=None):
        """按 options（是否 thumb）挑选指令集、汇编器、帧指针与寄存器类别。"""
        super().__init__(options=options)
        if self.has_option("thumb"):
            self.assembler = ThumbAssembler()
            self.isa = thumb_isa + data_isa
            # We use r7 as frame pointer (in case of thumb ;)):
            self.fp = R7
            self.callee_save = (R5, R6)

            # Registers usable by register allocator:
            register_classes = [
                RegisterClass(
                    "reg",
                    [ir.i8, ir.i32, ir.ptr, ir.u8, ir.u32, ir.i16, ir.u16],
                    LowArmRegister,
                    [R0, R1, R2, R3, R4, R5, R6, R7],
                )
            ]
        else:
            self.isa = arm_isa + data_isa
            self.assembler = ArmAssembler()
            self.fp = R11
            self.callee_save = (R5, R6, R7, R8, R9, R10)

            # Registers usable by register allocator:
            register_classes = [
                RegisterClass(
                    "loreg",
                    [],
                    LowArmRegister,
                    [R0, R1, R2, R3, R4, R5, R6, R7],
                ),
                RegisterClass(
                    "reg",
                    [ir.i8, ir.i32, ir.u8, ir.u32, ir.i16, ir.u16, ir.ptr],
                    ArmRegister,
                    [R0, R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11],
                ),
            ]
        self.assembler.gen_asm_parser(self.isa)
        self.gdb_registers = all_registers
        self.gdb_pc = PC

        self.info = ArchInfo(
            type_infos={
                ir.i8: TypeInfo(1, 1),
                ir.u8: TypeInfo(1, 1),
                ir.i16: TypeInfo(2, 2),
                ir.u16: TypeInfo(2, 2),
                ir.i32: TypeInfo(4, 4),
                ir.u32: TypeInfo(4, 4),
                ir.f32: TypeInfo(4, 4),
                ir.f64: TypeInfo(8, 8),
                "int": ir.i32,
                "long": ir.i32,
                "ptr": ir.u32,
                ir.ptr: ir.u32,
            },
            register_classes=register_classes,
        )


    def get_reloc_type(self, reloc_type, symbol):
        """Get the reloc type for ELF format（迁移增强：原 ppci 未实现，
        导致 arm 无法写出可重定位 ELF 目标文件）。

        ARM ELF 重定位编号：ABS32=2, THM_CALL=10, CALL=28,
        JUMP24=29, THM_JUMP24=30, PREL31=42。
        """
        mapping = {
            "rel8": 42,          # 8 位相对偏移 → R_ARM_PREL31
            "imm24": 29,         # B/BL 24 位偏移 → R_ARM_JUMP24
            "ldr_imm12": 2,      # 字面量池装载 → R_ARM_ABS32
            "adr_imm12": 2,      # adr 伪指令 → R_ARM_ABS32
            "lit8": 2,           # Thumb 字面量池 → R_ARM_ABS32
            "wrap_new11": 42,    # Thumb 相对 → R_ARM_PREL31
            "bl_imm11": 10,      # Thumb BL → R_ARM_THM_CALL
            "b_imm11_imm6": 30,  # Thumb 条件分支 → R_ARM_THM_JUMP24
            "absaddr32": 2,      # 数据指令 32 位绝对地址 → R_ARM_ABS32
        }
        return mapping[reloc_type]

    def get_runtime(self):
        """返回该目标的运行时（汇编源码），Thumb 模式下为空。

        Implement compiler runtime functions"""
        from qcc.api import asm

        if self.has_option("thumb"):
            asm_src = ""
        else:
            asm_src = ARM_ASM_RT
        return asm(io.StringIO(asm_src), self)

    def move(self, dst, src):
        """生成一条从 src 到 dst 的搬移指令（两种模式各用各自的别名指令）。

        Generate a move from src to dst"""
        if self.has_option("thumb"):
            return thumb_instructions.Mov2(dst, src, ismove=True)
        else:
            return arm_instructions.Mov2(
                dst, src, arm_instructions.NoShift(), ismove=True
            )

    def gen_prologue(self, frame):
        """生成函数序言指令序列：压栈保存 LR/FP、建立帧指针、分配栈帧。

        Returns prologue instruction sequence.

        Reserve stack for this calling frame for:

          - local variables
          - save registers
          - parameters to called functions
        """
        # Label indication function:
        yield Label(frame.name)

        # Save the link register and the frame pointer:
        if self.has_option("thumb"):
            yield thumb_instructions.Push({LR, R7})
        else:
            yield arm_instructions.Push(RegisterSet({LR, R11}))

        # Setup frame pointer:
        if self.has_option("thumb"):
            yield thumb_instructions.Mov2(R7, SP)
        else:
            yield arm_instructions.Mov2(R11, SP, arm_instructions.NoShift())

        # Reserve stack for this calling frame for:
        # 1. local variables
        # 2. save registers
        # 3. parameters to called functions
        if frame.stacksize:
            ssize = round_up(frame.stacksize)
            if self.has_option("thumb"):
                # Reserve stack space:
                # subSp cannot handle large numbers:
                while ssize > 0:
                    inc = min(124, ssize)
                    yield thumb_instructions.SubSp(inc)
                    ssize -= inc
            else:
                yield arm_instructions.SubImm(SP, SP, ssize)

        # Callee save registers:
        callee_save = self.get_callee_saved(frame)
        if callee_save:
            if self.has_option("thumb"):
                yield thumb_instructions.Push(callee_save)
            else:
                yield arm_instructions.Push(RegisterSet(callee_save))

        # Allocate space for outgoing calls:
        extras = max(frame.out_calls) if frame.out_calls else 0
        if extras:
            ssize = round_up(extras)
            if self.has_option("thumb"):
                raise NotImplementedError()
            else:
                yield arm_instructions.SubImm(SP, SP, ssize)

    def gen_epilogue(self, frame):
        """生成函数尾声指令序列：恢复被保存寄存器、释放栈帧并弹出 LR/FP 返回。

        Return epilogue sequence for a frame.

        Adjust frame pointer and add constant pool.

        Also free up space on stack for:

          - Space for parameters passed to called functions.
          - Space for save registers
          - Space for local variables
        """

        # Free space for outgoing calls:
        extras = max(frame.out_calls) if frame.out_calls else 0
        if extras:
            ssize = round_up(extras)
            if self.has_option("thumb"):
                raise NotImplementedError()
            else:
                yield arm_instructions.AddImm(SP, SP, ssize)

        # Callee save registers:
        callee_save = self.get_callee_saved(frame)
        if callee_save:
            if self.has_option("thumb"):
                yield thumb_instructions.Pop(callee_save)
            else:
                yield arm_instructions.Pop(RegisterSet(callee_save))

        if frame.stacksize > 0:
            ssize = round_up(frame.stacksize)
            if self.has_option("thumb"):
                # subSp cannot handle large numbers:
                while ssize > 0:
                    inc = min(124, ssize)
                    yield thumb_instructions.AddSp(inc)
                    ssize -= inc
            else:
                yield arm_instructions.AddImm(SP, SP, ssize)

        if self.has_option("thumb"):
            yield thumb_instructions.Pop({PC, R7})
        else:
            yield arm_instructions.Pop(RegisterSet({PC, R11}))

        # Add final literal pool
        yield from self.litpool(frame)

        if not self.has_option("thumb"):
            yield Alignment(4)  # Align at 4 bytes

    def get_callee_saved(self, frame):
        """返回本帧实际用到、因此需要在序言/尾声中保存恢复的被调用者保存寄存器。"""
        saved_registers = set()
        for register in self.callee_save:
            if register in frame.used_regs:
                saved_registers.add(register)
        return saved_registers

    def gen_arm_memcpy(self, p1, p2, v3, size):
        """用逐字节 ldrb/strb 循环生成一段栈到栈的复制（寄存器分配前调用）。"""
        # Called before register allocation
        # Major crappy memcpy, can be improved!
        for idx in range(size):
            yield arm_instructions.Ldrb(v3, p2, idx)
            yield arm_instructions.Strb(v3, p1, idx)
            # TODO: yield the below from time to time for really big stuff:
            # yield arm_instructions.AddImm(p1, 1)
            # yield arm_instructions.AddImm(p2, 1)

    def gen_call(self, frame, label, args, rv):
        """生成函数调用序列：实参搬入寄存器或栈、发 bl/blx、取回返回值。

        寄存器分配前调用；Thumb 模式下若目标是寄存器还需把最低位置 1 以保证
        切入 Thumb 状态（Blx）。"""
        arg_types = [a[0] for a in args]
        arg_locs = self.determine_arg_locations(arg_types)

        arg_regs = []
        stack_size = 0
        for arg_loc, arg2 in zip(arg_locs, args):
            arg = arg2[1]
            if isinstance(arg_loc, ArmRegister):
                arg_regs.append(arg_loc)
                yield self.move(arg_loc, arg)
            elif isinstance(arg_loc, StackLocation):
                stack_size += arg_loc.size
                if isinstance(arg, ArmRegister):
                    # Store register on stack:
                    if self.has_option("thumb"):
                        yield thumb_instructions.Str1(arg, SP, arg_loc.offset)
                    else:
                        yield arm_instructions.Str1(arg, SP, arg_loc.offset)
                elif isinstance(arg, StackLocation):
                    if self.has_option("thumb"):
                        raise NotImplementedError()
                    else:
                        # Generate memcpy now:
                        # print(arg2, arg_loc)
                        assert arg.size == arg_loc.size
                        # Now start a copy routine to copy some stack:
                        p1 = frame.new_reg(ArmRegister)
                        p2 = frame.new_reg(ArmRegister)
                        v3 = frame.new_reg(ArmRegister)

                        # Destination location:
                        # Remember that the LR and FP are pushed in between
                        # So hence -8:
                        # （迁移修正：ARM 立即数必须是可旋转编码的非负数，
                        #   原 AddImm(SP, offset-8) 在 offset<8 时编码失败）
                        delta = arg_loc.offset - 8
                        if delta >= 0:
                            yield arm_instructions.AddImm(p1, SP, delta)
                        else:
                            yield arm_instructions.SubImm(p1, SP, -delta)
                        # Source location:（迁移修正：arg.offset 可能为负，
                        #   按符号选 add/sub，保持原语义 fp - (-arg.offset)）
                        if arg.offset >= 0:
                            yield arm_instructions.AddImm(
                                p2, self.fp, arg.offset
                            )
                        else:
                            yield arm_instructions.SubImm(
                                p2, self.fp, -arg.offset
                            )
                        yield from self.gen_arm_memcpy(p1, p2, v3, arg.size)

                else:  # pragma: no cover
                    raise NotImplementedError(str(arg))
            else:  # pragma: no cover
                raise NotImplementedError("Parameters in memory not impl")

        # Record that certain amount of stack is required:
        frame.add_out_call(stack_size)

        yield RegisterUseDef(uses=arg_regs)

        clobbers = [R0, R1, R2, R3, R4]
        if self.has_option("thumb"):
            if isinstance(label, ArmRegister):
                # Ensure thumb mode!
                yield thumb_instructions.AddImm(label, label, 1)
                yield thumb_instructions.Blx(label, clobbers=clobbers)
            else:
                yield thumb_instructions.Bl(label, clobbers=clobbers)
        else:
            if isinstance(label, ArmRegister):
                yield arm_instructions.Blx(label, clobbers=clobbers)
            else:
                yield arm_instructions.Bl(label, clobbers=clobbers)

        if rv:
            retval_loc = self.determine_rv_location(rv[0])
            yield RegisterUseDef(defs=(retval_loc,))
            yield self.move(rv[1], retval_loc)

    def gen_function_enter(self, args):
        """函数入口处把传入参数从约定位置搬进它们的 vreg（局部标号）。"""
        arg_types = [a[0] for a in args]
        arg_locs = self.determine_arg_locations(arg_types)

        arg_regs = {
            arg_loc for arg_loc in arg_locs if isinstance(arg_loc, ArmRegister)
        }
        yield RegisterUseDef(defs=arg_regs)

        for arg_loc, arg2 in zip(arg_locs, args):
            arg = arg2[1]
            if isinstance(arg_loc, ArmRegister):
                yield self.move(arg, arg_loc)
            elif isinstance(arg_loc, StackLocation):
                pass
            else:  # pragma: no cover
                raise NotImplementedError("Parameters in memory not impl")

    def gen_function_exit(self, rv):
        """函数出口处把返回值搬进约定寄存器（R0）并声明其活跃性。"""
        live_out = set()
        if rv:
            retval_loc = self.determine_rv_location(rv[0])
            yield self.move(retval_loc, rv[1])
            live_out.add(retval_loc)
        yield RegisterUseDef(uses=live_out)

    def litpool(self, frame):
        """输出当前帧常量字面量池：对齐 4 字节后依次给出 Label + 数据（dd/dcd/db）。

        Generate instruction for the current literals"""
        # Align at 4 bytes
        if frame.constants:
            yield Alignment(4)

        # Add constant literals:
        while frame.constants:
            label, value = frame.constants.pop(0)
            yield Label(label)
            if isinstance(value, int):
                yield Dd(value)
            elif isinstance(value, str):
                yield Dcd2(value)
            elif isinstance(value, bytes):
                for byte in value:
                    yield Db(byte)
                yield Alignment(4)  # Align at 4 bytes
            else:  # pragma: no cover
                raise NotImplementedError(f"Constant of type {value}")

    def between_blocks(self, frame):
        """基本块之间输出字面量池（跳转跨过时避免执行到常量数据）。"""
        yield from self.litpool(frame)

    def determine_arg_locations(self, arg_types):
        """按 ABI 决定每个实参的位置：前四个非 blob 参数用 R1–R4，其余走栈。

        Given a set of argument types, determine location for argument
        ABI:
        pass arg1 in R1
        pass arg2 in R2
        pass arg3 in R3
        pass arg4 in R4
        return value in R0
        """
        # TODO: what ABI to use?
        # Perhaps follow the arm ABI spec?
        locations = []
        regs = [R1, R2, R3, R4]
        offset = 8
        for arg_ty in arg_types:
            if arg_ty.is_blob:
                r = StackLocation(offset, arg_ty.size)
                offset += arg_ty.size
            else:
                # Pass non-blob values in registers if possible:
                if regs:
                    r = regs.pop(0)
                else:
                    arg_size = self.info.get_size(arg_ty)
                    r = StackLocation(offset, arg_size)
                    offset += arg_size
            locations.append(r)
        return locations

    def determine_rv_location(self, ret_type):
        """返回值统一放在 R0（按 ABI 约定）。"""
        rv = R0
        return rv


class ArmAssembler(BaseAssembler):
    """ARM（A32）指令集的汇编器，扩展了寄存器列表与 ldr 伪指令语法。

    Assembler for the arm instruction set"""

    def __init__(self):
        """初始化汇编器并注册额外语法规则与字面量池状态。"""
        super().__init__()
        # self.parser.assembler = self
        self.add_extra_rules()

        self.lit_pool = []
        self.lit_counter = 0

    def add_extra_rules(self):
        """为语法解析器添加 "{}" 寄存器列表、寄存器范围与 ldr 伪指令规则。"""
        # Implement register list syntaxis:
        reg_nt = "$reg_cls_armregister$"
        self.typ2nt[RegisterSet] = "reg_list"
        self.add_rule(
            "reg_list", ["{", "reg_list_inner", "}"], lambda rhs: rhs[1]
        )
        self.add_rule("reg_list_inner", ["reg_or_range"], lambda rhs: rhs[0])

        # self.add_rule(
        #    'reg_list_inner',
        #    ['reg_or_range', ',', 'reg_list_inner'],
        #    lambda rhs: RegisterSet(rhs[0] | rhs[2]))
        self.add_rule(
            "reg_list_inner",
            ["reg_list_inner", ",", "reg_or_range"],
            lambda rhs: RegisterSet(rhs[0] | rhs[2]),
        )

        self.add_rule(
            "reg_or_range", [reg_nt], lambda rhs: RegisterSet([rhs[0]])
        )
        self.add_rule(
            "reg_or_range",
            [reg_nt, "-", reg_nt],
            lambda rhs: RegisterSet(register_range(rhs[0], rhs[2])),
        )

        # Ldr pseudo instruction:
        # TODO: fix the add_literal other way:
        self.add_rule(
            "instruction",
            ["ldr", reg_nt, ",", "=", "ID"],
            lambda rhs: LdrPseudo(rhs[1], rhs[4].val, self.add_literal),
        )

    def flush(self):
        """把挂起的字面量池内容（Label + dcd）真正发射出去。"""
        assert not self.in_macro
        while self.lit_pool:
            i = self.lit_pool.pop(0)
            self.emit(i)

    def add_literal(self, v):
        """为伪指令 LDR r0, =SOMESYM 登记一个字面量，返回生成的标签名。

        For use in the pseudo instruction LDR r0, =SOMESYM"""
        # Invent some label for the literal and store it.
        assert isinstance(v, str)
        self.lit_counter += 1
        label_name = f"_lit_{self.lit_counter}"
        self.lit_pool.append(Label(label_name))
        self.lit_pool.append(Dcd2(v))
        return label_name


class ThumbAssembler(BaseAssembler):
    """Thumb 指令集汇编器，扩展 "{}" 寄存器列表语法（Thumb 用 set 表示）。"""

    def __init__(self):
        """初始化汇编器并注册 Thumb 版寄存器列表语法规则。"""
        super().__init__()
        self.parser.assembler = self
        self.add_extra_rules()

    def add_extra_rules(self):
        """添加 "{}" 寄存器列表与寄存器范围（如 r0-r3）的语法规则。"""
        # Implement register list syntaxis:
        reg_nt = "$reg_cls_armregister$"
        self.typ2nt[set] = "reg_list"
        self.add_rule(
            "reg_list", ["{", "reg_list_inner", "}"], lambda rhs: rhs[1]
        )
        self.add_rule("reg_list_inner", ["reg_or_range"], lambda rhs: rhs[0])

        # For a left right parser, or right left parser, this is important:
        self.add_rule(
            "reg_list_inner",
            ["reg_list_inner", ",", "reg_or_range"],
            lambda rhs: rhs[0] | rhs[2],
        )
        # self.add_rule(
        # 'reg_list_inner',
        # ['reg_or_range', ',', 'reg_list_inner'], lambda rhs: rhs[0] | rhs[2])

        self.add_rule("reg_or_range", [reg_nt], lambda rhs: {rhs[0]})
        self.add_rule(
            "reg_or_range",
            [reg_nt, "-", reg_nt],
            lambda rhs: register_range(rhs[0], rhs[2]),
        )


def round_up(s):
    """把字节数向上取整到 4 字节边界（ARM 栈与字面量池均要求 4 对齐）。"""
    return s + (4 - s % 4)


# ARM 模式运行时（汇编实现）：__sdiv 用移位相减的软件算法实现 r1 / r2，
# 结果（商）放 r0，供可执行文件链接时提供；Thumb 模式无此运行时。
ARM_ASM_RT = """
global __sdiv
__sdiv:
   ; Divide r1 by r2
   ; R4 is a work register.
   ; r0 is the quotient
   push {r4}
   mov r4, r2         ; mov divisor into temporary register.

   ; Blow up divisor until it is larger than the divident.
   cmp r4, r1, lsr 1  ; If r4 < r1, then, shift left once more.
__sdiv_inc:
   movls r4, r4, lsl 1
   cmp r4, r1, lsr 1
   bls __sdiv_inc
   mov r0, 0          ; Initialize the result
                      ; Repeatedly substract shifted divisor
__sdiv_dec:
   cmp r1, r4         ; Can we substract the current temp value?
   subcs r1, r1, r4   ; Substract temp from divisor if carry
   adc r0, r0, r0     ; double (shift left) and add carry
   mov r4, r4, lsr 1  ; Shift right one
   cmp r4, r2         ; Is temp less than divisor?
   bhs __sdiv_dec     ; If so, repeat.

   pop {r4}
   mov pc, lr         ; Return from function.
"""
