# codegenerator.py 详解
## 一、这个文件的作用是什么？
这是 C 前端流水线的最后一站：IR 代码生成器。

前面三讲的链条是：
```python
builder.py 组装 → parser.py 检查语法 → semantics.py 赋语义（带类型的 AST）
                                        ↓
                        codegenerator.py：遍历 AST → 生成 qcc 的 IR 模块
                                        ↓
                  api.optimize()：用 qcc/opt/ 的 pass 优化 IR → 后端

```
docstring（1–8 行）说得很清楚："Walks over the AST of the C sourcecode and generates IR-code"——它遍历 semantics 产出的 AST，把每个节点翻译成 qcc 的 IR 指令（ir.Load、ir.Store、ir.Binop、ir.CJump……）。

注意分界：绝大多数错误（类型错误、未定义符号等）在语义阶段就已经拦下了，codegenerator 假设进来的 AST 是合法的、带类型的，它只负责"翻译"。

它生成的是类 LLVM 的三地址 SSA-ish IR，但刻意做得"笨"：每个局部变量都先 alloca 一块栈内存、赋值都走 store、读取都走 load。

内存访问的消除（即 SSA 化）不是它的职责，而是交给 qcc/opt/mem2reg.py 之后再做——这是理解它与优化器分工的关键。

## 二、每个方法的作用
### 2.1 初始化（28–60 行）
__init__(self, context) 接收 semantics 同款的 CContext（sizeof/对齐/eval_expr 的来源），并准备：
- 32–38 行：工作状态——break_block_stack/continue_block_stack（生成循环/switch 时记录 break/continue 跳向哪个块）、labeled_blocks（goto 标签 → IR 块）、ir_var_map（AST 声明对象 → IR 值的映射表，整个文件最重要的字典）；

- 39–58 行 ir_type_map：C 类型 → IR 类型的映射表——char→i8、int→i32/i16/i64（按 arch_info 的 get_size("int") 动态选择）、float→f32、double→f64、va_list→指针等；这就是语义类型落成机器类型的地方；

- 60 行：LinkTimeExpressionEvaluator（文件末尾，见 2.9），用于求值全局变量初始化器里的常量表达式。

### 2.2 总入口 gen_code（71–109 行）
#### IR 生成的唯一入口，流程：
- 73 行 新建 irutils.Builder()（指令发射器，提供 emit/emit_load/emit_binop/new_block 等），77 行 新建 ir.Module("main")；

- 80–96 行 把 compile_unit.declarations 分类：Typedef/枚举常量 → 跳过（不需要生成代码）；FunctionDeclaration → functions 列表；VariableDeclaration → variables 列表；

- 99–100 行 先生成所有全局变量；

- 102–106 行 分两遍处理函数：先 create_function 全部建签名（C 允许互相调用而不必先声明），再 gen_function 逐个填函数体；

- 109 行 返回 ir.Module。

### 2.3 发射辅助（111–143 行）
- emit（111–113 行）：向当前块追加一条指令；

- emit_alloca（115–124 行）：为局部变量预留栈空间——ir.Alloc + ir.AddressOf 两条指令（AddressOf 是"取这个 alloc 的地址"，后续 store/
load 都作用在地址上——mem2reg 就是识别这种模式）；

- emit_global_variable（126–131 行）：创建全局 ir.Variable 并加进 module；

- emit_const（133–136 行）：按 C 类型取 IR 类型、发射常量。

### 2.4 全局变量（145–330 行）
- gen_global_variable（145–168 行）：extern 且无定义 → ir.ExternalVariable；否则求初始值内存映像、static 用 LOCAL binding、建全局变量；

- gen_global_ival（170–189 行）：按类型分派：数组/结构体/联合体走专门的填充函数，标量走 gen_global_initialize_expression——用 LinkTimeExpressionEvaluator.eval_expr 把 &g + 4 这类链接期常量求成 (ir.ptr, 符号名) 元组；

- gen_global_initialize_array/struct/union（233–318 行）：把初始化值变成字节映像——数组按元素大小填字节、不足补零（implicit_value）；结构体按字段偏移插入 padding、处理位域（bit 拼接 bits_to_bytes）；联合体只初始化首字段其余补零；

