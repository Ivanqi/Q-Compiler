"""CastXML 输出的 XML 声明读取器：把 castxml 生成的 XML 转成内部结点树。

该模块不属于常规 C 前端主流水线（预处理→词法→语法→语义→IR），
而是借助外部工具 CastXML 解析 C 代码、再转换为内部 AST 的实验性辅助模块。

Reads xml produced by cast xml for further processing.
"""

import xml.etree.ElementTree as ET

from qcc.frontend.c.nodes import nodes


class CastXmlReader:
    """读取 castxml（CastXML 项目）为 C 代码生成的 XML，并转换为内部结点。

    Reads xml produced by cast xml for further processing.

    Cast xml converts (compiles) C code to xml format.

    https://github.com/CastXML/CastXML
    """

    def process(self, filename):
        tree = ET.parse(filename)
        root = tree.getroot()
        for child in root:
            print(child)

        declarations = []
        cu = nodes.CompilationUnit(declarations)
        return cu
