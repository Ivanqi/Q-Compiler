# builder.py 详解
## 一、这个文件说明的是什么？
如果说 parser.py 是 C 前端流水线上"语法检查"那一站，那么 builder.py 就是整条流水线的"总装车间"（门面/Facade）——它不自己解析语法，而是负责把四个组件按正确顺序组装起来，完成从"一段 C 源码文本"到"IR 模块"的完整转换：
```python
C 源码
  ↓ ① CPreProcessor   预处理 + 词法分析 → Token 流
  ↓ ② prepare_for_parsing   Token 流适配（去空白、ID 转关键字、拼接相邻字符串）
  ↓ ③ CParser + CSemantics  语法分析 + 语义分析 → AST（编译单元）
  ↓ ④ CCodeGenerator   遍历 AST → qcc 的 IR 模块 (ir.Module)


```
文件中每个函数本质上都是一个"流水线装配函数"，区别只在于入口形式不同（文件对象 / 字符串 / 类型片段）和是否走到 IR 那一步。

它对外提供两种产物：
- AST：parse_text / create_ast —— 只到第 ③ 步；
- IR 模块：CBuilder.build —— 走完全部 ④ 步。

## 二、每个方法的作用
### class CBuilder（第 13–45 行）
13–14 行：docstring——"把 C 代码转换成 IR 代码的构建器"。

18–21 行 __init__(self, arch_info, coptions)：保存两个"全局配置"：
- arch_info：目标架构信息（get_arch('x86_64').info），决定 int 是 4 字节还是 2 字节、指针多大等——codegenerator.py:41-43 里 get_size("int")、get_size("ptr") 查的就是它；

- coptions：C 方言选项字典（COptions()，含 "std": "c99" 等，parser 里的 is_c99() 读的就是它）；

- self.cgen = None：占位（历史上可能存代码生成器实例，现在 build 里用局部变量了）。

### 23–40 行 build(self, src, filename, reporter=None)：主入口，C → IR 的完整流程：
- 24–28 行：可选的 reporter 是文档生成器（生成编译报告 HTML），有的话先输出一个"H2 级标题：C builder"和欢迎信息；

- 29–30 行：取出方言名（coptions["std"]）并打日志；

- 32 行：创建 CContext(coptions, arch_info)——上下文对象，持有类型系统、sizeof()、eval_expr()、字段偏移计算等能力，是语义层和代码生成器共享的"公共数据区"；

- 33 行：调 _parse(src, filename, context) 拿到 AST（编译单元）；

- 35–38 行：如果有 reporter，用 utils.py 的 print_ast(compile_unit, file=f) 把 AST 打印成文本，作为"C-ast"一节塞进报告；

- 39–40 行：创建 CCodeGenerator(context)，调 gen_code(compile_unit) 遍历 AST 生成 IR，把 ir.Module 返回给调用者（调用者是 api.py 里的 ir_to_object 等流水线，随后是优化、指令选择、汇编、链接）。

### 42–45 行 _create_ast(self, src, filename)
小工具方法，转调模块级 create_ast（用自己保存的 arch_info 和 coptions），方便其他代码从 CBuilder 实例直接拿 AST。

## 模块级函数

### 48–56 行 parse_text(text, arch="x86_64")：

从字符串解析出 AST 的便捷入口（测试、文档示例、REPL 常用）。做的事就是：把字符串包成 io.StringIO（模拟文件对象）→ get_arch(arch).info 取架构信息 → 建 COptions 和 CContext → 调 _parse，文件名给个 "?" 占位。

### 59–64 行 create_ast(src, arch_info, filename="<snippet>", coptions=None)：

与上面几乎一样，只是架构信息由调用者传入（不查全局注册表），coptions 可空。返回 AST。

### 67–74 行 _parse(src, filename, context)：
全文件的核心，四条装配指令：

```python
def _parse(src, filename, context):
    preprocessor = CPreProcessor(context.coptions)      # ① 组件一：预处理+词法
    tokens = preprocessor.process_file(src, filename)   #    文本 → Token 迭代器
    semantics = CSemantics(context)                     # ② 组件二：语义层
    parser = CParser(context.coptions, semantics)       # ③ 组件三：语法层（上一讲的文件）
    tokens = prepare_for_parsing(tokens, parser.keywords)  # ④ 适配层
    ast = parser.parse(tokens)                          # ⑤ 跑起解析器
    return ast

```

每一行都是一次"组件接线"（详见第三节的联动分析）。

### 77–102 行 parse_type(text, context, filename="foo.c")：

