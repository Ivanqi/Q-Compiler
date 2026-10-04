# parser.py 详解
## 一、这个文件说的是什么？
这是 qcc 编译器中 C 语言前端的语法解析器（Parser）。整个 qcc 的 C 前端是一条流水线：
```
C 源码 → 预处理 (preprocessor) → 词法分析 (lexer, 产出 Token) → 本文件 (语法分析) → 语义分析/IR 生成
```

本文件用 **递归下降（Recursive Descent）** 方法，把 Token 流变成一颗 AST / IR 树。

它有三个显著设计特点：
- 语法与语义分离：解析器自己只负责"语法对不对"（看 Token 结构），几乎不构造任何结果，所有"这是什么类型、这个变量存在吗"之类的工作全部通过 self.semantics.on_xxx(...) 回调丢给 semantics.py 里的 CSemantics 类。
- 没有 lexer hack：C 语言有个著名难题——T * x; 里 T 可能是类型（声明）也可能是变量（乘法）。经典做法是"lexer hack"（词法器问符号表）。这里改由解析器自己维护一个 typedefs 集合（见 parser.py:1238 的 at_type_id()）来判断。
- 表达式用优先级爬升法（Precedence Climbing）：不用为每级优先级写一个函数，而是用一张优先级表 + 一个通用函数（见 parser.py:1080）。

它还继承了 recursivedescent.py 的 RecursiveDescentParser，其中最重要的基础设施是：
| 基类方法 | 作用 |
| :--- | :--- |
| self.peek | 偷看当前 Token 的类型（如 "ID"、"*"），不消耗它 |
| next_token() | 吃掉当前 Token 并前进到下一个，返回被吃掉的 Token |
| consume(typ) | 断言下一个 Token 是 typ，是则吃掉并返回；不是则报错 |
| has_consumed(typ) | 如果下一个是 typ 就吃掉并返回 True，否则不动返回 False |
| look_ahead(n) | 看后面第 n 个 Token |
| error(msg, loc) | 抛出 CompilerError |


## 二、逐段逐行讲解
### 第 1–25 行：文档、导入、常量
- 1–16 行：模块 docstring，说明这是递归下降解析器，分三部分：声明解析（declaration）、语句解析（statement）、表达式解析（expression），并列出灵感来源（clang、gcc、8cc）。
- 18 行：导入 logging，用于调试日志。
- 20 行：导入基类 RecursiveDescentParser（提供上面表格里的 Token 操作）。
- 21 行：从 nodes 包导入 expressions（表达式节点类）和 statements（语句节点类），解析时直接构造这些 AST 节点（如 statements.Break、expressions.CharLiteral）。
- 22 行：导入 type_to_str，把类型变成字符串用于报错信息（如 parser.py:270）。
- 24–25 行：定义两个常量 LEFT_ASSOCIATIVE / RIGHT_ASSOCIATIVE，用作优先级表里的结合性标记。

### 第 28–49 行：类声明
- 28 行：class CParser(RecursiveDescentParser)——本文件的主角。
- 29–46 行：docstring，解释参考了 CLANG 前端、无 lexer hack、参考 gcc 和 libfirm。
- 48 行：logger = logging.getLogger("cparser")，类级日志器（所有实例共用）。
- 49 行：verbose = False，开启后会把每个 Token 都打日志（配合 parser.py:1231 的 next_token 重载）。

### 第 51–158 行：__init__ 初始化
- 51–54 行：构造函数接收 coptions（编译选项字典，如 {"std": "c99"}）和 semantics（CSemantics 实例）；先调用 super().__init__() 初始化 Token 游标（self.token = None 等）。
- 55–62 行：type_qualifiers = {"volatile", "const"}（类型限定符）和 storage_classes = {"typedef", "static", "extern", "register", "auto"}（存储类别）。
- 63–74 行：type_specifiers：C 内建类型关键字，void, char, int, float, double, short, long, signed, unsigned 外加 __builtin_va_list。
- 76–100 行：keywords 集合：语句关键字（if/while/for/return/switch/case/goto…）、sizeof、struct/union/enum、asm、变参内建函数等。这个集合主要用于 is_declaration_statement() 判断。
- 102–103 行：加入 GCC 扩展 __attribute__。
- 106–108 行：C99 扩展——如果 is_c99() 为真，则 restrict 加入限定符、inline 加入关键字（见 parser.py:157：self.coptions["std"] == "c99"）。
- 111–113 行：把存储类别、限定符、类型说明符三个集合全部并入 keywords。
- 115–152 行：prio_map 优先级表，键是运算符，值是 (结合性, 优先级数字)，数字越大绑定越紧：

