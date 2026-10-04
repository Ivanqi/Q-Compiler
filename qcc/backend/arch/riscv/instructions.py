"""RISC-V（RV32I 基础整数指令集）机器指令定义与指令选择模式表。

本模块是 RISC-V 后端的"指令字典 + 指令选择规则表"，共分两大部分：

一、机器指令定义（isa 容器 ~ "Instruction selection patterns" 之前）
  每条指令都是一个 Instruction 子类，用类属性从三个维度描述一条指令：
    * Operand：操作数（寄存器 / 立即数 / 标签），read=True/write=True 标记读写性，
      寄存器分配器据此分析指令的使用与定值；
    * Syntax：汇编语法（若干字符串与操作数交错的列表），asm_printer 据此打印汇编；
    * tokens 与 patterns（或手写 encode()）：二进制编码。tokens 给出指令字的宽度
      （RiscvToken=32 位，RiscvIToken 同宽但字段布局不同），patterns 是声明式的
      位字段映射字典；对字段有特殊处理（如立即数拆位、条件取反）的指令则手写 encode()。
  RISC-V 的字段布局（见各工厂函数中的 patterns，均为 R/I/S/B 型标准编码）：
    opcode 占 bit[0:7]，rd 占 [7:12]，funct3 占 [12:15]，rs1 占 [15:20]，
    rs2 占 [20:25]，funct7 占 [25:32]；I 型把 [20:32] 当 12 位符号扩展立即数，
    S 型把立即数拆成 imm[4:0]→[7:12] 与 imm[11:5]→[25:32]。
  两个关键方法：
    * encode()：把操作数按字段拼装成 32 位机器字并返回字节序列（编码）；
    * relocations()：返回本指令需要链接器回填的重定位对象（如跳转目标、绝对地址）。
  由于 RV32 指令高度规整、大量指令只差几个位字段，模块用"类工厂函数"
  （make_regregreg / make_i / make_si / make_branch / make_str / make_ldr / make_mext
  等）配合 type() 动态批量生成同构指令类，避免重复样板代码。
  伪指令（PseudoRiscvInstruction 子类，如 Li / La / Labelrel / Align / Section）：
  自身不直接编码，render() 在发射时展开成若干条真指令。

二、指令选择模式（"Instruction selection patterns" 之后至文件末）
  @isa.pattern(非终结符, 树模式, size=代价, condition=条件) 是把 irdag 的树模式
  映射到发射函数的 BURG 规则：
    * 第一个参数是规则产生的非终结符（"reg" 值、"stm" 语句、"mem" 内存地址对）；
    * 第二个参数是树模式，如 "ADDI32(reg, CONSTI32)"，括号里是子节点的非终结符；
    * size 是代价，TreeSelector 用动态规划选取总代价最小的覆盖方案；
    * condition 是附加门槛（如立即数范围），不满足则该规则不可用；
    * 被装饰函数签名 (context, tree, c0, ...)，c0/c1 是已匹配子树的返回值，
      函数内通过 context.new_reg/emit/move 发射指令并返回结果 vreg。
  模式写得越丰富，越能写出"整棵子树融进一条指令"的规则，例如
  LDRI32(ADDI32(reg, CONSTI32)) 直接把 load(add(p, c)) 匹配成一条 lw rd, c(p)。

模块末尾的浮点相关模式（ADDF32/MULF32/CJMPF32 等）在没有硬件 FPU 时
通过 call_internal1/2 调用运行时软浮点函数（float32_add 等）实现。

Definitions of Riscv instructions.
"""

# pylint: disable=no-member,invalid-name
import struct

from qcc.utils.bitfun import inrange
from qcc.backend.arch.data_instructions import Dd
from qcc.backend.arch.encoding import Instruction, Operand, Syntax
from qcc.backend.arch.generic_instructions import (
    Alignment,
    ArtificialInstruction,
    Global,
    RegisterUseDef,
    SectionInstruction,
)
from qcc.backend.arch.isa import Isa
from qcc.backend.arch.riscv.registers import (
    FP,
    LR,
    R0,
    R10,
    R12,
    R13,
    RiscvCsrRegister,
    RiscvRegister,
)
from qcc.backend.arch.riscv.relocations import (
    Abs32Imm12Relocation,
    Abs32Imm20Relocation,
    AbsAddr32Relocation,
    BImm12Relocation,
    BImm20Relocation,
    RelImm12Relocation,
    RelImm20Relocation,
)
from qcc.backend.arch.riscv.tokens import RiscvIToken, RiscvToken

# ===========================================================================
# 全局指令集容器：register_relocation 登记本架构支持的重定位类型，
# 链接器按名字查表；@isa.pattern 装饰器则把指令选择规则追加进 isa.patterns。
# 注册的 7 种重定位（B 型 12/20 位 pc 相对跳转、绝对地址、绝对/相对立即数高低位）
# 覆盖了跳转、取地址（lui/addi）与位置无关代码（auipc/addi %pcrel_hi/%pcrel_lo）三类需求。
# ===========================================================================
isa = Isa()

isa.register_relocation(BImm12Relocation)
isa.register_relocation(BImm20Relocation)
isa.register_relocation(AbsAddr32Relocation)
isa.register_relocation(Abs32Imm20Relocation)
isa.register_relocation(Abs32Imm12Relocation)
isa.register_relocation(RelImm20Relocation)
isa.register_relocation(RelImm12Relocation)


class RiscvInstruction(Instruction):
    """所有真指令的基类：32 位定长指令字（RiscvToken），并绑定本架构的 isa 容器。"""

    tokens = [RiscvToken]
    isa = isa


class PseudoRiscvInstruction(ArtificialInstruction):
    """伪指令基类：本身不编码，靠 render() 在发射阶段展开成若干条真指令。"""

    isa = isa
    pass


# ---------------------------------------------------------------------------
# 一、伪指令与数据定义：对齐、段切换、数据字（dcd）与标签地址装载
# ---------------------------------------------------------------------------


class Align(PseudoRiscvInstruction):
    """伪指令 .align imm：展开成通用 Alignment 指令，把位置计数器对齐到 imm 边界。"""

    imm = Operand("imm", int)
    syntax = Syntax([".", "align", " ", imm])

    def render(self):
        self.rep = self.syntax.render(self)
        yield Alignment(self.imm, self.rep)


class Section(PseudoRiscvInstruction):
    """伪指令 .section sec：展开成通用 SectionInstruction，切换当前汇编段。"""

    sec = Operand("sec", str)
    syntax = Syntax([".", "section", " ", sec])

    def render(self):
        self.rep = self.syntax.render(self)
        yield SectionInstruction(self.sec, self.rep)


def dcd(v):
    """数据定义小工具：整数直接发原始数据 Dd，字符串（标签/符号引用）发 Dcd2。"""

    if type(v) is int:
        return Dd(v)
    elif type(v) is str:
        return Dcd2(v)
    else:  # pragma: no cover
        raise NotImplementedError()


class Dcd2(RiscvInstruction):
    """dcd = label：发射一个 32 位标签引用（地址常量）。

    编码时先填 0 占位，同时挂 AbsAddr32Relocation，由链接器在重定位阶段写入真实地址
    ——这是"编码期留空、链接期回填"的重定位指令标准套路。
    """

    v = Operand("v", str)
    syntax = Syntax(["dcd", "=", v])

    def encode(self):
        tokens = self.get_tokens()
        tokens[0][0:32] = 0
        return tokens[0].encode()

    def relocations(self):
        return [AbsAddr32Relocation(self.v)]


# ---------------------------------------------------------------------------
# 二、指令工厂函数：RISC-V 指令高度同构（仅少数位字段不同），
#     故用 type(name, (基类,), members) 动态批量造类，避免几十份重复样板。
# ---------------------------------------------------------------------------


class Movr(RiscvInstruction):
    """mv rd, rm：寄存器搬移（本质是 addi rd, rm, 0，故 opcode 用 0b0010011）。"""

    rd = Operand("rd", RiscvRegister, write=True)
    rm = Operand("rm", RiscvRegister, read=True)
    syntax = Syntax(["mv", " ", rd, ",", " ", rm])
    patterns = {
        "opcode": 0b0010011,
        "rd": rd,
        "funct3": 0,
        "rs1": rm,
        "rs2": 0,
        "funct7": 0,
    }