- gen_global_string_constant（202–213 行）：字符串字面量 → 名为 __txt_const_N 的全局字节数组；

- gen_global_compound_literal（215–231 行）：复合字面量 → __compound_N 全局变量；
mem_len（320–330 行）：算字节映像长度。


#### 2.5 函数生成（332–483 行）
- gen_function / create_function（332–342 行）：有函数体才生成定义，否则是外部引用；

- create_function_internal（344–365 行）：按返回类型建 IR 函数——void → new_procedure；返回 struct → 也建 procedure 并插入隐式第一个参数 return_value_address（复杂类型通过指针返回，调用方负责传地址，见 774 行）；其他 → new_function(name, binding, return_type)；

- create_function_external（367–386 行）：外部函数引用，变参函数附加一个 ptr 形参；

- gen_function_def（388–483 行）：函数体的完整生成：
	- 402–408 行：建两个块——entry（放 alloca）和第一个真实代码块；
	
	- 413–428 行：每个参数 alloca 一块空间并把实参 store 进去（这就是"参数也是内存变量"的设计，给 mem2reg 用）；
	
	- 431–435 行：变参函数附加 varargz 指针参数；
	
	- 453 行：gen_compound_statement 递归生成函数体；
	
	- 455–469 行：块没被关闭时补尾——void 函数补 Exit，非 void 补 warning + 返回 0；
	
	- 472–475 行：把收集在 _allocs 里的 alloca 全部发射到 entry 块，再跳转到第一个代码块（保证每个函数的 alloca 都在函数入口，这是 mem2reg 和汇编后端的约定）；
	
	- 478 行 delete_unreachable() 清掉不可达块。

### 2.6 语句生成（485–805 行）
- gen_stmt（485–511 行）：语句分发表——fn_map 把 AST 节点类映射到生成函数（If→gen_if、While→gen_while、Return→gen_return……），同时用 builder.use_location 关联源码位置（调试信息）。

- gen_empty_statement（513–515 行）：什么也不做。

- gen_compound_statement（517–520 行）：顺序生成每条子语句。

- gen_declaration_statement（522–535 行）：局部变量 → gen_local_variable；static 局部 → gen_local_static_variable（537–548 行：改名为 name_N 存为全局 LOCAL 变量）。

- gen_expression_statement（550–553 行）：按 rvalue 生成表达式（结果丢弃）。

- gen_if（555–571 行）：经典结构——yes_block/no_block/final_block 三个块，gen_condition 条件跳转，两个分支最后都跳向 final。

- gen_switch（573–621 行）：先收集 case → 目标块映射，再在 test 块里发射一串 CJump 比较链（TODO 注释：尚未实现跳转表）；break_block_stack 压入 final 块。
- gen_while / gen_do_while / gen_for（623–692 行）：构造 condition/body/iterator/final 块环。注意 continue 的目标因循环种类而异：while 跳到 condition 块、for 跳到 iterator 块——这就是 continue_block_stack 的作用。

- gen_label（694–699 行）：标签名 → 块（get_label_block 按需创建），fall-through + 生成子语句。

- gen_case / gen_range_case / gen_default（701–736 行）：case 常量（用 context.eval_expr 求值）→ 记录到 switch_options 映射；gen_range_case 展开区间逐个登记（case 1 ... 3 每个值都映射到同一块）。

- gen_goto / gen_continue / gen_break（738–765 行）：跳向标签块 / continue_block_stack 栈顶 / break_block_stack 栈顶；栈空时报"Cannot break here!"（其实语义层已保证不会出现，这里是防御）。

- gen_return（767–786 行）：返回 struct → store 到 return_value_address 参数再 Exit；普通值 → emit_return；return; → Exit。

- gen_inline_assembly（788–802 行）：生成 ir.InlineAsm，把输入/输出操作数求值后挂上。

- gen_condition（804–839 行）：短路求值——||/&& 展开成两段条件跳转（a && b：a 假跳 no，a 真再测 b）；比较运算符直接发 CJump；其他表达式走 check_non_zero（841–845 行：与 0 比较跳转）。

### 2.7 局部初始化与布尔化（847–976 行）
- gen_local_variable（847–853 行）：alloca + 有初值则 gen_local_init。