只解析一个"类型名"（如 int[2]、struct S *），不解析完整程序。用途是让库使用者/测试计算类型大小（docstring 里的例子：parse_type('int[2]', context) 然后 context.eval_expr(ast.size) 得 2、context.sizeof(ast) 得 4）。

实现上的两个细节：
- 93–94 行：src = io.StringIO(text + ";")——在类型名后硬补一个分号（TODO 注释承认这是 hack）：parse_typename 解析完类型规格后需要一个终止符，int[2] 的 ] 之后没有东西，补 ; 让解析能干净收尾；

- 100–102 行：跳过 parser.parse() 完整入口，直接 parser.init_lexer(tokens) 装好 Token、parser.typedefs = set() 清空 typedef 集合，然后直接调 parser.parse_typename()——这是 builder 与 parser 的另一个精细联动点：复用 parser 的初始化逻辑，但只跑"解析类型名"那一个方法。

## 三、与 parser.py 怎么联动？
两个文件的协作关系可以概括为：builder 负责"接线"，parser 负责"干语法活"，semantics 负责"干语义活"。

具体的接线点有 4 个：

### 联动点 1：keywords 从 parser 流向适配层（builder.py:72）
```python
tokens = prepare_for_parsing(tokens, parser.keywords)

```
parser.keywords 是 CParser.__init__ 里拼出来的关键字集合（parser.py:76-113）。

它被传给 prepare_for_parsing（preprocessor.py:1367），做三件事：
- 去掉空白 Token；

- 把 ID 转成关键字——C 词法器自己不认关键字（C 的关键字与标识符词法形式相同），所有 int、return 都以 ID 形式从 lexer 出来；这里对照 parser.keywords 把 ID("int") 的 typ 改成 "int"，之后 parser 里 self.peek in self.type_specifiers、self.peek == "return" 之类的判断才成立；

- 拼接相邻字符串字面量（C 允许 "ab" "cd" 等价于 "abcd"）。

也就是说：parser 定义"哪些词是关键字"，builder 在进 parser 之前按这个定义改造 Token 流。

### 联动点 2：semantics 对象被注入 parser（builder.py:70-71）
```python
semantics = CSemantics(context)
parser = CParser(context.coptions, semantics)

```

CSemantics 是 CParser 构造函数的第二个参数（parser.py:51）。

上一讲说过，parser 自己不构造任何类型/声明对象，而是把一切"真正干活"的事通过 self.semantics.on_xxx(...) 回调出去。

这里的接线意味着：parser 每喊一声 on_binop(a, "+", b)，实际执行的是 semantics.py:30 CSemantics 的对应方法，由它查符号表、算类型、构造 AST 节点。

### 联动点 3：parser 的返回值其实是 semantics 的产物
parser.py:161-171 里 parse(tokens) 最后 return cu，而 cu 来自 parse_translation_unit() 里的 self.semantics.finish_compilation_unit()（parser.py:178）。

所以 _parse 里的 ast = parser.parse(tokens) 拿到的这颗 AST 是 CSemantics 在解析过程中逐步累积、最后打包出来的编译单元（含所有顶层 CDeclaration）。

builder 拿到它之后，顺手交给 print_ast（报告用）和 CCodeGenerator.gen_code。

### 联动点 4：builder 与 parser 的方法级复用（builder.py:100-102）
parse_type 不跑 parser.parse()，而是手动执行 parser 的"开机三件套"（init_lexer、typedefs = set()——对照 parser.py:167-168）后直接调用 parser.parse_typename()。

这是 builder 把 parser 当成"可部分使用的组件"来用的例子。

#### 整体数据流图
```python
CBuilder.build(src, filename)
 ├─ CContext(coptions, arch_info)            ← 公共数据区：类型系统 / sizeof / eval_expr
 ├─ _parse(src, filename, context)
 │   ├─ CPreProcessor.process_file()          ← 文本 → Token 流
 │   ├─ CSemantics(context)                   ← 语义层（AST 的真正构建者）
 │   ├─ CParser(coptions, semantics)          ← 语法层（递归下降，on_* 回调）
 │   ├─ prepare_for_parsing(tokens, parser.keywords)
 │   └─ parser.parse(tokens) → CompilationUnit（= AST）
 └─ CCodeGenerator(context).gen_code(compile_unit)
      ├─ 按声明类型分流：Typedef/枚举常量跳过；变量 → gen_global_variable；函数 → create_function + gen_function
      ├─ 语句用 fn_map 分发表：gen_if / gen_while / gen_for / gen_return ...
      ├─ 表达式按节点类型分发：gen_binop / gen_unop / gen_call / gen_variable_access ...
      └─ 返回 ir.Module

```
其中 CCodeGenerator.gen_code 的行为（codegenerator.py:71-109）值得一提：