# --- CSR（控制状态寄存器）指令组：统一使用 0x73 opcode，CSR 编号放在 imm 字段 ---


class Csrs(RiscvInstruction):
    """csrs csr, rs：把 rs 的位或进 CSR（置位）。"""

    rd = Operand("rd", RiscvCsrRegister, write=True)
    rm = Operand("rm", RiscvRegister, read=True)
    syntax = Syntax(["csrs", " ", rd, ",", " ", rm])
    tokens = [RiscvIToken]
    patterns = {"opcode": 0x73, "rd": 0, "funct3": 2, "rs1": rm, "imm": rd}


def make_csrwi(mnemonic, func):
    """工厂：CSR 立即数写指令（csrwi/csrsi/csrci），funct3 区分"写/置位/清位"。

    注意 imm 操作数实际装的是 CSR 编号（patterns 中 imm 字段映射到 rd 操作数），
    真正的 5 位立即数固定为 0。
    """

    rd = Operand("rd", RiscvCsrRegister, write=True)
    imm = Operand("imm", int)
    syntax = Syntax([mnemonic, " ", rd, ",", " ", imm])
    tokens = [RiscvIToken]
    patterns = {"opcode": 0x73, "rd": 0, "funct3": func, "rs1": imm, "imm": rd}
    members = {
        "syntax": syntax,
        "tokens": tokens,
        "patterns": patterns,
        "rd": rd,
        "imm": imm,
    }
    return type(mnemonic.title(), (RiscvInstruction,), members)


Csrwi = make_csrwi("csrwi", 0b101)
Csrsi = make_csrwi("csrsi", 0b110)
Csrci = make_csrwi("csrci", 0b111)


class Csrw(RiscvInstruction):
    """csrw csr, rs：把 rs 的值写入 CSR。"""

    rd = Operand("rd", RiscvCsrRegister, write=True)
    rm = Operand("rm", RiscvRegister, read=True)
    syntax = Syntax(["csrw", " ", rd, ",", " ", rm])
    tokens = [RiscvIToken]
    patterns = {"opcode": 0x73, "rd": 0, "funct3": 1, "rs1": rm, "imm": rd}


class Csrr(RiscvInstruction):
    """csrr rd, csr：读取 CSR 到通用寄存器 rd。"""

    rd = Operand("rd", RiscvRegister, write=True)
    rm = Operand("rm", RiscvCsrRegister, read=True)
    syntax = Syntax(["csrr", " ", rd, ",", " ", rm])
    tokens = [RiscvIToken]
    patterns = {"opcode": 0x73, "rd": rd, "funct3": 2, "rs1": 0, "imm": rm}


class Mret(RiscvInstruction):
    """mret：从机器模式异常/中断返回（CSR 0x302 编码进 imm 字段）。"""

    syntax = Syntax(["mret"])
    tokens = [RiscvIToken]
    patterns = {"opcode": 0x73, "rd": 0, "funct3": 0, "rs1": 0, "imm": 0x302}


# --- R 型三寄存器指令工厂：一次造出全部 10 条算术/逻辑/移位指令 ---


def make_regregreg(mnemonic, opcode, func):
    """工厂：R 型三寄存器指令（opcode=0b0110011）。

    参数 opcode 实际对应 funct7（区分普通运算与带符号右移/sub），func 对应 funct3。
    """

    rd = Operand("rd", RiscvRegister, write=True)
    rn = Operand("rn", RiscvRegister, read=True)
    rm = Operand("rm", RiscvRegister, read=True)
    syntax = Syntax([mnemonic, " ", rd, ",", " ", rn, ",", " ", rm])
    tokens = [RiscvToken]
    patterns = {
        "opcode": 0b0110011,
        "rd": rd,
        "funct3": func,
        "rs1": rn,
        "rs2": rm,
        "funct7": opcode,
    }
    members = {
        "syntax": syntax,
        "rd": rd,
        "rn": rn,
        "rm": rm,
        "patterns": patterns,
        "tokens": tokens,
        "opcode": opcode,
        "func": func,
    }
    name = mnemonic.title() + "RegRegReg"
    return type(name, (RiscvInstruction,), members)


# 10 条 R 型指令实例：函数名后缀 r 表示与同名伪指令/立即数版本区分
# （如 Addr 是 add 指令，Addi 是 addi）。Sra/Sub 的 funct7=0b0100000（最高位为 1）。
Addr = make_regregreg("add", 0b0000000, 0b000)
Subr = make_regregreg("sub", 0b0100000, 0b000)
Sll = make_regregreg("sll", 0b0000000, 0b001)
Slt = make_regregreg("slt", 0b0000000, 0b010)
Sltu = make_regregreg("sltu", 0b0000000, 0b011)
Xorr = make_regregreg("xor", 0b0000000, 0b100)
Srl = make_regregreg("srl", 0b0000000, 0b101)
Sra = make_regregreg("sra", 0b0100000, 0b101)
Orr = make_regregreg("or", 0b0000000, 0b110)
Andr = make_regregreg("and", 0b0000000, 0b111)


def make_si(mnemonic, code, func):
    """工厂：移位立即数型指令（slli/srli/srai，opcode=0b0010011）。

    立即数字段 rs2 复用为移位数（shamt），code 装入 funct7 高位。
    """

    rd = Operand("rd", RiscvRegister, write=True)
    rs1 = Operand("rs1", RiscvRegister, read=True)
    imm = Operand("imm", int)
    syntax = Syntax([mnemonic, " ", rd, ",", " ", rs1, ",", " ", imm])
    tokens = [RiscvToken]
    patterns = {
        "opcode": 0b0010011,
        "rd": rd,
        "funct3": func,
        "rs1": rs1,
        "rs2": imm,
        "funct7": code,
    }
    members = {
        "syntax": syntax,
        "tokens": tokens,
        "patterns": patterns,
        "rd": rd,
        "rs1": rs1,
        "imm": imm,
    }
    name = mnemonic.title() + "ShiftImm"
    return type(name, (RiscvInstruction,), members)


# 移位立即数实例：Srai 的 funct7=0b0100000（算术右移），与 Srli 只差最高位
Slli = make_si("slli", 0b0000000, 0b001)
Srli = make_si("srli", 0b0000000, 0b101)
Srai = make_si("srai", 0b0100000, 0b101)


# --- I 型立即数运算指令：手写 encode() 的范例（字段位置一目了然） ---


class IBase(RiscvInstruction):
    """I 型立即数类指令基类：addi/slti/xori/ori/andi 共用同一套位字段编码。"""

    def encode(self):
        """手工拼装 I 型 32 位指令字：opcode|rd|funct3|rs1|imm[11:0]。"""
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b0010011
        tokens[0][7:12] = self.rd.num
        tokens[0][12:15] = self.func
        tokens[0][15:20] = self.rs1.num
        self.offset = self.offset & 0xFFF
        tokens[0][20:32] = self.offset
        return tokens[0].encode()


def make_i(mnemonic, func):
    """工厂：I 型立即数运算指令；fprel 标记该指令是否用于栈帧相对寻址。

    Factory function for immediate value instructions
    """
    rd = Operand("rd", RiscvRegister, write=True)
    rs1 = Operand("rs1", RiscvRegister, read=True)
    offset = Operand("offset", int)
    fprel = False
    syntax = Syntax([mnemonic, " ", rd, ",", " ", rs1, ",", " ", offset])
    members = {
        "syntax": syntax,
        "func": func,
        "fprel": fprel,
        "rd": rd,
        "rs1": rs1,
        "offset": offset,
    }
    return type(mnemonic + "_ins", (IBase,), members)


# I 型立即数运算实例
Addi = make_i("addi", 0b000)
Slti = make_i("slti", 0b010)
Sltiu = make_i("sltiu", 0b011)
Xori = make_i("xori", 0b100)
Ori = make_i("ori", 0b110)
Andi = make_i("andi", 0b111)


# ---------------------------------------------------------------------------
# 三、跳转、分支与地址装载指令
# ---------------------------------------------------------------------------


