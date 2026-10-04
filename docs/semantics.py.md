# semantics.py 详解
## 一、这个文件说明的是什么？
前面讲过，parser.py 只回答"语法对不对"（Token 顺序是否合法），而 builder.py 是"装配车间"。

那么 semantics.py 就是"语义车间"——parser 每解析出一个语法结构，就喊一声 self.semantics.on_xxx(...)，由 CSemantics 里的同名方法回答三个问题：
- 这个东西的类型是什么？（类型计算、类型检查）
- 需要插入哪些隐式转换？（char 提升为 int、数组退化为指针、整数常量选型等）
- 它是否合法？（变量有没有定义、lvalue 检查、重复定义、重复 case、参数个数/类型、返回类型等）

文件开头 docstring（1–18 行）把职责边界写得很清楚：做类型检查、隐式转换、初始化器检查；不做常量求值（eval_expr 委托给 context）、sizeof 求值。

最终产物是一颗"带类型的 AST"（type-checked AST），它接着被 codegenerator.py 的 CCodeGenerator 消费，生成 IR。

核心数据结构（__init__，35–58 行）：
- self.context（36 行）：builder 创建的 CContext，提供 sizeof/eval_expr/alignment/报错输出等能力；

- self.scope / self._root_scope（37–38 行）：符号表——Scope 是可嵌套的作用域链（{ } 每进一层新建一个），RootScope 是全局唯一的根，存基本类型和全局类型信息；

- 40–54 行：预取常用类型（int/long/char/char*/__builtin_va_list），并根据"int 和指针是否一样大"选择 size_t 是 int 还是 long（LP64 的 TODO）；

- 57–58 行：两个工作变量——compounds 是语句收集栈（当前花括号块里的语句列表），switch_stack 是 switch 嵌套栈（检查 case 重复用）。

## 二、每个方法的作用
### 2.1 编译单元与作用域管理（60–100 行）
- begin()（60–62 行）：开始解析一个文件时由 parser 调用（parser.py:175），新建顶层 Scope。

- finish_compilation_unit()（64–69 行）：断言回到了顶层作用域，取出作用域里登记的所有顶层声明，打包成 nodes.CompilationUnit 返回——这就是 parser.parse() 的返回值（也是 builder _parse 拿到的 AST）。

- enter_function(function)（71–89 行）：函数体开始。开新作用域；把函数参数逐个插入作用域（84–88 行，重名则报"Illegal redefine"）。之后函数体内的 a、b 就能被 on_variable_access 找到了。

- end_function(body)（90–100 行）：退出作用域、把解析好的函数体挂到 function.body 上。


### 2.2 类型修饰应用（102–165 行）
#### apply_type_modifiers(type_modifiers, typ)（102–134 行）：
把 parser 的 parse_type_modifiers 产物变成真正的类型对象。parser 只收集 ("POINTER", 限定符)、("ARRAY", 大小)、("FUNCTION", 参数) 元组，这里倒序应用（107 行 reversed，对应 parser 里 first_modifiers 反序拼接的"螺旋法则"）：

- POINTER → typ.pointer_to()，再叠加限定符；

- ARRAY → 先检查大小是常量（ensure_constant，VLA 例外），再包成 types.ArrayType(typ, size)；

- FUNCTION → 处理 ... 变参（122–125 行）和 f(void) 特例（126–128 行：(void) 等价于无参数），包成 FunctionType(arguments, typ, is_vararg)。

#### on_function_argument(...)（136–148 行）：

应用修饰后，把 int a[] 这种参数退化为 int*（C 规定数组参数就是指针），生成 ParameterDeclaration。

#### on_variable_declaration(...)（150–165 行）：

应用修饰；若修饰出了函数类型（如 int f(int); 是声明函数，int (*fp)(int); 是函数指针）就转给 on_function_declaration；否则生成 VariableDeclaration；最后 register_declaration 登记进符号表。