- gen_local_init（855–870 行）：按类型分派——标量直接 store；数组/结构体/联合体递归填（gen_local_init_array 892 行、gen_local_init_struct 905 行——后者处理字段偏移、padding、位域的"位操作黑魔法"、以及"整个结构体赋值来自函数返回值"的特例 949–952 行）。

- gen_condition_to_integer（955–976 行）：把布尔条件变成 int（!x、a < b 用作数值时）——yes/no 两块分别存 1/0，汇合处用 ir.Phi 合并。

### 2.8 表达式生成（978–1617 行）
- gen_expr（978–1030 行）：表达式总入口。按节点类型分派到各个 gen_*（UnaryOperator→gen_unop、BinaryOperator→gen_binop、VariableAccess→gen_variable_access……）；随后 1020–1029 行是本文件最重要的约定：检查节点的 typ/lvalue 标注——rvalue=True 且表达式是 lvalue 时补发 load（_load_value，1032–1058 行）；rvalue=False 则断言是 lvalue（拿地址）。_load_value 里数组类型不 load（直接就是首元素指针），Blob 类型（结构体）特殊处理。

- gen_char_literal / gen_string_literal / gen_numeric_literal（1152–1165 行）：字面量 → Const / LiteralData+AddressOf。

- gen_unop（1167–1189 行）：x++/--x → gen_inplace_mutation（1191–1209 行：load、±1（指针按元素大小步进）、store，前后缀取旧/新值）；* 解引用（rvalue 加载）；& 取地址（lvalue 不加载）；-/~ 发 Unop；! → gen_condition_to_integer。

- gen_binop（1211–1317 行）：按运算符分类生成
	- 算术/位运算 → emit_binop；
	- , → 求值左边丢弃、返回右边；
	- +/- 的指针算术（1225–1268 行）：指针 ± 整数要把整数乘以元素大小再转 ptr 相加；指针 − 指针得差值后除以元素大小；注释 1230–1231 行明确说明"左右顺序已在 semantics 里交换好"（呼应 semantics.py:810-811）——这是两个文件协作的典型例子；
	- 比较/逻辑 → gen_condition_to_integer（布尔值 = int）；
	- 赋值类 → 结构体赋值走 CopyBlob，其他 load-算-store；+= 等复合赋值先 load 再算再 store。

- gen_ternop（1323–1358 行）：三元——条件跳转 + 两条路径各自求值 + Phi 汇合。

- gen_variable_access（1360–1381 行）：查 ir_var_map——把 AST 的声明对象映射回 IR 值（局部变量是 alloc 的地址、全局是 ir.Variable、枚举常量直接发射常量）。

- gen_call（1383–1406 行）：生成实参（prepare_arguments，1408–1443 行：返回 struct 时先 alloc 结果区并把地址当第一个实参；变参函数把剩余实参装进一块内存——gen_fill_varargs，1445–1490 行——再传指针），然后按返回类型发 FunctionCall 或 ProcedureCall。

- gen_compound_literal（1492–1498 行）：alloca + 局部初始化。

- gen_field_select（1500–1515 行）：基地址 + context.get_field_offsets 查出的字段偏移（位域拆成字节偏移 + 位偏移，返回 BitFieldAccess 对象）。
- gen_array_index（1517–1529 行）：base + index * 元素大小。

- gen_builtin 系列（1531–1583 行）：va_start（把 varargz 指针存进 va_list）、va_arg（从 va_list 指针读值并推进指针）、va_copy、offsetof（context.offsetof 直接算常量）。

- gen_cast（1585–1606 行）：处理 semantics 插入的 ImplicitCast/Cast——数组退化不用真发指令（数组 load 已是地址），否则 emit_cast。

- gen_sizeof（1608–1617 行）：context.sizeof 编译期算出，发射常量。

- get_ir_type / data_layout / sizeof（1619–1644 行）：C 类型 → IR 类型（指针/数组/函数 → ir.ptr，struct → BlobDataTyp）、尺寸/对齐查询。

- get_debug_type（1646–1704 行）：为调试信息构建 DWARF 类型描述。

### 2.9 文件末尾两个类（1707–1764 行）
- BitFieldAccess：位域访问的封装（地址 + 位偏移 + 位宽 + 符号性），配套 _load_bitfield/_store_bitfield（1067–1139 行，移位+掩码的位操作序列）。

