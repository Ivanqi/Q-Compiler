# -*- coding: utf-8 -*-
"""① 前端：把 C 语言源码翻译为中间表示（midend.ir）。

- tools/  词法/语法分析工具基础（handlexer、recursivedescent、yacc…）
- c/      C 前端：lexer → preprocessor → parser → semantics →
           builder → codegenerator（AST → IR）
- common.py 跨语言公共设施（SourceLocation、Token）
"""
