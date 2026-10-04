"""C 构建器（门面/总装车间）：把 C 源码文本转换（装配）成 IR 模块。

功能说明：
- 作用：本模块自身不做语法/语义分析，而是把 C 前端的各组件按正确顺序接线组装，
  完成「C 源码 → 预处理+词法 → 语法/语义分析（带类型的 AST）→ IR」的整条流水线。
- 在 前端→中端→后端 流水线中的位置：前端最外层的入口（CBuilder.build 是 C→IR 的
  唯一主入口）；其产物 ir.Module 随后交给中端（qcc.api.optimize 里的 mem2reg、
  常量折叠等优化 pass）与后端（指令选择 → 寄存器分配 → 汇编 → 链接）。
- 关键类/函数：CBuilder（门面）、_parse（真正组装五步流水线的地方）、
  parse_text / create_ast（文本 → AST）、parse_type（类型片段 → 类型 AST）。
- 与 parser.py 的联动：parser.keywords 交给 prepare_for_parsing 做 Token 适配（把
  关键字 ID 改写为关键字类型）；CSemantics 注入 parser 充当 on_xxx 回调对象；
  parser.parse() 返回的编译单元其实是 semantics 产出的；parse_type 复用 parser 的
  init_lexer / parse_typename。
详见 docs/builder.py.md。
"""

import io
import logging

from qcc.frontend.c.codegenerator import CCodeGenerator
from qcc.frontend.c.context import CContext
from qcc.frontend.c.options import COptions
from qcc.frontend.c.parser import CParser
from qcc.frontend.c.preprocessor import CPreProcessor, prepare_for_parsing
from qcc.frontend.c.semantics import CSemantics
from qcc.frontend.c.utils import print_ast


class CBuilder:
    """C 代码构建器门面：持有目标架构信息与 C 方言选项，对外提供 C → IR 的构建入口。

    C builder that converts C code into ir-code
    """

    logger = logging.getLogger("cbuilder")

    def __init__(self, arch_info, coptions):
        self.arch_info = arch_info
        self.coptions = coptions
        self.cgen = None

    def build(self, src: io.TextIOBase, filename: str, reporter=None):
        """主入口：跑完整条流水线，把 C 源码文件对象编译成 IR 模块并返回。"""
        if reporter:
            reporter.heading(2, "C builder")
            reporter.message(
                f"Welcome to the C building report for {filename}"
            )
        cdialect = self.coptions["std"]
        self.logger.info("Starting C compilation (%s)", cdialect)

        context = CContext(self.coptions, self.arch_info)
        compile_unit = _parse(src, filename, context)

        if reporter:
            f = io.StringIO()
            print_ast(compile_unit, file=f)
            reporter.dump_source("C-ast", f.getvalue())
        cgen = CCodeGenerator(context)
        return cgen.gen_code(compile_unit)

    def _create_ast(self, src, filename):
        """便捷方法：复用本实例保存的架构信息与选项，从源码得到 AST。"""
        return create_ast(
            src, self.arch_info, filename=filename, coptions=self.coptions
        )


def parse_text(text, arch="x86_64"):
    """从 C 源码字符串解析出 AST（测试/示例常用入口，架构只按名字查表）。

    Parse given C sourcecode into an AST
    """
    from qcc.api import get_arch

    f = io.StringIO(text)
    arch_info = get_arch(arch).info
    coptions = COptions()
    context = CContext(coptions, arch_info)
    return _parse(f, "?", context)


def create_ast(src, arch_info, filename="<snippet>", coptions=None):
    """与 parse_text 类似，但架构信息由调用者直接传入，coptions 可选。

    Create a C ast from the given source
    """
    if coptions is None:
        coptions = COptions()
    context = CContext(coptions, arch_info)
    return _parse(src, filename, context)


def _parse(src, filename, context):
    """全文件的核心：按顺序组装 预处理+词法、语义层、语法层与 Token 适配层，产出 AST。"""
    # ① 预处理 + 词法：源码 → Token 迭代器
    preprocessor = CPreProcessor(context.coptions)
    tokens = preprocessor.process_file(src, filename)
    # ② 语义层与 ③ 语法层：semantics 作为回调对象注入 parser，两者交替工作
    semantics = CSemantics(context)
    parser = CParser(context.coptions, semantics)
    # ④ 适配层：去空白、按 parser 的关键字表把 ID 改写成关键字、拼接相邻字符串
    tokens = prepare_for_parsing(tokens, parser.keywords)
    # ⑤ 跑解析器，返回的编译单元（AST）实际由 semantics 逐步累积而成
    ast = parser.parse(tokens)
    return ast


def parse_type(text, context, filename="foo.c"):
    """只解析一个"类型名"（如 int[2]、struct S *）返回类型 AST，不解析完整程序。

    Parse given C-type AST.

    For example:

    >>> from qcc.api import get_arch
    >>> from qcc.frontend.c import parse_type, CContext, COptions
    >>> example_arch = get_arch('example')
    >>> coptions = COptions()
    >>> context = CContext(coptions, msp430_arch.info)
    >>> ast = parse_type('int[2]', context)
    >>> context.eval_expr(ast.size)
    2
    >>> context.sizeof(ast)
    4
    """
    # TODO: fix + ; hack below:
    # 在类型名后硬补分号，让 parse_typename 解析完类型规格后能干净收尾（见 docs）。
    src = io.StringIO(text + ";")
    preprocessor = CPreProcessor(context.coptions)
    tokens = preprocessor.process_file(src, filename)
    semantics = CSemantics(context)
    parser = CParser(context.coptions, semantics)
    tokens = prepare_for_parsing(tokens, parser.keywords)
    # 不跑完整入口 parser.parse()，而是手动执行"开机三件套"后只调用 parse_typename：
    # 复用 parser 的初始化逻辑，但不解析整个编译单元。
    parser.init_lexer(tokens)
    parser.typedefs = set()
    return parser.parse_typename()
