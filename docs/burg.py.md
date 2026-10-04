# burg.py 详解
## 一、这个文件的作用是什么？
BURG = Bottom-Up Rewrite Generator（自底向上重写生成器）——编译器界经典工具（源自 iburg/burg）的 qcc 实现。

docstring（3–57 行）给出了完整定义：
- 输入一份模式描述，输出一个能把树按模式匹配的匹配器。
- 生成的匹配器用动态规划寻找树的最佳匹配：分两步——label（自底向上遍历，给每个节点标注可匹配的规则和代价）和 select（再次遍历，每步选择到达目标最便宜的路径）。

它处理的"树"就是上一讲 irdag.py 拆出来的树森林里的 Tree（qcc/utils/tree.py）。规则长这样（docstring 12–17 行）：
```c
reg -> ADDI32(reg, reg) 2 (. add NT0 NT1 .)     ← 加法：树模式 ADDI32(reg,reg)，代价 2，模板"add 第0子 第1子"
reg -> MULI32(reg, reg) 3 (. mul NT0 NT1 .)     ← 乘法：代价 3
reg -> ADDI32(MULI32(reg, reg), reg) 4 (. muladd $1, $2, $3 .)  ← 乘加融合指令：一条顶两条

```

其中 reg 是非终结符（一棵子树"可计算进寄存器"的目标），ADDI32/MULI32 是终结符（具体的图节点）；

reg -> rc 这类是链规则（chain rule，让一个非终结符可由另一个非终结符派生）。

本文件在 qcc 里承担三重角色：
- 运行时数据模型：Rule/BurgSystem/Term/Nonterm——指令选择器在运行时持有的重写规则系统（instructionselector.py:155 的 TreeSelector 用的全部是它的数据结构与方法）；

- 规格解析器：BurgLexer/BurgParser 解析上面那种 .burg 文本格式（语法规则在 burg.grammar）；

- 代码生成器：BurgGenerator 从规格生成一个独立的 Python Matcher 类（docstring 说的"输出的 matcher class"），这是经典 BURG 的"编译"路径，可用 CLI 运行。

## 二、每个方法的作用
### 词法分析 BurgLexer（72–106 行）—— 解析 .burg 规格文本
75–84 行：token 规格——id（标识符）、kw（%keyword）、number（代价数字，转 int）、STRING（'...'，剥掉引号）、OTHER（: ; | ( ) , 标点）、SKIP（空格丢弃）；

86–106 行 tokenize：按 %% 分节——%% 之前的所有行收进 header 区（作为整体产出一个 header token，这些行会被原样抄进生成的代码，比如 import 语句）；%% 之后逐行交给基类词法器产出规则 token。

### 数据模型（109–263 行）
#### Rule（109–122 行）：
一条重写规则——nonterm（左部目标）、tree（树模式）、cost、acceptance（附加接受条件）、template（命中后要执行的代码）、nr（规则编号，用于状态表）。

#### Symbol/Term/Nonterm（125–137 行）：
符号三兄弟；Nonterm 额外带 chain_rules 列表（以它为左部的链规则）。

#### BurgSystem（140–259 行）—— 重写系统的核心容器：
- add_rule（149–163 行）：模板去空白、空模板补 pass；链规则识别（155–156 行）：树模式是"无子节点且名字是非终结符"（即 reg -> rc）→ 挂到该非终结符的 chain_rules 上；登记编号、按根节点名入 rule_map（同根节点的规则放一起，供按根快速查找）；

- get_rule(nr)（165–169 行）：按编号取规则（状态表里存的就是编号）；

- get_rules_for_root(name)（171–173 行）：取"根节点名为 name"的所有规则——TreeSelector.burm_label 每处理一个节点就调它；

- non_term（175–180 行）：第一个被定义的非终结符自动成为 goal（目标符号）——匹配成功与否就看根节点能否达成 goal；

- install（189–201 行）：符号表安装，Term/Nonterm 分记两个集合，并预建 rule_map[name] = []；

- tree_terminal_equal（206–222 行）—— 树与模式的结构匹配：终结符名字必须相同且子节点递归匹配；遇到非终结符位置即"开放端"（open end），无条件匹配——这是模式里 reg 能吞下任意子树的原因；
- get_kids(tree, template_tree)（224–233 行）：收集匹配成功后"开放的子树"——即模式里非终结符位置对应的真实子树（生成代码时 NT0/NT1 引用的就是它们）；

- get_nts(template_tree)（235–244 行）：模式里非终结符的名字列表（对应"每个开放端必须达成哪个目标"）；

- check_tree_defined/check（246–259 行）：自检——规则树里的所有名字都必须已定义（TODO：还缺"所有可能的输入都能被覆盖"的完备性检查）。

