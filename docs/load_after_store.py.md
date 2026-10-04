# load_after_store.py 详解
## 一、这个文件的作用是什么？
这是 qcc 的 store/load 转发与冗余存储消除 pass，专门收拾 mem2reg 之后残留在基本块内的内存操作。

背景：mem2reg 只提升"纯局部变量"（alloc 仅被 load/store 使用的那种）。

全局变量、数组元素、结构体字段、指针指向的内存提升不了，仍然以 store/load 形式留在 IR 里。

本 pass 在单块内做两类化简（docstring 6–20 行给出了第一类的例子）：

### load-after-store 转发：刚 store 过的地址立刻 load → 直接用存进去的值，省一次读：
```c
[x] = a          →       [x] = a
b = [x]          →       （b 的使用处直接换成 a）
c = b + 2        →       c = a + 2

```

### 冗余 store 消除（死存储消除）：同一地址连续两次 store、中间没人读 → 删掉第一次：
```c
[x] = a          →       [x] = b
[x] = b          →

```

它是 BlockPass（上一讲框架的第三级），作用域同样是单个基本块内；同时刻意避开 volatile 访问（volatile 的语义就是"每次必须真实访存"，绝不能被优化掉，见 51、72 行的 not ins.volatile 过滤）。

## 二、每个方法的作用
### find_store_backwards(self, i, ty, stop_on=(ir.FunctionCall, ir.ProcedureCall, ir.Store))（22–40 行）—— 核心查找：从指令 i 向前（块首方向）找"最近一次同地址 store"

26–28 行：取 i 所在块、指令列表、i 的下标 pos；

29 行：for x in range(pos - 1, 0, -1)——从 i 的前一条指令往前扫。注意一个细节：range(..., 0, ...) 的右端是开区间，下标 0（块内第一条指令）永远不会被检查到——这是个小瑕疵：位于块首的 store 会被错过（保守方向上的遗漏，不产生错误结果）；

31–36 行：扫到的第一条 ir.Store（值类型 ty is i2.value.ty 匹配时）：
	- 地址相同（i2.address is i.address）→ 返回这条 store——这就是"最近一次写同一地址"；
	- 地址不同 → 直接返回 None——保守放弃。为什么不再往前找更早的同地址 store？因为中间隔着一条"可能别名的写"（qcc 没有别名分析，p 和 q 是否指向同一内存无法证明），跨过它做转发可能读错值；

37–39 行：扫到 stop_on 里的指令（默认是函数调用/过程调用/store）→ 返回 None。调用能写任意内存，所以调用之后再往前的 store 状态不可信。

这个方法的"找到最近 store"策略 + 三类"stop"条件（不同地址的 store、调用、volatile 在调用方过滤）共同保证了转发只看得到必然正确的机会。

### on_block(self, block)（42–44 行）—— pass 对单块的入口
按顺序执行两个阶段：先 replace_load_after_store，再 remove_redundant_stores。

顺序有意义：先把能转发的 load 换成纯值，之后判断"两个 store 之间有没有人读"时才不会误删（被转发的 load 已消失，但"是否曾存在读"在第二阶段扫描时仍以指令形式可见——实际第二阶段用 stop_on 里的 ir.Load 挡住）。

### replace_load_after_store(self, block)（46–67 行）—— 阶段一：load 转发成 store 的值
48–52 行：收集块内所有非 volatile 的 ir.Load；

56–63 行：对每个 load，find_store_backwards(load, load.ty) 找最近同地址 store；找到则：
	- 61 行 assert load.ty is store.value.ty——类型一致性自检（理论上由查找条件保证）；
	- 62 行 load.replace_by(store.value)——把该 load 的所有使用处替换成 store 存进去的值（b = [x] 之后的 c = b + 2 变成 c = a + 2）。被替换的 load 沦为孤儿，交给流水线里的 DCE；

64–65 行：TODO 注释——作者在犹豫替换后是否需要重新取指令列表（实际上 replace_by 不移除指令，列表仍然有效）；

66–67 行：有替换就记一条 debug 日志。


### remove_redundant_stores(self, block)（69–88 行）—— 阶段二：删掉"没人读就再被覆盖"的死 store
71–73 行：收集块内所有非 volatile 的 ir.Store；

75 行：count = 0——注意它在循环里从未递增，所以 87–88 行的日志永远不会触发（一个无害的小 bug，删除动作本身是有效的）；

76 行：TODO 注释（作者备注"假设 volatile 存储总是发生"之类的心得）；

78–85 行：对每条 store，用 find_store_backwards 向前找"最近同地址 store"，但 stop_on 多了一项 ir.Load——含义：如果两次 store 之间夹着一次同地址（或可能别名）的 load，第一次 store 的值可能被读走，不能删；只有当中间只有纯算术、且前面的 store 非 volatile 时，store_prev.remove_from_block() 删掉它。

一个直观的不变量："两次 store 之间无 load、无调用" ⟺ "第一次 store 的值没有任何观察者" ⟹ 删除安全。