### 2.3 初始化器检查（168–276 行）
#### on_variable_initialization（168–181 行）：

检查重复定义 → patch_size_from_initializer（190–205 行：int a[] = {1,2,3}; 用初始化元素个数补出数组大小）→ 挂上初始值。

#### on_variable_finished（183–188 行）：

声明收尾检查——类型不完整（如 extern 之外的未定长数组）就报错。

#### new_init_cursor / on_init_compound_enter / init_store（207–244 行）：

与 parser 的 parse_initializer_list 配合的初始化游标操作。init_store 里有个巧妙的循环（225–233 行）：{1, 2} 初始化 int a[2][2] 这种"嵌套但没写够花括号"的情况，会自动隐式下潜一层；多余的初值发 warning（220–221 行）。

#### on_array_designator（246–262 行）：

{[2]=4} 中的 [2]——求值索引、检查非负、检查目标是数组、移动游标位置。


#### on_field_designator（264–276 行）：

{.x=4} 中的 .x——检查是 struct/union、检查字段存在、游标选中该字段。

### 2.4 声明与符号表（279–374 行）
#### on_typedef（279–288 行）：
生成 Typedef 声明登记进作用域——以后 on_typename 在这里查（parser 侧对应 at_type_id 集合）。

#### on_function_declaration（294–303 行）：

生成 FunctionDeclaration 并登记。函数可以先声明后定义、extern 再定义，都靠下面的重声明检查放行。

#### register_declaration（306–334 行）：

符号表插入总入口。名字已存在 → 检查类型一致（check_redeclaration_type）、检查存储类别组合合法（check_redeclaration_storage_class，如先 static 后非 static 就报错）、复合语句内不允许重复定义；否则插入当前作用域。

330–334 行：在复合语句（函数体）内登记的声明同时包装成 DeclarationStatement 追加到当前语句流——这是代码生成器能遇到局部变量声明的原因。

#### invalid_redeclaration（368–374 行）：

报错并附带"First defined here"提示。

### 2.5 类型定义（377–513 行）
#### on_basic_type（377–383 行）：

["unsigned", "long", "long"] → 查 RootScope 得到唯一的基本类型对象（类型是单例的，所以之后能直接用 is/equal_types 比较）。

#### on_typename（385–391 行）：

typedef 名 → 从符号表取出 Typedef 声明 → 返回其类型。这就是 parser 的 at_type_id() 配合的语义端。

#### on_struct_or_union（397–431 行）：

处理 struct/union 的定义与引用两种形态：
- 定义（struct S { ... };、struct S;、匿名 struct { ... };）→ define_tag_type 拿到 tag 类型，把字段挂上（布局在 context 的 get_field_offsets 时才算）；

- 引用（struct S 用在变量声明里）→ 从作用域按 tag 名查找已有类型，找不到就创建一个不完整的占位类型（C 允许前置引用）；

- 405 行 assert tag or fields is not None 挡住纯 struct 无标签无定义的非法情况。

#### on_field_def（433–449 行）：
字段声明——位域必须是整型，生成 types.Field(ctyp, name, bitsize)。

#### on_enum / on_enum_value / exit_enum_values（451–491 行）：

枚举 tag 类型的定义/引用同 struct；枚举常量（EnumConstantDeclaration）插入作用域（它是普通标识符，不是 tag）；收尾时把常量列表挂到类型上（自动增值是在 context.eval_expr 里做的，见 docstring 的"不做"清单）。

#### define_tag_type（493–506 行）：

tag 类型"取或建"——已存在则检查种类一致（struct 名不能再当 union 用）和未重复定义；否则新建并登记 tag。

#### on_type_qualifiers（508–513 行）：
把 const/volatile 集合写到类型的 qualifiers 属性上。

### 2.6 语句语义（516–684 行）
#### enter_scope/leave_scope（524–530 行）：
作用域进出（每个 { }、for 循环都用）。

#### enter_compound_statement/on_compound_statement（532–539 行）：

