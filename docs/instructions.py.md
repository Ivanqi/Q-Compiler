# instructions.py（RISC-V）详解
## 一、这个文件的作用是什么？
这是 RISC-V 架构的"指令字典"——docstring（1 行）："Riscv 指令的定义"。

它完成两件大事：
- 定义 RISC-V 机器指令：每条指令一个类，包含操作数（Operand）、汇编语法（Syntax）、二进制编码（patterns/encode）、重定位（relocations），以及伪指令（render 展开成多条真指令）；

- 定义指令选择模式：683 行起的 @isa.pattern(...) 装饰器把 irdag 的树模式（上一系列 instructionselector.py 建规则库时消费的 arch.isa.patterns）映射到 Python 函数——"选择 DAG 的树长什么样 → 发射哪些 RISC-V 指令"。

它是前几讲后端链条的最终落地：InstructionSelector1.__init__ 遍历 arch.isa.patterns → BurgSystem.add_rule → TreeSelector 动态规划匹配 → 模板函数 context.emit(...) 把 RISC-V 指令填进 frame。

它还展示了 qcc 架构描述的两个惯用技巧：类工厂函数（一条函数批量造出同构指令类）和伪指令 + render（先发语义清晰的抽象指令，发射时再展开成真实机器码）。

## 二、每个部分/方法的作用
### 头部与 ISA 容器（38–56 行）
isa = Isa()（38 行）：RISC-V 的指令集容器（isa.py），随后注册 7 种重定位类型（40–46 行：跳转的 BImm12/BImm20、绝对地址、pcrel 高低位等）——链接器处理 label 操作数时按这些类型计算补丁；

RiscvInstruction（49–51 行）：所有真指令的基类，tokens = [RiscvToken]（32 位定长指令字）；

PseudoRiscvInstruction（54–56 行）：伪指令基类（继承 ArtificialInstruction）——伪指令不直接编码，靠 render() 展开成真指令（发射流水线 codegen.py:265 对 ArtificialInstruction 调 render）。

### 伪指令与数据（59–96 行）
Align（59–65 行）：.align imm → render 成 Alignment 通用指令；

Section（68–74 行）：.section sec → SectionInstruction；

dcd(v)（77–83 行）：小工具——int 直接 Dd（原始数据），str 走 Dcd2；

Dcd2（86–96 行）：dcd = label（发射一个 32 位标签引用），encode 填 0 占位、relocations 挂 AbsAddr32Relocation——编码时留空、链接时填真值是重定位指令的标准套路。

### 指令工厂函数（这是"方法"的主体）
RISC-V 指令高度规整（同构的字段布局），作者用工厂函数 + type() 动态建类避免重复（元编程）：

#### make_csrwi(mnemonic, func)（121–134 行）：
造 csrwi/csrsi/csrci 三个类（137–139 行）——CSR 立即数写指令，imm 字段实际装 CSR 编号（patterns["imm"] = rd）；

#### make_regregreg(mnemonic, opcode, func)（164–189 行）：

三寄存器 R 型指令工厂（opcode 0b0110011）——一个函数造出 Addr/Subr/Sll/Slt/Sltu/Xorr/Srl/Sra/Orr/Andr 全部 10 条（192–201 行），每类只差 funct3/funct7；

#### make_si(mnemonic, code, func)（204–227 行）：

移位立即数型（Slli/Srli/Srai，230–232 行）——注意 Srai 的 funct7=0b0100000（算术右移），与 Srli 只差最高位；

#### IBase + make_i(mnemonic, func)（235–262 行）：

I 型立即数运算（Addi/Slti/Sltiu/Xori/Ori/Andi，265–270 行）。IBase.encode（236–244 行）是手写位字段编码的范例：tokens[0][0:7] = opcode、[7:12] = rd、[12:15] = func、[15:20] = rs1、[20:32] = offset & 0xFFF；

#### make_sm(mnemonic, code)（299–303 行）：

CSR 读（rdcycle/rdtime/rdinstret 及 hi 变体，306–311 行）；

#### make_branch(mnemonic, cond, invert)（545–559 行）：

条件分支工厂——RISC-V 原生只有 beq/bne/blt/bge/bltu/bgeu 六种，Bgt/Ble/Bgtu/Bleu 靠 invert=True 实现：把 rs1/rs2 交换（BranchBase.encode 533–538 行：invert 时 rs1 装 rm、rs2 装 rn）——bgt a, b ≡ blt b, a，零成本伪指令；

#### make_str(mnemonic, func)（595–609 行）：

S 型存储（Sb/Sh/Sw，612–614 行），StrBase.encode（581–592 行）演示了 S 型立即数拆位：imm[4:0] 装进 [7:12]、imm[11:5] 装进 [25:32]；

