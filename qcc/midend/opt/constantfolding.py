"""常量折叠 pass：在基本块内把常量表达式直接算出结果。

功能说明：
    把 "x = (4+5)" 折叠成 "x = 9"，并顺带做简单的重结合优化（如 (y+5)+5 → y+10、
    (y-5)-5 → y-10）；参与折叠的运算结果会按目标类型截断（correct 函数处理
    位宽与符号），保证折叠结果与运行期计算结果一致。
    属于中端优化阶段，由 api.optimize() 调用，排在 mem2reg/RemoveAddZeroPass 之后、
    CSE 之前（与 RemoveAddZeroPass 分工：它管"两边都是常量"，后者管"一边是恒等元"）。

    关键类/函数：
    - ConstantFolder：继承 BlockPass 的折叠 pass 主体；
    - is_const / eval_const：判断表达式是否可静态求值、并递归求值；
    - cast / correct / enhance：按类型截断与运算增强的辅助函数。
"""

import operator

from qcc.midend import ir
from qcc.midend.opt.transform import BlockPass


def cast(value, ty):
    """按给定类型做静态转换（指针取整、整数按键宽/符号截断）。

    Cast a value to the given type"""
    if isinstance(ty, ir.PointerTyp):
        return int(value)
    elif ty.is_integer:
        return correct(int(value), ty)
    else:
        assert isinstance(ty, ir.FloatingPointTyp)
        return value


def correct(value, ty):
    """把数值截断到目标类型的位宽（有符号则还原为负数）。

    Correct a value to the given bits"""
    bits = ty.bits
    signed = ty.signed
    base = 1 << bits
    value %= base
    return value - base if signed and value.bit_length() == bits else value


def enhance(f):
    """包装一个二元运算符，使其结果按类型截断。

    Create a new enhanced method that corrects for the given type"""
    return lambda ty, a, b: correct(f(a, b), ty)


class ConstantFolder(BlockPass):
    """在基本块内折叠常量表达式（并做 (y+5)+5、(y-5)-5 之类的重结合）。

    Try to fold common constant expressions"""

    def __init__(self):
        super().__init__()
        self.ops = {
            "+": enhance(operator.add),
            "-": enhance(operator.sub),
            "*": enhance(operator.mul),
            "%": enhance(operator.mod),
            "<<": enhance(operator.lshift),
            ">>": enhance(operator.rshift),
        }

    def is_const(self, value):
        """判断一个值是否可在编译期求值为常量（递归检查 Cast/Binop 的操作数）。

        Determine if a value can be evaluated as a constant value"""
        if isinstance(value, ir.Const):
            return True
        elif isinstance(value, ir.Cast):
            return self.is_const(value.src)
        elif isinstance(value, ir.Binop):
            return (
                value.operation in self.ops
                and value.ty.is_integer
                and self.is_const(value.a)
                and self.is_const(value.b)
            )
        else:
            return False

    def eval_const(self, value):
        """递归求值常量表达式，返回新的 Const 指令。

        Evaluate expression, and return a new const instance"""
        if isinstance(value, ir.Const):
            return value
        elif isinstance(value, ir.Binop):
            a = self.eval_const(value.a)
            b = self.eval_const(value.b)
            assert a.ty is b.ty
            assert a.ty is value.ty
            res = self.ops[value.operation](value.ty, a.value, b.value)
            return ir.Const(res, "new_fold", a.ty)
        elif isinstance(value, ir.Cast):
            c_val = self.eval_const(value.src)
            numeric_value = cast(c_val.value, value.ty)
            return ir.Const(numeric_value, "casted", value.ty)
        else:  # pragma: no cover
            raise NotImplementedError(str(value))

    def on_block(self, block):
        """对块内每条指令尝试折叠：命中则新建 const 插在其前并替换所有使用。"""
        instructions = list(block)
        count = 0
        for instruction in instructions:
            # First of all, skip values that are const already:
            if isinstance(instruction, ir.Const):
                continue

            if self.is_const(instruction):
                # Now we can replace x = (4+5) with x = 9
                cnst = self.eval_const(instruction)
                block.insert_instruction(cnst, before_instruction=instruction)
                instruction.replace_by(cnst)
                count += 1
            else:
                if (
                    isinstance(instruction, ir.Binop)
                    and isinstance(instruction.a, ir.Binop)
                    and instruction.a.operation == "+"
                    and self.is_const(instruction.a.b)
                    and (instruction.operation == "+")
                    and self.is_const(instruction.b)
                ):
                    # Now we can replace x = (y+5)+5 with x = y + 10
                    a = self.eval_const(instruction.a.b)
                    b = self.eval_const(instruction.b)
                    assert a.ty is b.ty
                    cn = ir.Const(a.value + b.value, "new_fold", a.ty)
                    block.insert_instruction(
                        cn, before_instruction=instruction
                    )
                    instruction.a = instruction.a.a
                    instruction.b = cn
                    assert instruction.ty is cn.ty
                    assert instruction.ty is instruction.a.ty
                    count += 1
                elif (
                    isinstance(instruction, ir.Binop)
                    and isinstance(instruction.a, ir.Binop)
                    and instruction.a.operation == "-"
                    and self.is_const(instruction.a.b)
                    and instruction.operation == "-"
                    and self.is_const(instruction.b)
                ):
                    # Now we can replace x = (y-5)-5 with x = y - 10
                    a = self.eval_const(instruction.a.b)
                    b = self.eval_const(instruction.b)
                    assert a.ty is b.ty
                    cn = ir.Const(a.value + b.value, "new_fold", a.ty)
                    block.insert_instruction(
                        cn, before_instruction=instruction
                    )
                    instruction.a = instruction.a.a
                    instruction.b = cn
                    assert instruction.ty is cn.ty
                    assert instruction.ty is instruction.a.ty
                    count += 1
        if count > 0:
            self.logger.debug("Folded %i expressions", count)
