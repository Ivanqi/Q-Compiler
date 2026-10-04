"""C 前端包入口：集中导出 C 编译流水线各阶段的组件。

作为包的门面，这里统一暴露预处理(CPreProcessor)→词法(CLexer)→语法(CParser)
→语义(CSemantics)→IR 生成(CBuilder) 所需的公开类与函数，方便外部按需引用。

C front end.
"""

from qcc.frontend.c.api import c_to_ir, preprocess
from qcc.frontend.c.builder import CBuilder, create_ast, parse_text, parse_type
from qcc.frontend.c.context import CContext
from qcc.frontend.c.lexer import CLexer
from qcc.frontend.c.options import COptions
from qcc.frontend.c.parser import CParser
from qcc.frontend.c.preprocessor import CPreProcessor
from qcc.frontend.c.printer import CPrinter, render_ast
from qcc.frontend.c.semantics import CSemantics
from qcc.frontend.c.synthesize import CSynthesizer
from qcc.frontend.c.token import CTokenPrinter
from qcc.frontend.c.utils import CAstPrinter, print_ast

__all__ = [
    "create_ast",
    "preprocess",
    "c_to_ir",
    "print_ast",
    "parse_text",
    "render_ast",
    "parse_type",
    "CBuilder",
    "CContext",
    "CLexer",
    "COptions",
    "CPreProcessor",
    "CParser",
    "CAstPrinter",
    "CSemantics",
    "CSynthesizer",
    "CPrinter",
    "CTokenPrinter",
]