- LinkTimeExpressionEvaluator：继承 eval.py 的 ConstantExpressionEvaluator，只多换三个求值函数——全局变量引用、字符串字面量、& 取地址不返回数值，而是返回 (ir.ptr, 符号名) 链接期符号引用。这是全局初始化器能写 char *p = "hello";、int *q = &arr[3]; 的原因。


## 三、它是怎么用 semantics 的 AST 生成 IR 的？
核心机制是两条约定：
- 节点自带类型与 lvalue 标注。semantics 的每个节点都带 typ（C 类型）和 lvalue（是否可寻址）。codegenerator 的 gen_expr 只按这两个属性决定"要不要 load"（codegenerator.py:1024），而不需要重新做任何类型分析。

- ir_var_map 把 AST 声明与 IR 值绑定。semantics 产出的 VariableAccess 节点里嵌着它查到的 VariableDeclaration 对象（semantics.py:1164 VariableAccess(symbol, typ, lvalue, location)）；codegenerator 在建变量/参数/函数时把"该声明对象 → IR 值"写进 ir_var_map（如 428 行、850 行），之后 gen_variable_access 直接查表。符号解析发生在语义层，代码生成层只做表查找。

再配合 gen_stmt/gen_expr 两个按节点类分派的 fn_map/isinstance 链，整棵 AST 的翻译就是"递归下降遍历 + 节点 → IR 指令"的直接映射。

## 四、怎么用 qcc/opt/ 的优化器优化 IR？
### 4.1 谁在调用优化器
codegenerator 不碰优化——它产出"笨 IR"后交回给 api.py 的流水线。以 C 编译器入口 cc() 为例（api.py:361-369）：
```python
ir_module = c_to_ir(source, march, coptions=coptions, reporter=reporter)  # 内部就是 CBuilder.build
optimize(ir_module, level=opt_level, reporter=reporter)    # ← qcc/opt/ 在这里登场
return ir_to_object([ir_module], march, debug=debug, reporter=reporter)

```

### 4.2 optimize() 干了什么（api.py:190-262）
level="0" 直接返回（默认 opt_level=0，不优化）；

否则把 8 个 pass 组成列表连跑 3 遍（* 3——多遍迭代让"一个 pass 暴露的机会被下一个 pass 利用"，比如 CleanPass 合并块后 mem2reg 又能消除更多 alloc）：

```python
opt_passes = [
    Mem2RegPromotor(),                  # mem2reg：内存提升为寄存器/Phi
    RemoveAddZeroPass(),                # x + 0 → x
    ConstantFolder(),                   # 常量折叠
    CommonSubexpressionEliminationPass(), # 公共子表达式消除 (CSE)
    TailCallOptimization(),             # 尾调用优化
    LoadAfterStorePass(),               # store 后紧跟 load 同一地址 → 直接用存的值
    DeleteUnusedInstructionsPass(),     # 死代码删除
    CleanPass(),                        # 清理 CFG：删空块、合并单前驱块
] * 3

```

level == "3" 再追加 CJumpPass()（合并 CJump 链）。

每个 pass 都是 FunctionPass（遍历 module 里每个函数）或 BlockPass（遍历每个基本块），接口统一是 run(ir_module)。verify_module 在前后校验 IR 合法性。

### 4.3 关键 pass 做了什么
- Mem2RegPromotor（mem2reg.py）：SSA 构建的核心。is_alloc_promotable 检查一个 alloc 是否"只被一个 AddressOf 引用、且其用途全是 load/store、非 volatile、地址不被当值存储"；通过后走经典算法——place_phi_nodes 用**支配边界（dominance frontier）**放置 Phi 节点，rename 把每个 load 替换成到达处的值。codegenerator 生成的全是 alloca+load/store（见 emit_alloca、参数 store），所以 mem2reg 一跑，绝大多数局部变量就变成寄存器/SSA 值了。

- ConstantFolder（constantfolding.py）：add(Const 1, Const 2) → Const 3、jump 到恒真恒假条件的块等编译期可算的运算。

- LoadAfterStorePass（load_after_store.py）：store x, p; y = load p → y = x。

- DeleteUnusedInstructionsPass：结果没人用的指令删除（常量折叠后留下的死常量等）。