class Nop(RiscvInstruction):
    """nop：空操作，编码为 addi x0, x0, 0。"""

    syntax = Syntax(["nop"])
    patterns = {
        "opcode": 0b0010011,
        "rd": 0,
        "funct3": 0,
        "rs1": 0,
        "rs2": 0,
        "funct7": 0,
    }


# --- 计数器读取指令（rdcycle/rdtime/rdinstret 及其高 32 位变体） ---


class SmBase(RiscvInstruction):
    """rd* 系列基类：编码 CSR 编号到 imm 字段、funct3=0b010，产生新值到 rd。"""

    def encode(self):
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b1110011
        tokens[0][7:12] = self.rd.num
        tokens[0][12:15] = 0b010
        tokens[0][15:20] = 0
        tokens[0][20:32] = self.code
        return tokens[0].encode()


def make_sm(mnemonic, code):
    """工厂：rdcycle/rdtime/rdinstret 等计数器读取指令，code 即 CSR 编号。"""

    rd = Operand("rd", RiscvRegister, write=True)
    syntax = Syntax([mnemonic, " ", rd])
    members = {"syntax": syntax, "rd": rd, "code": code}
    return type(mnemonic + "_ins", (SmBase,), members)


# 计数器读取实例：低 32 位与高 32 位（hi）变体
Rdcyclei = make_sm("rdcycle", 0b110000000000)
Rdcyclehi = make_sm("rdcycleh", 0b110010000000)
Rdtimei = make_sm("rdtime", 0b110000000001)
Rdtimehi = make_sm("rdtimeh", 0b110010000001)
Rdinstreti = make_sm("rdinstret", 0b110000000010)
Rdinstrethi = make_sm("rdinstreth", 0b110010000010)


class Ebreak(RiscvInstruction):
    """ebreak：断点陷阱（调试器/模拟器中断）。"""

    syntax = Syntax(["ebreak"])
    patterns = {
        "opcode": 0b1110011,
        "rd": 0,
        "funct3": 0,
        "rs1": 0,
        "rs2": 0b1,
        "funct7": 0,
    }


# --- 跳转与函数调用（jal / jalr）：目标地址由 20 位/12 位 pc 相对重定位回填 ---


class Bl(RiscvInstruction):
    """jal rd, target：跳转并保存返回地址（调用），返回地址写入 rd。"""

    target = Operand("target", str)
    rd = Operand("rd", RiscvRegister, write=True)
    syntax = Syntax(["jal", " ", rd, ",", " ", target])

    def encode(self):
        """只填 opcode 与 rd，20 位目标偏移留给 BImm20Relocation 在链接期写入。"""
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b1101111
        tokens[0][7:12] = self.rd.num
        return tokens[0].encode()

    def relocations(self):
        return [BImm20Relocation(self.target)]


class B(RiscvInstruction):
    """j target：无条件跳转，即 jal x0, target（rd 固定为 0，不保存返回地址）。"""

    target = Operand("target", str)
    syntax = Syntax(["j", " ", target])

    def encode(self):
        """opcode=0b1101111 且 rd=0，目标偏移由 BImm20Relocation 回填。"""
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b1101111
        tokens[0][7:12] = 0
        return tokens[0].encode()

    def relocations(self):
        return [BImm20Relocation(self.target)]


class Blr(RiscvInstruction):
    """jalr rd, rs1, offset：间接跳转/返回，跳转目标为 rs1+offset 并写返回地址到 rd。

    常见的 ret 即 jalr x0, ra, 0；函数指针调用也用它。
    """

    rd = Operand("rd", RiscvRegister, write=True)
    rs1 = Operand("rs1", RiscvRegister, read=True)
    offset = Operand("offset", int)
    syntax = Syntax(["jalr", " ", rd, ",", rs1, ",", " ", offset])

    def encode(self):
        """I 型编码：opcode|rd|funct3=0|rs1|偏移(12 位)。"""
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b1100111
        tokens[0][7:12] = self.rd.num
        tokens[0][12:15] = 0
        tokens[0][15:20] = self.rs1.num
        tokens[0][20:32] = self.offset
        return tokens[0].encode()


class Lui(RiscvInstruction):
    """lui rd, imm：把 20 位立即数装入 rd 的高 20 位（低 12 位清零）。"""

    rd = Operand("rd", RiscvRegister, write=True)
    imm = Operand("imm", int)
    syntax = Syntax(["lui", " ", rd, ",", " ", imm])

    def encode(self):
        """U 型编码：opcode=0b0110111，高 20 位为立即数。"""
        tokens = self.get_tokens()
        imm20 = self.imm & 0xFFFFF
        tokens[0][0:7] = 0b0110111
        tokens[0][7:12] = self.rd.num
        tokens[0][12:32] = imm20
        return tokens[0].encode()


# --- 地址装载四件套：绝对地址（lui+addi）与位置无关（auipc+addi）各一对，
#     分别配合绝对/相对重定位，实现 32 位常量地址在 32 位指令中的拼接 ---


class Adru(RiscvInstruction):
    """lui rd, label：装载 label 绝对地址的高 20 位，低位由 Adrl 补齐。"""

    rd = Operand("rd", RiscvRegister, write=True)
    label = Operand("label", str)
    syntax = Syntax(["lui", " ", rd, ",", " ", label])

    def encode(self):
        """立即数位先填 0，由 Abs32Imm20Relocation 在链接期写入地址高位。"""
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b0110111
        tokens[0][7:12] = self.rd.num
        tokens[0][12:32] = 0
        return tokens[0].encode()

    def relocations(self):
        return [Abs32Imm20Relocation(self.label)]


class Adrurel(RiscvInstruction):
    """auipc rd, %pcrel_hi(label)：位置无关地取 label 相对地址的高 20 位。"""

    rd = Operand("rd", RiscvRegister, write=True)
    label = Operand("label", str)
    syntax = Syntax(
        ["auipc", " ", rd, ",", " ", "%", "pcrel_hi", "(", label, ")"]
    )

    def encode(self):
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b0010111
        tokens[0][7:12] = self.rd.num
        tokens[0][12:32] = 0
        return tokens[0].encode()

    def relocations(self):
        return [RelImm20Relocation(self.label)]


class Adrl(RiscvInstruction):
    """addi rd, rs1, label：取 label 绝对地址的低 12 位并与 Adru 的高位相加。"""

    rd = Operand("rd", RiscvRegister, write=True)
    rs1 = Operand("rs1", RiscvRegister, read=True)
    label = Operand("label", str)
    syntax = Syntax(["addi", " ", rd, ",", " ", rs1, ",", " ", label])

    def encode(self):
        """立即数位先填 0，由 Abs32Imm12Relocation 在链接期写入地址低位。"""
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b0010011
        tokens[0][7:12] = self.rd.num
        tokens[0][12:15] = 0
        tokens[0][15:20] = self.rs1.num
        tokens[0][20:32] = 0
        return tokens[0].encode()

    def relocations(self):
        return [Abs32Imm12Relocation(self.label)]


class Loadlrel(RiscvInstruction):
    """lw rd, %pcrel_lo(label)(rd)：按 pc 相对低 12 位偏移从内存载入一个字。

    rd 既是地址基址又是目的寄存器（read=True, write=True），
    低 12 位由 RelImm12Relocation 相对于前面的 auipc（Adrurel）回填。
    """

    rd = Operand("rd", RiscvRegister, write=True, read=True)
    label = Operand("label", str)
    syntax = Syntax(
        ["lw", " ", rd, "%", "pcrel_lo", "(", label, ")", "(", rd, ")"]
    )

    def encode(self):
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b0000011
        tokens[0][7:12] = self.rd.num
        tokens[0][12:15] = 0b010
        tokens[0][15:20] = self.rd.num
        tokens[0][20:32] = 0
        return tokens[0].encode()

    def relocations(self):
        return [RelImm12Relocation(self.label)]


