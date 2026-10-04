"""解析器工具公共异常：供文法、LR 分析器与 yacc 生成器共用。

本模块是 C 前端词法/语法基础设施的公共部分，定义语法分析阶段的
两类错误：解析器生成错误与解析过程错误，供 parser.py 及其生成器抛出。

Common exceptions shared by the parser tooling.
"""


class ParserGenerationException(Exception):
    """解析器生成阶段出错时抛出（如文法冲突、符号未定义）。

    Raised when something goes wrong during parser generation"""

    pass


class ParserException(Exception):
    """解析过程中失败时抛出（如遇到无法处理的 Token）。

    Raised during a failure in the parsing process"""

    pass
