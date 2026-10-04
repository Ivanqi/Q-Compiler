"""调用约定模块：目前仅提供 CallingConvention 基类，用来描述目标平台的
函数调用约定（参数传递位置与返回值位置等规则由各后端据此实现）。

Calling conventions"""


class CallingConvention:
    """调用约定基类：以名称标识一套参数传递与返回值约定。"""

    def __init__(self, name):
        self.name = name