进花括号块时同时开作用域和开一个语句列表（compounds.append([])）；块结束时出作用域、弹出语句列表包成 statements.Compound。

#### add_statement（541–543 行）：
parser 解析完一条语句就追加到当前块的列表里（对应 parser.py:785）。

#### check_condition（545–549 行）：
条件表达式——指针直接可用，否则强制转成 int（C 里 if (2.5) 合法，非零即真）。

#### on_if/on_while/on_do/on_for（551–626 行）：
检查条件后包成对应语句节点。

#### on_switch_enter/on_switch_exit（556–564 行）：
压入/弹出 CSwitchContext（记录被 switch 的表达式和已见过的 case 值集合）。

#### on_case（566–596 行）：

switch 外出现 case 报错；求值 case 常量、强制转换到 switch 表达式类型、检查重复（IntegerSet 支持区间交叠检测，GNU 的 case 1 ... 3: 走 on_range_case 逻辑）。

#### on_default（598–610 行）：
检查不在 switch 外、不重复。

#### on_return（628–641 行）：
对照 current_function 的返回类型——void 函数不能带值、非 void 函数不能空返；带值则 pointer()（数组退化）+ coerce 到返回类型。

#### on_asm（643–684 行）：
内联汇编——输出操作数必须是 lvalue 且约束为 =r；输入约束 r 时先 coerce 成 long（TODO）；

clobber 寄存器名用 arch_info.has_register 验证存在。

### 2.7 表达式语义（687–1165 行）
#### 字面量（687–744 行）：
- on_string："abc" → ArrayType(char, 4)（长度 +1 含结尾 '\0'）的 StringLiteral；

- on_number：整数常量选型阶梯（709–720 行）——按值大小依次尝试 int → unsigned int → long → unsigned long → long long → unsigned 
long long，带 L/U 后缀的直接按后缀选型；超限报错；

- on_float/on_char：浮点/字符字面量（charval 处理转义序列）。

#### on_ternop（746–759 行）：

条件转 int；结果为 b、c 的公共类型（get_common_type 取类型"秩"高者），两边都 coerce 过去。

#### on_binop（761–899 行）：语义层的"超级分发表"，按运算符分类：
- ||/&&：两边都当条件，结果是 int；

- 赋值类：左操作数必须 lvalue（788–789 行），右操作数 coerce 到左类型；+=/-= 对指针做"确保右操作数是整数"检查（791–793 行）；

- ,：结果类型取右操作数；

- +：指针 + 整数 / 整数 + 指针——810–811 行把 整数 + 指针 交换成 指针 + 整数，注释说"为了代码生成方便"（codegenerator 的 gen_binop 也依赖这个约定，codegenerator.py:1230-1231 的注释写着 "left and right are swapped in semantics if right is pointer"）；数值 + 数值走 promote + 公共类型；

- -：指针 − 指针（结果 size_t，要求元素类型兼容）、指针 − 整数、数值 − 数值；

- 比较：两边必须是标量或指针，coerce 到公共类型，结果 int；

- 位移/位运算：两边必须整数，promote 后公共类型；

- *///%：两边必须标量，同上；

- 最后包成 expressions.BinaryOperator(lhs, op, rhs, result_typ, False, location)。

#### on_unop（901–940 行）：
++/--（前后缀都要求 lvalue 的标量/指针）；-、~（要求整数）；+ 直接 pointer() 退化返回；* 解引用（要求指针，结果类型是元素类型且是 lvalue——928–930 行）；& 取地址 → on_take_address；! 按条件处理、结果 int。

#### on_take_address（942–954 行）：

&func 是函数名 → 函数指针不生成 & 节点（函数指示符本身隐式是指针）；其他要求 lvalue，类型为 pointer_to。

#### on_sizeof（956–959 行）：

只包成 Sizeof 节点，大小留给 codegenerator 调 context.sizeof 算（docstring 说的"不做 sizeof 求值"）。

#### on_cast（961–963 行）：

