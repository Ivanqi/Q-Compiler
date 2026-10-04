"""
The api module contains a set of handy functions to invoke compilation,
linking and assembling.

（迁移说明：本模块由 ppci/api.py 裁剪而来，只保留 C 语言编译链路。
已移除 build 构建系统、wasm 及其余语言前端（c3/python/bf/ws…）、
PE/ldb/uimage 等输出格式；TaskError 内联到 qcc.common。）
"""

import io
import logging
import os
import stat

from qcc.backend.arch import get_arch, get_current_arch
from qcc.backend.binutils.archive import archive
from qcc.backend.binutils.debuginfo import DebugInfo
from qcc.backend.binutils.disasm import Disassembler
from qcc.backend.binutils.linker import link
from qcc.backend.binutils.objectfile import ObjectFile, get_object
from qcc.backend.binutils.outstream import (
    BinaryOutputStream,
    FunctionOutputStream,
    MasterOutputStream,
    TextOutputStream,
)
from qcc.backend.codegen import CodeGenerator
from qcc.backend.format.elf import write_elf
from qcc.backend.format.hexfile import HexFile
from qcc.common import CompilerError, DiagnosticsManager, TaskError, get_file
from qcc.frontend.c import COptions, c_to_ir, preprocess
from qcc.midend.irutils import verify_module
from qcc.midend.opt import (
    CleanPass,
    CommonSubexpressionEliminationPass,
    ConstantFolder,
    LoadAfterStorePass,
)
from qcc.midend.opt.cjmp import CJumpPass
from qcc.midend.opt.mem2reg import Mem2RegPromotor
from qcc.midend.opt.tailcall import TailCallOptimization
from qcc.midend.opt.transform import DeleteUnusedInstructionsPass, RemoveAddZeroPass
from qcc.utils.reporting import DummyReportGenerator, HtmlReportGenerator

__all__ = [
    "asm",
    "archive",
    "cc",
    "link",
    "objcopy",
    "optimize",
    "preprocess",
    "get_arch",
    "get_current_arch",
    "is_platform_supported",
    "ir_to_object",
    "ir_to_assembly",
]


def get_reporter(reporter):
    if reporter is None:
        return DummyReportGenerator()
    elif isinstance(reporter, str):
        if reporter.endswith(".html"):
            f = open(reporter, "w", encoding="utf8")
            r = HtmlReportGenerator(f)
            r.header()
            return r
        else:
            raise ValueError(f"Cannot determine report type for {reporter}")
    else:
        return reporter


def is_platform_supported():
    """Determine if this platform is supported"""
    return get_current_arch() is not None


def asm(source, march, debug=False):
    """Assemble the given source for machine march.

    Args:
        source (str): can be a filename or a file like object.
        march (str): march can be a :class:`qcc.backend.arch.arch.Architecture`
            instance or a string indicating the machine architecture.
        debug: generate debugging information

    Returns:
        A :class:`qcc.backend.binutils.objectfile.ObjectFile` object

    .. doctest::

        >>> import io
        >>> from qcc.api import asm
        >>> source_file = io.StringIO("db 0x77")
        >>> obj = asm(source_file, 'arm')
        >>> print(obj)
        CodeObject of 1 bytes
    """
    logger = logging.getLogger("assemble")
    diag = DiagnosticsManager()
    march = get_arch(march)
    assembler = march.assembler
    source = get_file(source)
    obj = ObjectFile(march)
    if debug:
        obj.debug_info = DebugInfo()
    logger.debug("Assembling into code section")
    ostream = BinaryOutputStream(obj)
    ostream.select_section("code")
    try:
        assembler.prepare()
        assembler.assemble(source, ostream, diag, debug=debug)
        assembler.flush()
    except CompilerError as ex:
        diag.error(ex.msg, ex.loc)
        diag.print_errors()
        raise TaskError("Errors during assembling") from ex
    return obj


def disasm(data, march):
    """Disassemble the given binary data for machine march.

    Args:
        data: a filename or a file like object.
        march: a machine instance or a string indicating the architecture.

    .. doctest::

        >>> import io
        >>> from qcc.api import disasm
        >>> source_file = io.BytesIO([0x77])
        >>> disasm(source_file, 'arm')
    """
    march = get_arch(march)
    disassembler = Disassembler(march)
    f = get_file(data)
    data = f.read()
    f.close()
    ostream = TextOutputStream()
    disassembler.disasm(data, ostream)


OPT_LEVELS = ("0", "1", "2", "s")


def optimize(ir_module, level=0, reporter=None):
    """Run a bag of tricks against the :doc:`ir-code<ir/index>`.

    This is an in-place operation!

    Args:
        ir_module (qcc.midend.ir.Module): The ir module to optimize.
        level: The optimization level, 0 is default. Can be 0,1,2 or s
            0: No optimization
            1: some optimization
            2: more optimization
            s: optimize for size
        reporter: Report detailed log to this reporter
    """
    logger = logging.getLogger("optimize")
    level = str(level)

    logger.info("Optimizing module %s level %s", ir_module.name, level)

    if reporter:
        reporter.message(f"{ir_module} before optimization:")
        reporter.message(f"{ir_module} {ir_module.stats()}")
        reporter.dump_ir(ir_module)

    assert level in OPT_LEVELS
    if level == "0":
        return

    # TODO: differentiate between optimization levels!

    # Optimization passes (bag of tricks) run them three times:
    opt_passes = [
        Mem2RegPromotor(),
        RemoveAddZeroPass(),
        ConstantFolder(),
        CommonSubexpressionEliminationPass(),
        TailCallOptimization(),
        LoadAfterStorePass(),
        DeleteUnusedInstructionsPass(),
        CleanPass(),
    ] * 3

    if level == "3":
        opt_passes.append(CJumpPass())

    # Run the passes over the module:
    verify_module(ir_module)
    for opt_pass in opt_passes:
        opt_pass.run(ir_module)
        # reporter.message('{} after {}:'.format(ir_module, opt_pass))
        # reporter.dump_ir(ir_module)

    if reporter:
        # Dump report:
        reporter.message(f"{ir_module} after optimization:")
        reporter.message(f"{ir_module} {ir_module.stats()}")
        reporter.dump_ir(ir_module)

    verify_module(ir_module)