### 解析器 BurgParser（266–272 行）
继承由 burg.grammar 自动生成的解析器（68–69 行 yacc.load_as_module 动态生成），parse(lexer) 时新建 BurgSystem、把规则灌进去后返回。

### 代码生成器 BurgGenerator（275–405 行）—— 经典 BURG 输出
#### generate(system, output_file)（280–332 行）：生成一个完整 Python 模块：
输出 Matcher(BaseMatcher) 类骨架（286–289 行），__init__ 里建三张表——kid_functions（取开放子树的 lambda，298–307 行）、nts_map（开放端目标名）、pat_f（规则 → 模板函数指针）；

309–326 行：为每条规则生成 P{编号} 模板函数（把模板里的语句按 ; 拆行写入函数体），有 acceptance 的再生成 A{编号} 检查函数；

328–332 行：生成入口 gen(tree)——burm_label(tree) 标注；has_goal(goal) 检查覆盖（没覆盖抛 "Tree not covered"）；apply_rules(tree, goal) 递归选规则出代码。

#### emit_record(rule, state_var)（334–360 行）—— 生成 label 阶段单条规则的代价计算代码：
检查各开放子树都已达成目标（has_goal）+ 满足 acceptance → c = Σ 子树代价 + 规则代价 → set_cost(non_term, c, rule_nr)；

随即把所有链规则也按加算后的代价写入（354–360 行）——链规则传播是实现"reg 可达即其他非终结符可达"的关键。

#### emit_state（362–368 行）：
生成 burm_state——给节点挂新的 State 对象，然后按终结符分类发射各规则匹配（emitcase）。

#### emitcase（370–375 行）：
对根节点名等于该终结符的每条规则，生成 if 条件: + emit_record。

#### compute_kids（377–390 行）：
递归扫描模式树，找到所有开放端（非终结符位置），生成访问表达式（t.children[0].children[1] 之类）和目标名列表。

#### emittest（392–405 行）：
生成树模式的结构条件，如 tree.name == "ADD" and (tree.children[0].name == ...)——终结符逐层比名字，非终结符位置不比较（开放）。

### CLI（408–440 行）
make_argument_parser + main：python -m qcc.codegen.burg 规格文件.burg -o 匹配器.py——读规格 → BurgLexer + BurgParser 解析成 BurgSystem → BurgGenerator.generate 写出 Matcher 模块。

## 三、在 qcc 里怎么被使用（与指令选择的联动）
有趣的是：qcc 目前用的是"解释路径"，而 BurgGenerator 的"编译路径"作为独立工具保留。

联动链条：
```python
arch/isa.py:63-84  @isa.pattern(non_term, tree, condition, size, cycles, energy) 装饰器
   └─ 架构作者把指令模式写成 Python 装饰器，树用 from_string 解析（与 .burg 同一种括号记法）
       例如（示意）：@isa.pattern("reg", "ADDI32(reg, reg)", size=2) def pat_add(...)

codegen/instructionselector.py: __init__ (280-289 行)
   └─ for pattern in arch.isa.patterns:
        self.sys.add_rule(pattern.non_term, pattern.tree, cost, pattern.condition, pattern.method)
   └─ self.sys.check()
   └─ self.tree_selector = TreeSelector(self.sys)     ← self.sys 就是 BurgSystem 实例！

TreeSelector (instructionselector.py:155-232) —— burg.py 算法的"运行时解释版"
   ├─ burm_label(tree)：自底向上，对每节点 get_rules_for_root(tree.name) + tree_terminal_equal
   │     + 子目标可达检查 → set_cost（含链规则传播 mark_tree）
   └─ apply_rules(context, tree, "stm")：从 goal 出发选最便宜规则，get_kids/get_nts 递归
         → 调 rule.template（架构的 Python 函数）→ context.emit(机器指令)

```

即：burg.py 的 BurgSystem/Rule/tree_terminal_equal/get_kids/get_nts/chain_rules_for_nt 是 TreeSelector 的运行时引擎；

docstring 里 label/select 两步算法在 burm_label/apply_rules 中逐字实现；

BurgGenerator 生成的是同样的算法（burm_label + apply_rules 方法的 Matcher 类），供想离线生成匹配器的人用 CLI 编译。

再接上两讲：irdag 拆树 → TreeSelector.gen(context, tree)（instructionselector.py:408）→ burg 规则匹配 → 架构指令。