显式转换直接包 Cast 节点。

#### on_compound_literal（965–969 行）：
(int[]){1,2} → 先补数组大小，再包 CompoundLiteral。

#### on_array_index（971–986 行）：

下标强制转 int；基必须是数组/指针（IndexableType），且必须 lvalue；结果类型是元素类型、lvalue=True。

#### on_field_select（988–1025 行）：

基必须是 struct/union 且是 lvalue；人性化报错——对指针却用 . 的给出 Did you mean "p->x" instead of "p.x"? 提示（993–1002 行）；字段不存在时列出该类型的全部字段（1014–1021 行）；结果类型是字段类型、lvalue=True。

#### 四个 builtin（1027–1074 行）：
va_start/va_arg/va_copy 都要求实参是 __builtin_va_list 类型的 lvalue；offsetof 要求 struct/union、成员存在且不是位域。

#### on_call（1076–1131 行）：

函数调用检查——被调对象必须是指向 FunctionType 的指针（函数名先 pointer() 退化，因此 add(a,b) 和 fp(a,b) 都能过）；参数个数检查（变参函数是"至少 N 个"）；

定参逐个 pointer()+coerce 到形参类型；

变参部分只做默认提升（promote，float→double、char→int 这类）；返回 struct 时结果是 lvalue。

#### on_variable_access（1133–1165 行）：
符号查找——未定义时报 Undeclared identifier: "x"，并用 difflib.get_close_matches 给出"did you mean ..."拼写建议（1136–1147 行，这是 qcc 编译器体验细节）；

找到后按声明种类决定 lvalue（变量/参数是 lvalue，枚举常量/函数不是），生成 VariableAccess(symbol, typ, lvalue, location)。

### 2.8 类型转换与辅助（1168–1340 行）
#### coerce(expr, typ)（1168–1208 行）：

隐式转换的唯一入口。两类型相等 → 不动；指针/枚举 ↔ 基本类型、指针 ↔ 指针、基本类型 ↔ 数组/指针、基本类型/枚举之间 → 插入 ImplicitCast 节点；其他（如 struct→int）报"Cannot convert"。注意：代码生成器只处理带了 ImplicitCast 的 AST，真正的转换指令是 codegen 发的。
pointer(expr)（1210–1229 行）：数组 → 指针退化（decay()）、函数 → 函数指针，都包装成 ImplicitCast。几乎所有表达式入口（on_binop 763–764 行）先各来一次。

#### promote(expr)（1231–1240 行）：

整数提升（char/short 运算前变 int，is_promotable 判断）。

#### equal_types/get_type（1242–1248 行）：

委托 RootScope。

#### ensure_integer/ensure_constant（1250–1261 行）：
断言助手——位运算、case 值等场景的检查。

#### eval_expr（1263–1264 行）：
委托 context.eval_expr（常量求值在 context，不在 semantics）。

#### ensure_no_void_ptr（1266–1280 行）：
指针运算遇 void* → 警告并隐式转 char*（GNU 扩展行为）。

#### get_common_type + basic_ranks + _get_rank（1282–1327 行）：

类型秩表决定二元运算结果类型——long double(110) > double(100) > float(90) > 指针(83) > unsigned long long(71) > … > char(30)；取两者秩高者为公共类型。

#### error/warning/not_impl（1329–1340 行）：
统一走 context.error/warning（负责定位、着色、hints 输出）。

### 2.9 CSwitchContext（1343–1365 行）
switch 的辅助上下文：

记住被 switch 的表达式及其类型、default_seen 标志、以及一个 IntegerSet 累计所有 case 值（用整数集合而不是 set，是为了支持 case 1 ... 3: 区间与单个值之间的交叠检测——contains_range 算集合交集）。

## 三、与 parser.py 和 builder.py 怎么联动？
### 1. 装配关系（builder → semantics）
builder.py:70-71 完成接线：
```python
semantics = CSemantics(context)              # 语义层，持有共享的 CContext
parser = CParser(context.coptions, semantics)  # 注入 parser，成为 self.semantics

```