| 运算符 | 优先级 | 结合性 |
| :--- | :--- | :--- |
| , | 3 | 左 |
| = += ... ^= | 10 | 右 |
| ? : | 17 | 右 |
| `||` `&&` | 20 / 30 | 左 |
| `|` `^` `&` | 40 / 50 / 60 | 左 |
| `<` `<=` `>` `>=` `!=` `==` | 70 | 左 |
| `>>` `<<` | 80 | 左 |
| `+` `-` | 90 | 左 |
| `*` `/` `%` | 100 | 左 |

（131–132 行注释掉的 ++/-- 是因为它们被当作前缀/后缀一元运算符单独处理，不走这张表。）

- 154–155 行：self.typedefs = set()——typedef 名集合，替代 lexer hack 的关键数据结构。

### 第 160–178 行：解析入口
- 161–171 行 parse(tokens)：整个解析的入口。167 行 init_lexer(tokens) 把 Token 迭代器装进来并取第一个 Token；168 行 清空 typedefs（支持多次复用）；169 行 调用 parse_translation_unit() 开始解析整个编译单元；171 行 返回最终产物（finish_compilation_unit 的结果，即整个模块的 IR）。
- 173–178 行 parse_translation_unit()：175 行 通知语义层 begin()（开全局作用域）；176–177 行 只要还有 Token 就不断解析顶层声明；178 行 finish_compilation_unit() 收尾并返回编译单元。

### 第 181–284 行：声明规格（declaration specifiers）解析
- 181–186 行 parse_declarations()：先 parse_decl_specifiers() 解析出"类型 + 存储类别"（DeclSpec）；185 行 如果接下来不是 ;，才继续解析声明体 parse_decl_group(decl_spec)。这里的 ; 分支处理形如 struct S; 的前置声明（类型在 parse_struct_or_union 里已经消费掉）。

- 188–284 行 parse_decl_specifiers(allow_storage_class=True)：解析声明开头的一串"规格词"，例如 static const unsigned long。用一个 while True 循环（214 行）逐 Token 收集：

	- 215–223 行：at_type_id() 为真 → 下一个标识符是 typedef 过的类型名（如 size_t）。若还没有类型（217 行检查 typ or type_specifiers），就吃掉它，并调用 semantics.on_typename(...) 查出真正的类型；否则 break（说明这个 ID 是变量名，规格部分结束）。
	- 224–230 行：self.peek in self.type_specifiers → 吃掉内建类型关键字，追加到 type_specifiers 列表（unsigned long long 由三个关键字拼成）；若 typ 已经有了就报错"Type already determined"。
	- 231–240 行：enum / struct / union → 调用专门的解析函数直接得到类型对象 typ；重复出现报错。
	- 241–248 行：存储类别（static 等）→ 记入 storage_class；出现两个报错"Multiple storage classes"；若 allow_storage_class=False（如结构体字段里）则直接报错。
	- 249–254 行：类型限定符（const/volatile）→ 加入 type_qualifiers 集合；重复报错。
	- 255–258 行：inline → 吃掉并忽略（只是编译器提示，日志一条 debug）。
	- 259–261 行：__attribute__ → 解析属性后丢弃（日志打 error 级别提示"忽略"）。
	- 262–263 行：其余情况（遇到变量名、*、; 等）→ break 结束收集。
	- 266–274 行：如果收集了内建类型关键字，调 semantics.on_basic_type(type_specifiers, location) 合成基础类型；若同时还有 typ（typedef 名/enum/struct）则报错。
	- 276–280 行：如果最后 typ 仍为空 → 隐式 int（C89 老特性，static x; 等价于 static int x;），打 warning。
	- 282–284 行：把限定符包到类型上（on_type_qualifiers），打包成 DeclSpec(storage_class, typ) 返回。

