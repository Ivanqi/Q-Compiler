"""IR 插桩：给模块里的每个函数入口插入对外部 trace 函数的调用。

功能说明：
    add_tracer 在每个函数入口块插入"函数名字符串 + 取地址 + 调用外部过程"的
    指令序列，用于运行期跟踪函数调用（调试/教学）。属于中端对 IR 的辅助改造，
    不参与优化流水线，按需手动调用。

Functions to add instrumentation to IR code."""

import logging

from qcc.midend import ir


def add_tracer(ir_module, trace_function_name="trace"):
    """Instrument the given ir-module with a call tracer function"""
    logger = logging.getLogger("instrument")
    trace_func = ir.ExternalProcedure(trace_function_name, [ir.ptr])
    ir_module.add_external(trace_func)
    logger.info("Add trace function to %s", ir_module)
    for function in ir_module.functions:
        # Create 0 terminated string of function name:
        encoded_name = function.name.encode("ascii") + bytes([0])
        name_literal = ir.LiteralData(encoded_name, "func_name")
        name_ptr = ir.AddressOf(name_literal, "name_ptr")
        trace_call = ir.ProcedureCall(trace_func, [name_ptr])
        entry = function.entry
        entry.insert_instruction(trace_call)
        entry.insert_instruction(name_ptr)
        entry.insert_instruction(name_literal)
