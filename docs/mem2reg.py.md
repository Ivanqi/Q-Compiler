# mem2reg.py 详解
## 一、这个文件的作用是什么？
这是 qcc 优化器里最重要的一个 pass：内存到寄存器提升（memory-to-register promotion），即编译器教科书里的 mem2reg / SSA 构造。

它的核心思想写在文件头 docstring 里（1–5 行）：
- 当一块内存只被 store 和 load 使用时，存进去的值也可以直接放进"寄存器"（SSA 值），从而提升性能。

背景要接上一讲：CCodeGenerator 生成的是故意的"笨 IR"——每个局部变量都 alloca 一块栈空间，写变量用 store，读变量用 load（codegenerator.py:115-124 的 emit_alloca、codegenerator.py:413-428 的参数 store）。

这些内存操作若原样进入后端，每个局部变量都要占用栈槽、产生真实的访存指令。

mem2reg 的使命就是把这些"只在函数内活动的内存变量"提升成纯 SSA 值（必要时插入 Phi 节点），把内存访问全部消掉——之后才谈得上常量折叠、死代码删除等进一步优化。

它实现的是 Cytron 等人经典算法的两步：放置 Phi（place_phi_nodes）+ 重命名（rename），依赖支配树/支配边界信息（CfgInfo）。

## 二、每个方法的作用
模块级函数 is_alloc_promotable(alloc_inst)（12–54 行）

"这个 alloc 能不能提升"的安全检查，一条条对应经典的 alloca 提升条件：
- 15–16 行：alloc 必须恰好被一条指令引用（len(used_by) != 1 就拒绝——因为分配后立刻取地址是唯一合法用法）；
- 18–20 行：那条引用必须是 ir.AddressOf（取地址；如果 alloc 被直接当值用，拒绝）；
- 22–23 行：地址至少要有人用；
- 26–29 行：地址的所有使用者必须全是 Load 或 Store——只要有别处把地址传出去（如传给函数当参数、参与指针算术），就拒绝提升；
- 36–37 行：地址本身不能被 store 成值（store p, q 中 store.value is addr_inst 的情形，即指针被存进内存）；
- 40–41 行：volatile 内存操作拒绝（volatile 语义要求每次真实访存）；
- 44–48 行：所有 load/store 的类型必须一致（Phi 需要一个统一的类型）；
- 50–54 行：注释掉的"alloc 字节数 == 类型字节数"检查（TODO，需要目标架构知识）。

### class Mem2RegPromotor(FunctionPass)（57 行）
继承 transform.py 的 FunctionPass——其 run(ir_module) 遍历模块中每个函数调用 on_function，并提供 self.logger、self.debug_db（模块的调试信息，用于把 alloc 的调试映射转给 phi）。

#### place_phi_nodes(stores, phi_ty, name, cfg_info)（61–87 行）
算法第一步：在支配边界（dominance frontier）上放置 Phi 节点。

教科书规则：变量在块 x 中有定义，则 x 的支配边界 DF(x) 里每个块都需要一个 Phi；而放置的 Phi 本身又是一个"定义"，所以要用迭代支配边界（工作列表算法）：
- 67 行：defining_blocks = 所有 store 所在块的集合；

- 70–72 行：工作列表初始化为这些定义块；has_phi 记录已放置 Phi 的块；

- 76–86 行：循环——弹出块 defining_block，遍历 cfg_info.df[defining_block]（该块的支配边界块集合）；若某边界块还没有 Phi，就 insert_instruction(phi) 插到该块的开头（86 行），并把该块加入工作列表（因为它现在也是定义块，可能引发更远的 Phi）；Phi 命名为 phi_{name}_{idx}；

- 87 行：返回所有新建的 Phi 列表。

cfg_info.df 来自 domtree.py 的 CfgInfo——它先把函数转成 CFG 图、calculate_dominance_frontier() 算好每个块的支配边界。

#### rename(initial_value, phis, loads, stores, cfg_info)（89–142 行）
算法第二步：沿支配树自顶向下重命名。维护一个 **"到达值栈"** stack（96 行，初始压入 Undefined），用闭包 search(tree_node) 递归遍历支配树：
- 100–104 行：从支配树节点拿到对应 CFG 块（树里可能有 cfg 外的虚节点，has_block 跳过）；

- 106–117 行：顺序扫描块内指令：
	- 遇到本变量的 Phi（109–111 行）：把 Phi 压栈（它代表本块内"变量当前的值"）；
	- 遇到 store（113–115 行）：把 store.value（存进去的值）压栈；
	- 遇到 load（117–120 行）：instruction.replace_by(stack[-1])——把该 load 的所有使用处替换成栈顶值，这是重命名的核心动作；
	- defs 记录本块压了几次栈（用于退出时弹出，138–140 行）；

