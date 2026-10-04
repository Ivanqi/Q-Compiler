"""机器架构描述模块：定义架构抽象基类 MachineArchitecture/Architecture
与虚拟机架构 VirtualMachineArchitecture。Architecture 规定后端必须实现
的接口——序言/尾声生成、调用序列、参数与返回值位置，并配套帧（Frame）
创建、寄存器类查询、重定位与运行时库获取。

Machine architecture description module"""

import abc
import logging
from functools import lru_cache

from qcc.midend import ir
from qcc.backend.arch.asm_printer import AsmPrinter
from qcc.backend.arch.stack import Frame, FramePointerLocation


# Idea: create several types of architectures.
# One for real machines, one for virtual machines
class MachineArchitecture(abc.ABC):
    """所有机器架构的抽象基类：持有架构信息 info 与帧指针位置
    fp_location，提供创建帧和按类型查找寄存器类的通用实现。
    """

    logger = logging.getLogger("arch")
    name = None
    desc = None
    option_names = ()

    def __init__(self):
        self.info = None
        self.fp_location = FramePointerLocation.TOP

    def new_frame(self, frame_name, function):
        """为某个 IR 函数创建名为 frame_name 的新帧（Frame）。

    Create a new frame with name frame_name for an ir-function"""
        frame = Frame(frame_name, fp_location=self.fp_location)
        return frame

    def get_reg_class(self, bitsize=None, ty=None):
        """按位宽或 IR 类型查找对应的寄存器类。

    Look for a register class"""
        if bitsize:
            ty = {8: ir.i8, 16: ir.i16, 32: ir.i32, 64: ir.i64}[bitsize]
        if ty:
            return self.info.value_classes[ty]
        raise NotImplementedError()  # pragma: no cover


class VirtualMachineArchitecture(MachineArchitecture):
    """虚拟机架构：不针对真实硬件，供解释器/虚拟机类目标复用。

    Virtual machine architecture."""

    pass


class Architecture(MachineArchitecture):
    """所有具体目标后端的基类：解析架构选项、持有 isa/info 与汇编打印器，
    并声明序言、尾声、调用、参数/返回值定位等必须实现的抽象接口。

    Base class for all targets"""

    def __init__(self, options=None):
        """Create a new machine instance.

        Arguments:
            options: a tuple with which options to enable.
        """
        super().__init__()
        self.logger.debug("Creating %s arch", self.name)
        self.option_settings = dict.fromkeys(self.option_names, False)
        if options:
            assert isinstance(options, tuple)
            for option_name in options:
                assert option_name in self.option_names
                self.option_settings[option_name] = True
        self.asm_printer = AsmPrinter()

    def has_option(self, name):
        """Check for an option setting selected"""
        return self.option_settings[name]

    def __repr__(self):
        opstring = ""
        for n in self.option_names:
            if self.option_settings[n]:
                opstring += ":" + n
        return f"{self.name}{opstring}-arch"

    def make_id_str(self):
        """Return a string uniquely identifying this machine"""
        options = [n for n, v in self.option_settings.items() if v]
        return ":".join([self.name] + options)

    def get_size(self, typ):
        """Get type of ir type"""
        return self.info.get_size(typ)

    def move(self, dst, src):  # pragma: no cover
        """Generate a move from src to dst"""
        raise NotImplementedError("Implement this")

    @abc.abstractmethod
    def gen_prologue(self, frame):  # pragma: no cover
        """生成函数序言（栈帧建立、被调用者保存寄存器入栈等）指令。

        Generate instructions for the epilogue of a frame.

        Arguments:
            frame: the function frame for which to create a prologue
        """
        raise NotImplementedError("Implement this!")

    @abc.abstractmethod
    def gen_epilogue(self, frame):  # pragma: no cover
        """生成函数尾声（恢复现场、栈帧拆除、返回）指令。

        Generate instructions for the epilogue of a frame.

        Arguments:
            frame: the function frame for which to create a prologue
        """
        raise NotImplementedError("Implement this!")

    @abc.abstractmethod
    def gen_call(self, frame, label, args, rv):  # pragma: no cover
        """生成函数调用指令序列（调用由 label 指定的函数）。

        Generate instructions for a function call."""
        raise NotImplementedError("Implement this!")

    @abc.abstractmethod
    def gen_function_enter(self, args):  # pragma: no cover
        """生成从约定的实参位置（寄存器/栈）取出参数放入给定虚拟寄存器的代码。

        Generate code to extract arguments from the proper locations

        The default implementation tries to use registers and move
        instructions.

        Arguments:
            args: an iterable of virtual registers in which the arguments
                  must be placed.
        """
        raise NotImplementedError("Implement me!")

    @abc.abstractmethod
    def gen_function_exit(self, rv):  # pragma: no cover
        """生成把返回值 rv 放到约定返回位置的代码。"""
        raise NotImplementedError("Implement me!")

    def between_blocks(self, frame):
        """Generate any instructions here if needed between two blocks"""
        return []

    @abc.abstractmethod
    def determine_arg_locations(self, arg_types):  # pragma: no cover
        """按调用约定确定各参数类型对应的存放位置。

        Determine argument location for a given function"""
        raise NotImplementedError("Implement this")

    @abc.abstractmethod
    def determine_rv_location(self, ret_type):  # pragma: no cover
        """按返回值类型确定函数返回值的存放位置。

        Determine the location of a return value of a function given the
        type of return value"""
        raise NotImplementedError("Implement this")

    def get_reloc(self, name):
        """按名称从 isa 的重定位表中取出重定位类型。

        Retrieve a relocation identified by a name"""
        return self.isa.relocation_map[name]

    def get_runtime(self):
        """Create an object with an optional runtime."""
        import io

        from qcc.api import asm

        asm_src = ""
        return asm(io.StringIO(asm_src), self)

    @lru_cache(maxsize=30)
    def get_compiler_rt_lib(self):
        """Gets the runtime for the compiler. Returns an object with the
        compiler runtime for this architecture"""
        return self.get_runtime()

    runtime = property(get_compiler_rt_lib)

    def get_reloc_type(self, reloc_type, symbol):
        """Re-implement this function to support ELF format
        relocations.
        """
        raise NotImplementedError("ELF format relocations")