它把 compile_unit.declarations 分成函数和全局变量两堆，先创建全部函数签名/全局变量，再逐个生成函数体（支持 C 里函数互相调用而不必前向声明严格有序）。

## 四、详细例子：完整走一遍
以这段代码为例：
```python
int add(int a, int b) { return a + b; }

```

### 第 0 步：调用入口
```python
builder = CBuilder(get_arch('x86_64').info, COptions())
mod = builder.build(io.StringIO(src), 'demo.c')

```

build 先建 CContext：此时 x86_64 的 arch_info 告诉它 int 是 4 字节、指针 8 字节（后续 sizeof(int)、sizeof(int*) 都从这里查）。

### 第 1 步：预处理 + 词法（_parse 第 68–69 行）
CPreProcessor.process_file 对 demo.c 做注释剥离、宏展开（本例无宏）、词法切分，产出一个 Token 迭代器。

此时所有关键字都还是 ID：
```python
ID(int)  ID(add)  (  ID(int)  ID(a)  ,  ID(int)  ID(b)  )  {  ID(return)  ID(a)  +  ID(b)  ;  }

```

### 第 2 步：Token 适配（_parse 第 72 行）
prepare_for_parsing(tokens, parser.keywords) 对照 parser.keywords（含 "int"、"return" 等），把对应 ID 的 typ 改成关键字本身，并去掉空白 Token：

```python
int  ID(add)  (  int  ID(a)  ,  int  ID(b)  )  {  return  ID(a)  +  ID(b)  ;  }

```

### 第 3 步：语法 + 语义（_parse 第 73 行）
parser.parse(tokens) 开跑（上一讲讲过详细过程），parser 与 semantics 交替工作：

| parser 的动作 | 触发的 semantics 回调 | 产生的 AST 节点/类型 |
| :--- | :--- | :--- |
| `parse_decl_specifiers` 吃掉 int | `on_basic_type("int")` | `BasicType(INT)` |
| `parse_function_declaration` 中解析参数 | `on_function_argument(name, type)` | `ParameterDeclaration` |
| `parse_function_declaration` 整体 | — | `FunctionDeclaration` |
| `parse_expression` 解析标识符 | — | `VariableAccess` |
| `parse_expression` 解析二元运算 | `on_binop(op, left, right)` | `BinaryOperator` |
| `parse_return_statement` 里 `parse_expression` | — | `Return` |
| 顶层聚合 | — | `CompilationUnit` |

parser.parse 返回的 CompilationUnit 大致是（print_ast 打印出来会是这种结构）：

```python
CompilationUnit
  Declarations
    FunctionDeclaration: add
      FunctionType: int (int a, int b)
      Compound
        Return (a + b)

```

### 第 4 步：IR 生成（build 第 39–40 行）
CCodeGenerator(context).gen_code(compile_unit)：
- 把 add 分到 functions 列表；

- create_function：返回类型是 int → builder.new_function("add", GLOBAL, ir.i32)；

- gen_function_def：为参数 a、b 各 alloca 一块栈空间并 Store 实参；然后 gen_stmt 分发表命中 Return → gen_return → gen_expr 命中 

- BinaryOperator → gen_binop 发出加法指令 → emit_return；

- 返回 ir.Module("main")。

### 第 5 步：拿到结果——build 

把这个 ir.Module 返回给 api.py 的调用者，之后的优化、指令选择、汇编都与 C 前端无关了。

IR 示意：
```python
module main
function add(a: i32, b: i32) -> i32:
  <entry>: ...
  <block>:
    ret (a + b)

```

## 五、一句话总结
- builder.py = 装配车间：CBuilder.build（文本 → IR）、create_ast/parse_text（文本 → AST）、parse_type（类型片段 → 类型 AST）三个入口，共用核心函数 _parse；

- _parse 是唯一真正"组装"的地方：预处理器出 Token → 按 parser 的关键字表适配 Token → CParser（语法）+ CSemantics（语义）联合产出 AST → 交给 CCodeGenerator 出 IR；

- 与 parser.py 的联动就是四处：parser.keywords 喂给适配层、CSemantics 注入 parser 当回调对象、parser.parse() 的返回值实为 semantics 的编译单元、以及 parse_type 对 parser 的 init_lexer/parse_typename 方法级复用。