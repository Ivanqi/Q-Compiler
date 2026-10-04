# linker.py 详解
## 一、这个文件的作用是什么？
这是 qcc 的链接器——把多个目标文件（ObjectFile）合并成一个可执行映像。

编译阶段每个源文件独立产出一个目标文件，其中跨文件的引用无法解析（函数 foo 在别的文件里、地址未知）；

链接器把大家拼到一起后，所有符号地址都确定了，再把每条指令/数据里预留的"空位"填上真实值。docstring（1 行）言简意赅："Linker utility"；

20–86 行的 link() docstring 里的 doctest 展示了典型用法：
```c
>>> obj1 = asm(asm_source, 'arm')          # 汇编出一个目标文件
>>> obj2 = c3c([c3_source], [], 'arm')     # C3 编译出另一个
>>> obj = link([obj1, obj2])               # ← 本文件：合并

```

link() 的内部流程（Linker.link 100–169 行）：
```python
merge_objects（合并各文件的节、符号、重定位条目——偏移/编号平移）
  → [可选] add_missing_symbols_from_libraries（从库里拉代码补符号）
  → layout_sections（按内存布局脚本把节放进映像、定地址）
  → check_undefined_symbols（还有未解析符号 → 报错）
  → do_relaxations（链接期松弛：把可缩短的跳转换短形式）
  → do_relocations（★ 核心：把符号地址按重定位类型 patch 进节数据）

```

## 二、每个方法的作用
### 模块级入口 link(...)（20–86 行）
62 行：get_object 把输入（文件名/文件对象/ObjectFile）统一成目标文件；

66–67 行：layout 字符串/文件 → get_layout 解析成 Layout 对象（链接脚本）；

69–72 行：取架构（以第一个对象为准）；use_runtime=True 时把 march.runtime（编译器运行时库，如软浮点 float32_add——还记得 RISC-V 模式里的 call_internal2 吗？它引用的符号就靠这里补上）；

74 行：libraries → get_archive 统一成档案对象；

76–85 行：创建 Linker 并调 linker.link(...) 返回合并后的目标文件。

### Linker.link（100–169 行）—— 主流程
117–118 行：架构一致性检查（不同架构的对象不能链一起）；

120–123 行：新建输出 ObjectFile（debug 时附 DebugInfo）；

125–136 行：入口符号——优先用命令行 entry，否则用布局脚本里的；inject_symbol 登记"程序从哪里开始执行"；

138–142 行：extra_symbols（如裸机程序的 _start 地址）注入符号表；

144–145 行：merge_objects——合并全部输入；

147–164 行：partial_link（部分链接）时只合并、不做布局与重定位（供增量链接用）；否则依次：库补符号 → 布局 → 未定义检查 → 松弛 → 重定位；

166–169 行：报告 + 返回 dst。

#### 合并阶段（193–307 行）
merge_objects（193–197 行）：对每个输入对象调 inject_object；

inject_object（199–279 行）：把单个对象"贴"进输出：
	- 203–227 行：节合并——同名节并入输出节（get_section(create=True)），按输入节的对齐要求填零对齐（215–217 行），记录节内偏移；
	
	- 229–252 行：符号重映射——已定义符号的值 = 节偏移 + 原值（233 行，因为节在输出里挪了位置）；global 走 merge_global_symbol（281–299 行：未定义符号被后来的定义"点亮"——先见到 extern foo 再见到 foo:，前者的 undefined 状态被补成定义；两个都定义 → 报错 "Multiple defined symbol"，293 行），local 直接 inject_symbol；
	
	- 254–264 行：重定位条目重映射——offset 加上节偏移、symbol_id 换成新符号表编号（重定位跟着节和符号一起平移）；
	
	- 266–274 行：入口符号合并（两个对象都有入口 → 报错 "Multiple entry points defined"）；
	
	- 276–279 行：debug 信息复制（SymbolIdAdjustingReplicator 同步调整符号编号）。

#### 布局 layout_sections（309–375 行）
按链接脚本（Layout）把节安排进内存映像：遍历每条内存的输入条目——
- Section：取节、对齐当前地址、定 section.address、加入 Image（316–331 行）；
- SectionData：节数据按当前地址重排（332–348 行）；
- SymbolDefinition：在当前位置定义符号（349–363 行）；
- Align：地址对齐（364–366 行）；
- 371–374 行：映像超过内存容量 → CompilerError。

