"""C 初始化器（CInitializer）实现：跟踪嵌套初始化列表中的当前位置。

用游标栈（InitCursor + 各级 InitLevel）解析结构体、联合体与数组的
花括号初始化语法，供语法(parser)与语义(semantics)阶段构造初始化表达式。

C initializer helping classes.

The classes and functions here mainly deal with keeping track of the
position in an C style initializer.
"""

import abc

from qcc.frontend.c.nodes import expressions, types


class InitCursor:
    """初始化游标：用栈记录当前正被填充的嵌套初始化器层次。

    A cursor into an arbitrary complex data structure.
    """

    def __init__(self, context):
        self.context = context
        self._stack = []

    def __repr__(self):
        return f"InitCursor({self._stack})"

    @property
    def level(self):
        """当前所处的初始化层次（栈顶）。

        The current initial level.
        """
        return self._stack[-1]

    @property
    def is_toplevel(self):
        return len(self._stack) == 0

    def at_end(self):
        """检查游标是否已越过当前层次的末尾。

        Check if we point the cursor into the void.
        """
        return self.level.at_end()

    def at_typ(self):
        """取得游标当前指向元素的类型。

        Get the type we are pointing to.
        """
        return self.level.element_typ()

    def get_value(self):
        """取得游标当前位置上的表达式。

        Get current expression under cursor.
        """
        return self.level.get_value()

    def set_value(self, value):
        """在游标当前位置写入表达式。

        Set value at cursor position.
        """
        self.level.set_value(value)

    def enter_compound(self, typ, location, implicit):
        """进入一层新的复合初始化器（结构体/联合/数组），并压入游标栈。

        Contrapt new initializer element, and append to stack.
        """
        is_toplevel = self.is_toplevel
        # Get current initializer:
        if is_toplevel:
            initializer = None
        else:
            initializer = self.get_value()

        init_level = self._make_init_level(
            typ, location, initializer, implicit
        )

        if not is_toplevel and not initializer:
            self.set_value(init_level.initializer)

        self._stack.append(init_level)

    def _make_init_level(self, typ, location, initializer, implicit):
        """按类型创建对应的初始化层次（结构体/联合体/数组）。

        Create an initialization level.
        """
        assert isinstance(typ, types.CType)

        if typ.is_struct:
            if not initializer:
                initializer = expressions.StructInitializer(typ, location)
            init_level = StructInitLevel(initializer, implicit)
        elif typ.is_union:
            if not initializer:
                initializer = expressions.UnionInitializer(typ, location)
            init_level = UnionInitLevel(initializer, implicit)
        else:
            assert typ.is_array

            if typ.size is None:
                size = None
            else:
                size = self.context.eval_expr(typ.size)

            if not initializer:
                initializer = expressions.ArrayInitializer(typ, [], location)

            init_level = ArrayInitLevel(initializer, size, implicit)
        return init_level

    def leave_compound(self):
        """退出一层复合初始化器并返回其初始化器结点（如 type.a.b → type.a）。

        As in, leave the current sub type.a.b -> type.a
        """
        return self._stack.pop(-1).initializer

    def unwind(self):
        """回退掉所有隐式（花括号省略）的层次。

        Unwind levels to last explicit level.
        """
        while self.level.implicit:
            self._stack.pop()

    def next_element(self):
        """把游标推进到下一个待初始化元素。

        Proceed cursor to next slot to come.
        """
        # Proceed to next element:
        self.level.go_next()
        while self.level.at_end() and self.level.implicit:
            self.leave_compound()
            self.level.go_next()

    def select_field(self, field_name, location):
        """将游标定位到指定字段，自动穿透匿名结构体/联合体。

        Select the given field name taking anonymous structs
        into account.
        """
        assert not self.is_toplevel
        typ = self.level.typ
        assert typ.is_struct_or_union

        field_path = typ.get_field_path(field_name)

        assert len(field_path) > 0
        if len(field_path) > 1:
            # We must first enter anonymous structs on the init stack
            # Those are implicit init levels.
            for field in field_path[:-1]:
                self.level.go_to_field(field)
                self.enter_compound(field.typ, location, True)

        self.level.go_to_field(field_path[-1])


