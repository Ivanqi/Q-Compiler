# clean.py 详解
## 一、这个文件的作用是什么？
这是 qcc 的 CFG（控制流图）整形 pass，是优化流水线里的最后一道工序。

前面几个 pass（mem2reg、折叠、CSE、DCE……）在"指令级"上删删改改之后，会留下两类基本块级别的结构垃圾：
- 空块：块里只剩一条 Jump（比如 tailcall 改造出的 new_entry、if 分支被优化空掉后的块）；
- 可合并的相邻块：块 B 只有唯一前驱 A，且 A 以无条件 Jump 结尾——A 和 B 完全可以粘成一块。

CleanPass 就干这两件事（docstring 5–25 行画出了第一类）：
```c
jump A            →        jump B
A:                         B:
jump B                     ...
B:
...

```

它继承 FunctionPass（transform.py:30），覆盖 on_function。

除了让代码更紧凑，它还有一个重要作用：配合 codegenerator 的"入口块 + 首块"两段式设计——codegenerator 故意把 alloca 放在 entry 块、再跳进第一个代码块（codegenerator.py:402-408）；

mem2reg 删光 alloca 后 entry 就只剩一条 Jump，正好由本 pass 把两者重新粘回一块。

## 二、每个方法的作用
### on_function(self, function)（27–29 行）—— 入口

按固定顺序执行两个阶段：先删空块（remove_empty_blocks），再粘单前驱块（remove_one_preds）。

顺序有意义：穿越（threading）掉空块之后，会暴露出新的"单前驱 + 无条件跳转"对，第二阶段才有更多可粘的对象。

### find_empty_blocks(self, function)（31–39 行）—— 第一阶段之"找"

扫描函数所有块，判定空块的规则很简单：
- 35–36 行：block.is_entry → 跳过——入口块永远保留（它可能有特殊约束，且改动它影响函数签名处信息）；

- 37–38 行：isinstance(block.first_instruction, ir.Jump)——第一条指令就是 Jump 意味着整块只有一条跳转（Jump 之后不可能再有指令被执行到，qcc 块里 Jump 必然收尾），即"空块"。

### remove_empty_blocks(self, function)（41–66 行）—— 第一阶段之"删"（跳转穿越）

对每个空块执行"绕道"：让所有跳向它的边改跳向它的目标：
- 45–46 行：取该块的 predecessors（所有跳向它的块）和 successors（它跳向的块）；

- 49–50 行：if block in predecessors: continue——自循环保护：L: jump L 这种死循环块（自己是自己的前驱）不能删，删了无限循环就消失了；

- 52–54 行：更新后继块的入口信息——successor.replace_incoming(block, predecessors)：后继块里的 Phi 节点凡是标着"从 block 来"的入口，改成"从 block 的所有前驱来"（保持 CFG 边与 Phi 入口一一对应）；

- 56–59 行：更新前驱的跳转目标——pred.change_target(block, tgt)：所有 jump block/cjmp ..., block 改指向 block 的目标块 tgt（block.last_instruction.target）；

- 62–63 行：删除块内的 Jump 指令、把块从函数中移除；

- 65–66 行：统计并打日志 "Removed N empty blocks"。

### find_single_predecessor_block(self, function)（68–85 行）—— 第二阶段之"找"
返回第一个满足全部条件的可粘块：

- 71–75 行：len(preds) != 1 → 跳过——恰好一个前驱；

- 81–82 行：block is pred → 跳过——自循环保护（同上）；

- 84–85 行：前驱的最后一条指令必须是无条件 Jump——只有"前驱的出口就是跳到本块"时，粘合才安全（如果前驱以 CJump 结尾，本块只是它两个出口之一，粘进去会改变另一条分支的控制流）；

- 找不到时返回 None（隐式）。

### remove_one_preds(self, function)（87–96 行）—— 第二阶段之"粘"（循环驱动）
while change: 循环——每找到一块就粘一块，粘完重新找（change = True 继续循环），直到 find_single_predecessor_block 返回 None。

为什么必须循环：粘合会改变前驱关系，粘完一块可能让下一块变成"单前驱"，所以要迭代到不动点。

### glue_blocks(self, block1, block2)（98–118 行）—— 粘合动作本体

把 block2 的内容并入 block1（block1 是前驱，block2 是单前驱块）：
- 105–107 行：删掉 block1 的最后一条指令（那条 Jump block2）——这条跳转删掉后，block1 自然"fall-through"进 block2 的内容，这是粘合正确性的关键；

- 110–111 行：把 block2 的指令逐条搬进 block1 末尾（add_instruction 会移动指令并把它们的 block 属性更新为 block1）；

- 114–115 行：更新后继信息——block2 的后继块的 Phi 里"从 block2 来"的入口改成"从 block1 来"（replace_incoming(block2, [block1])）；

- 118 行：把 block2 从函数中移除。

## 三、通过什么方式使用 clean.py？
### 1. 作为 api.optimize() 流水线的一员（主要方式）
api.py:216-228 中它排第 8 位——列表的最后一个：
```python
opt_passes = [
    Mem2RegPromotor(),          # 1. SSA 化
    RemoveAddZeroPass(),        # 2.
    ConstantFolder(),           # 3.
    CommonSubexpressionEliminationPass(),  # 4.
    TailCallOptimization(),     # 5. 制造出 new_entry（空块）
    LoadAfterStorePass(),       # 6.
    DeleteUnusedInstructionsPass(),  # 7. 删指令（可能删空块的内容）
    CleanPass(),                # 8. ← 本文件：最后收拾块级结构
] * 3
for opt_pass in opt_passes:
    opt_pass.run(ir_module)

```