class Adrlrel(RiscvInstruction):
    """addi rd, label：把 pc 相对低 12 位加到 rd 上，得到 label 的最终地址。

    rd 同时作为源与目的（read=True, write=True）：其值是前面 Adrurel（auipc）装入的高 20 位。
    """

    rd = Operand("rd", RiscvRegister, write=True, read=True)
    label = Operand("label", str)
    syntax = Syntax(["addi", " ", rd, ",", " ", label])

    def encode(self):
        """立即数位先填 0，由 RelImm12Relocation 相对 auipc 指令地址回填。"""
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b0010011
        tokens[0][7:12] = self.rd.num
        tokens[0][12:15] = 0b000
        tokens[0][15:20] = self.rd.num
        tokens[0][20:32] = 0
        return tokens[0].encode()

    def relocations(self):
        return [RelImm12Relocation(self.label)]


class Auipc(RiscvInstruction):
    """auipc rd, imm：rd = pc + (imm << 12)，用于 pc 相对地址计算。"""

    rd = Operand("rd", RiscvRegister, write=True)
    imm = Operand("imm", int)
    syntax = Syntax(["auipc", " ", rd, ",", " ", imm])

    def encode(self):
        """U 型编码：opcode=0b0010111，高 20 位为立即数。"""
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b0010111
        tokens[0][7:12] = self.rd.num
        tokens[0][12:32] = self.imm
        return tokens[0].encode()


# --- 伪指令三兄弟：render() 把语义清晰的抽象指令展开成若干条真指令 ---


class Labelrel(PseudoRiscvInstruction):
    """伪指令 lw rd, label（位置无关版）：展开为 auipc %pcrel_hi + lw %pcrel_lo。"""

    rd = Operand("rd", RiscvRegister, write=True)
    label = Operand("label", str)
    syntax = Syntax(["lw", " ", rd, ",", " ", label])

    def render(self):
        yield Adrurel(self.rd, self.label)
        yield Loadlrel(self.rd, self.label, self.rd)


class La(PseudoRiscvInstruction):
    """伪指令 la rd, label：把标签地址装入 rd，展开为 auipc %pcrel_hi + addi %pcrel_lo。"""

    rd = Operand("rd", RiscvRegister, write=True)
    label = Operand("label", str)
    syntax = Syntax(["la", " ", rd, ",", " ", label])

    def render(self):
        """先取 pc 相对高位，再加低位偏移得到标签地址。"""
        yield Adrurel(self.rd, self.label)
        yield Adrlrel(self.rd, self.label)


class Li(PseudoRiscvInstruction):
    """伪指令 li rd, imm：按立即数大小自适应展开为一条或两条指令。

    12 位有符号数放得下就只用一条 addi rd, x0, imm；
    否则用 lui 装高 20 位 + addi 补低 12 位。
    注意 addi 的 12 位立即数会符号扩展，若 bit11 为 1 会“多减 0x1000”，
    所以先把 imm 加 0x1000 再拆高位，让 lui 侧补偿掉这个偏差。
    """

    rd = Operand("rd", RiscvRegister, write=True)
    imm = Operand("imm", int)
    syntax = Syntax(["li", " ", rd, ",", " ", imm])

    def render(self):
        """自适应展开：小立即数一条 addi，大立即数 lui+addi 两条。"""
        # If the immediate value fits into 12 bits, do so!
        if inrange(self.imm, 12):
            yield Addi(self.rd, R0, self.imm)
        else:
            if (self.imm & 0x800) != 0:
                self.imm += 0x1000
            yield Lui(self.rd, self.imm >> 12)
            lower_bits = self.imm & 0xFFF
            yield Addi(self.rd, self.rd, lower_bits)


# --- B 型条件分支：原生只有 beq/bne/blt/bge/bltu/bgeu 六种，
#     bgt/ble/bgtu/bleu 通过 invert 交换两个源寄存器实现（零成本伪指令） ---


class BranchBase(RiscvInstruction):
    """条件分支基类：B 型编码，cond 为 funct3（分支条件），立即数为 12 位 pc 相对偏移。"""

    target = Operand("target", str)

    def encode(self):
        """按 cond 填 funct3；invert 为真时交换 rn/rm 的寄存器字段。

        例如 bgt a, b 等价于 blt b, a：只需交换 rs1/rs2 的位置即可复用同一 cond 码。
        """
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b1100011
        tokens[0][12:15] = self.cond
        if self.invert:
            tokens[0][15:20] = self.rm.num
            tokens[0][20:25] = self.rn.num
        else:
            tokens[0][15:20] = self.rn.num
            tokens[0][20:25] = self.rm.num
        return tokens[0].encode()

    def relocations(self):
        return [BImm12Relocation(self.target)]


def make_branch(mnemonic, cond, invert):
    """工厂：条件分支指令；invert=True 时编码期交换 rn/rm 实现反向条件。"""

    target = Operand("target", str)
    rn = Operand("rn", RiscvRegister, read=True)
    rm = Operand("rm", RiscvRegister, read=True)
    syntax = Syntax([mnemonic, " ", rn, ",", " ", rm, ",", " ", target])

    members = {
        "syntax": syntax,
        "target": target,
        "rn": rn,
        "rm": rm,
        "cond": cond,
        "invert": invert,
    }
    return type(mnemonic + "_ins", (BranchBase,), members)


# 分支实例：Bgt/Ble 复用 blt/bge 的 cond 码，通过 invert=True 交换操作数实现
Beq = make_branch("beq", 0b000, False)
Bne = make_branch("bne", 0b001, False)
Blt = make_branch("blt", 0b100, False)
Bgt = make_branch("bgt", 0b100, True)
Bge = make_branch("bge", 0b101, False)
Ble = make_branch("bge", 0b101, True)
Bltu = make_branch("bltu", 0b110, False)
Bgtu = make_branch("bgtu", 0b110, True)
Bgeu = make_branch("bgeu", 0b111, False)
Bleu = make_branch("bleu", 0b111, True)


def reg_list_to_mask(reg_list):
    """把寄存器列表压成位掩码（第 n 位表示寄存器 n），供调用约定/寄存器分配使用。"""

    mask = 0
    for reg in reg_list:
        mask |= 1 << reg.num
    return mask


# --- S 型存储指令：立即数被拆成两段（imm[4:0] 与 imm[11:5]）跨字段存放 ---


class StrBase(RiscvInstruction):
    """存储指令基类（sb/sh/sw）：把 rs2 的值写入地址 rs1+offset。"""

    def encode(self):
        """S 型编码：立即数低 5 位放 [7:12]，高 7 位放 [25:32]。"""
        imml5 = self.offset & 0x1F
        immh7 = (self.offset >> 5) & 0x7F
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b0100011
        tokens[0][7:12] = imml5
        tokens[0][12:15] = self.func
        tokens[0][15:20] = self.rs1.num
        tokens[0][20:25] = self.rs2.num
        tokens[0][25:32] = immh7
        return tokens[0].encode()


def make_str(mnemonic, func):
    """工厂：存储指令（按 func 区分字节/半字/字宽度）。"""

    rs2 = Operand("rs2", RiscvRegister, read=True)
    offset = Operand("offset", int)
    rs1 = Operand("rs1", RiscvRegister, read=True)
    fprel = False
    syntax = Syntax([mnemonic, " ", rs2, ",", " ", offset, "(", rs1, ")"])
    members = {
        "syntax": syntax,
        "func": func,
        "fprel": fprel,
        "offset": offset,
        "rs1": rs1,
        "rs2": rs2,
    }
    return type(mnemonic.title(), (StrBase,), members)


Sb = make_str("sb", 0b000)
Sh = make_str("sh", 0b001)
Sw = make_str("sw", 0b010)


# --- I 型加载指令：从地址 rs1+offset 读取并（按宽度）扩展后写入 rd ---


def make_ldr(mnemonic, func):
    """工厂：加载指令（lb/lh/lw 符号扩展，lbu/lhu 零扩展）。"""

    rd = Operand("rd", RiscvRegister, write=True)
    offset = Operand("offset", int)
    rs1 = Operand("rs1", RiscvRegister, read=True)
    fprel = False
    syntax = Syntax([mnemonic, " ", rd, ",", " ", offset, "(", rs1, ")"])
    tokens = [RiscvIToken]
    patterns = {
        "opcode": 0b0000011,
        "rd": rd,
        "funct3": func,
        "rs1": rs1,
        "imm": offset,
    }
    members = {
        "syntax": syntax,
        "tokens": tokens,
        "patterns": patterns,
        "fprel": fprel,
        "offset": offset,
        "rd": rd,
        "rs1": rs1,
    }
    return type(mnemonic.title(), (RiscvInstruction,), members)


