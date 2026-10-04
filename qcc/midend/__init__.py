# -*- coding: utf-8 -*-
"""② 中端：中间表示（IR）与优化。

- ir.py    统一 IR：Module → Function → Block → Instruction
- irutils/ IR 校验、Builder、文本读写
- opt/     优化 pass：mem2reg、常量折叠、CSE、尾调用、DCE、CleanPass…
- graph/   图算法基础：流图、支配树、干涉图用图结构
"""
