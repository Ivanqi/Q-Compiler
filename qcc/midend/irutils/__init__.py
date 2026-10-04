"""IR 工具包：对 ir.Module 进行构造、读写、序列化、链接、校验等操作的入口。

功能说明：
    汇总导出中端 IR 的各类辅助工具——Builder（IR 构造辅助）、reader/writer
    （文本格式读写）、io（JSON 序列化）、link（模块链接）、verify（一致性校验）、
    instrument（插桩）。位于中端（前端生成 IR 之后、后端代码生成之前）使用。

Various utilities to operate on IR-code."""

from qcc.midend.irutils.builder import Builder, split_block
from qcc.midend.irutils.instrument import add_tracer
from qcc.midend.irutils.io import from_json, to_json
from qcc.midend.irutils.link import ir_link
from qcc.midend.irutils.reader import Reader, read_module
from qcc.midend.irutils.verify import Verifier, verify_module
from qcc.midend.irutils.writer import Writer, print_module

__all__ = [
    "Builder",
    "ir_link",
    "print_module",
    "read_module",
    "Reader",
    "split_block",
    "Verifier",
    "verify_module",
    "Writer",
    "to_json",
    "from_json",
    "add_tracer",
]
