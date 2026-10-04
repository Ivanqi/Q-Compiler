"""词法与语法分析工具包：pcc（python compiler compiler）的实现集合。

这些模块构成 C 前端的基础设施：Lexer 基类、手写词法器（handlexer）、
正则词法器（baselex）、递归下降基类（recursivedescent）、
LR 分析器生成器（lr、yacc）与文法表示（grammar），
服务于“预处理→词法→语法(parser.py)”阶段，为后续语义分析与 IR 生成提供语法树。

This module contains the pcc (python compiler compiler).
It is an alternative to bison or yacc etc..
"""