# 加载实例：lbu/lhu 为零扩展加载（高位置 0）
Lb = make_ldr("lb", 0b000)
Lh = make_ldr("lh", 0b001)
Lw = make_ldr("lw", 0b010)
Lbu = make_ldr("lbu", 0b100)
Lhu = make_ldr("lhu", 0b101)


# --- M 扩展（乘除法）：R 型编码，funct7=0b0000001 将其与基础算术指令区分开 ---


class MextBase(RiscvInstruction):
    """M 扩展指令基类：mul/div/divu/rem/remu 共用编码，func 即 funct3。"""

    def encode(self):
        tokens = self.get_tokens()
        tokens[0][0:7] = 0b0110011
        tokens[0][7:12] = self.rd.num
        tokens[0][12:15] = self.func
        tokens[0][15:20] = self.rs1.num
        tokens[0][20:25] = self.rs2.num
        tokens[0][25:32] = 0b0000001
        return tokens[0].encode()


def make_mext(mnemonic, func):
    """工厂：M 扩展乘除法指令。"""

    rs1 = Operand("rs1", RiscvRegister, read=True)
    rs2 = Operand("rs2", RiscvRegister, read=True)
    rd = Operand("rd", RiscvRegister, write=True)
    syntax = Syntax([mnemonic, " ", rd, ",", " ", rs1, ",", " ", rs2])
    members = {
        "syntax": syntax,
        "func": func,
        "rd": rd,
        "rs1": rs1,
        "rs2": rs2,
    }
    return type(mnemonic + "_ins", (MextBase,), members)


Mul = make_mext("mul", 0b000)
Div = make_mext("div", 0b100)
Divu = make_mext("divu", 0b101)
Rem = make_mext("rem", 0b110)
Remu = make_mext("remu", 0b111)

# ===========================================================================
# 四、指令选择模式（BURG 规则）
#
# @isa.pattern(非终结符, 树模式, size=代价, condition=条件) 由 InstructionSelector
# 收集进规则库，TreeSelector 对 IR 树做动态规划，选出总代价最小的覆盖方案后调用
# 下面的函数。约定：
#   * 非终结符："reg" 表示产生一个值（返回 vreg），"stm" 表示产生副作用（无返回），
#     "mem" 表示产生"基址寄存器 + 偏移"二元组供访存指令消费；
#   * size：该覆盖的代价，越便宜越优先（复杂树模式代价低即可"吃掉"整棵子树）；
#   * condition：可用性门槛（如常量必须落在立即数范围内），不满足则不采用此规则；
#   * 函数参数 context/tree 之外多出的 c0、c1…… 是已匹配子树的返回值（"reg" 为 vreg，
#     "mem" 为 (基址, 偏移) 元组）。
# 本文件按 IR 节点族组织了 MOV/JMP/REG/类型转换/CONST/算术/分支/访存/浮点等模式组。
# ===========================================================================

# Instruction selection patterns:


# --- 数据传送：MOV 系列把子节点的值搬进本节点的目标 vreg ---
@isa.pattern("stm", "MOVI16(reg)", size=2)
@isa.pattern("stm", "MOVU16(reg)", size=2)
@isa.pattern("stm", "MOVI32(reg)", size=2)
@isa.pattern("stm", "MOVU32(reg)", size=2)
@isa.pattern("stm", "MOVF32(reg)", size=10)
@isa.pattern("stm", "MOVF64(reg)", size=10)
def pattern_mov32(context, tree, c0):
    """把子节点的值 move 到本节点的目标 vreg（寄存器分配器负责真正落地）。"""

    context.move(tree.value, c0)
    return tree.value


@isa.pattern("stm", "MOVU8(reg)", size=2)
@isa.pattern("stm", "MOVI8(reg)", size=2)
def pattern_movi8(context, tree, c0):
    context.move(tree.value, c0)
    return tree.value


# --- 无条件跳转：发射伪指令 j，并把目标基本块登记进指令的 jumps 列表 ---
@isa.pattern("stm", "JMP", size=4)
def pattern_jmp(context, tree):
    """发射 j target；jumps=[tgt] 供 CFG 分析/寄存器分配使用。"""

    tgt = tree.value
    context.emit(B(tgt.name, jumps=[tgt]))


# --- 内存块搬运：借助架构的 gen_riscv_memcpy 展开成逐字拷贝序列 ---
@isa.pattern("stm", "MOVB(reg, reg)", size=40)
def pattern_movb(context, tree, c0, c1):
    """块拷贝 d = s（size 存在 tree.value 里），用架构生成的 memcpy 指令序列实现。"""

    # Emit memcpy
    dst = c0
    src = c1
    tmp = context.new_reg(RiscvRegister)
    size = tree.value
    for instruction in context.arch.gen_riscv_memcpy(dst, src, tmp, size):
        context.emit(instruction)


# --- 寄存器引用：直接返回节点上的 vreg，代价为 0（不发射任何指令） ---
@isa.pattern("reg", "REGI32", size=0)
@isa.pattern("reg", "REGI16", size=0)
@isa.pattern("reg", "REGI8", size=0)
@isa.pattern("reg", "REGU32", size=0)
@isa.pattern("reg", "REGF32", size=10)
@isa.pattern("reg", "REGF64", size=10)
@isa.pattern("reg", "REGU16", size=0)
@isa.pattern("reg", "REGU8", size=0)
def pattern_reg(context, tree):
    """寄存器节点：零代价直接复用已分配的虚拟寄存器。"""

    return tree.value


@isa.pattern("reg", "U32TOU16(reg)", size=0)
@isa.pattern("reg", "U32TOI16(reg)", size=0)
@isa.pattern("reg", "I32TOI16(reg)", size=0)
@isa.pattern("reg", "I32TOU16(reg)", size=0)
@isa.pattern("reg", "U16TOU8(reg)", size=0)
@isa.pattern("reg", "U16TOI8(reg)", size=0)
@isa.pattern("reg", "I16TOI8(reg)", size=0)
@isa.pattern("reg", "I16TOU8(reg)", size=0)
@isa.pattern("reg", "F32TOF64(reg)", size=10)
@isa.pattern("reg", "F64TOF32(reg)", size=10)
def pattern_i32_to_i32(context, tree, c0):
    """等宽或收窄到 32 位的转换（I32<->I16/U16、U16<->U8……）：无需指令，原值返回。"""

    return c0


@isa.pattern("reg", "I8TOI16(reg)", size=4)
@isa.pattern("reg", "I8TOI32(reg)", size=4)
def pattern_i8_to_i32(context, tree, c0):
    """有符号 8 位扩展为 32 位：左移 24 位再算术右移 24 位（把符号位铺满高位）。"""

    context.emit(Slli(c0, c0, 24))
    context.emit(Srai(c0, c0, 24))
    return c0


@isa.pattern("reg", "I16TOI32(reg)", size=4)
def pattern_i16_to_i32(context, tree, c0):
    context.emit(Slli(c0, c0, 16))
    context.emit(Srai(c0, c0, 16))
    return c0


@isa.pattern("reg", "I8TOU16(reg)", size=4)
@isa.pattern("reg", "U8TOU16(reg)", size=4)
@isa.pattern("reg", "U8TOI16(reg)", size=4)
def pattern_8_to_16(context, tree, c0):
    """扩展为无符号 16 位：左移 24 位再逻辑右移 24 位（高位置 0）。"""

    context.emit(Slli(c0, c0, 24))
    context.emit(Srli(c0, c0, 24))
    return c0


@isa.pattern("reg", "I8TOU32(reg)", size=4)
@isa.pattern("reg", "U8TOU32(reg)", size=4)
@isa.pattern("reg", "U8TOI32(reg)", size=4)
def pattern_8_to_32(context, tree, c0):
    context.emit(Slli(c0, c0, 24))
    context.emit(Srli(c0, c0, 24))
    return c0