## 四、详细例子
### 第 1 步：一套规则（用 docstring 的记法）
```c
%%
reg -> CONSTI32 1            (. mov NT0 .)
reg -> ADDI32(reg, reg) 2    (. add NT0 NT1 .)
reg -> MULI32(reg, reg) 3    (. mul NT0 NT1 .)
reg -> ADDI32(MULI32(reg, reg), reg) 4  (. muladd NT0 NT1 NT2 .)

```
对应 BurgSystem 状态：goal = reg（第一条规则定义的左部）；rule_map = {"CONSTI32": [r1], "ADDI32": [r2, r4], "MULI32": [r3]}；无链规则。

### 第 2 步：输入树
来自 irdag 拆出来的某棵树（略去叶子常量节点名）：
```c
t0 = MULI32(
       ADDI32(CONSTI32, CONSTI32),
       CONSTI32
     )

```

### 第 3 步：label 阶段（自底向上，burm_label）
| 节点 | 检查 | 结果 |
| :--- | :--- | :--- |
| 左下 `CONSTI32` | 根名命名 r1；无开放端、无 acceptance → 成立 | state[reg] = (cost=1, rule=1) |
| 右下 `CONSTI32` | 同左 | state[reg] = (1, 1) |
| 顶层右 `CONSTI32` | 同左 | state[reg] = (1, 1) |
| `ADDI32(...)` | r2：开放端是两个 `reg`，两个子节点都 `has_goal("reg")` ✓ → cost = 1+1+2 = 4；r4 不匹配（左子不是 MULI32） | state[reg] = (4, 2) |
| 顶层 `MULI32(...)` | r3：开放端两个 `reg`，左子 cost 4、右子 cost 1 → cost = 4+1+3 = 8 | state[reg] = (8, 3) |

### 第 4 步：select 阶段（apply_rules(tree, "reg")）
从目标 reg 出发，顶层 state[reg] 选中 r3（代价 8）→ 模板 mul NT0 NT1 → 沿 get_kids 递归两个开放子树：
- 左子树 ADDI32 的 state[reg] 选 r2 → add NT0 NT1 → 再递归两个 CONSTI32 各选 r1 → mov NT0；
- 右子树 CONSTI32 选 r1 → mov NT0。

#### 产出的指令序列（示意）：
```c
mov  r1, c1
mov  r2, c2
add  r3, r1, r2      ; ADDI32 子树
mov  r4, c3
mul  r5, r3, r4      ; 顶层 MULI32

```

### 第 5 步：动态规划的价值——乘加融合
把输入树换成 ADDI32(MULI32(a, b), c)：
- 方案 A（逐条）：MUL（r3，cost 3）+ ADD（r2，cost 2）= 5；

- 方案 B（融合指令）：r4 muladd 一条 = 4。

### 第 6 步：BurgGenerator 的编译路径输出骨架（示意）

对同一规格，python -m qcc.codegen.burg spec.burg -o matcher.py 生成的 Matcher 大致是：

```python
class Matcher(BaseMatcher):
    def __init__(self):
        self.kid_functions = {1: lambda t: [t], 2: lambda t: [t.children[0], t.children[1]], ...}
        self.nts_map = {1: [], 2: ["reg", "reg"], ...}
        self.pat_f = {1: self.P1, 2: self.P2, ...}
    def P2(self, tree, c0, c1):
        add c0, c1            # ← 规格模板里的语句
    def burm_state(self, tree):
        tree.state = State()
        if tree.name == "CONSTI32":
            nts = self.nts(1); kids = self.kids(tree, 1)
            if all(...): c = ... ; tree.state.set_cost("reg", c, 1)
        if tree.name == "ADDI32": ...  # r2、r4 两条
    def gen(self, tree):
        self.burm_label(tree)
        if not tree.state.has_goal("reg"): raise Exception("Tree not covered")
        return self.apply_rules(tree, "reg")

```

与 TreeSelector 的解释版逐行对应——同一个算法，一份跑在"读规则"里，一份跑在"生成的代码"里。

## 五、一句话总结
burg.py 是 qcc 的 BURG 实现：自底向上重写规则系统 + 动态规划树匹配。它定义规则与重写系统的数据模型（Rule/BurgSystem，含链规则、开放端收集 get_kids/get_nts、结构匹配 tree_terminal_equal）

提供 .burg 规格的词法/语法解析（BurgLexer/BurgParser，按 %% 分 header 与规则区），

并可通过 BurgGenerator 离线生成独立 Matcher 类（label + select 两阶段）。

在当前 qcc 中，架构以 @isa.pattern 装饰器声明模式，InstructionSelector1 把它们灌进 BurgSystem，TreeSelector 以解释方式执行 label/select 算法——把 irdag 产出的树"铺满"架构指令模式并挑出总代价最小的覆盖，这是指令选择器动态规划核心的载体。