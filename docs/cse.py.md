# cse.py 详解
## 一、这个文件的作用是什么？
这是 qcc 的公共子表达式消除（Common Subexpression Elimination, CSE）pass——整个文件只有 30 行，是优化器里最小巧也最典型的一个。

要解决的问题：代码里同一表达式被重复计算（比如手写重复、或 codegenerator 模式化产出重复）。

CSE 把第二次出现的计算结果直接替换成第一次的结果，省掉重复计算：
```c
t1 = add a, b      →      t1 = add a, b
t2 = add a, b      →      （t2 被替换成 t1）
t3 = mul t1, t2    →      t3 = mul t1, t1

```
它继承上一讲的 BlockPass（transform.py:48），因此它的作用域是"单个基本块内"——是局部 CSE（local CSE），不是跨块的全局值编号（GVN）。

这个界限后面详述。

## 二、文件中的每个方法/每一行
### 类声明（5–8 行）

class CommonSubexpressionEliminationPass(BlockPass)——继承 BlockPass 意味着：run(ir_module) 时框架自动遍历"每个函数 → 每个基本块 → 调 on_block"。

它没有重写 __init__，直接用 ModulePass.__init__ 里按类名配置好的 self.logger。

#### on_block(self, block)（10–29 行）—— 唯一的实际逻辑
10 行：对一个基本块运行；11 行：ins_map = {}——本块的"已知表达式表"（键 → 最早产生该值的指令）。注意它在每次进入块时新建——这就是"局部"的由来：块 A 里见过的表达式不会带到块 B。

12 行：stats = 0 替换计数（打日志用）。

13 行：for i in block:——按指令顺序扫描块内每条指令（顺序很重要：先见到的先登记，后见到的被替换成先见到的）。

14–15 行：指令是 ir.Binop → 生成键 k = (i.a, i.operation, i.b, i.ty)——两个操作数（Value 对象）、运算符、结果类型的四元组。两个细节决定正确性：

操作数放进键的是 Value 对象本身（ir.py:233-236 的 Value 没有定义 __eq__/__hash__，Python 默认按对象身份比较）——在 SSA 语义下这是保守且正确的：两个不同的 add 指令即使数值结果可能相同也不会被误判相等；而同一个 Value 对象（同一名字）在函数内不可变，身份相等 ⇒ 值必然相等；
i.ty 也参与比较：同样的运算符和操作数组合如果结果类型不同（编译器不同阶段允许存在），绝不合并。
16–17 行：指令是 ir.Const → 键为 (i.value, i.ty)——常量去重：同一块里两个 const 3（i32）只留第一个。ty 参与比较避免了"i32 的 1"和"i64 的 1"被错误合并。

18–21 行：其他一切指令（load、call、phi、cast……）直接跳过——只处理"纯算术"和常量，绝不碰可能有副作用或地址敏感的指令。这里有个有趣的注释（19–20 行）：这个 else 分支其实会被执行（块里任何非 Binop/Const 指令都会走到），但覆盖率工具报告它为"未覆盖"，因为 Python 的窥孔优化器把这段字节码优化掉了——覆盖率的假阴性。

22–27 行：核心动作——键 k 在 ins_map 里出现过 → i.replace_by(ins_new)（把后出现指令的所有使用处替换成先出现的那个值，ir.py:269），stats += 1；没出现过 → ins_map[k] = i 登记（成为后续同键指令的替换目标）。

28–29 行：本块有替换发生就打一条 debug 日志："Replaced N instructions"。

一个设计要点：被替换的指令并不在这里删除——replace_by 只是让它们失去所有使用者。删除是同一遍流水线中排第 7 位的 DeleteUnusedInstructionsPass 的职责（与上一讲 RemoveAddZeroPass 的分工完全一致）。

## 三、通过什么方式使用 cse.py？
### 1. 作为 api.optimize() 流水线的一员（主要方式）
api.py:216-228 的 pass 列表里它排第 4 位：
```python
opt_passes = [
    Mem2RegPromotor(),          # 1. 先把内存变量提升成 SSA 值
    RemoveAddZeroPass(),        # 2. 代数恒等化简 (x+0, x*1)
    ConstantFolder(),           # 3. 常量折叠
    CommonSubexpressionEliminationPass(),  # 4. ← 本文件
    TailCallOptimization(),
    LoadAfterStorePass(),
    DeleteUnusedInstructionsPass(),         # 7. 删除被 replace_by 留下的孤儿指令
    CleanPass(),
] * 3
for opt_pass in opt_passes:
    opt_pass.run(ir_module)

```
排位逻辑：CSE 必须在 mem2reg 之后——SSA 化之前变量都是 load/store 形式，没有可比较的纯值表达式；也要在 DCE 之前——它制造的孤儿指令需要 DCE 清理。

列表 * 3 保证连跑三遍（后一遍能看到前一遍化简后暴露出的新公共子表达式）。

### 2. 继承链决定执行方式

与 RemoveAddZeroPass 同款（上上一讲讲过的模板方法链）：