"压轴"是精心安排的：前 7 个 pass 都会让块变空或变"单前驱"（DCE 删光某块的指令、tailcall 造出 new_entry、mem2reg 掏空入口块），全部做完之后再由 CleanPass 一次性整形；

同时列表 * 3 让它也有三遍机会处理迭代暴露的结构。

### 2. 继承链决定执行方式
```python
CleanPass().run(ir_module)      # api.py:229
  → FunctionPass.run (transform.py:33)：遍历模块每个函数
      → on_function (clean.py:27)：
           remove_empty_blocks → remove_one_preds（内部 while 到不动点）

```

### 3. 单独使用
from qcc.opt.clean import CleanPass（api.py 就是这么导入的），直接 CleanPass().run(ir_module) 即可——测试、自定义流水线通用。

## 四、详细例子
### 例 1：入口块粘合（最常发生的真实场景）
```c
int f(int a) { return a * 2; }

```
codegenerator 按"entry 块放 alloca、block1 放真代码"的两段式生成；

mem2reg 提升掉 alloc 后（第 1 位 pass），entry 只剩一条跳转：
```c
function f(a: i32) -> i32 {
  entry:
    jump block1
  block1:
    t1 = mul a, 2
    return t1
}

```

#### 第一阶段 remove_empty_blocks：
find_empty_blocks 检查——entry 是入口块 → 跳过；block1 第一条指令是 mul 不是 Jump → 不是空块。

无事发生（注意：entry 虽只剩 Jump，但受入口保护不删）。

#### 第二阶段 remove_one_preds：
- find_single_predecessor_block：block1.predecessors == [entry]（恰一个）✓；block1 is not entry ✓；entry.last_instruction 是无条件 Jump ✓ → 选中 block1；

- glue_blocks(entry, block1)：
	- 删掉 entry 的 jump block1（106–107 行）；
	- 把 t1 = mul a, 2 和 return t1 搬进 entry（110–111 行）；
	- block1 没有后继，无需改 Phi；
	- 移除 block1（118 行）。

结果——两段式结构重新合成一块：
```python
function f(a: i32) -> i32 {
  entry:
    t1 = mul a, 2
    return t1
}

```
这就是 codegenerator 与 CleanPass 跨模块配合的完整闭环：前者故意拆出 entry 块方便 mem2reg，后者负责事后把结构缝回来。

### 例 2：空块穿越（docstring 场景 + Phi 更新）
假设某函数经前面 pass 处理后出现这样的结构（空块 A 夹在中间）：
```c
block0:
    jump A
A:
    jump B
B:
    p = phi [A: t1, C: t2]
    return p

``` 

（C 是 B 的另一个前驱，未画出。）remove_empty_blocks 对空块 A 执行穿越：
- A 的前驱 = {block0}，后继 = {B}；A 不在自己前驱里 → 可删；

- 52–54 行：B 的 Phi 入口 A → t1 改写成 block0 → t1：p = phi [block0: t1, C: t2]；

- 56–59 行：block0 的 jump A 改指向 B：jump B；

- 删除 A 与其 Jump。

```c
block0:
    jump B
B:
    p = phi [block0: t1, C: t2]
    return p

```

与 docstring 的 jump A / A: jump B / B: → jump B / B: 完全一致，且 Phi 入口跟着 CFG 边一起改，语义不丢。

### 例 3：自循环保护——为什么不删 L: jump L
```c
loop_block:
    jump loop_block        ← 无限循环

```
find_empty_blocks 会注意到它的第一条指令是 Jump，但 49 行 block in predecessors 检查发现它是自己的前驱 → 跳过。

假如删掉它，一个死循环就被"优化"没了——程序语义改变。

同样，find_single_predecessor_block 的 81 行也对 block is pred 设防。

### 例 4：粘合的循环迭代（while change）

若一次粘合后出现了新的"单前驱 + 无条件跳转"对，remove_one_preds 的 while change 会立即再找再粘，直到 find_single_predecessor_block 返回 None。

比如三个块链 A → B → C（A、B 都以 Jump 结尾、B/C 各只有一个前驱）：

第一轮把 B 粘进 A，粘完 C 的前驱仍是"合并后的 A"且其末尾已是原来的 Jump（若指向 C）——继续粘，直到只剩一块。

## 五、一句话总结
clean.py 是 qcc 优化流水线的"块级收尾" pass：

它继承 FunctionPass，分两个阶段整形 CFG——remove_empty_blocks 把"只有一条 Jump 的空块"穿越掉（改前驱跳转目标、同步更新后继 Phi 入口，自循环块和入口块受保护），remove_one_preds + glue_blocks 循环地把"唯一前驱以无条件 Jump 结尾"的块粘进前驱（删跳转、搬指令、更新 Phi），直到不动点。

它排在 api.py 优化列表最后一位（第 8），随流水线跑三遍——前 7 个 pass 负责指令级的"减料"，它负责把 CFG 的"边角料"缝合干净，与 codegenerator 的 entry 两段式设计正好形成"先拆后合"的闭环。