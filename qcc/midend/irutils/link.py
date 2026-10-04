"""IR 模块链接（ir_link）：把多个 IR 模块合并为一个。

类似目标文件的链接：符号解析后把各模块的函数/全局变量并入
同一个 ir.Module。Link two ir-modules, such that external references are resolved."""

from qcc.midend import ir
from qcc.midend.irutils.verify import verify_module


def ir_link(ir_modules, name="linked") -> ir.Module:
    """Link IR-modules into a single module.

    Example:

    .. doctest::

        >>> from qcc.midend import ir
        >>> from qcc.midend.irutils import ir_link
        >>> m1 = ir.Module('m1')
        >>> m2 = ir.Module('m2')
        >>> m3 = ir_link([m1, m2])

    Note that the original modules are not usable after this action.

    TODO: TBD: do not modify source modules?
    """
    mod0 = ir.Module(name)

    # Add all variables and functions:
    for module in ir_modules:
        for variable in module.variables:
            mod0.add_variable(variable)

        for p in module.functions:
            mod0.add_function(p)

    # Add externals, if not already resolved:
    internal_functions = {p.name: p for p in mod0.functions}
    for module in ir_modules:
        for external in module.externals:
            if isinstance(external, ir.ExternalSubRoutine):
                # TODO: check argument similarity?
                if external.name in internal_functions:
                    p = internal_functions[external.name]
                    external.replace_by(p)
                else:
                    mod0.add_external(external)
                    internal_functions[external.name] = external
            else:
                # TODO: link external variables?
                mod0.add_external(external)
                internal_functions[external.name] = external

    # Verify, just to be sure:
    verify_module(mod0)
    return mod0