### 第 286–432 行：struct / union / enum / attribute
- 286–313 行 parse_struct_or_union()：

	- 288 行 吃掉 struct 或 union 关键字；
	- 291–300 行 可选标签名：ID → 标签；{ → 匿名（tag=None）；其他 → 报错；
	- 302–304 行 is_definition：后面是 {（带成员定义），或"有标签且紧跟 ;"（struct S; 这种前置声明也视为定义，因为 C 里首次声明就确定了 tag 类型）；
	- 306–309 行 有 { 就解析成员，否则 fields=None（引用已有类型）；
	- 311–313 行 交给 semantics.on_struct_or_union(keyword, tag, is_definition, fields, loc)。

- 315–351 行 parse_struct_fields()：{ 后循环解析成员直到 }。每个成员：321 行 解析规格（禁止存储类别）；323 行 parse_type_modifiers(abstract=True) 拿到指针/数组修饰和名字（可以是 None，用于匿名位域）；330–334 行 若遇 : 是位域，解析常量表达式作为位数；336–344 行 on_field_def(...) 生成字段，逗号分隔的多个声明者继续循环；349–350 行 每个成员组以 ; 结束，最后吃掉 }。

- 353–397 行 parse_enum() / parse_enum_fields()：与 struct 同构——enum [标签] [{ ID [= 常量表达式], ... }]。372 行 on_enum(tag, is_definition, loc) 创建枚举类型；390–392 行 每个枚举值 on_enum_value；397 行 exit_enum_values 收尾（负责"没有显式赋值时自动递增"）。

- 399–432 行 parse_attributes() / parse_gnu_attribute()：解析 __attribute__((noreturn)) 这种 GCC 语法。417–419 行 依次吃掉 __attribute__、(、(；421–429 行 循环读取逗号分隔的属性名，存进字典 {name: 1}（注意 TODO：还不支持带参数的属性）；430–431 行 吃掉两个 )。

### 第 434–519 行：声明组（declaration group）
- 434–459 行 parse_decl_group(decl_spec)：解析完 static int 这类前缀之后的部分。442 行 先解析一个 parse_declarator()（得到名字 + 类型修饰列表）。然后分三种情况：

	- 443–448 行 typedef：逐个声明者调 parse_typedef，中间用逗号分隔，最后 ;；
	- 449–452 行 紧跟 {：是函数实现，调 parse_function_declaration；
	- 453–459 行 其他：是变量声明，逐个声明者调 parse_variable_declaration，逗号分隔，最后 ;。

- 461–472 行 parse_function_declaration()：463–469 行 调 semantics.on_function_declaration(存储类别, 类型, 名字, 类型修饰, 位置) 注册函数（语义层会处理函数签名、重复定义检查等）；470 行 enter_function 进入函数作用域；471 行 解析花括号包围的函数体；472 行 end_function 收尾。

- 474–490 行 parse_variable_declaration()：477–483 行 on_variable_declaration(...) 创建变量；486–488 行 若有 = 就解析初始化器并调 on_variable_initialization；490 行 on_variable_finished 收尾。

- 492–500 行 parse_typedef()：494 行 self.typedefs.add(declarator.name) ——这是关键行，把新 typedef 名登记到解析器自己的集合里，之后遇到同名 ID 就当作类型名（lexer hack 的替代实现）；495–500 行 通知语义层记录这个 typedef。

- 502–519 行 parse_declarator(abstract=False)：509 行 调 parse_type_modifiers 得到 (type_modifiers, name)；510–513 行 拆出名字和位置（匿名时两者都是 None）；519 行 打包成 Declarator(name, type_modifiers, location) 返回。

### 第 522–659 行：初始化器（initializer）
- 522–545 行 parse_initializer(typ)：按三种形态分派：

	- 536–537 行 { → 花括号初始化列表；
	- 538–539 行 字符数组 + 字符串字面量 → char s[] = "hi"; 特例；
	- 540–543 行 其他 → 解析常量表达式，然后 semantics.pointer(expr)（数组退化为指针）、semantics.coerce(expr, typ) 做类型转换。

- 547–566 行 parse_array_string_initializer()：552 行 吃掉 STRING Token；554–561 行 把字符串逐字符拆成 CharLiteral（ord(c) 转码点）；562–564 行 末尾补一个 CharLiteral(0)（C 字符串自动追加 '\0'）；565 行 包成 ArrayInitializer。

- 568–597 行 parse_initializer_list() / parse_initializer_list_sub()：574 行 创建 init_cursor（初始化游标，语义层提供的状态对象，跟踪"当前初始化到哪一层、哪个元素"）；583–585 行 on_init_compound_enter 进入一层花括号；589–593 行 循环解析元素、逗号分隔；595–597 行 吃掉 }、unwind() 回退游标、leave_compound() 收尾。

	- 602–621 行：若 is_c99 且下一个是 . 或 [ → 进入设计器模式；606 行 unwind() 先退回顶层；
	- 607–619 行 循环解析 [索引] 或 .字段名 设计器，若后面还有设计器则 on_init_compound_enter(..., True) 下沉一层；621 行 吃掉 =；
	- 626 行 取当前层元素的类型；
	- 627–631 行 元素是 { 则递归，否则解析常量表达式并 init_store 存入游标；
	- 634–638 行 有设计器时 unwind() 弹出设计器层，然后 next_element() 移到下一个元素。

- 640–646 行 parse_array_designator()：[常量表达式] → on_array_designator（如 {2, [10]=4}）。

- 648–654 行 parse_struct_designator()：.字段名 → on_field_designator。

- 656–659 行 skip_initializer_lists()：一路跳过 Token 直到 }（当前未使用）。

### 第 662–773 行：类型修饰（pointer / array / function 后缀）
- 662–687 行 parse_function_arguments()：解析参数列表。672–676 行 ... → 可变参数（如 printf(const char*, ...)），参数列表里记一个 "..."；677–685 行 正常参数：解析规格 + 抽象声明者，on_function_argument(typ, name, mods, loc) 生成参数，逗号分隔循环。注意 C 只要求参数类型，参数名可省略（int foo(int, int)）。

- 689–763 行 parse_type_modifiers(abstract=False)：这是 C 声明语法最难的部分，处理 *、[]、() 如何套在类型上（"螺旋法则"）。分成三段收集：

	- 698–708 行 first_modifiers：先收集前置的 *，每个 * 后面还可跟限定符（int * const p）。收集到的是 ("POINTER", 限定符集合)。
	- 713–714 行：ID → 这就是声明者的名字；
	- 715–729 行：( → 两种可能：718 行 用 is_declaration_statement() 判断括号内是不是类型开头——是则为抽象函数类型 int (int)（函数指针声明 int (*fp)(int) 里的参数部分），否则是"分组修饰"（如 int (*fp)(int) 中的 *fp），递归解析后放进 middle_modifiers；
	- 730–736 行：其他且非抽象 → 报错"Expected a name"；
	- 739–758 行 last_modifiers：后缀循环——( 参数列表 → ("FUNCTION", 参数列表)；[ → ("ARRAY", 长度)，其中 748–750 行 [* ] 表示 VLA（变长数组，记 "vla"）、751–752 行 [] 表示长度未知（None）、753–754 行 否则解析表达式作长度；
	- 760–762 行：first_modifiers.reverse() 后按 middle + last + first 拼接。为什么 reverse？因为 C 的声明语义是"后缀先作用、前缀后作用"：int *p[3] 意思是"3 个元素的数组，元素是指向 int 的指针"，即 [ARRAY(3), POINTER]——先遇到 * 后遇到 [3]，但数组要套在外层。注释那句 go right when you can, go left when you must 正是此意。


- 765–773 行 parse_typename()：解析纯类型（用于 sizeof(int)、类型转换、__builtin_va_arg 等）。767 行 规格部分禁止存储类别；769 行 抽象声明者；770–771 行 出现了名字则报错；773 行 on_type 应用修饰得到最终类型。


### 第 776–987 行：语句（statement）
- 776–785 行 parse_statement_or_declaration()：C99 允许声明与语句混排，所以先判断——782 行 是声明就 parse_declarations()，否则解析语句并 add_statement。

- 787–804 行 is_declaration_statement()：这是无 lexer hack 的核心判断逻辑。当前 Token 是存储类别/限定符/内建类型/struct/union/enum → 声明；是 ID → 798–800 行 若后面跟 : 则是 goto 标签（语句），否则 802 行 查 at_type_id()（是否在 typedefs 集合里）——是 typedef 名则 T x; 是声明，否则 x = 1; 是表达式语句。

- 806–833 行 parse_statement()：808–823 行 建立关键字 → 解析方法的分发表（for/if/do/while/switch/case/default/break/continue/goto/return/asm/{/;）；824–825 行 命中就调用对应方法；826–827 行 ID : → 标签；828–832 行 兜底是表达式语句（解析表达式、包成语句、吃掉 ;）

- 后续每个语句方法结构高度一致，都是"记录位置 → 吃掉关键字 → 解析各组成部分 → 交给 semantics.on_xxx"：

	- 835–840 行 parse_label：ID : 语句；
	- 842–845 行 parse_empty_statement：; → statements.Empty(loc)（这里是直接构造 AST 节点，而非走语义层——因为空语句没有类型）；
	- 847–854 行 parse_compound_statement：{ 语句... }，进入/退出作用域；
	- 856–867 行 parse_if_statement：条件用 parse_condition()（花括号包起来的表达式），then/可选 else 各是一个语句，on_if；
	- 869–877 行 parse_switch_statement：on_switch_enter / on_switch_exit 包住整个 switch 上下文；
	- 879–899 行 parse_case_statement：解析常量表达式；891–895 行 GCC 扩展 case 5 ... 10: 把两个值打包成元组；on_case；
	- 901–906 行 parse_default_statement；
	- 908–918 行 break/continue：吃掉关键字和 ;，直接构造 statements.Break/Continue；
	- 920–925 行 goto：goto ID ; → statements.Goto；
	- 927–932 行 while：条件 + 循环体 → on_while；
	- 934–941 行 do-while：先体后条件，最后有 ;；
	- 943–977 行 parse_for_statement：946 行 enter_scope()——for 循环有自己的作用域（C99 可在 for(int i=...;;) 里声明变量）；948–960 行 初始化部分：空/声明（C99）/表达式三选一；963–967 行 条件部分；969–973 行 增量部分；976 行 leave_scope()；
	- 979–987 行 return：有表达式则解析，否则 None（return;）。

### 第 990–1040 行：内联汇编（GCC 扩展）
- 990–1009 行 parse_asm_statement()：解析 asm("模板" : 输出操作数 : 输入操作数 : 破坏列表) 的 GCC 扩展语法。1000 行 汇编模板字符串；1002–1004 行 三组列表；1007–1009 行 on_asm 交给语义层（通常是"不支持时抛错"）。

- 1011–1021 行 parse_asm_operands()：: 引导的操作数列表（可为空——1014 行 判断下一 Token 不是 ) 也不是 : 才解析第一个）。

- 1023–1029 行 parse_asm_operand()："约束字符串" (表达式) → 二元组 (constraint, variable)。

- 1031–1040 行 parse_clobbers()：: 后逗号分隔的寄存器名字符串列表。

### 第 1043–1228 行：表达式（核心是优先级爬升）
- 1043–1048 行 parse_condition()：( 表达式 )。

- 1050–1052 行 parse_constant_expression()：从优先级 17 开始爬——? :（优先级 17，右结合，17 >= 17 成立）可以进来，但赋值（10）和逗号（3）被排除，正好符合 C 对常量表达式的定义。

- 1054–1055 行 parse_assignment_expression()：从 10 开始——允许所有赋值运算（10 >= 10），只排除逗号（3）。函数调用的实参、__builtin_va_arg 的实参都按这个规则（C 标准要求实参是赋值表达式）。

- 1057–1062 行 parse_expression()：从 0 开始——什么运算符都可以。
- 1064–1078 行 _binop_take(op, priority)：判断"遇到这个运算符时要不要继续归约"。1071–1072 行 不在 prio_map 里（如 ;、)）→ 停下。1074 行 取出该运算符的 (结合性, 优先级)；1075–1078 行 左结合用 > priority，右结合用 >= priority——右结合的 >= 让同级运算符继续向右延伸，这就是结合性实现的全部秘密。


- 1080–1098 行 parse_binop_with_precedence(priority)：优先级爬升主循环。
	- 1081 行 先解析一个 parse_primary_expression() 作为左操作数；
	- 1084 行 循环：只要当前 Token 是"比给定优先级更紧"的运算符就继续；
	- 1085–1086 行 吃掉运算符、取出其优先级 op_prio；
	- 1087–1094 行 三元运算符 ? : 特判：中间部分用 parse_expression()（完整表达式），吃掉 :，右边用 op_prio 继续爬，on_ternop；
	- 1095–1097 行 普通二元：右操作数从 op_prio 开始递归解析（这保证了右边更紧的运算符先被吸走），on_binop；
	- 1098 行 返回最终的 lhs。

- 1100–1216 行 parse_primary_expression()：解析"基本表达式"+后缀操作。前半段是一串 if/elif：
ID → on_variable_access（变量引用；函数指针赋值也会走这里）；NUMBER/FLOAT/CHAR/STRING → 对应字面量回调；
	- 1119–1126 行 前缀一元运算符 ! * + - ~ & -- ++：1121–1124 行 把前缀 ++/-- 改名为 "++x"/"--x"（与后缀 "x++" 区分开），递归解析操作数后 on_unop；
	- 1127–1156 行 四个 __builtin_va_start / va_arg / va_copy / offsetof 的专用解析（语法各不相同，offsetof 里要解析类型名和成员名）；
	- 1157–1169 行 sizeof：1159–1166 行 有 ( 时用 is_declaration_statement() 区分 sizeof(int)（解析类型）和 sizeof(x)（解析表达式）；没有括号则按一元运算符处理（sizeof x 合法）；
	- 1170–1188 行 (：1173 行 再次用 is_declaration_statement() 区分三种情况——1177–1181 行 后面是 { 则为复合字面量（(struct S){...}，C99）；1183–1184 行 否则是类型转换（(int)x）；都不是则是括号表达式（1187 行 parse_expression() 从优先级 0 重新开始，括号重置优先级）；
	- 1189–1190 行 都不是 → 报错"Expected expression"。
	- 1192–1216 行 后缀循环（优先级最高的操作，紧跟在基本表达式后面循环处理）：++/-- → 后缀形式 "x++"；[ → 数组下标 on_array_index；( → 函数调用 parse_call；. → 字段选择；-> → 1212–1213 行 先解引用（on_unop("*", ...)）再字段选择——p->x 等价于 (*p).x。

- 1218–1228 行 parse_call(callee)：( 后循环解析逗号分隔的实参（每个按赋值表达式规则），on_call(callee, args, location)，最后吃掉 )。

### 第 1230–1248 行：词法辅助
- 1231–1236 行 next_token()：重载基类方法，verbose 打开时把每个 Token 打日志（调试用）。

- 1238–1245 行 at_type_id()："lexer hack"的实现位置——当前 Token 是 ID 且其值在 self.typedefs 集合里，就认为它是类型名。这个集合在 parser.py:494 由 parse_typedef 维护。

- 1247–1248 行 parse_string()：吃掉一个 STRING Token 并返回其字符串值（汇编解析里多次用到）。

### 第 1251–1266 行：两个小的数据类
- 1251–1259 行 DeclSpec：装着 storage_class 和 typ（在规格循环中逐步确定的类型），有 __repr__ 便于调试。

- 1262–1266 行 Declarator：装着 name、type_modifiers（parse_type_modifiers 的产物）和 location。

## 三、详细例子：完整走一遍
以这段代码为例：
```c
int *foo(int a, int b) {
    int sum = a + b * 2;
    return sum;
}

```

### 第 1 步：词法分析产出 Token 流（大致为）：
```python
ID(int)  *  ID(foo)  (  ID(int)  ID(a)  ,  ID(int)  ID(b)  )  {
  ID(int)  ID(sum)  =  ID(a)  +  ID(b)  *  NUMBER(2)  ;
  ID(return)  ID(sum)  ;
}

```

### 第 2 步：调用链
```python
parse(tokens)
└─ parse_translation_unit()
   └─ parse_declarations()          ← 顶层循环

```

### 第 3 步：parse_decl_specifiers() 处理 int *
1. while True 循环：self.peek == "int" 在 type_specifiers 里 → 吃掉，type_specifiers = ["int"]；

2. 下一 Token 是 *，不匹配任何分支 → break；

3. type_specifiers 非空 → typ = semantics.on_basic_type(["int"], loc)，得到基础类型 int；

4. 返回 DeclSpec(storage_class=None, typ=int)。

### 第 4 步：parse_decl_group() → parse_declarator() → parse_type_modifiers() 处理 * foo(int a, int b)
1. has_consumed("*") 为真 → first_modifiers = [("POINTER", set())]；

2. self.peek == "ID" → name = consume("ID") = "foo"；

3. 后缀循环：self.peek == "(" → 吃掉，调 parse_function_arguments()：
	1. 第一轮：parse_decl_specifiers() 吃掉 int；parse_declarator(abstract=True) 里 parse_type_modifiers 拿到 name="a"、无修饰；on_function_argument(int, "a", [], loc)；has_consumed(",") 为真 → 继续；
	
	2. 第二轮：同样解析出参数 b；has_consumed(",") 为假 → 退出循环；
	
	3. 得到 last_modifiers = [("FUNCTION", [arg_a, arg_b])]；

4. 后缀循环再看：self.peek == "{" → 退出循环；

5. 760–762 行：first_modifiers.reverse() 后拼接： type_modifiers = [] + [("FUNCTION", [a, b])] + [("POINTER", set())] = [FUNCTION, POINTER] —— 语义层按此顺序应用，先套 FUNCTION 再套 POINTER，得到"返回 int* 的函数"。

### 第 5 步：回到 parse_decl_group
storage_class 不是 typedef，且 self.peek == "{" → parse_function_declaration() → on_function_declaration(None, int, "foo", [FUNCTION, POINTER], loc) 创建函数，enter_function 开作用域，解析函数体。

### 第 6 步：函数体内 int sum = a + b * 2;
1. parse_compound_statement() 吃掉 {；

2. parse_statement_or_declaration() → is_declaration_statement()：peek 是 "int" 在内建类型集合 → True → parse_declarations()；

3. 规格解析得 DeclSpec(int)；peek 是 sum 不是 ; → parse_decl_group → 变量分支 → parse_variable_declaration：on_variable_declaration(int, "sum", [], loc)；

4. has_consumed("=") 为真 → parse_initializer(int) → 不是 { 也不是字符串 → parse_constant_expression() → parse_binop_with_precedence(17)，现在开始优先级爬升，Token 是 a + b * 2 ;：

```python
parse_binop_with_precedence(17):
  lhs = parse_primary_expression()          → ID a → on_variable_access("a")
  peek = "+", _binop_take("+", 17):
    ("+", 左结合, 90)，90 > 17 → 继续
  op = "+", op_prio = 90
  rhs = parse_binop_with_precedence(90):
    lhs = parse_primary_expression()        → ID b
    peek = "*", _binop_take("*", 90):
      ("*", 左结合, 100)，100 > 90 → 继续
    op = "*", op_prio = 100
    rhs = parse_binop_with_precedence(100):
      lhs = NUMBER(2) → on_number("2")
      peek = ";"，不在 prio_map → _binop_take 返回 False → 循环不进入
      return 2
    lhs = on_binop(b, "*", 2)               ← b * 2 先被组合！
    peek = ";" → 停止
    return (b * 2)
  lhs = on_binop(a, "+", b * 2)             ← a + (b * 2)
  peek = ";" → 停止

```
关键点：因为 * 的优先级 100 > 递归入口的 90，右操作数的递归把 b * 2 先"吸走"了，所以结果天然是 a + (b * 2) 而不是 (a + b) * 2。之后 coerce 转成 int、on_variable_initialization 完成初始化，吃掉 ;。

### 第 7 步：return sum;
parse_statement() 分发表命中 "return" → parse_return_statement()：peek 是 sum 不是 ; → parse_expression() → parse_binop_with_precedence(0) → on_variable_access("sum")；peek 是 ; 直接停下；on_return(sum, loc)。

### 第 8 步：收尾
吃掉 } → end_function → 顶层 at_end → finish_compilation_unit() 返回编译单元。

最终产物（语义层构造出的 IR 示意）：
```c
function foo(a: int, b: int) -> int*:
    sum: int = (a + (b * 2))
    return sum

```

## 补充例子 1：右结合性是怎么实现的
`a = b = c`; 走 `parse_binop_with_precedence(0)`：

```python
lhs = a
peek = "=", _binop_take("=", 0): ("=", 右结合, 10)，10 >= 0 → 继续
  op_prio = 10
  rhs = parse_binop_with_precedence(10):
    lhs = b
    peek = "=", _binop_take("=", 10): 右结合用 >=，10 >= 10 → 继续！
      rhs = parse_binop_with_precedence(10) → c
      lhs = (b = c)
    return (b = c)
  lhs = (a = (b = c))

```

如果这里用 > 而不是 >=，a = b = c 会被解析成 (a = b) = c——一个类型错误。这就是 parser.py:1075-1078 里 >= 的意义。

## 补充例子 2：typedef 与"lexer hack"
```python
typedef int size_t;
size_t x;        // 声明
size_t * y;      // 声明（指针）

```
第一句走 parse_decl_group 的 typedef 分支 → parser.py:494 把 size_t 加入 self.typedefs。

第二句解析时，parse_decl_specifiers 的循环在 at_type_id() 处命中（size_t 在集合里）→ 作为类型吃掉；

而普通语句 x = 1; 里 x 不在集合里 → is_declaration_statement() 返回 False → 走表达式语句分支。

这就是文件 docstring 说的 "without the lexer hack"——用解析器内的集合替代了词法器与符号表的交互。