def ir_to_stream(
    ir_module, march, output_stream, reporter=None, debug=False, opt="speed"
):
    """Translate IR module to output stream."""
    march = get_arch(march)

    if not reporter:  # pragma: no cover
        reporter = DummyReportGenerator()

    code_generator = CodeGenerator(march, reporter, optimize_for=opt)
    verify_module(ir_module)

    # Code generation:
    code_generator.generate(ir_module, output_stream, debug=debug)


def ir_to_assembly(ir_modules, march, add_binary=False):
    """Translate the given ir-code into assembly code."""
    text_file = io.StringIO()
    text_stream = TextOutputStream(f=text_file, add_binary=add_binary)
    for ir_module in ir_modules:
        ir_to_stream(ir_module, march, text_stream)
    return text_file.getvalue()


def ir_to_object(
    ir_modules, march, reporter=None, debug=False, opt="speed", outstream=None
):
    """Translate IR-modules into code for the given architecture.

    Args:
        ir_modules: a collection of ir-modules that will be transformed into
            machine code.
        march: the architecture for which to compile.
        reporter: reporter to write compilation report to
        debug (bool): include debugging information
        opt (str): optimization goal. Can be 'speed', 'size' or 'co2'.
        outstream: instruction stream to write instructions to

    Returns:
        ObjectFile: An object file
    """
    march = get_arch(march)

    if not reporter:  # pragma: no cover
        reporter = DummyReportGenerator()

    reporter.heading(2, "Code generation")
    reporter.message(f"Target: {march}")

    # Construct output object:
    obj = ObjectFile(march)
    if debug:
        obj.debug_info = DebugInfo()

    # Construct the various instruction streams:
    binary_output_stream = BinaryOutputStream(obj)
    sub_streams = [binary_output_stream]
    instruction_list = []
    sub_streams.append(FunctionOutputStream(instruction_list.append))
    if outstream:
        sub_streams.append(outstream)
    output_stream = MasterOutputStream(sub_streams)

    for ir_module in ir_modules:
        ir_to_stream(
            ir_module,
            march,
            output_stream,
            reporter=reporter,
            debug=debug,
            opt=opt,
        )

    reporter.message("All modules generated!")
    reporter.dump_instructions(instruction_list, march)
    return obj


def cc(
    source: io.TextIOBase,
    march,
    coptions=None,
    opt_level=0,
    debug=False,
    reporter=None,
):
    """C compiler. compiles a single source file into an object file.

    Args:
        source: file like object from which text can be read
        march: The architecture for which to compile
        coptions: options for the C frontend
        debug: Create debug info when set to True

    Returns:
        an object file

    .. doctest::

        >>> import io
        >>> from qcc.api import cc
        >>> source_file = io.StringIO("void main() { int a; }")
        >>> obj = cc(source_file, 'x86_64')
        >>> print(obj)
        CodeObject of 20 bytes

    """
    if not reporter:  # pragma: no cover
        reporter = DummyReportGenerator()

    if not coptions:
        coptions = COptions()

    ir_module = c_to_ir(source, march, coptions=coptions, reporter=reporter)
    reporter.message(f"{ir_module} {ir_module.stats()}")
    reporter.dump_ir(ir_module)
    optimize(ir_module, level=opt_level, reporter=reporter)
    return ir_to_object([ir_module], march, debug=debug, reporter=reporter)


def objcopy(obj: ObjectFile, image_name: str, fmt: str, output_filename):
    """Copy some parts of an object file to an output"""
    fmts = ["bin", "hex", "elf"]
    if fmt not in fmts:
        formats = ", ".join(fmts[:-1]) + " and " + fmts[-1]
        raise TaskError(f"Only {formats} are supported")

    obj = get_object(obj)
    if fmt == "bin":
        image = obj.get_image(image_name)
        with open(output_filename, "wb") as output_file:
            output_file.write(image.data)
    elif fmt == "elf":
        elf_type = "executable" if obj.is_executable else "relocatable"
        with open(output_filename, "wb") as output_file:
            write_elf(obj, output_file, type=elf_type)

        if elf_type == "executable":
            chmod_x(output_filename)

    elif fmt == "hex":
        image = obj.get_image(image_name)
        hexfile = HexFile()
        hexfile.add_region(image.address, image.data)
        with open(output_filename, "w", encoding="utf8") as output_file:
            hexfile.save(output_file)
    else:  # pragma: no cover
        raise NotImplementedError("output format not implemented")


def chmod_x(filename):
    """Perform sort of chmod +x on filename."""
    status = os.stat(filename)
    os.chmod(filename, status.st_mode | stat.S_IEXEC)
