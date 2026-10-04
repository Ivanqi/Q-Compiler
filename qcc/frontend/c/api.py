"""C 前端对外驱动/门面模块：提供预处理与 C 源码到 IR 的编译入口。

此模块串起整条 C 前端流水线：预处理(preprocessor)→词法(lexer)→语法(parser)
→语义(semantics)→IR 生成(codegenerator)，并对外提供 preprocess() 与
c_to_ir() 等入口函数，编译产物随后交由优化器与后端继续处理。

Drive / facade module for the C frontend.
"""

import io

from qcc.utils.reporting import DummyReportGenerator
from qcc.frontend.c.builder import CBuilder
from qcc.frontend.c.options import COptions
from qcc.frontend.c.preprocessor import CPreProcessor
from qcc.frontend.c.token import CTokenPrinter


def preprocess(f, output_file, coptions=None):
    """对 C 源文件做预处理，并把 token 流写入输出文件。

    Pre-process a file into the other file.
    """
    if coptions is None:
        coptions = COptions()
    preprocessor = CPreProcessor(coptions)
    filename = f.name if hasattr(f, "name") else None
    tokens = preprocessor.process_file(f, filename=filename)
    CTokenPrinter().dump(tokens, file=output_file)


def c_to_ir(source: io.TextIOBase, march, coptions=None, reporter=None):
    """C 前端主入口：把 C 源代码翻译为中间表示（IR）模块。

    内部依次驱动语法/语义分析与 IR 生成（CBuilder.build），
    返回的 ir.Module 可直接交给优化器与后端。

    C to ir translation.

    Args:
        source (file-like object): The C source to compile.
        march (str): The targetted architecture.
        coptions: C specific compilation options.

    Returns:
        An :class:`qcc.midend.ir.Module`.
    """

    if not reporter:  # pragma: no cover
        reporter = DummyReportGenerator()

    if not coptions:  # pragma: no cover
        coptions = COptions()

    from qcc.api import get_arch

    march = get_arch(march)
    cbuilder = CBuilder(march.info, coptions)
    assert isinstance(source, io.TextIOBase)
    if hasattr(source, "name"):
        filename = getattr(source, "name")
    else:
        filename = None

    ir_module = cbuilder.build(source, filename, reporter=reporter)
    return ir_module
