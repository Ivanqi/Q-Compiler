# transform.py 详解
## 一、这个文件的作用是什么？
这是 qcc 优化器的 **"pass 框架"文件**：定义了编写优化 pass 的基类体系，以及两个最简单的示例级 pass（RemoveAddZeroPass 和 DeleteUnusedInstructionsPass）。

qcc/opt/ 目录里所有真正的优化 pass（mem2reg、常量折叠、CSE……）都继承自这里的基类，从而获得统一的执行方式：opt_pass.run(ir_module)。

基类体系是一个"遍历粒度逐级细化"的继承链：
```c
ModulePass            —— 处理整个模块（抽象 run(ir_module)）
  └─ FunctionPass     —— 自动遍历模块里每个函数 → on_function(function)
       └─ BlockPass   —— 自动遍历函数里每个基本块 → on_block(block)
            └─ InstructionPass —— 自动遍历块里每条指令 → on_instruction(instruction)

```

pass 作者只需要覆盖最细粒度的那一个方法（比如 on_instruction），"怎么遍历整个模块"由框架代劳。这正是模板方法模式（Template Method）。

## 二、每个类/方法的作用
### ModulePass（9–27 行）—— 所有 pass 的根
15–16 行 __init__：给每个 pass 实例配一个以类名命名的 logger（所以优化日志里能区分是哪个 pass 打的）。

18–19 行 __repr__：返回类名，报告里显示 "RemoveAddZeroPass" 这样的名字。

21–22 行 prepare：钩子方法，默认什么都不做——子类可在正式运行前初始化状态。

24–27 行 run(ir_module)：抽象方法——每个 pass 的统一入口，接受 ir.Module，原地修改（in-place）。

### FunctionPass（30–45 行）—— 按函数遍历
33–40 行 run：先 prepare()；把模块的调试信息存到 self.debug_db（供 mem2reg 这类需要迁移调试信息的 pass 用，见 mem2reg.py:175）；然后遍历 ir_module.functions，对每个函数调 on_function；最后清掉 debug_db。

42–45 行 on_function：抽象方法，子类实现"对单个函数做什么"。

### BlockPass（48–59 行）—— 按基本块遍历
on_function 的实现就是遍历 function.blocks，逐个调 on_block。on_block 是新的抽象方法。

### InstructionPass（62–73 行）—— 按指令遍历
on_block 的实现就是遍历块内每条指令，逐个调 on_instruction。

on_instruction 是抽象方法。

RemoveAddZeroPass 继承 InstructionPass，所以它要写的代码只有一个 on_instruction——运行时会自动走 run → 每个函数 → 每个块 → 每条指令 的完整遍历。

### RemoveAddZeroPass（76–99 行）—— 用户选中的这个 pass
76–79 行：docstring——"把加零替换为原值本身，把乘以一替换为原值本身"（两个代数恒等式）。

81–99 行 on_instruction(instruction)，对每条指令做三个模式匹配（全部基于 replace_by，即"把所有使用这条指令的地方替换成另一个值"）：

	- 82–88 行：指令是 ir.Binop 且操作是 +，且右操作数 b 是常量 0 → instruction.replace_by(instruction.a)（x + 0 → x）；
	- 89–93 行：同上，但左操作数 a 是常量 0 → replace_by(instruction.b)（0 + x → x）；
	- 94–99 行：操作是 * 且右操作数 b 是常量 1 → replace_by(instruction.a)（x * 1 → x）。

实现细节值得注意：
- 82 行用 type(instruction) is ir.Binop 精确类型匹配（而不是 isinstance）——只处理标准的 Binop，若将来出现子类（比如带快速数学标志的 Binop）不会被误伤；

- 常量判断也是 type(...) is ir.Const（85、90、96 行）——只认"纯常量"，不认 LiteralData 等其他常量类；

- 不对称性：+ 0 检查了左右两边，但 * 1 只检查了右边（1 * x 不会被这个 pass 处理）。这种小不完整是可以接受的——pass 追求"快而常见"的局部简化，剩下的交给其他 pass 或多轮迭代；

- 被 replace_by 后，原指令变成"没人使用"，真正的删除留给流水线后面的 DeleteUnusedInstructionsPass——各 pass 分工明确。

### DeleteUnusedInstructionsPass（102–119 行）—— 同文件的另一个 pass
on_block 收集块内"是值（ir.Value）、没人使用、且不是 FunctionCall"的指令，全部 remove_from_block。

排除函数调用是因为调用可能有副作用（即使返回值没人用，调用本身也要保留——C 的 foo(); 就是这么生成的）。

删除后打一条 debug 日志。

它正是 ReplaceAddZeroPass 的最佳搭档：前者制造"孤儿指令"，后者负责收尸。

## 三、通过什么方式使用 RemoveAddZeroPass？
### 1. 作为 api.optimize() 流水线的一员（主要方式）
上一讲见过 api.py:216-228：
```python
from .opt.transform import DeleteUnusedInstructionsPass, RemoveAddZeroPass   # api.py:51

opt_passes = [
    Mem2RegPromotor(),
    RemoveAddZeroPass(),        # ← 排第 2 位，紧跟 mem2reg 之后
    ConstantFolder(),
    ...
    DeleteUnusedInstructionsPass(),   # ← 排第 7 位，负责删除被 replace_by 剩下的死指令
    CleanPass(),
] * 3
for opt_pass in opt_passes:
    opt_pass.run(ir_module)

```

它排在 mem2reg 之后、常量折叠之前，理由是：mem2reg 刚把内存访问替换成纯值，紧接着先做一轮"免费"的代数化简，能让后面的 ConstantFolder/CSE 看到更干净的代码。