#### 符号解析（377–424 行）
get_symbol_value（377–381 行）：查符号值（重定位用）；

add_missing_symbols_from_libraries（383–410 行）：档案库解析的经典算法——反复扫描各库，只要某成员对象定义了当前未定义符号之一，就 

inject_object 拉进来（可能带来新的未定义符号，所以 reloop 直到不动点——注释自嘲"兔子洞"）；

check_undefined_symbols（416–424 行）：最终仍有未定义符号 → 列出全部并 CompilerError("Undefined references: ...")。

#### 松弛 do_relaxations（426–537 行）
##### 链接期松弛（linker relaxation）——docstring（427–448 行）解释：
编译器总是生成保守的远跳转，链接后地址已定，可把"32 位跳转"换成"8 位短跳转"：
- 457–492 行：扫描所有重定位，用 isa.relocation_map 实例化架构重定位类，can_shrink 判断能否缩短，能则 do_shrink 打补丁、记录"字节洞"（hole）；

- 501–525 行：删旧重定位、注入补丁产生的新重定位、登记洞；

- 537 行：_apply_relaxation_holes（539–613 行）——把洞从整个世界扣掉：符号值（557–570 行）、重定位偏移（573–583 行）、节数据（586–592 行，倒序删避免偏移错位）、映像内节地址（600–613 行）。

（对 RISC-V：当前 relocations.py 的 7 个重定位类只实现了 apply、没有 can_shrink，所以这一阶段在 RISC-V 上是"扫描后空转"，机制留给 ARM 等架构。）

#### 重定位 do_relocations（615–647 行）—— ★ 核心
##### _do_relocation（623–647 行）：对每条重定位条目：
- sym_value = get_symbol_value(...)——目标符号的最终地址；

- reloc_value = section.address + relocation.offset——被 patch 位置自己的最终地址；

- rcls = arch.isa.relocation_map[relocation.reloc_type]（637 行）——按类型从架构的 isa 里查出重定位类；

- reloc.apply(sym_value, data, reloc_value)（645 行）——把 (符号地址 − 自身地址) 按该类型的位格式"锤"进节数据的指定字节（docstring 626–627 行的"hammering bits"）。

## 三、是通过 RISC-V 指令集生成、再调用 linker 的吗？（回答你的问题）
是的，而且两者的接口非常精确——分两个方向：

### ① 编译端（instructions.py → 目标文件里的"欠条"）：
RISC-V 指令类里凡是带 relocations() 的指令（前几讲见过），encode() 时把目标位先填 0 占位，同时把一条 RelocationEntry 写进 ObjectFile。

例如 instructions.py：
| 指令/伪指令 | 挂的重定位 | 含义 |
| :--- | :--- | :--- |
| B（j label） 351 行 | BImm20Relocation | 20 位 pc 相对跳转 |
| BranchBase（beq 等） 541 行 | BImm12Relocation | 12 位 pc 相对分支 |
| Adru（lui rd, label） 397 行 | Abs32Imm20Relocation | 绝对地址高 20 位 |
| Adrl（addi rd, rs1, label） 434 行 | Abs32Imm12Relocation | 绝对地址低 12 位 |
| Adrurel / Adrlrel / Loadlrel | RelImm20/RelImm12Relocation | %pcrel_hi/%pcrel_lo 位置无关寻址 |
| Dcd2（dcd = label） 95 行 | AbsAddr32Relocation | 数据区 32 位符号引用 |

### ② 链接端（linker.py → 兑现"欠条"）：
instructions.py 38–46 行的 isa.register_relocation(...) 把这些类注册进 isa.relocation_map；链接器的 _do_relocation（637 行）与 do_relaxations（462 行）正是用 arch.isa.relocation_map[relocation.reloc_type] 查出对应类，再调 apply 完成补丁。

所以完整链条是：
```c
riscv/instructions.py（@isa.pattern → emit Bl/Beq/... → encode 留 0 + RelocationEntry 欠条）
  → ObjectFile
api.link([obj1, obj2], 'riscv') → linker.py
  → merge_objects（合并节/符号/欠条）
  → layout_sections（定最终地址）
  → do_relocations（按 relocation_map 查到 BImm20Relocation 等 → apply 把地址锤进指令）
  → 可执行映像

```

