"""Package containing several graph algorithms

图算法子包：目前只有 fixed_point_dominator，即用朴素不动点迭代计算
支配者集合、后支配者集合，并由此推导直接支配者/直接后支配者。
它是 cfg 模块中 Lengauer-Tarjan 快速算法之外的另一套（较慢但直观的）
实现，主要被 ControlFlowGraph 用于后支配者信息计算。

Package containing several graph algorithms
"""
