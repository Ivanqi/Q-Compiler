"""指令调度器（当前默认 NoScheduler，直接顺序发射）。

VLIW 类架构需要重排指令减少停顿；当前三个后端（arm/riscv/x86_64）
为顺序发射，保留此接口以备扩展。codegen 主循环的第二步。
This algorithm takes the selected instructions and schedules them in
a linear form.
"""


class InstructionScheduler:
    def schedule(self, graph, frame):
        # TODO: schedule traces in better order.
        # This is optional!
        pass
