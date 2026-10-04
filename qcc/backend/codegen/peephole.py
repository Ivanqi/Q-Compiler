"""PeepHoleStream：指令输出前的滑动窗口，做简单窥孔优化。

在最终输出前检查相邻的几条指令（如冗余的寄存器搬移、可合并的
跳转），用更短/更少的指令替换。codegen.emit_frame_to_stream
把指令先过 PeepHoleStream 再进入真实输出流。Peephole optimization using the pipe-filter approach.

We face a certain stream of instructions. We take a look
at a very specific window and check if we can apply the
optimization. It's like scrolling over a sequence of
instructions and checking for possible optimizations.

"""

import logging

from qcc.backend.arch.generic_instructions import Label
from qcc.backend.binutils.outstream import OutputStream

logger = logging.getLogger("peephole")


class PeepHoleStream(OutputStream):
    """This is a peephole optimizing output stream.

    Having the peep hole optimizer as an output stream allows
    to use the peephole optimizer in several places.
    """

    def __init__(self, downstream):
        super().__init__()
        self._downstream = downstream
        self._window = []

    def do_emit(self, item):
        self._window.append(item)
        self.clip_window(2)
        if len(self._window) == 2:
            a, b = self._window
            if hasattr(a, "effect") and hasattr(b, "effect"):
                # print('peephole', self._window)
                effect_a = a.effect()
                effect_b = b.effect()
                # print('a', a.effect())
                # print('b', b.effect())
                if effect_a == effect_b:
                    if not isinstance(a, Label):
                        logger.debug("Peephole remove %s", a)
                        self._window.pop(0)

    def clip_window(self, size):
        """Flush items, until we have `size` items in scope."""
        while len(self._window) > size:
            item = self._window.pop(0)
            self._downstream.emit(item)

    def flush(self):
        """Flush remaining items in the peephole window."""
        self.clip_window(0)


class PeepHoleOptimization:
    """Inherit this class to implement a peephole optimization."""

    def apply(self):
        pass


# def peephole():
#     for optimization in optimizations:
#         optimization.filter