@isa.pattern("reg", "I16TOU32(reg)", size=4)
@isa.pattern("reg", "U16TOU32(reg)", size=4)
@isa.pattern("reg", "U16TOI32(reg)", size=4)
def pattern_16_to_32(context, tree, c0):
    context.emit(Slli(c0, c0, 16))
    context.emit(Srli(c0, c0, 16))
    return c0


@isa.pattern("reg", "I32TOI8(reg)", size=0)
@isa.pattern("reg", "I32TOU8(reg)", size=0)
@isa.pattern("reg", "I32TOI16(reg)", size=0)
@isa.pattern("reg", "I32TOU16(reg)", size=0)
@isa.pattern("reg", "U32TOU8(reg)", size=0)
@isa.pattern("reg", "U32TOI8(reg)", size=0)
@isa.pattern("reg", "U32TOU16(reg)", size=0)
@isa.pattern("reg", "U32TOI16(reg)", size=0)
def pattern_32_to_8_16(context, tree, c0):
    """收窄到 8/16 位：RISC-V 寄存器运算天然按 32 位进行，暂不做额外处理，直接返回。"""

    # TODO: do something like sign extend or something else?
    return c0


# --- 常量装载：小常量（-2048..2048，落在 addi 立即数范围内）代价 2 优先，
#     其余用代价 4 的通用规则兜底；两者都发射自适应伪指令 Li ---
@isa.pattern("reg", "CONSTI32", size=4)
@isa.pattern("reg", "CONSTU32", size=4)
@isa.pattern("reg", "CONSTI16", size=4)
@isa.pattern("reg", "CONSTU16", size=4)
@isa.pattern(
    "reg",
    "CONSTI32",
    size=2,
    condition=lambda t: t.value in range(-2048, 2048),
)
@isa.pattern(
    "reg",
    "CONSTI16",
    size=2,
    condition=lambda t: t.value in range(-2048, 2048),
)
@isa.pattern(
    "reg", "CONSTI8", size=2, condition=lambda t: t.value in range(-128, 128)
)
@isa.pattern("reg", "CONSTU8", size=2, condition=lambda t: t.value < 256)
def pattern_const_i32(context, tree):
    """整数常量：发 Li 伪指令装载（小常量一条 addi，大常量 lui+addi）。"""

    d = context.new_reg(RiscvRegister)
    c0 = tree.value
    context.emit(Li(d, c0))
    return d


@isa.pattern("reg", "CONSTF32", size=10)
@isa.pattern("reg", "CONSTF64", size=10)
def pattern_const_f32(context, tree):
    """浮点常量：先把 float 的 IEEE 754 位模式拆成整数，再用 Li 装载。"""

    float_const = struct.pack("f", tree.value)
    (c0,) = struct.unpack("i", float_const)
    d = context.new_reg(RiscvRegister)
    context.emit(Li(d, c0))
    return d


# --- 有符号条件分支：按比较运算符查表选分支指令，直接跳 yes；
#     再追加一条 j 走 no 路径（真分支 + 无条件跳转的两段式发射） ---
@isa.pattern("stm", "CJMPI32(reg, reg)", size=4)
@isa.pattern("stm", "CJMPI16(reg, reg)", size=4)
@isa.pattern("stm", "CJMPI8(reg, reg)", size=4)
def pattern_cjmpi(context, tree, c0, c1):
    """发射 Bop(c0, c1, yes) 与 j no；jumps 里登记 yes 与 jmp_ins（CFG 用）。"""

    op, yes_label, no_label = tree.value
    opnames = {"<": Blt, ">": Bgt, "==": Beq, "!=": Bne, ">=": Bge, "<=": Ble}
    Bop = opnames[op]
    jmp_ins = B(no_label.name, jumps=[no_label])
    context.emit(Bop(c0, c1, yes_label.name, jumps=[yes_label, jmp_ins]))
    context.emit(jmp_ins)


@isa.pattern("stm", "CJMPU8(reg, reg)", size=4)
@isa.pattern("stm", "CJMPU16(reg, reg)", size=4)
@isa.pattern("stm", "CJMPU32(reg, reg)", size=4)
def pattern_cjmpu(context, tree, c0, c1):
    """无符号条件分支：与 cjmpi 同构，改用 bltu/bgtu/bgeu/bleu 系列。"""

    op, yes_label, no_label = tree.value
    opnames = {
        "<": Bltu,
        ">": Bgtu,
        "==": Beq,
        "!=": Bne,
        ">=": Bgeu,
        "<=": Bleu,
    }
    Bop = opnames[op]
    jmp_ins = B(no_label.name, jumps=[no_label])
    context.emit(Bop(c0, c1, yes_label.name, jumps=[yes_label, jmp_ins]))
    context.emit(jmp_ins)


# --- 算术运算：同一条 IR 运算按"寄存器-寄存器"与"寄存器-小常量"给出两套规则，
#     BURG 动态规划会挑选代价更小、能吃掉更多子树的那个组合 ---
@isa.pattern("reg", "ADDU32(reg, reg)", size=2)
@isa.pattern("reg", "ADDI32(reg, reg)", size=2)
def pattern_add_i32(context, tree, c0, c1):
    """32 位加法：add d, c0, c1。"""

    d = context.new_reg(RiscvRegister)
    context.emit(Addr(d, c0, c1))
    return d


@isa.pattern("reg", "ADDU16(reg, reg)", size=2)
@isa.pattern("reg", "ADDI16(reg, reg)", size=2)
def pattern_add_i16(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Addr(d, c0, c1))
    return d