- 124–132 行：块结束时，为每个后继块中本变量的 Phi 设置入口：phi.set_incoming(block, stack[-1])（"从当前块过来时，这个变量等于栈顶值"）；

- 134–136 行：递归处理支配树的子节点（dominator tree 遍历顺序保证"先父后子"，而栈让"子块继承父块的值"）；

- 138–140 行：弹出本块压入的 defs，恢复父块视角。

#### promote(alloc, cfg_info)（144–216 行）
提升单个 alloc 的编排者，把两步算法串起来并做清理：
- 149–153 行：取地址指令、收集该 alloc 的全部 load/store；

- 162–167 行：从 load/store 的类型确定 phi_ty（安全检查已保证一致）；

- 170–181 行：有 load 就需要 Phi：place_phi_nodes 放置；调试信息迁移（174–175 行 debug_db.map(alloc, phi)，这样调试器查这个变量时能看到 Phi）；178–179 行 在入口块插入 ir.Undefined("und_name", phi_ty) 作为初始值——它处理"某些路径从未给变量赋值"的情况（C 允许使用未初始化变量，此时该值就是 undef）；

- 181 行：调 rename 完成替换；

- 183–187 行：校验每个 Phi 的入口数 == 其块的前驱数（重命名正确性的自检）；

- 189–199 行：清理循环——initial_value 和 Phi 里没人使用的逐个删除，反复迭代直到不动点（删掉一个 Phi 可能让另一个 Phi 变没用）；

- 201–216 行：删掉全部 store、全部 load（先 assert not load.is_used 确认替换已完成）、AddressOf、最后是 alloc 本身。

## 三、通过什么方式被使用？
### 1. 作为优化流水线的一员（主要方式）

上一讲看过 api.py:216-228 的 optimize()：
```python
opt_passes = [
    Mem2RegPromotor(),          # ← 本文件
    RemoveAddZeroPass(),
    ConstantFolder(),
    ...
] * 3
...
for opt_pass in opt_passes:
    opt_pass.run(ir_module)

```

调用链是：
```python
qcc.api.optimize(ir_module, level=2)
  └─ Mem2RegPromotor().run(ir_module)     # 继承自 FunctionPass (transform.py)
       └─ for function in ir_module.functions:
            self.on_function(function)    # mem2reg.py:218
                 └─ for alloc ...: is_alloc_promotable → promote

```

两个细节：
- 排在流水线第一位——它必须先于 ConstantFolder/LoadAfterStore/CleanPass 运行，因为这些 pass 都期待 SSA 形式；

- 列表 * 3 连跑三遍——一遍 mem2reg 后 CFG 可能变化（CleanPass 合并块），下一遍又能提升更多 alloc；迭代直到收敛。

### 2. 单独使用
任何拿到 ir.Module 的地方都能直接跑：Mem2RegPromotor().run(ir_module)（它只需一个 logger，不依赖其他状态）。

单元测试、教学实验、或自定义流水线里都这么用。


### 3. 与 codegenerator 的"合同"关系
mem2reg 之所以有效，是因为 CCodeGenerator 遵守了三条约定（这构成两个模块间隐式的"合同"）：
- 所有局部变量都是 Alloc + AddressOf + Load/Store 形态（codegenerator.py:115-124）；

- alloc 都集中在函数入口块（codegenerator.py:471-475）——所以插入的 Undefined 初始值放在 alloc.function.entry 一定在支配树上位于所有使用之前；

- 不做任何 SSA 化——把这件苦差事留给本文件。


## 四、详细例子
以这段 C 为例（需要一个"会合点"才能看到 Phi）：
```c
int max(int a, int b) {
    int result;
    if (a > b) { result = a; } else { result = b; }
    return result;
}

```

### 第 1 步：codegenerator 产出的"笨 IR"（简化）
```c
function max(a: i32, b: i32) -> i32 {
  entry:
    result_addr = alloc 4
    a_addr = alloc 4
    b_addr = alloc 4
    jump block1
  block1:
    store a, a_addr
    store b, b_addr
    t1 = load a_addr
    t2 = load b_addr
    cjmp t1 > t2 ? then_block : else_block
  then_block:
    t3 = load a_addr
    store t3, result_addr
    jump cont_block
  else_block:
    t4 = load b_addr
    store t4, result_addr
    jump cont_block
  cont_block:
    t5 = load result_addr
    return t5
}

```

（result、a、b 都是 alloc+load/store 形态。）

