贯穿案例：int sum(int a, int b) { return a + b; }

  以 x86_64 为目标，追踪它从源码到机器码的完整路径。

  ---
  一、C 前端：源码 → AST → IR

  1.1 词法分析 (lexer.py)

  输入: "int sum(int a, int b) { return a + b; }"
  输出: Token 流

  int     → 关键字 "int"
  sum     → 标识符 (ID)
  (       → 左括号
  int     → 关键字 "int"
  a       → 标识符
  ,       → 逗号
  int     → 关键字
  b       → 标识符
  )       → 右括号
  {       → 左花括号
  return  → 关键字 "return"
  a       → 标识符
  +       → 运算符 "+"
  b       → 标识符
  ;       → 分号
  }       → 右花括号

  lexer.py 是一个手写词法分析器 (HandLexerBase)，由 preprocessor.py 在预处理阶段驱动——预处理器先展开宏、处理
  #include、拼接续行，然后产出一个被 CParser 消费的 token 流。

  1.2 语法分析 (parser.py → AST)

  CParser 是一个递归下降解析器 (parser.py)，对每个 C 语法规则有一个对应的解析函数：

  Token 流
    │ CParser.parse()
    │ → parse_declaration()       识别到 "int sum(..."
    │ → parse_function_declaration()
    │   → parse_parameter_list()  识别 "int a, int b"
    │   → parse_compound_statement()  识别 "{ return a + b; }"
    │     → parse_statement()         识别 "return ..."
    │       → parse_expression()      识别 "a + b"
    ▼
  AST 节点树 (builder.py:33)

  生成的 AST：

  Compilation unit
  └── FunctionDeclaration name="sum"
      ├── typ: FunctionType
      │   ├── arguments:
      │   │   ├── Parameter typ=int name=a
      │   │   └── Parameter typ=int name=b
      │   └── return_type: BasicType int
      └── body: Compound
          └── Return
              └── BinaryOperator op="+", typ=int
                  ├── VariableAccess name="a"
                  └── VariableAccess name="b"

  1.3 语义分析 (semantics.py)

  CSemantics 遍历 AST 做类型检查和符号解析：
  - 为 a 和 b 建立符号表条目，绑定到对应的 Parameter 声明
  - a + b 的两个操作数都是 int，结果也是 int——类型匹配
  - return 语句的值类型 int 与函数返回类型 int 匹配

  1.4 代码生成 (codegenerator.py → IR)

  CCodeGenerator.gen_code() (codegenerator.py:71) 是入口。核心流程：

  AST 节点                             codegenerator.py 方法              IR 输出
  ────────────────────────────────────────────────────────────────────────────────
  FunctionDeclaration("sum")          create_function_internal():344    ir.Function("sum", GLOBAL, i32)
    Parameter "a"                     gen_function_def():413            ir.Parameter("a", i32) → Alloc → Store
    Parameter "b"                                                        ir.Parameter("b", i32) → Alloc → Store
    Return                            gen_return():767
      BinaryOperator "+"              gen_binop():1225
        VariableAccess "a"            gen_variable_access() → Load      Load(addr_a)
        VariableAccess "b"            gen_variable_access() → Load      Load(addr_b)
                                      builder.emit_binop(lhs,"+",rhs)   Binop(a_loaded, "+", b_loaded, "tmp", i32)
                                      builder.emit_return(value)        Return(tmp)

  关键代码路径：

  1. create_function_internal (codegenerator.py:344)：根据返回类型 int 不是 void，走 builder.new_function("sum", GLOBAL, i32)，创建
  ir.Function
  2. gen_function_def (codegenerator.py:388-427)：参数处理——每个参数：
  ir_argument = ir.Parameter("a", ir.i32)     # 行 419
  ir_function.add_parameter(ir_argument)       # 行 420
  ir_var = self.emit_alloca(argument.typ)      # 行 426 — Alloc 栈空间
  self.emit(ir.Store(ir_argument, ir_var))     # 行 427 — 参数存入栈
  3. gen_expr → gen_binop (codegenerator.py:1225-1241)：处理 a + b
  lhs = self.gen_expr(expr.a, rvalue=True)  # Load(addr_a) → ir value
  rhs = self.gen_expr(expr.b, rvalue=True)  # Load(addr_b) → ir value
  value = self.builder.emit_binop(lhs, "+", rhs, ir.i32)  # Binop
  4. gen_return (codegenerator.py:767-786)：
  value = self.gen_expr(stmt.value, rvalue=True)  # 计算 a+b
  self.builder.emit_return(value)                  # Return(tmp)

  生成的 IR（未优化）：
  global function i32 sum(i32 a, i32 b) {
    sum_block0: {                         ← entry (只有 alloc, 然后跳走)
      alloca = alloc 4 bytes              ← 为 a 分配栈空间
      alloca_addr = &alloca
      alloca_1 = alloc 4 bytes            ← 为 b 分配栈空间
      alloca_addr_2 = &alloca_1
      jmp sum_block1
    }
    sum_block1: {                         ← 真正执行代码的块
      store a, alloca_addr                ← 把参数 a 存到栈
      store b, alloca_addr_2              ← 把参数 b 存到栈
      tmp_load = load alloca_addr         ← 从栈读出 a
      tmp_load_0 = load alloca_addr_2     ← 从栈读出 b
      tmp = tmp_load + tmp_load_0         ← a + b
      return tmp
    }
  }

  优化后（Mem2Reg 消除所有 alloc/load/store）：
  global function i32 sum(i32 a, i32 b) {
    sum_block0: {
      tmp_3 = a + b
      return tmp_3
    }
  }

  ---
  二、BURG 树匹配算法：IR → 机器指令

  2.1 整体思路

  代码生成 (codegen.py:62 generate()) 对每个函数执行:

  def generate_function(self, ir_function, output_stream, debug):
      frame = self.arch.new_frame(ir_function.name, ir_function)
      self.select_and_schedule(ir_function, frame)   # ← BURG 在这里
      self.register_allocator.alloc_frame(frame)      # ← 图着色在这里
      self.emit_frame_to_stream(frame, output_stream) # ← 输出汇编

  2.2 SelectionDAG 构建 (irdag.py)

  SelectionGraphBuilder 遍历 IR 的每个基本块，把 IR 指令转成 SelectionDAG：

  IR 指令                           SelectionDAG 节点
  ───────────────────────────────────────────────────
  a (Parameter)              →     REGI32(vreg_a)
  b (Parameter)              →     REGI32(vreg_b)
  tmp_3 = a + b              →     ADDI32(REGI32(vreg_a), REGI32(vreg_b))
  return tmp_3               →     RET(ADDI32(...))

  这不是一棵树而是一个 DAG（因为值可以被多条指令引用）。但树匹配比 DAG 匹配简单，所以 DagSplitter (dagsplit.py) 把 DAG 切割成树森林：

  DAG:                         切分为树:
    [RET]                       树1: RET(reg)
      │                           │
    [ADDI32]                     树2: reg = ADDI32(reg, reg)
     ├── [REG a]                   ├── [REG a]
     └── [REG b]                   └── [REG b]

  切割发生在每个需要"具体化"到寄存器的节点——RET 需要一个寄存器中的值，所以 ADDI32 的结果必须被"物化"为一个 reg 非终结符。

  2.3 BURG 树模式匹配 (burg.py + instructionselector.py)

  BURG（Bottom-Up Rewrite Generator）做两遍遍历：

  第一遍（Label）：自底向上，给每个树节点打上所有可匹配规则的标签和代价。

  对于我们的 ADDI32(REGI32, REGI32) 树：

                      ADDI32(reg, reg)        ← 树根节点
                      /            \
                REGI32(a)        REGI32(b)    ← 叶子

  每个叶子 REGI32(vreg) 可以被匹配：

  # instructions.py:2195 — 零代价规则，只是传递
  @isa.pattern("reg64", "REGI64", size=0)
  def pattern_reg64(context, tree):
      return tree.value

  根节点 ADDI32(reg, reg) 可以匹配到多条规则，每条有不同的代价（size/cycles/energy）：

  规则1: reg32 = ADDI32(reg32, rm32)           size=2, cycles=2  (reg+reg形式)
  规则2: reg32 = ADDI32(reg32, con32)           size=8, cycles=3  (reg+const形式)
  规则3: mem64 = ADDI64(reg64, con32)           size=1, cycles=1  (地址计算形式)

  BURG 系统选择总代价最小的匹配链。这里 size=2 的 ADDI32(reg, reg) 最优。

  第二遍（Select）：自顶向下，从根节点的目标非终结符出发，沿着最小代价路径向下选择。

  instructionselector.py 的 select() 函数调用 gen_tree()：

  # instructionselector.py 中 gen_tree 的简化逻辑:
  def gen_tree(self, context, tree):
      # 从树的 state (label阶段算出来的) 拿到最优规则
      rule = tree.state.get_rule(tree.goal)
      # 调用对应的 pattern 函数
      rule.method(context, tree, *child_results)

  对于 ADDI32，匹配到的 pattern 是 pattern_add32：

  # instructions.py:1630
  @isa.pattern("reg32", "ADDI32(reg32, rm32)", size=2, cycles=2, energy=1)
  def pattern_add32(context, tree, c0, c1):
      d = context.new_reg(Register32)   # 新虚拟寄存器
      context.move(d, c0)               # 把 c0 (vreg_a) 复制到 d
      context.emit(bits32.AddRegRm(d, c1))  # 发出 add 指令
      return d

  对于我们的例子，完整匹配过程：

  树                             匹配的pattern                   产出的x86指令(虚拟寄存器)
  ────────────────────────────────────────────────────────────────────────────────
  RET(reg)                       → 无显式pattern, 约定:          mov eax, vreg_result
                                   值必须在返回寄存器中              ret

  ADDI32(REGI32(a), REGI32(b))   → pattern_add32():             mov vreg1, vreg_a
                                    size=2                       add vreg1, vreg_b

  最终产出的指令序列（尚未寄存器分配，全是虚拟寄存器）：

  sum:
      push rbp
      mov rbp, rsp
  sum_block0:
      mov vreg1, vreg_a      ← context.move(d, c0)
      add vreg1, vreg_b      ← AddRegRm(d, c1)
  sum_epilog:
      mov eax, vreg1         ← 返回值放入 eax (调用约定)
      pop rbp
      ret

  2.4 BURG 的权重权衡

  注意 pattern 上的三个权重：

  weights_map = {            # (size_weight, speed_weight, co2_weight)
      "size":  (10, 1, 1),   # 强调代码体积
      "speed": (3, 10, 1),   # 强调运行速度
      "co2":   (1, 2, 10),   # 强调低碳排放
  }

  对 ADDI32(reg, reg) 的两个竞争 pattern：
  - size=2, cycles=2 的 reg+reg 形式 → size 代价: 10×2=20
  - size=8, cycles=3 的 reg+const 形式 → size 代价: 10×8=80

  所以在 opt="size" 下绝对选择 size=2 的版本。

  ---
  三、图着色寄存器分配

  3.1 目标

  此时指令还在用虚拟寄存器（数量不限）。需要把它们映射到 x86_64 的物理寄存器 (rax, rbx, rcx, rdx, rdi, rsi, r8-r15...)。

  3.2 构建数据流图 (flowgraph.py)

  FlowGraph 把线性指令序列转成 CFG 节点，计算每个虚拟寄存器的活跃范围 (liveness)：

  指令                   vreg_a  vreg_b  vreg1  (活跃的虚拟寄存器)
  ──────────────────────────────────────────
  mov vreg1, vreg_a      live    -       def    ← vreg1 在此定义
  add vreg1, vreg_b      live    live    use+def
  mov eax, vreg1         -       -       use    ← vreg1 最后使用
  ret                    -       -       -

  vreg_a 和 vreg_b 在整个函数期间都活跃（参数），vreg1 从 mov 定义到 mov eax 使用。

  3.3 构建干涉图 (interferencegraph.py)

  两个虚拟寄存器同时在某个程序点活跃，则它们有一条干涉边。

  干涉图:
    vreg_a ──── vreg1      ← vreg_a 在 vreg1 定义时仍活跃 → 干涉
    vreg_b ──── vreg1      ← vreg_b 在 vreg1 定义时仍活跃 → 干涉
    vreg_a ──── vreg_b     ← a 和 b 同时活跃 → 干涉

  邻接数: vreg_a=2, vreg_b=2, vreg1=2

  3.4 图着色 (registerallocator.py:228 alloc_frame())

  这是迭代寄存器合并 (Iterated Register Coalescing)，George-Appel 算法。核心循环 (registerallocator.py:244-257)：

  while True:
      if self.simplify_worklist:   self.simplify()    # ①简化
      elif self.worklistMoves:     self.coalesc()     # ②合并
      elif self.freeze_worklist:   self.freeze()      # ③冻结
      elif self.spill_worklist:    self.select_spill() # ④溢出
      else: break

  ①简化 (Simplify)：度数 < K 的节点可以被删掉并压入栈——它们总能被着色。x86_64 有 16 个通用寄存器，3 个虚拟寄存器远小于
  K，所以全部节点都被简化入栈。

  ②合并 (Coalesce)：检查 mov 指令的源和目的——如果它们不干涉，合并为一个节点，消除这条 mov。在我们的例子中：
  mov vreg1, vreg_a   ← 这是 move 指令
  如果 vreg1 和 vreg_a 不干涉（它们确实不干涉，vreg_a 在 add 中还要用，但合并后没问题），则合并它们。

  ⑤分配颜色 (Assign Colors)：从栈中逐个弹出节点，分配一个与已着色邻居不冲突的颜色（物理寄存器）：

  弹栈顺序: vreg_b → vreg1(vreg_a) → ...

  vreg_b:    邻居已着色={}, 可用={rdi,rsi,rdx,rcx,...}, 分配 rsi
  vreg1+合并: 邻居={vreg_b=rsi}, 可用={rdi,rdx,rcx,...}, 分配 rdi

  3.5 最终输出

  apply_colors() 把虚拟寄存器替换为物理寄存器：

  分配前:                          分配后:
  mov vreg1, vreg_a      →        (合并消除 — 不需要 mov!)
  add vreg1, vreg_b      →        add edi, esi
  mov eax, vreg1         →        mov eax, edi

  再加序言/尾声 emit_frame_to_stream：

  sum:
      push rbp
      mov rbp, rsp
  sum_block0:
      add edi, esi       ← a+b, 结果在 edi (x86_64 ABI: rdi=第1参数, rsi=第2参数)
  sum_epilog:
      mov eax, edi       ← 返回值放入 eax
      pop rbp
      ret

  ---
  总结：三个子系统的全景串联

  int sum(int a, int b) { return a + b; }
          │
          ▼ lexer.py + preprocessor.py
  [int] [ID:sum] [(] [int] [ID:a] [,] [int] [ID:b] [)] [{] [return] [ID:a] [+] [ID:b] [;] [}]
          │
          ▼ parser.py (递归下降)
  FunctionDeclaration { name="sum", params=[a,b], body={ Return(BinaryOp("+", a, b)) } }
          │
          ▼ semantics.py (类型检查, 符号解析)
          │
          ▼ codegenerator.py (AST walk → IR emit)
  ir.Function "sum" { entry_block: { alloc a, alloc b, store a, store b,
                                      load a, load b, add, return } }
          │
          ▼ opt/mem2reg.py (消除栈变量)
  ir.Function "sum" { entry_block: { a + b, return } }
          │
          ▼ irdag.py + instructionselector.py (BURG)
  SelectionDAG: RET ← ADDI32(REGI32(a), REGI32(b))
          │ pattern_add32: context.emit(AddRegRm(d, c1))
          ▼
  mov vreg1, vreg_a       ← BURG选出的最优指令 (size=2)
  add vreg1, vreg_b
  mov eax, vreg1
          │
          ▼ flowgraph.py → interferencegraph.py → registerallocator.py
  干涉图: vreg_a─vreg1, vreg_b─vreg1, vreg_a─vreg_b
  简化→合并(mov消除)→着色: {vreg_a→rdi, vreg_b→rsi, vreg1合并到rdi}
          │
          ▼ emit_frame_to_stream
  add edi, esi            ← 最终的 x86_64 汇编
  mov eax, edi
  ret

  三个子系统的关键文件一览：

  ┌────────────┬────────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────┐
  │   子系统   │                          核心文件                          │                       核心类/函数                       │
  ├────────────┼────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ C 前端     │ lang/c/lexer.py, parser.py, semantics.py, codegenerator.py │ CParser, CSemantics, CCodeGenerator                     │
  ├────────────┼────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ BURG       │ codegen/burg.py, instructionselector.py, irdag.py          │ BurgSystem, InstructionSelector1, SelectionGraphBuilder │
  │ 树匹配     │                                                            │                                                         │
  ├────────────┼────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ 图着色分配 │ codegen/registerallocator.py, flowgraph.py,                │ GraphColoringRegisterAllocator, FlowGraph,              │
  │            │ interferencegraph.py                                       │ InterferenceGraph                                       │
  └────────────┴────────────────────────────────────────────────────────────┴─────────────────────────────────────────────────────────┘
