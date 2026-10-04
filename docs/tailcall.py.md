# tailcall.py 详解
## 一、这个文件的作用是什么？
这是 qcc 的尾调用优化（Tail Call Optimization, TCO）pass。

它解决的是递归函数的一个经典性能问题：
	- 当函数最后做的一件事是"调用自身并把结果直接 return"时，这次调用其实不需要保留当前栈帧——可以直接跳回函数开头，把递归变成循环。

docstring（5–55 行）用 fib 的例子把改写前后画得很清楚：
```c
// 改写前（递归，每次调用都开新栈帧）       // 改写后（循环，栈帧不再增长）
i32 fib(i32 n) {                            i32 fib(i32 n) {
  block0:                                     block3: {          // entry
    cjmp n > 0 ? block1 : block2                jmp block0
  block1:                                     }
    return call fib(n - 2)                    block0: {
  block2:                                       n_phi = {block3: n, block1: n2}
    i32 c = 1;                                  cjmp n_phi > 0 ? block1 : block2
    return c;                                 }
}                                             block1: {
                                                n2 = n_phi - 2
                                                jmp block0
                                              }
                                              block2: {
                                                i32 c = 1;
                                                return c;
                                              }
                                            }

```
"return call" 组合被替换成"jump 到函数头"，递归参数通过 Phi 节点传递。

实现上它继承 FunctionPass（上一讲框架里的第二级），只覆盖 on_function。

## 二、每个方法的作用
### on_function(self, function)（57–71 行）—— 第一步：检测尾调用
59 行：tail_calls = [] 收集器；

60 行：遍历函数的每个基本块；

61–68 行：判断一个块是否以尾调用收尾，五个条件必须同时满足：
	- len(block) >= 2——块里至少两条指令（61–62 行）；
	- isinstance(block[-1], ir.Return)——最后一条是 return（63 行）；
	- isinstance(block[-2], ir.FunctionCall)——倒数第二条是函数调用（64 行）；
	- block[-2] is block[-1].result——return 的正是这条调用指令的结果（65 行）。这是"尾位置"的本质判断：调用结果除了被 return 之外没有任何其他用途（如果有其他使用，block[-1].result 就不会是这个调用本身）；
	- block[-2].callee is function——被调用的必须是函数自己（66 行，对象身份比较）。所以这个 pass 只优化直接自尾递归；函数指针间接调用、A↔B 互递归都不在射程内（后者的调用对象不是本函数）；

68 行：命中的块把 (return指令, call指令) 对收进列表；

70–71 行：没有任何尾调用就什么都不做（早退，避免无谓改动）。

### _replace_entry(self, function)（73–94 行）—— 第二步：改造函数入口为"循环头"
75–76 行：z = []; z.append((function.entry, function.arguments))——死代码（写了没用，历史遗留）；

77 行：创建新块 new_entry；

78–79 行：add_block 把它追加到块列表末尾，然后 blocks.insert(0, blocks.pop()) 把最后一块挪到最前——保持"入口块排第一"的布局约定；

80–81 行：old_entry = function.entry（旧入口），function.entry = new_entry（新入口，ir.py:366 里 entry 就是个普通属性，直接改）；

82 行：new_entry 里放一条 Jump(old_entry)——所有初始进入都跳去旧入口；

85–93 行：为每个参数建一个 Phi 节点，插在 old_entry（现在成为循环头）的开头：
	- 89 行 argument.replace_by(arg_phi)——函数体内所有对参数的引用都改指向 Phi（比如 cjmp n > 0 变成 cjmp n_phi > 0）；
	- 93 行 arg_phi.set_incoming(new_entry, argument)——设置"从入口进来时"的初值分支：Phi 取原始参数；

94 行：返回 (old_entry, arg_phis) 供下一步使用。

这一步的效果：函数体不再直接用参数，而是用"Phi 选出来的当前值"——初值来自入口，后续迭代值来自尾调用点。

### rewrite_tailcalls(self, function, tail_calls)（96–112 行）—— 第三步：call+return 改成 jump
98 行：先做入口改造，拿到 old_entry 和参数 Phi 列表；

101–104 行：对每个尾调用，取出它所在块；

105–108 行：删掉 Return 和 FunctionCall 两条指令，换一条 Jump(old_entry)——"调用并返回"变成"跳回循环头"；

110–112 行：给每个参数 Phi 添加本块作为入口，入口值是这次"调用"的实参：arg_phi.set_incoming(block, arg_value)——这样跳回 old_entry 时，Phi 选中的就是"递归调用"传的新参数。

正确性来源：SSA 语义下，被删掉的 call 的实参都是已在该块内先计算好的纯值（比如 n2 = sub n_phi, 2 仍在块里，位于 Jump 之前），删掉 call 不会丢任何计算，只是把"值传给新栈帧的参数"改成"值沿 Phi 边流回循环头"。