class InitLevel(abc.ABC):
    """一层进行中的初始化器（结构体/联合体/数组层次的抽象基类）。

    An in progress initializer.
    """

    def __init__(self, initializer, implicit):
        self.typ = initializer.typ
        self.initializer = initializer
        self.implicit = implicit

    def element_typ(self):
        """游标当前位置元素的类型。

        Current type under cursor.
        """
        raise NotImplementedError()

    def at_end(self):
        """检查是否还有待初始化的元素。

        Check if there are more elements to be initialized.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def go_next(self):  # pragma: no cover
        raise NotImplementedError()

    @abc.abstractmethod
    def get_value(self):  # pragma: no cover
        raise NotImplementedError()

    @abc.abstractmethod
    def set_value(self, value):  # pragma: no cover
        raise NotImplementedError()


class StructInitLevel(InitLevel):
    """结构体初始化层次：按字段顺序推进。"""

    def __init__(self, initializer, implicit):
        assert initializer.typ.is_struct
        assert isinstance(initializer, expressions.StructInitializer)
        super().__init__(initializer, implicit)
        self.pos = 0  # TODO: integer pos or field name?

    def __repr__(self):
        return (
            f"Initializing struct {self.typ}, "
            + f"got so far: {self.initializer}, at pos: {self.pos}"
        )

    def element_typ(self):
        field = self.typ.fields[self.pos]
        return field.typ

    def at_end(self):
        return self.pos >= len(self.typ.fields)

    def go_next(self):
        self.pos += 1

    def go_to_field(self, field):
        pos = self.typ.fields.index(field)
        self.pos = pos

    def get_value(self):
        field = self.typ.fields[self.pos]
        if field in self.initializer.values:
            return self.initializer.values[field]

    def set_value(self, value):
        field = self.typ.fields[self.pos]
        self.initializer.values[field] = value


class UnionInitLevel(InitLevel):
    """联合体初始化层次：一次只初始化一个字段。

    Union initialization in progress.
    """

    def __init__(self, initializer, implicit):
        assert initializer.typ.is_union
        assert isinstance(initializer, expressions.UnionInitializer)
        super().__init__(initializer, implicit)
        self._field = self.typ.fields[0]
        self._end = False

    def go_to_field(self, field):
        self._field = field
        self._end = False

    def __repr__(self):
        return f"Initializing union {self.typ}, got so far: {self.initializer}"

    def element_typ(self):
        return self._field.typ

    def at_end(self):
        return self._end

    def go_next(self):
        # Done, contains only one type.
        self._end = True

    def get_value(self):
        return self.initializer.value

    def set_value(self, value):
        self.initializer.field = self._field
        self.initializer.value = value


class ArrayInitLevel(InitLevel):
    """数组初始化层次：按下标顺序推进（支持指定位置与自动扩容）。

    Array initialization in progress.
    """

    def __init__(self, initializer, size, implicit):
        assert initializer.typ.is_array
        assert isinstance(initializer, expressions.ArrayInitializer)
        super().__init__(initializer, implicit)
        self.size = size  # Array size
        self.pos = 0  # The position in the array

    def __repr__(self):
        return (
            f"Initializing array {self.typ} "
            + f"at position {self.pos}, got so far: {self.initializer}"
        )

    def element_typ(self):
        return self.typ.element_type

    def at_end(self):
        if self.size is None:
            return False
        else:
            return self.pos >= self.size

    def go_to_pos(self, pos):
        self.pos = pos

    def go_next(self):
        self.pos += 1

    def get_value(self):
        if self.pos < len(self.initializer.values):
            return self.initializer.values[self.pos]

    def set_value(self, initial_value):
        # Fill holes:
        while len(self.initializer.values) <= self.pos:
            self.initializer.values.append(None)

        self.initializer.values[self.pos] = initial_value
