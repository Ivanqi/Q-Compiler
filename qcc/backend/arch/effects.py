"""指令效果（Effect）描述模块：用 ("set", 左值, 右值) 三元组表达一条指令
对程序状态的影响（如设置寄存器、设置 PC），供数据流分析等使用；PC 是程序
计数器这一特殊位置的标识名。

Effect descriptions."""


def Assign(lhs, rhs):
    """构造赋值效果：把 rhs 写到 lhs（即 ("set", lhs, rhs)）。"""
    return ("set", lhs, rhs)


def Set(lhs, rhs):
    """Set 是 Assign 的别名，构造赋值效果元组。"""
    return Assign(lhs, rhs)


# 程序计数器（控制流位置）的标识名：
PC = "pc"