### 第 2 步：on_function → 对 result_addr 调 promote
#### ① is_alloc_promotable(result_addr) 检查：
result_addr 只被 AddressOf 引用 ✓；
地址的使用者是 store t3、store t4、load t5——全是 Load/Store ✓；
地址没被当值存 ✓；
无 volatile ✓；
类型全是 i32 ✓ → 可提升。

#### place_phi_nodes：
- stores = [store(t3), store(t4)] → defining_blocks = {then_block, else_block}；

- 查支配边界：df(then_block) = {cont_block}，df(else_block) = {cont_block}（cont 是两条分支的会合点，正好在支配边界上）；

- 工作列表：弹 then_block → 在 cont_block 插入 phi_result；弹 else_block → cont 已有 Phi，跳过；df(cont_block) 无 → 结束。phi_

- result 被插到 cont_block 开头。

#### 插入初始值 + rename：入口块插入 und_result = undefined；栈 = [und_result]；沿支配树遍历：
- entry：无相关指令；后继 block1 没有本变量 Phi，不设入口；

- block1：无；后继 then/else 无 Phi；

- then_block：遇到 store t3, result_addr → 压栈 t3；块尾对后继 cont_block 的 Phi：phi_result.set_incoming(then_block, t3)；递归进入 cont_block：Phi 在集合里 → 压栈 phi_result；load t5 → t5.replace_by(phi_result)，即 return t5 变成 return phi_result；回退弹出；

- 兄弟 else_block 同理：phi_result.set_incoming(else_block, t4)；

- 校验：phi_result 有 2 个入口 == cont_block 的 2 个前驱 ✓。


### 第 3 步：一轮 pass 后
```c
function max(a: i32, b: i32) -> i32 {
  entry:
    und_result = undefined
    a_addr = alloc 4
    b_addr = alloc 4
    jump block1
  block1:
    store a, a_addr
    store b, b_addr
    t1 = load a_addr
    t2 = load b_addr
    cjmp t1 > t2 ? then_block : else_block
  then_block:
    t3 = load a_addr
    jump cont_block
  else_block:
    t4 = load b_addr
    jump cont_block
  cont_block:
    phi_result = phi [then_block: t3, else_block: t4]
    return phi_result
}

```
result 已经从内存变量变成了 phi_result 这个 SSA 值。

### 第 4 步：后续 pass 接力（* 3 的意义）
同一轮里 LoadAfterStorePass 把 store a, a_addr; load a_addr 配对消除；下一遍 Mem2RegPromotor 把 a_addr、b_addr 也提升掉（它们同样只被 load/store 用）→ t1/t2/t3/t4 被直接替换成参数 a、b；

CleanPass 合并单前驱块（then/else 只剩一条 jump，cont 合并）；最终：
```c
function max(a: i32, b: i32) -> i32 {
  entry:
    cjmp a > b ? then_block : else_block
  then_block:
    jump cont_block
  else_block:
    jump cont_block
  cont_block:
    phi_result = phi [then_block: a, else_block: b]
    return phi_result
}

```

从"每次读 result 都要访存"变成"一个 Phi + 纯寄存器数据流"。

后端把 Phi 消解成 then 存一个寄存器、else 存另一个、cont 选择其一即可（或者按目标架构做 copy）。

### 补充：Undefined 存在的意义
若改成：
```c
int f(int cond, int x) {
    int r;
    if (cond) { r = x; }
    return r;   // cond 为假时 r 未初始化（C 允许，未定义行为）
}

```

place_phi_nodes 只在 cont_block 放一个 Phi，但只有 then_block 给它设置入口——此时 rename 里 cont 的 Phi 从父链拿到的栈顶是 und_r，缺的那条路径（else → cont）会以 und_r 作为入口。

这正是 mem2reg.py:178 插入 ir.Undefined 的原因：给"可能未赋值"的路径一个占位值，保持 SSA 形式完整。

## 五、一句话总结
mem2reg.py 是 qcc 里把"内存变量"变回"寄存器值"的 SSA 构造 pass：
is_alloc_promotable 用一组安全检查圈定可提升的 alloc（只被取地址 + load/store、非 volatile、类型一致）；

promote 按经典两阶段算法执行——place_phi_nodes 用迭代支配边界在会合点插 Phi，rename 沿支配树用一个"到达值栈"把每个 load 替换成正确的 SSA 值、并填好 Phi 的入口；

最后删光 store/load/alloc。

它通过 api.py 的 optimize() 以 FunctionPass.run(ir_module) 的形式排在优化流水线第一位、连跑三遍，与 codegenerator 的"故意笨 IR"约定配合，是整个优化链条（折叠、CSE、DCE、CFG 整形）得以生效的前提。