编译时不知道地址，链接时才知道——指令集文件负责"留空位 + 说明空位类型"，链接器负责"填值"。

## 四、详细例子
### 场景：两个 RISC-V 源文件互相引用
```c
/* file1.c */
extern int foo(void);
int main(void) { return foo(); }

/* file2.c */
int foo(void) { return 42; }

```

### 第 1 步：分别编译（cc(..., 'riscv')）
file2 的目标文件：code 节里是 foo 的函数体（假设 8 字节），符号表有 foo（global，定义于 offset 0）；


file1 的目标文件：main 里对 foo 的调用经 RISC-V 模式发射 Bl(LR, "foo")——encode() 只填 opcode 和 rd，目标位全部留 0，relocations() 产出一条 RelocationEntry(BImm20Relocation, symbol_id=foo, section="code", offset=4, addend=0)；符号表里 foo 是 undefined，main 定义于 offset 0。

### 第 2 步：link([obj1, obj2], 'riscv')
merge_objects：
- 节合并：obj1 的 code（8 字节）→ 输出 offset 0–8；obj2 的 code（8 字节）对齐后 → offset 8–16；
- 符号：先注入 obj1——foo 是 undefined（value=None）；main 定义于 0。再注入 obj2——merge_global_symbol("foo", ...)：已存在且 undefined → 补成定义，value = 8（obj2 内 offset 0 + 节偏移 8）；
- 欠条平移：那条 BImm20Relocation 的 offset 从 obj1 内 4 → 输出 4，symbol_id 换成新符号表编号。

layout_sections（默认布局）：code 节地址假设 0x1000（obj2 的 foo 即 0x1008）。

check_undefined_symbols：foo 已被"点亮" → 通过。

do_relocations：处理那条欠条——sym_value = 0x1008（foo）、reloc_value = 0x1000 + 4 = 0x1004；查出 BImm20Relocation 类 → apply(0x1008, data, 0x1004)：算偏移 0x1008 − 0x1004 = 4，按 RISC-V jal 的位格式（imm[20|10:1|11|19:12]）拆进指令字节——jal ra, foo 的机器码被锤进 4 字节偏移。

返回合并后的 ObjectFile——此时 main 里的调用真正指向 foo，可直接 objcopy(..., 'hex') 烧录。

#### 补充：la（伪指令）的双重定位
若 file1 还写 extern int g; int main(void) { return g; }，指令选择用 La 伪指令（instructions.py:499）→ render 成两条欠条：
```c
auipc t0, %pcrel_hi(g)   → RelImm20Relocation：填 g 与 auipc 之间偏移的高 20 位
addi  t0, t0, %pcrel_lo(g) → RelImm12Relocation：填低 12 位

```
链接时两次 apply 把同一符号地址的高低位分别锤进两条指令——位置无关代码（PIC）在 qcc 里就是这么链接出来的。

#### 补充：软浮点与运行时库
RISC-V 无 FPU 时 pattern_add_f32 发 call_internal2("float32_add", ...)（instructions.py:1417）——目标文件里出现未定义符号 float32_add；用户调 link(objects, use_runtime=True) 时 link() 72 行把 march.runtime 追加入输入，该符号由运行时库定义，链接成功。

## 五、一句话总结
linker.py 是 qcc 的链接器：

link() 是统一入口；Linker.link 按"合并节与符号（偏移/编号平移、未定义符号被定义点亮、重复定义报错）→ 库补符号 → 布局定地址 → 未定义检查 → 松弛（缩短跳转并扣除字节洞）→ 重定位（arch.isa.relocation_map 查类型、apply 把符号地址锤进指令/数据位）"的标准流程把多个目标文件拼成可执行映像。

它与你提到的 RISC-V instructions.py 是"欠条与兑现"的关系：

指令集文件在 isa.register_relocation 里注册重定位类型、在带 relocations() 的指令编码时留下 0 占位与 RelocationEntry 欠条；

链接器经 arch.isa.relocation_map 找到对应类并把最终地址 patch 进去——编译定指令形状，链接定地址归宿。