## 三、通过什么方式使用 tailcall.py？
### 1. 作为 api.optimize() 流水线的一员（主要方式）
api.py:216-228 里它排第 5 位：
```python
opt_passes = [
    Mem2RegPromotor(),          # 1. SSA 化（Phi 语义就绪）
    RemoveAddZeroPass(),        # 2. 恒等化简
    ConstantFolder(),           # 3. 常量折叠
    CommonSubexpressionEliminationPass(),  # 4. 局部 CSE
    TailCallOptimization(),     # 5. ← 本文件
    LoadAfterStorePass(),       # 6.
    DeleteUnusedInstructionsPass(),  # 7.
    CleanPass(),                # 8. CFG 整形（合并多余块）
] * 3
for opt_pass in opt_passes:
    opt_pass.run(ir_module)

```

排位逻辑：必须在 mem2reg 之后——本 pass 要插入/修改 Phi 节点，这要求 IR 已经是 SSA 形态；

在 CleanPass 之前——它制造出的 new_entry（只含一条 Jump）和多余边由后续 CFG 整形消化。

三遍迭代让"改写后新暴露的尾调用"（如内层函数被化简后）也能被抓住。

### 2. 继承链决定执行方式
```python
TailCallOptimization().run(ir_module)      # api.py:229
  → FunctionPass.run (transform.py:33)：遍历模块每个函数
      → on_function(function) (tailcall.py:57)：
            检测 tail_calls → rewrite_tailcalls → _replace_entry

```
### 3. 单独使用
from qcc.opt.tailcall import TailCallOptimization 后可直接 TailCallOptimization().run(ir_module)——测试、实验或自定义流水线均可。

## 四、详细例子：fib 完整走一遍
对应 C 代码：
```c
int fib(int n) {
    if (n > 0) return fib(n - 2);
    return 1;
}

```

### 第 1 步：进入 pass 前的 IR
（codegenerator 产出、mem2reg 提升后，块 0/1/2 与 docstring 一致）：
```c
function fib(n: i32) -> i32 {
  block0:
    cjmp n > 0 ? block1 : block2
  block1:
    n2 = sub n, 2
    t = call fib(n2)      ← 尾调用
    return t
  block2:
    c = const 1
    return c
}

```

### 第 2 步：on_function 检测
block0：末尾不是 Return → 否；

block1：len == 3 ✓；block[-1] 是 return t ✓；block[-2] 是 t = call fib(n2) ✓；block[-1].result is block[-2]（return 的值正是调用结果 t）✓；block[-2].callee is function（fib 调用 fib 自己）✓ → 收进 tail_calls = [(return t, call fib(n2))]；

block2：末尾是 Return 但倒数第二条是 const，不是调用 → 否。

### 第 3 步：_replace_entry 改造入口
建 new_entry，块序变为 [new_entry, block0, block1, block2]（79 行的"末块挪到队首"）；

function.entry = new_entry，new_entry 里加 jump block0；

为参数 n 建 n_phi = phi (i32) 插到 block0 开头；n.replace_by(n_phi) → cjmp n > 0 变成 cjmp n_phi > 0；n_phi.set_incoming(new_entry, n)（入口分支）。

### 第 4 步：rewrite_tailcalls 改写块 1
删 return t、删 t = call fib(n2)，加 jump block0；

n_phi.set_incoming(block1, n2)（回边分支）。

### 第 5 步：最终 IR
```c
function fib(n: i32) -> i32 {
  new_entry:
    jump block0
  block0:
    n_phi = phi [new_entry: n, block1: n2]
    cjmp n_phi > 0 ? block1 : block2
  block1:
    n2 = sub n_phi, 2
    jump block0
  block2:
    c = const 1
    return c
}

```
与 docstring 的"改写后"形态逐字一致：return call fib(n-2) 变成了 jump block0，n 变成由 Phi 维护的循环变量。

语义完全等价，但执行时栈帧不再随递归深度增长——fib(10000) 从"爆栈"变成"跑一个循环"。

### 补充观察
多尾调用点：若两个分支都尾调（如 return f(a); ... return f(b);），每个尾调用块都会成为 n_phi 的一个入口，Phi 的入口数正好等于 new_entry + 尾调用块数，与 CFG 前驱数一致；

后续 pass 接力：new_entry 只剩一条 Jump，CleanPass 的 remove_empty_blocks 会把它合并（入口块受保护，但单前驱合并仍可优化边结构）；

局限性：互递归（f 尾调 g、g 尾调 f）和函数指针间接调用因 callee is function 身份检查而被保守地放过。

## 五、一句话总结
tailcall.py 是 qcc 的尾调用优化 pass：on_function 用五条精确的尾部判定（块以 Return 收尾、倒数第二条是 FunctionCall、return 的正是调用结果、且 callee 就是函数自身）找出所有自尾递归调用；

_replace_entry 新建入口块并给每个参数在旧入口放置 Phi（参数的所有使用改走 Phi）；rewrite_tailcalls 把每个"call + return"改写成"jump 回循环头 + 给 Phi 添回边"——把递归变成循环。

它由 api.py:226 的 optimize() 以 run(ir_module) 调用，排在 mem2reg 与 CSE 之后、随流水线连跑三遍，是优化链里唯一改变"函数调用结构"（而不只是指令级简化）的 pass。