@isa.pattern("reg", "ADDI8(reg, reg)", size=2)
@isa.pattern("reg", "ADDU8(reg, reg)", size=2)
def pattern_add8(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Addr(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "ADDI32(reg, CONSTI32)",
    size=2,
    condition=lambda t: t[1].value < 2048,
)
@isa.pattern(
    "reg",
    "ADDU32(reg, CONSTU32)",
    size=2,
    condition=lambda t: t[1].value < 2048,
)
def pattern_add_i32_reg_const(context, tree, c0):
    """加法右操作数是小常量：融进立即数加法指令 addi d, c0, imm。"""

    d = context.new_reg(RiscvRegister)
    c1 = tree.children[1].value
    context.emit(Addi(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "ADDI32(CONSTI32, reg)",
    size=2,
    condition=lambda t: t.children[0].value < 2048,
)
@isa.pattern(
    "reg",
    "ADDU32(CONSTU32, reg)",
    size=2,
    condition=lambda t: t.children[0].value < 2048,
)
def pattern_add_i32_const_reg(context, tree, c0):
    """加法左操作数是小常量（交换律）：同样融成 addi。"""

    d = context.new_reg(RiscvRegister)
    c1 = tree.children[0].value
    context.emit(Addi(d, c0, c1))
    return d


@isa.pattern("reg", "SUBI8(reg, reg)", size=2)
@isa.pattern("reg", "SUBU8(reg, reg)", size=2)
@isa.pattern("reg", "SUBI16(reg, reg)", size=2)
@isa.pattern("reg", "SUBU16(reg, reg)", size=2)
@isa.pattern("reg", "SUBI32(reg, reg)", size=2)
@isa.pattern("reg", "SUBU32(reg, reg)", size=2)
def pattern_sub_i32(context, tree, c0, c1):
    """各宽度减法统一用 sub d, c0, c1。"""

    d = context.new_reg(RiscvRegister)
    context.emit(Subr(d, c0, c1))
    return d


# --- 标签取址：LABEL 节点取符号地址（两条规则按代价竞争：
#     size=6 的绝对地址版 lui+addi+lw，size=4 的位置无关版 lw %pcrel） ---
@isa.pattern("reg", "LABEL", size=6)
def pattern_label1(context, tree):
    """绝对地址版：lui 高位 + addi 低位拼出地址，再 lw 取出常量值。"""

    d = context.new_reg(RiscvRegister)
    ln = context.frame.add_constant(tree.value)
    context.emit(Adru(d, ln))
    context.emit(Adrl(d, d, ln))
    context.emit(Lw(d, 0, d))
    return d


@isa.pattern("reg", "LABEL", size=4)
def pattern_label2(context, tree):
    """位置无关版：用 Labelrel 伪指令（auipc+lw）取标签处的内容。"""

    d = context.new_reg(RiscvRegister)
    ln = context.frame.add_constant(tree.value)
    context.emit(Labelrel(d, ln))
    return d


@isa.pattern(
    "reg",
    "FPRELU32",
    size=4,
    condition=lambda t: t.value.offset in range(-2048, 2048),
)
def pattern_fpreli32(context, tree):
    d = context.new_reg(RiscvRegister)
    offset = tree.value.offset
    Code = Addi(d, FP, offset)
    Code.fprel = True
    context.emit(Code)
    return d


# Memory patterns:
@isa.pattern(
    "mem",
    "FPRELU32",
    size=0,
    condition=lambda t: t.value.offset in range(-2048, 2048),
)
def pattern_mem_fpreli32(context, tree):
    offset = tree.value.offset
    return FP, offset


@isa.pattern("mem", "reg", size=10)
def pattern_mem_reg(context, tree, c0):
    return c0, 0


@isa.pattern("stm", "STRU32(mem, reg)", size=2)
@isa.pattern("stm", "STRI32(mem, reg)", size=2)
@isa.pattern("stm", "STRF32(mem, reg)", size=10)
@isa.pattern("stm", "STRF64(mem, reg)", size=10)
def pattern_sw32(context, tree, c0, c1):
    base_reg, offset = c0
    Code = Sw(c1, offset, base_reg)
    Code.fprel = True
    context.emit(Code)


@isa.pattern("stm", "STRU32(reg, reg)", size=2)
@isa.pattern("stm", "STRI32(reg, reg)", size=2)
@isa.pattern("stm", "STRF32(reg, reg)", size=10)
@isa.pattern("stm", "STRF64(reg, reg)", size=10)
def pattern_sw32_reg(context, tree, c0, c1):
    base_reg = c0
    Code = Sw(c1, 0, base_reg)
    context.emit(Code)


@isa.pattern("stm", "STRI16(mem, reg)", size=2)
@isa.pattern("stm", "STRU16(mem, reg)", size=2)
def pattern_str16_mem(context, tree, c0, c1):
    base_reg, offset = c0
    Code = Sh(c1, offset, base_reg)
    Code.fprel = True
    context.emit(Code)


@isa.pattern("stm", "STRI16(reg, reg)", size=2)
@isa.pattern("stm", "STRU16(reg, reg)", size=2)
def pattern_str16_reg(context, tree, c0, c1):
    base_reg = c0
    Code = Sh(c1, 0, base_reg)
    context.emit(Code)


@isa.pattern("stm", "STRU8(mem, reg)", size=2)
@isa.pattern("stm", "STRI8(mem, reg)", size=2)
def pattern_sbi8_mem(context, tree, c0, c1):
    base_reg, offset = c0
    Code = Sb(c1, offset, base_reg)
    Code.fprel = True
    context.emit(Code)


@isa.pattern("stm", "STRU8(reg, reg)", size=2)
@isa.pattern("stm", "STRI8(reg, reg)", size=2)
def pattern_sbi8_reg(context, tree, c0, c1):
    base_reg = c0
    Code = Sb(c1, 0, base_reg)
    context.emit(Code)


@isa.pattern("reg", "LDRI8(mem)", size=2)
def pattern_ldri8(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    base_reg, offset = c0
    Code = Lb(d, offset, base_reg)
    Code.fprel = True
    context.emit(Code)
    return d


@isa.pattern("reg", "LDRI8(reg)", size=2)
def pattern_ldri8_reg(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    base_reg = c0
    Code = Lb(d, 0, base_reg)
    context.emit(Code)
    return d


@isa.pattern("reg", "LDRU8(mem)", size=2)
def pattern_ldru8_fprel(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    base_reg, offset = c0
    Code = Lbu(d, offset, base_reg)
    Code.fprel = True
    context.emit(Code)
    return d


@isa.pattern("reg", "LDRU8(reg)", size=2)
def pattern_ldru8_reg(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    base_reg = c0
    Code = Lbu(d, 0, base_reg)
    context.emit(Code)
    return d


@isa.pattern("reg", "LDRU32(mem)", size=2)
@isa.pattern("reg", "LDRI32(mem)", size=2)
@isa.pattern("reg", "LDRF32(mem)", size=10)
@isa.pattern("reg", "LDRF64(mem)", size=10)
def pattern_ldr32_fprel(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    base_reg, offset = c0
    Code = Lw(d, offset, base_reg)
    Code.fprel = True
    context.emit(Code)
    return d


@isa.pattern("reg", "LDRU32(reg)", size=2)
@isa.pattern("reg", "LDRI32(reg)", size=2)
@isa.pattern("reg", "LDRF32(reg)", size=10)
@isa.pattern("reg", "LDRF64(reg)", size=10)
def pattern_ldr32_reg(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    base_reg = c0
    Code = Lw(d, 0, base_reg)
    context.emit(Code)
    return d


@isa.pattern("reg", "NEGI8(reg)", size=2)
@isa.pattern("reg", "NEGI16(reg)", size=2)
@isa.pattern("reg", "NEGI32(reg)", size=2)
@isa.pattern("reg", "NEGU32(reg)", size=2)
def pattern_negi32(context, tree, c0):
    context.emit(Subr(c0, R0, c0))
    return c0


@isa.pattern("reg", "INVI8(reg)", size=2)
@isa.pattern("reg", "INVU8(reg)", size=2)
@isa.pattern("reg", "INVU32(reg)", size=2)
@isa.pattern("reg", "INVI32(reg)", size=2)
def pattern_inv(context, tree, c0):
    context.emit(Xori(c0, c0, -1))
    return c0


@isa.pattern("reg", "LDRU16(reg)", size=2)
def pattern_ldru16(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    context.emit(Lhu(d, 0, c0))
    return d


@isa.pattern("reg", "LDRI16(reg)", size=2)
def pattern_ldri16(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    context.emit(Lh(d, 0, c0))
    return d


@isa.pattern("reg", "LDRU32(reg)", size=2)
@isa.pattern("reg", "LDRI32(reg)", size=2)
def pattern_ldr_i32(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    context.emit(Lw(d, 0, c0))
    return d


@isa.pattern("reg", "ANDI8(reg, reg)", size=2)
@isa.pattern("reg", "ANDU8(reg, reg)", size=2)
@isa.pattern("reg", "ANDI16(reg, reg)", size=2)
@isa.pattern("reg", "ANDU16(reg, reg)", size=2)
@isa.pattern("reg", "ANDI32(reg, reg)", size=2)
@isa.pattern("reg", "ANDU32(reg, reg)", size=2)
def pattern_and_i(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Andr(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "ANDI32(reg, CONSTI32)",
    size=2,
    condition=lambda t: t.children[1].value < 2048,
)
def pattern_and_i32(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    c1 = tree.children[1].value
    context.emit(Andi(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "ANDI8(reg, CONSTI8)",
    size=2,
    condition=lambda t: t.children[1].value < 256,
)
@isa.pattern(
    "reg",
    "ANDU8(reg, CONSTU8)",
    size=2,
    condition=lambda t: t.children[1].value < 256,
)
def pattern_and8_reg_const(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    c1 = tree.children[1].value
    context.emit(Andi(d, c0, c1))
    return d


@isa.pattern("reg", "ORU32(reg, reg)", size=2)
@isa.pattern("reg", "ORI32(reg, reg)", size=2)
@isa.pattern("reg", "ORU16(reg, reg)", size=2)
@isa.pattern("reg", "ORI16(reg, reg)", size=2)
@isa.pattern("reg", "ORU8(reg, reg)", size=2)
@isa.pattern("reg", "ORI8(reg, reg)", size=2)
def pattern_or_i32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Orr(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "ORI32(reg, CONSTI32)",
    size=2,
    condition=lambda t: t.children[1].value < 2048,
)
def pattern_or_i32_reg_const(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    c1 = tree.children[1].value
    context.emit(Ori(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "ORI32(CONSTI32, reg)",
    size=2,
    condition=lambda t: t.children[0].value < 2048,
)
def pattern_or_i32_const_reg(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    c1 = tree.children[0].value
    context.emit(Ori(d, c0, c1))
    return d


@isa.pattern("reg", "SHRU8(reg, reg)", size=2)
@isa.pattern("reg", "SHRU16(reg, reg)", size=2)
@isa.pattern("reg", "SHRU32(reg, reg)", size=2)
def pattern_shr_u32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Srl(d, c0, c1))
    return d


@isa.pattern("reg", "SHRI8(reg, reg)", size=2)
def pattern_shr_i8(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Slli(c0, c0, 24))
    context.emit(Srai(c0, c0, 24))
    context.emit(Sra(d, c0, c1))
    return d


@isa.pattern("reg", "SHRI16(reg, reg)", size=2)
def pattern_shr_i16(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Slli(c0, c0, 16))
    context.emit(Srai(c0, c0, 16))
    context.emit(Sra(d, c0, c1))
    return d


@isa.pattern("reg", "SHRI32(reg, reg)", size=2)
def pattern_shr_i32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Sra(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "SHRI32(reg, CONSTI32)",
    size=2,
    condition=lambda t: t.children[1].value < 32,
)
def pattern_shr_i32_reg_const(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    c1 = tree.children[1].value
    context.emit(Srai(d, c0, c1))
    return d


@isa.pattern("reg", "SHLU8(reg, reg)", size=2)
@isa.pattern("reg", "SHLI8(reg, reg)", size=2)
@isa.pattern("reg", "SHLU16(reg, reg)", size=2)
@isa.pattern("reg", "SHLI16(reg, reg)", size=2)
@isa.pattern("reg", "SHLU32(reg, reg)", size=2)
@isa.pattern("reg", "SHLI32(reg, reg)", size=2)
def pattern_shl_i32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Sll(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "SHLI32(reg, CONSTI32)",
    size=2,
    condition=lambda t: t.children[1].value < 32,
)
def pattern_shl_i32_reg_const(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    c1 = tree.children[1].value
    context.emit(Slli(d, c0, c1))
    return d


@isa.pattern("reg", "MULI8(reg, reg)", size=10)
@isa.pattern("reg", "MULU8(reg, reg)", size=10)
@isa.pattern("reg", "MULU16(reg, reg)", size=10)
@isa.pattern("reg", "MULI32(reg, reg)", size=10)
@isa.pattern("reg", "MULU32(reg, reg)", size=10)
def pattern_mul_i32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Mul(d, c0, c1))
    return d


@isa.pattern("reg", "LDRI32(ADDI32(reg, CONSTI32))", size=2)
def pattern_ldr_i32_add(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    c1 = tree.children[0].children[1].value
    assert isinstance(c1, int)
    context.emit(Lw(d, c1, c0))
    return d


@isa.pattern("reg", "DIVI32(reg, reg)", size=10)
def pattern_div_i32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Div(d, c0, c1))
    return d


@isa.pattern("reg", "DIVU16(reg, reg)", size=10)
@isa.pattern("reg", "DIVU32(reg, reg)", size=10)
def pattern_div_u32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Divu(d, c0, c1))
    return d


@isa.pattern("reg", "REMI32(reg, reg)", size=10)
def pattern_rem_i32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Rem(d, c0, c1))
    return d


@isa.pattern("reg", "REMU16(reg, reg)", size=10)
@isa.pattern("reg", "REMU32(reg, reg)", size=10)
def pattern_rem_u32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Remu(d, c0, c1))
    return d


@isa.pattern("reg", "XORU8(reg, reg)", size=2)
@isa.pattern("reg", "XORI8(reg, reg)", size=2)
@isa.pattern("reg", "XORU16(reg, reg)", size=2)
@isa.pattern("reg", "XORI16(reg, reg)", size=2)
@isa.pattern("reg", "XORU32(reg, reg)", size=2)
@isa.pattern("reg", "XORI32(reg, reg)", size=2)
def pattern_xor_i32(context, tree, c0, c1):
    d = context.new_reg(RiscvRegister)
    context.emit(Xorr(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "XORI32(reg, CONSTI32)",
    size=2,
    condition=lambda t: t.children[1].value < 2048,
)
def pattern_xor_i32_reg_const(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    c1 = tree.children[1].value
    context.emit(Xori(d, c0, c1))
    return d


@isa.pattern(
    "reg",
    "XORI32(CONSTI32, reg)",
    size=2,
    condition=lambda t: t.children[0].value < 2048,
)
def pattern_xor_i32_const_reg(context, tree, c0):
    d = context.new_reg(RiscvRegister)
    c1 = tree.children[0].value
    context.emit(Xori(d, c0, c1))
    return d


def call_internal2(context, name, a, b, clobbers=()):
    d = context.new_reg(RiscvRegister)
    context.move(R12, a)
    context.move(R13, b)
    context.emit(RegisterUseDef(uses=(R12, R13)))
    context.emit(Global(name))
    context.emit(Bl(LR, name, clobbers=clobbers))
    context.emit(RegisterUseDef(uses=(R10,)))
    context.move(d, R10)
    return d


def call_internal1(context, name, a, clobbers=()):
    d = context.new_reg(RiscvRegister)
    context.move(R12, a)
    context.emit(RegisterUseDef(uses=(R12,)))
    context.emit(Global(name))
    context.emit(Bl(LR, name, clobbers=clobbers))
    context.emit(RegisterUseDef(uses=(R10,)))
    context.move(d, R10)
    return d


@isa.pattern("reg", "ADDF64(reg, reg)", size=20)
@isa.pattern("reg", "ADDF32(reg, reg)", size=20)
def pattern_add_f32(context, tree, c0, c1):
    return call_internal2(
        context, "float32_add", c0, c1, clobbers=context.arch.caller_save
    )


@isa.pattern("reg", "SUBF64(reg, reg)", size=20)
@isa.pattern("reg", "SUBF32(reg, reg)", size=20)
def pattern_sub_f32(context, tree, c0, c1):
    return call_internal2(
        context, "float32_sub", c0, c1, clobbers=context.arch.caller_save
    )


@isa.pattern("reg", "MULF64(reg, reg)", size=20)
@isa.pattern("reg", "MULF32(reg, reg)", size=20)
def pattern_mul_f32(context, tree, c0, c1):
    return call_internal2(
        context, "float32_mul", c0, c1, clobbers=context.arch.caller_save
    )


@isa.pattern("reg", "DIVF64(reg, reg)", size=20)
@isa.pattern("reg", "DIVF32(reg, reg)", size=20)
def pattern_div_f32(context, tree, c0, c1):
    return call_internal2(
        context, "float32_div", c0, c1, clobbers=context.arch.caller_save
    )


@isa.pattern("reg", "NEGF64(reg)", size=20)
@isa.pattern("reg", "NEGF32(reg)", size=20)
def pattern_neg_f32(context, tree, c0):
    return call_internal1(
        context, "float32_neg", c0, clobbers=context.arch.caller_save
    )


@isa.pattern("reg", "F32TOI32(reg)", size=20)
@isa.pattern("reg", "F64TOI32(reg)", size=20)
def pattern_ftoi_f32(context, tree, c0):
    return call_internal1(
        context, "float32_to_int32", c0, clobbers=context.arch.caller_save
    )


@isa.pattern("reg", "I32TOF32(reg)", size=20)
@isa.pattern("reg", "I32TOF64(reg)", size=20)
def pattern_itof_f32(context, tree, c0):
    return call_internal1(
        context, "int32_to_float32", c0, clobbers=context.arch.caller_save
    )


@isa.pattern("stm", "CJMPF32(reg, reg)", size=20)
@isa.pattern("stm", "CJMPF64(reg, reg)", size=20)
def pattern_cjmpf(context, tree, c0, c1):
    op, yes_label, no_label = tree.value
    opnames = {
        "<": "float32_lt",
        ">": "float32_gt",
        "==": "float32_eq",
        "!=": "float32_ne",
        ">=": "float32_ge",
        "<=": "float32_le",
    }
    Bop = opnames[op]
    jmp_ins = B(no_label.name, jumps=[no_label])
    call_internal2(context, Bop, c0, c1, clobbers=context.arch.caller_save)
    context.emit(Bne(R10, R0, yes_label.name, jumps=[yes_label, jmp_ins]))
    context.emit(jmp_ins)


def round_up(s):
    return s + (16 - s % 16)