#### make_ldr(mnemonic, func)（617–640 行）：

I 型加载（Lb/Lh/Lw/Lbu/Lhu，643–647 行）；

#### make_mext(mnemonic, func)（662–674 行）：

M 扩展（Mul/Div/Divu/Rem/Remu，677–681 行，funct7=0b0000001）。

### 跳转与地址指令（326–523 行）

Bl（326–338 行）：jal rd, target（调用）——encode 只填 opcode 和 rd，目标留给 BImm20Relocation（20 位 pc 相对跳转偏移）；

B（341–352 行）：j target（无条件跳转）——jal x0, target；

Blr（355–368 行）：jalr rd, rs1, offset（间接调用/返回）；

Lui/Auipc（371–382、476–486 行）：加载高位立即数/pc 相对高位；

Adru/Adrurel/Adrl/Loadlrel/Adrlrel（385–473 行）：地址装载四件套——lui+addi（绝对）与 auipc %pcrel_hi+addi %pcrel_lo（位置无关）各一对，分别挂绝对/相对重定位；

伪指令三兄弟（489–523 行）：
- Labelrel（489–496 行）：lw rd, label → render 成 Adrurel + Loadlrel（pcrel 高位 + 带 %pcrel_lo 的加载）；

- La（499–506 行）：la rd, label → Adrurel + Adrlrel（装载标签地址）；

- Li（509–523 行）：li rd, imm——按立即数大小自适应展开：12 位放得下 → 一条 addi rd, x0, imm；否则 lui 高 20 位 + addi 低 12 位；519–520 行的补偿是经典细节：addi 的 12 位立即数是符号扩展的，若 bit11 为 1 会"减掉 0x1000"，所以先把 imm 加 0x1000 让 lui 的高位补偿回来。

### 指令选择模式（683–1493 行）—— 本文件的"另一半"
每条 @isa.pattern(目标, 树模式, size=代价, condition=条件) 都是 burg 规则（上一系列 burg.py 的记法）：

| 模式组 | 例子 | 发射什么 |
| :--- | :--- | :--- |
| MOV (686–701 行) | MOVI32(reg) | context.move → mv |
| JMP/CJMP (704–707、839–867 行) | CJMPI32(reg, reg) | 查 opnames 映射（< → Blt ...）发 Bop(c0, c1, yes, jumps=[yes, jmpl]) + B(no) ——真分支 + 一条无条件跳转补 no 路径；无符号版用 Bltu/Bgtu/Bgeu/Bleu |
| REG (721–730 行) | REGI32 size=0 | 直接用节点里的 vreg，零代价 |
| 转换 (733–799 行) | I8TOI32(reg) | 符号扩展：slli 24 + srai 24；零扩展：slli + srli；同宽/收窄直接返回（无操作）<br>struct.pack/unpack 位平移 |
| CONST (802–836 行) | CONSTI32 + condition | 小常量（-2048..2048，addi 范围）size=2 优先；大常量 size=4 兜底；都发射 Li 伪指令；浮点常量用 |
| 算术 (870–941、1112–1391 行) | ADDI32(reg, reg) → Addr；ADDI32(reg, CONSTI32)（condition < 2048）→ Addi | 同一条 IR 加法，按操作数是寄存器还是小常量选出不同代价/不同指令——BURG 动态规划的直接体现；NEG → sub rd, x0, c0；INV → xori -1；AND/OR/XOR/SHL/SHR 都有 reg-reg 与 reg-const 两个版本 |
| 寻址融合 (1317–1323) | LDRI32(ADDI32(reg, CONSTI32)) | 复杂树模式：load(add(p, c)) 整棵树一条 lw rd, c(rs1) 吃掉——把地址加法融进加载指令，这就是树匹配相对逐条翻译的优势 |
| 内存 (962–1149 行) | STRU32(mem, reg) / LDRI32(mem) | 先由 mem 模式（978–991 行：FFRELU32 → (FP, offset)、reg → (reg, 0)）算出基址+偏移，再发 Sw/Lw/Sh/Sb（fprel=True 标记栈指针相对寻址） |
| 浮点 (1417–1489 行) | ADDF32(reg, reg) size=20 | RISC-V 无 FPU 时的软浮点：调运行时函数 float32_add（call_internal2） |

call_internal1/2（1394–1414 行）：按 RISC-V 调用约定组织运行时调用——实参 move 进 R12/R13、RegisterUseDef 声明寄存器使用（寄存器分配器据此绕开）、Global(name) + Bl(LR, name) 调用、返回值从 R10 取回。pattern_cjmpf（1473–1489 行）调 float32_lt 等比较函数后发 Bne(R10, R0, yes)。