CContext 是三者共享的"公共数据区"：semantics 用它做 sizeof、eval_expr、报错；

parser 用它的 coptions 判断方言；codegenerator 用它算字段偏移、对齐。

### 2. 回调关系（parser → semantics）
parser 的每个 on_xxx 调用点都精确对应 CSemantics 的一个方法，参数也一一对应。

这张"契约表"是两文件联动的核心：

| parser 位置 | semantics 回调 | 作用 |
| :--- | :--- | :--- |
| parser.py:175 `semantics.begin()` | `begin` | 建顶层作用域 |
| parser.py:221 `on_typename` | `on_typename` | typedef 名查符号表取类型 |
| parser.py:274 `on_basic_type(["int"], loc)` | `on_basic_type` | 查 RootScope 得单例基本类型 |
| parser.py:311 `on_struct_or_union` | `on_struct_or_union` | 定义/引用 tag 类型，挂字段 |
| parser.py:463 `on_function_declaration` | `on_function_declaration` | 生成 FunctionDeclaration、进符号表 |
| parser.py:477 `on_variable_declaration` | `on_variable_declaration` | 生成 VariableDeclaration、进符号表 |
| parser.py:680 `on_function_argument` | `on_function_argument` | 数组参数退化为指针 |
| parser.py:785 `add_statement` | `add_statement` | 语句追加进当前块 |
| parser.py:865 `on_if` | `on_if` | 条件检查 + 包 If 节点 |
| parser.py:1104 `on_variable_access` | `on_variable_access` | 符号查找、定 lvalue |
| parser.py:1097 `on_binop` | `on_binop` | 类型检查、插入隐式转换、定结果类型 |
| parser.py:1126 `on_unop` | `on_unop` | 一元运算检查 |
| parser.py:1226 `on_call` | `on_call` | 参数个数/类型检查 |
| parser.py:178 `finish_compilation_unit()` | `finish_compilation_unit` | 打包 CompilationUnit |

注意返回值的流向：parser 的方法几乎原样返回 semantics 方法返回的节点对象（如 parser.py:1097 lhs = self.semantics.on_binop(...)），parser 再用这些返回值继续组树。

所以"parser 产出 AST"更准确的说法是：parser 驱动、semantics 产出。

### 3. 消费关系（semantics → codegenerator）
semantics 产出的每个节点都带上了 typ 和 lvalue 两个属性。

builder 在 builder.py:39-40 把 AST 交给 CCodeGenerator.gen_code，codegenerator 完全依赖这些语义标注：
- codegenerator.py:1024 if rvalue and expr.lvalue: 决定是否发 load；

- gen_binop 里对指针加法的处理依赖 semantics 在 semantics.py:810-811 交换好的"指针在左"约定；

- ImplicitCast 节点由 semantics 插入、由 gen_cast 生成转换指令。

### 4. 完整数据流
```python
builder.py
  ├─ CContext ──────────────┐ (共享)
  ├─ CSemantics(context) ───┤
  ├─ CParser(coptions, semantics)
  │     └─ on_xxx(...) ─────→ CSemantics.on_xxx ──→ 带类型的 AST 节点
  │                                   │                    │
  │                                   └─ 符号表(scope) ────┘
  └─ CCodeGenerator(context).gen_code(compile_unit) ──→ ir.Module

```

## 四、详细例子：完整走一遍
### 例子 1：主流程 int add(int a, int b) { return a + b; }
（parser 侧的流程在前两讲，这里只标 semantics 侧的动作）