列表 * 3 意味着整个流水线连跑 3 遍——每遍里 RemoveAddZeroPass 都会再跑一次（后几遍的代码已来自前一遍的输出）。

### 2. 继承链决定执行方式
RemoveAddZeroPass() 实例调 run(ir_module) 时，实际执行路径（方法解析顺序）是：
```python
opt_pass.run(ir_module)                    # api.py:229
  → ModulePass 里没有 run 实现，用 FunctionPass.run        (transform.py:33)
      → prepare() + 保存 debug_db
      → for function in ir_module.functions:
            on_function(function)           # BlockPass 的实现 (transform.py:51)
              → for block in function.blocks:
                    on_block(block)         # InstructionPass 的实现 (transform.py:65)
                      → for instruction in block:
                            on_instruction(instruction)   # RemoveAddZeroPass 自己的 (transform.py:81)

```

所以这个 pass 只写了 19 行匹配代码，却能自动遍历整个模块。

### 3. 单独使用
它被导出在 qcc/opt/init.py:12，任何持有 ir.Module 的代码都可以直接跑：RemoveAddZeroPass().run(ir_module)——测试、实验或自定义流水线都行。

## 四、详细例子
### 例 1：x + 0 和 x * 1 的完整旅程
```c
int f(int x) {
    int y = x + 0;
    int z = y * 1;
    return z;
}

```

#### 第 1 步：codegenerator 产出"笨 IR"（alloca/load/store 形态）：
```c
function f(x: i32) -> i32 {
  entry:
    x_addr = alloc 4
    y_addr = alloc 4
    z_addr = alloc 4
    jump block1
  block1:
    store x, x_addr
    t1 = load x_addr
    t2 = const 0
    t3 = add t1, t2          ← x + 0
    store t3, y_addr
    t4 = load y_addr
    t5 = const 1
    t6 = mul t4, t5          ← y * 1
    store t6, z_addr
    t7 = load z_addr
    return t7
}

```

#### 第 2 步：api.optimize(ir_module, level=2)，第一遍 pass 列表：
#### Mem2RegPromotor 先跑：

x_addr/y_addr/z_addr 全部只被 load/store 使用 → 提升；

load 被替换成到达值（t4 → t3、t7 → t6），store/load/alloc 全删：
```c
block1:
    t2 = const 0
    t3 = add x, t2
    t5 = const 1
    t6 = mul t3, t5
    return t6

```

##### RemoveAddZeroPass.run(ir_module) 被调用，沿继承链逐条访问指令：
t2 = const 0：不是 ir.Binop → 跳过；

t3 = add x, t2：type(t3) is ir.Binop ✓，operation == "+" ✓，type(t2) is ir.Const 且 t2.value == 0 ✓ → t3.replace_by(x)。此刻 t6 = mul t3, t5 自动变成 t6 = mul x, t5，t3 沦为孤儿（is_used 变 False）；

t5 = const 1：跳过；

t6 = mul x, t5：operation == "*" ✓，t5 是常量 1 ✓ → t6.replace_by(x)。return t6 变成 return x，t6 也成孤儿。
```c
block1:
    t2 = const 0       ← 没人用了
    t3 = add x, t2     ← 没人用了
    t5 = const 1       ← 没人用了
    t6 = mul x, t5     ← 没人用了
    return x

```

##### DeleteUnusedInstructionsPass（同一遍列表的第 7 位）收尸
t2/t3/t5/t6 都是 ir.Value、不是 FunctionCall、is_used == False → 全部删除，日志打出 "Deleted 4 unused instructions"：
```c
block1:
    return x

```

##### 最后 CleanPass 把只剩一跳的空块合并/删除，最终：
```c
function f(x: i32) -> i32 {
  entry:
    return x
}

```
整个 int y = x + 0; int z = y * 1; return z; 

被优化成 return x——RemoveAddZeroPass 在其中贡献了两步关键替换。

### 例 2：现实中 x + 0 从哪来——结构体首字段
ReplaceAddZeroPass 并非只服务手写的 x + 0。

codegenerator 生成结构体字段访问时会无条件发 base + offset（codegenerator.py:1500-1514）：
```python
struct S { int first; int second; } s;
int g(void) { return s.first; }

```

s.first 的偏移是 0，codegenerator 产出 t = add s_ptr, 0。

RemoveAddZeroPass 的规则 2（右操作数为常量 0）把它替换成 s_ptr 本身，省掉一次指针加法。

这类"生成器模式化产出的冗余"正是这个 pass 的主要猎物。

### 例 3：与 ConstantFolder 的分工
return 1 + 2; 不归它管——ConstantFolder 负责"两边都是常量"的折叠（add(1,2) → 3）；

ReplaceAddZeroPass 专门负责"一边常量且是恒等元"的情况（+0、*1）。

两者一个排第 2、一个排第 3，互不重叠又彼此配合。

## 五、一句话总结
transform.py 是 qcc 优化器的 pass 框架：ModulePass → FunctionPass → BlockPass → InstructionPass 四级基类用模板方法把"遍历整个模块"自动化，pass 作者只需覆盖最细粒度的方法；

RemoveAddZeroPass 就是继承 InstructionPass 的一个 19 行示例——它逐指令匹配 x + 0、0 + x、x * 1 三种恒等模式并用 replace_by 化简（删除交给 DeleteUnusedInstructionsPass）。

它通过 api.py 的 optimize() 以 run(ir_module) 形式排在 mem2reg 之后、常量折叠之前，随流水线连跑 3 遍，专门清理"生成器产出的零偏移指针加法和恒等算术"。