reg_list_to_mask（574–578 行）、round_up（1492–1493 行）：小工具（寄存器位掩码、16 对齐）。

## 三、与前几讲怎么衔接（一句话）
```python
irdag.py 拆出的树 ──▶ InstructionSelector1（BurgSystem 规则库）
                        ├─ 规则来源 = 本文件 @isa.pattern 装饰器注册进 isa.patterns
                        ├─ TreeSelector 动态规划按 size 代价选最便宜覆盖
                        └─ 模板函数 context.emit(RiscvInstruction) ──▶ frame
codegen.py ──▶ 寄存器分配 ──▶ emit_frame_to_stream ──▶ encode()/render() ──▶ 机器码

```

## 四、详细例子
### 例 1：(a+b)*(a+b) 在 RISC-V 上的完整落地
沿用贯穿全系列的 C 代码，优化后 IR 为 t3 = add a, b; t5 = mul t3, t3; return t5。


#### 第 1 步：BURG 匹配（模式来自本文件）
- 树 MULI32(ADDI32(REGI32, REGI32), ADDI32(REGI32, REGI32)) 或拆成多棵小树；ADDI32(reg, reg) 命中 870–875 行的 pattern_add_i32（size=2）：Addr(d, c0, c1) → 发射 add，返回新 vreg d；

- MULI32(reg, reg) 命中 1306–1314 行 pattern_mul_i32（size=10）：发射 Mul，返回结果 vreg；

- MOVI32(reg) 命中 686–694 行：mv 把结果搬进 rv_vreg；JMP 命中 704–707 行发射 B(epilog)。

#### 第 2 步：寄存器分配：
a/b → a0/a1（RV32 调用约定，由 arch.gen_function_enter 搬运），中间 vreg → t0/t1，返回值 → a0。

#### 第 3 步：encode
Addr 的 patterns 填字段——opcode=0b0110011, funct3=0, funct7=0, rd=t0, rs1=a0, rs2=a1 → 32 位字 0x00A502B3；Mul 的 funct7=0b0000001 使其编码区别于 add。

#### 最终汇编（大致）：
```c
f:
    add  t0, a0, a1        ; t3 = a + b
    mul  t1, t0, t0        ; t5 = t3 * t3
    mv   a0, t1            ; 返回值约定寄存器
    ret                    ; jalr x0, 0(ra)

```

### 例 2：Li 伪指令的自适应展开（509–523 行）
li t0, 42：inrange(42, 12) 真 → 一条 addi t0, x0, 42；

li t0, 0x12345：放不下 → lui t0, 0x12（imm>>12）+ addi t0, t0, 0x345；

li t0, 0x800（bit11=1）：若不补偿，addi 的符号扩展会把结果减 0x1000；519–520 行先把 imm 加 0x1000 再拆 → lui t0, 0x1 + addi t0, t0, -0x800，结果正确。

### 例 3：条件分支的两段式发射（842–848 行）
IR 的 CJMPI32（比较 + 双目标跳转）在 RISC-V 上展开成：
```c
blt  a0, a1, yes_label     ; Bop 直接跳 yes
j    no_label              ; 追加的 jmp_ins 走 no

```
注意 Bop(... jumps=[yes_label, jmp_ins]) 把后续跳转目标登记进指令——CFG 信息随指令流动，供寄存器分配和 delete_unreachable 等使用。

### 例 4：复杂树模式——寻址融合（1317–1323 行）
LDRI32(ADDI32(reg, CONSTI32)) 匹配 x = p[4] 生成的"地址加常量再加载"整棵树，一条指令吃掉：
```c
lw  t0, 4(a0)        ; Lw(d, c1, c0)，c1 取自树里的 CONST 值

```
若没有这条复杂模式，BURG 只能先 addi t0, a0, 4 再 lw t0, 0(t0)——多一条指令。

这正是"树模式覆盖越丰富，选择结果越好"的实例，也是本文件模式写得如此详尽的原因。

## 五、一句话总结
instructions.py 是 RISC-V 后端的两张核心表：上半张是机器指令字典——用 Operand/Syntax/patterns/encode 描述每条指令的汇编形态与位编码

用工厂函数（make_regregreg/make_i/make_branch/make_ldr…）批量生成同构指令类

用伪指令 render（Li/La/Bgt 等）在发射期自适应展开，用 relocations 挂链接期补丁；

下半张是指令选择模式表——@isa.pattern 把 irdag 的树模式（含 condition 门槛、size 代价、复杂树模式）映射到发射函数，喂给 InstructionSelector1 的 BURG 动态规划匹配器。

两者合起来，把"选择 DAG 的树"翻译成"32 位 RISC-V 机器码"。