- CleanPass（clean.py）：删除只含一条 Jump 的空块、把"单前驱块"合并进前驱——CFG 整形，与 mem2reg 互为补益。

## 五、详细例子
### 例 1：从 C 到优化后的 IR 全流程
```c
int add(int a, int b) { return a + b; }

```

#### 第 1 步：semantics 产出 AST（前几讲的流程）：
```python
CompilationUnit
  FunctionDeclaration: add
    FunctionType: int (int a, int b)
    Compound
      Return (BinaryOperator: a + b, typ=int, lvalue=False)

```

#### 第 2 步：CCodeGenerator.gen_code 生成"笨 IR"：
- create_function：返回 int → new_function("add", GLOBAL, i32)；

- gen_function_def：给参数 a、b 各 alloc + store；gen_return → gen_expr(Return.value, rvalue=True) → gen_binop 先 rvalue 生成 a、b（它们 lvalue=True → 发 load），再 emit_binop(add)，最后 emit_return。

大致产出：
```c
module main

function add(a: i32, b: i32) -> i32 {
  entry:
    a_addr = alloc 4
    b_addr = alloc 4
    jump block1
  block1:
    store a, a_addr
    store b, b_addr
    t1 = load a_addr
    t2 = load b_addr
    t3 = add t1, t2
    return t3
}

```

注意它有多"笨"：参数要落到内存、加法要先 load 两遍——这一切都是故意留给 opt/ 处理的。

#### 第 3 步：api.optimize(ir_module, level=2) 跑 3 遍 pass 列表：

第 1 遍 Mem2RegPromotor：a_addr/b_addr 的 alloc 只被 load/store 使用 → 可提升。rename 把 store a, a_addr 与 load a_addr 配对，load 被替换成 SSA 值 a；所有 store/load/alloc 全部删除（DeleteUnusedInstructionsPass 兜底清理）：
```c
  entry:
    jump block1
  block1:
    return (a + b)      # t3 直接用参数 a、b

```

CleanPass：block1 只有一个前驱（entry）→ 合并进 entry；原 entry 只剩一条 jump → 空块删除：
```c
function add(a: i32, b: i32) -> i32 {
  entry:
    return (a + b)
}

```
这就是最终进入指令选择器的 IR——C 函数被"翻译 + 提升"成了一条加法返回。

### 例 2：常量折叠与死代码消除
```c
int f(void) { return 1 + 2; }

```
codegenerator 产出：`t1 = const 1; t2 = const 2; t3 = add t1, t2; return t3`。
- ConstantFolder：add(const 1, const 2) 编译期算出 → t3 = const 3；

- DeleteUnusedInstructionsPass：t1、t2 不再被引用 → 删除。最终 return 3。

### mem2reg 的 Phi 与短路求值
```c
int abs(int x) { if (x < 0) return -x; return x; }

```
codegenerator 的 gen_if 生成条件块结构，gen_condition 发 CJump(x < 0 → yes, no)。

x < 0 先经 gen_condition_to_integer 或直接 CJump；

两条 return 路径分别 return (0 - x) 与 return x。

mem2reg 在这里可能没有 alloc 可提升，但 gen_condition_to_integer 的 Phi 与三元运算符的 Phi（gen_ternop 1353 行）就是 SSA 汇合点；

若代码里再有 int t = ...; if ...; use(t) 的模式，mem2reg 会在支配边界处插入 Phi 完成提升，随后 CleanPass 把多余块合并。

## 六、一句话总结
codegenerator.py 是"翻译机"：

它信任 semantics 产出的带类型 AST，通过 ir_var_map（声明 → IR 值）和 gen_stmt/gen_expr 两个分发表，把每个 AST 节点机械地映射成 qcc IR 指令，刻意生成 alloca/load/store 形式的"笨 IR"；

优化交给 api.py 的 optimize()——它把 qcc/opt/ 里的 8 个 pass（mem2reg 的 alloc 提升与 Phi 插入、常量折叠、LoadAfterStore、死代码删除、CSE、尾调用、CleanPass 的 CFG 整形）连跑 3 遍，互相配合地把"笨 IR"打磨成紧凑的 SSA 形式，再送入指令选择、寄存器分配和汇编输出。

分工口诀：semantics 定类型、codegenerator 出形状、opt/ 提质量。