## 三、通过什么方式使用 load_after_store.py？
### 1. 作为 api.optimize() 流水线的一员（主要方式）
api.py:216-228 中它排第 6 位：
```python
opt_passes = [
    Mem2RegPromotor(),          # 1. SSA 化（提升不掉的留内存形式）
    RemoveAddZeroPass(),        # 2.
    ConstantFolder(),           # 3.
    CommonSubexpressionEliminationPass(),  # 4. 局部 CSE
    TailCallOptimization(),     # 5.
    LoadAfterStorePass(),       # 6. ← 本文件
    DeleteUnusedInstructionsPass(),  # 7. 删除被 replace_by/remove 留下的孤儿
    CleanPass(),                # 8.
] * 3
for opt_pass in opt_passes:
    opt_pass.run(ir_module)

```
排位逻辑值得细品：
- 必须在 mem2reg（第 1）之后——mem2reg 先把该提升的都提升掉，剩下的内存操作才轮到它管；
- 在 CSE（第 4）之后——CSE 先把"计算同一地址的两次指针算术"合并（见例 3），本 pass 才能看到"同地址"的 store 和 load；
- 在 DCE（第 7）之前——它用 replace_by 制造的孤儿指令需要 DCE 收尸。

### 2. 继承链决定执行方式
```python
LoadAfterStorePass().run(ir_module)         # api.py:229
  → FunctionPass.run (transform.py:33)：遍历每个函数
      → BlockPass.on_function (transform.py:51)：遍历每个基本块
          → on_block (load_after_store.py:42)：
               replace_load_after_store → remove_redundant_stores

```

### 3. 单独使用
from qcc.opt.load_after_store import LoadAfterStorePass（api.py:46 就是这么导入的），直接 LoadAfterStorePass().run(ir_module) 即可。


## 四、详细例子
### 例 1：load-after-store 转发（对应 docstring 场景）
```c
int g;
int f(int x) {
    g = x + 1;      // store
    int y = g;      // 紧跟的 load
    return y * 2;
}

```

### 第 1 步：codegenerator + mem2reg 之后的 IR（g 是全局变量，mem2reg 不碰；y 已被提升）：
```c
function f(x: i32) -> i32 {
  block1:
    t1 = add x, 1
    store t1, g
    t2 = load g
    t3 = mul t2, 2
    return t3
}

```

### 第 2 步：replace_load_after_store：
收集非 volatile load：[t2]；

find_store_backwards(t2, i32)：从 t2 往前扫——store t1, g：是 Store ✓、值类型 i32 is t1.ty ✓、地址 g is g ✓ → 返回该 store；

t2.replace_by(t1) → t3 = mul t1, 2；日志 "Replaced 1 loads after store"。

```c
block1:
    t1 = add x, 1
    store t1, g
    t2 = load g        ← 孤儿
    t3 = mul t1, 2
    return t3

```

### 第 3 步：同一遍流水线的 DCE 删除孤儿 t2。

最终：
```c
block1:
    t1 = add x, 1
    store t1, g
    t3 = mul t1, 2
    return t3

```

## 例 2：冗余 store 消除
```c
int g;
void f(int a, int b) {
    g = a;
    g = b;      // 第一个 store 没有任何观察者
}

```

```c
block1:
    store a, g
    store b, g

```
remove_redundant_stores：对 store b, g 调 find_store_backwards（stop_on 含 ir.Load）：往前第一条 store 是 store a, g，同地址、中间无 load/调用、非 volatile → store a, g 被删除。结果只剩 store b, g。

### 例 3：与 CSE 的跨 pass 配合（数组场景）
```c
int arr[10];
int f(int i) {
    arr[i] = 3;
    return arr[i];
}

```
codegenerator 产出两次独立的指针算术（p1 = arr + i*4 与 p2 = arr + i*4）：
```c
block1:
    p1 = arr + i*4
    store 3, p1
    p2 = arr + i*4       ← 与 p1 相同的计算
    t = load p2
    return t

```
单靠本 pass 不行——p1 与 p2 是两个不同的 Value 对象，store 和 load 的地址不同，转发失败。

但流水线里 CSE 排在第 4 位、本 pass 在第 6 位：CSE 先把 p2.replace_by(p1)，IR 变成 store 3, p1; t = load p1，于是本 pass 顺利把 t 转发成 3，最终 return 3。

这正是 pass 排序设计的价值体现。

## 例 4：被 stop 条件保守挡住的场景
```c
int g;
void f(int a) {
    g = a;
    foo();      // 调用可能修改 g
    return g;
}

```
return g 的 load 往前扫：先遇到 call foo（在 stop_on 里）→ 返回 None → load 保留。若强行转发成 a，当 foo 改了 g 时结果就错了——保守放弃是正确的。

```c
int arr[10];
void f(int i) {
    arr[i] = 1;
    int t = arr[i];   // 中间有 load，挡住死 store 消除
    arr[i] = 2;
}

```
对最后的 store 2 找前序 store 时，中间夹着 load（stop_on 含 ir.Load）→ 第一次 store 保留——因为 t 真的读了它。

## 五、一句话总结
load_after_store.py 是 qcc 的块内内存访问优化 pass：它以 find_store_backwards 为核心——从某条指令向前找"最近一次同地址 store"，遇到不同地址的 store、函数调用（乃至 load）就保守停下；

阶段一 replace_load_after_store 把"刚 store 就 load"的 load 直接替换成存进去的值，阶段二 remove_redundant_stores 把"两次 store 之间无人读"的前一次 store 删除；volatile 访问一律不碰。

它由 api.py:227 的 optimize() 以 run(ir_module) 形式调用，排在 mem2reg 与 CSE 之后、DCE 之前，专门清理那些 mem2reg 提升不掉的全局/数组/结构体内存访问。