```python
CommonSubexpressionEliminationPass().run(ir_module)   # api.py:229
  → FunctionPass.run (transform.py:33)：遍历每个函数
      → BlockPass.on_function (transform.py:51)：遍历每个块
          → on_block(block) (cse.py:10)：本文件的实际逻辑

```

### 3. 单独使用
它被导出在 qcc/opt/init.py:3（__all__ 第 21 行），任何持有 ir.Module 的代码都能直接跑：CommonSubexpressionEliminationPass().run(ir_module)。

## 四、详细例子
### 例 1：经典场景 (a + b) * (a + b)
```c
int f(int a, int b) {
    return (a + b) * (a + b);
}

```

#### 第 1 步：codegenerator + mem2reg 之后（alloc 已提升，a + b 被老老实实算了两遍）：
```c
function f(a: i32, b: i32) -> i32 {
  entry:
    jump block1
  block1:
    t1 = add a, b
    t2 = add a, b       ← 和 t1 完全相同的计算
    t3 = mul t1, t2
    return t3
}

```

#### 第 2 步：CommonSubexpressionEliminationPass 对 block1 调 on_block，逐条扫描：
| 指令 | 键 k | 处理 |
| :--- | :--- | :--- |
| t1 = add a, b | (a, "+", b, i32) | 表中没有 → ins_map[k] = t1 |
| t2 = add a, b | (a, "+", b, i32) | 命中 t1 → t2.replace_by(t1)；t3 = mul t1, t2 自动变成 t3 = mul t1, t1；stats = 1 |
| t3 = mul t1, t1 | (t1, "*", t1, i32) | 表中没有 → 登记 |


日志输出 Replaced 1 instructions。此刻的 IR：
```c
block1:
    t1 = add a, b
    t2 = add a, b       ← 已无人使用（孤儿）
    t3 = mul t1, t1
    return t3

```

#### 第 3 步：同一遍流水线里的 DeleteUnusedInstructionsPass 把孤儿 t2 删除，最终：
```c
function f(a: i32, b: i32) -> i32 {
  entry:
    t1 = add a, b
    t3 = mul t1, t1
    return t3
}

```
a + b 从算两遍变成算一遍。

### 例 2：常量去重（Const 键的用武之地）
```c
int g(int a, int b) {
    int x = 3;
    int y = 3;
    return a * x + b * y;
}

```
mem2reg 之后，codegenerator 给 x、y 各自发射了一个 const 3：
```c
block1:
    t1 = const 3
    t2 = const 3       ← 与 t1 完全相同的常量
    t3 = mul a, t1
    t4 = mul b, t2
    t5 = add t3, t4
    return t5

```

CSE 的 16–17 行规则：t1 键 (3, i32) 登记；t2 键 (3, i32) 命中 → t2.replace_by(t1)，t4 = mul b, t2 变成 t4 = mul b, t1。DCE 删掉 t2 后，两个常量只剩一个。

注意这与 ConstantFolder 的分工：折叠负责"算出来"，CSE 负责"同一个值只算/只存一份"——常量恰好是两者都会碰到的对象，但 CSE 还能处理常量折叠永远算不掉的 add a, b 这类含变量的表达式。

### 例 3：局部性的边界——跨块不合并
```c
int h(int a, int b, int c) {
    if (c) { return a + b; }
    else   { return a + b; }
}

```
mem2reg 后两个分支各有一个 add a, b，但它们在不同的基本块里：
```c
block1:
    cjmp c ? then_block : else_block
then_block:
    t1 = add a, b
    return t1
else_block:
    t2 = add a, b
    return t2

```

on_block(then_block) 和 on_block(else_block) 各自用全新的 ins_map，所以 t2 不会被替换成 t1——跨块的公共子表达式需要全局值编号（GVN）配合支配关系才能安全消除，那是这个 pass 能力之外的事。

理解这一点也就理解了它"局部 CSE"的定位。

### 例 4：为什么键里必须带 i.ty
若某块的 IR 是 t1 = const 1 (i32)、t2 = const 1 (i64)——如果键只含 value，t2 会被错误替换成 t1，把一个 64 位常量换成 32 位，类型系统立刻被破坏。

(i.value, i.ty) 里的 ty 挡住了这种错误合并（16–17 行）。

## 五、一句话总结
cse.py 是 qcc 的局部公共子表达式消除 pass：它继承 BlockPass，在每个基本块内维护一张"表达式 → 最早结果"的哈希表——Binop 以 (操作数对象, 运算符, 结果类型) 为键、Const 以 (值, 类型) 为键，后出现的同键指令被 replace_by 指向先出现者（操作数按 SSA 值对象身份比较，保守而正确；非纯指令一律不碰）；

孤儿指令由流水线里的 DCE 清理。它由 api.py:225 的 optimize() 以 run(ir_module) 形式调用，排在 mem2reg 与常量折叠之后、连跑三遍，把"重复计算"合并成"一次计算、多处引用"。