| 阶段 | parser 动作 | semantics 动作与产物 |
| :--- | :--- | :--- |
| 开始 | `parse_translation_unit` → `semantics.begin()` | 建顶层 Scope |
| int | `on_basic_type(["int"], loc)` | RootScope 返回单例 BasicType(INT) |
| 参数 a、b | `on_function_argument(int, "a", [], loc)` ×2 | 修饰列表为空，类型不变 → ParameterDeclaration(a, int)、ParameterDeclaration(b, int) |
| add | `on_function_declaration(None, int, "add", [("FUNCTION", [a, b])], loc)` | apply_type_modifiers 应用 FUNCTION → FunctionType(arguments=[a,b], return=int) → FunctionDeclaration(add, ...) → register_declaration 插入顶层作用域 |
| { | `enter_func(add)` | 开新作用域；参数 a、b 插入该作用域 |
| a + b | `on_binop(a_access, "+", b_access, loc)`（a_access、b_access 是先经 `on_variable_access` 得到的：查作用域命中参数声明 → VariableAccess(sym, int, lvalue=True)） | pointer()：int 非数组非函数，不动；promote()：int 不可提升，不动；get_common_type(int, int)：秩都是 50，取 int；coerce 两边类型相同不插 Cast → 返回 BinaryOperator(a, "+", b, int, lvalue=False) |
| return | `on_return(plus_expr, loc)` | current_function 返回类型 int，非 void → pointer() + coerce(plus_expr, int)（相同，无 Cast）→ statements.Return(plus_expr)，经 add_statement 进当前块列表 |
| } | `end_function(body)` | 出作用域，function.body = Compound([Return]) |
| 结束 | `finish_compilation_unit()` | 打包 CompilationUnit([FunctionDeclaration(add)]) → 返回给 builder → 交给 CCodeGenerator |

对比 parser 干的事：parser 只知道"这里有一串 ID ( ... ) { ... } 形状的 Token"；add 是谁、a 是什么类型、+ 两边能不能加、结果是什么类型——全是 semantics 在这条链上决定的。

### 例子 2：semantics 真正"加戏"的地方——隐式转换与 lvalue
```python
char c;
int i = c + 1;        // ① char 提升为 int
double d = 3;         // ② int → double 隐式转换
int *p = &i;
int j = *p + 2;       // ③ 解引用产生 lvalue

```
- ① on_binop(c_access, "+", 1_literal, ...)：promote(c_access) 发现 char 的 is_promotable 为真 → 插入 ImplicitCast(c, int)；最终 AST 是 BinaryOperator(ImplicitCast(c), "+", 1, int)。codegenerator 看到 ImplicitCast 才发 i8→i32 扩展指令。

- ② parse_initializer(int) 里 parser.py:541-543 调 coerce(expr, typ)：int 与 double 都在"可转换"白名单 → ImplicitCast(3, double)，赋值给 d。

- ③ on_unop("*", p_access, ...)：p 是指针 → 结果类型 int、lvalue=True（semantics.py:930）；随后 on_binop 检查到 *p 是 lvalue 无妨，codegen 的 gen_expr(rvalue=True) 会因 lvalue=True 而发 load。

### 例子 3：semantics 的报错与提示
```python
int f(void) { return; }        // → error: Must return a value from this function
int g(void) { return 3; }      // OK
void h(void) { return 3; }     // → error: Cannot return a value from this function

int main(void) { x = 3; }      // → error: Undeclared identifier: "x"
                               //    hint: "x" was not defined, did you mean ... (difflib 建议)

struct S { int x; } s;
int k = s.y;                   // → error: Field y not part of struct
                               //    hint: This type has those fields: ['x']

```

这些检查 parser 一概不管——它只保证 Token 形状合法。

## 五、一句话总结
parser 是"形状检查员"，semantics 是"含义赋予者"，builder 是"组装工"：

builder 把共享的 CContext、CSemantics 和 CParser 接在一起；parser 每识别出一个语法结构就回调 CSemantics.on_xxx；

semantics 查符号表、算类型、插隐式转换、做合法性检查，把 Token 背后的"含义"烙进每个 AST 节点的 typ/lvalue 属性；

这颗带类型的 AST 最后由 builder 转交给 CCodeGenerator 生成 IR。