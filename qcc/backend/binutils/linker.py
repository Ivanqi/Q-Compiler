"""链接器：把多个目标文件（ObjectFile）合并成一个可执行映像。

编译阶段每个源文件独立产出一个目标文件，其中跨文件的引用无法解析
（例如函数 foo 定义在别的文件里、地址未知）；链接器把各目标文件拼到一起后，
所有符号的地址都确定了，再把每条指令/数据里预留的"空位"按重定位类型填上真实值。

主流程（Linker.link）：
    merge_objects（合并各文件的节、符号、重定位条目——偏移/编号平移）
      → [可选] add_missing_symbols_from_libraries（从库里拉代码补符号）
      → layout_sections（按链接脚本把节放入映像、确定地址）
      → check_undefined_symbols（仍有未解析符号 → 报错）
      → do_relaxations（链接期松弛：把可缩短的跳转换用短形式）
      → do_relocations（★ 核心：把符号地址按重定位类型 patch 进节数据）

详见 docs/linker.py.md 与 docs/链接器.md

Linker utility."""

import logging
from collections import defaultdict

from qcc.common import CompilerError
from qcc.backend.binutils.archive import get_archive
from qcc.backend.binutils.debuginfo import DebugInfo, SymbolIdAdjustingReplicator
from qcc.backend.binutils.layout import (
    Align,
    Layout,
    Section,
    SectionData,
    SymbolDefinition,
    get_layout,
)
from qcc.backend.binutils.objectfile import (
    Image,
    ObjectFile,
    RelocationEntry,
    get_object,
)


def link(
    objects,
    layout=None,
    use_runtime=False,
    partial_link=False,
    reporter=None,
    debug=False,
    extra_symbols=None,
    libraries=None,
    entry=None,
):
    """链接器统一入口：把一组目标文件链接成一个映像。

    先把输入（文件名/文件对象/ObjectFile）统一成目标文件（get_object）；
    layout 字符串或文件经 get_layout 解析成 Layout 链接脚本对象；
    架构以第一个对象为准；use_runtime=True 时把该架构的运行时库
    （如软浮点 float32_add 所在的库）追加为输入；libraries 统一成档案对象；
    最后创建 Linker 并调用 Linker.link 返回合并后的目标文件。

    Links the iterable of objects into one using the given layout.

    Args:
        objects: a collection of objects to be linked together.
        layout: optional memory layout.
        use_runtime (bool): also link compiler runtime functions
        partial_link: Set this to true if you want to perform a partial link.
            This means, undefined symbols are no error.
        debug (bool): when true, keep debug information. Otherwise remove
            this debug information from the result.
        extra_symbols: a dict of extra symbols which can be used during
            linking.
        libraries: a list of libraries to use when searching for symbols.
        entry: the entry symbol where execution should begin.

    Returns:
        The linked object file

    .. doctest::

        >>> import io
        >>> from qcc.api import asm, cc, link
        >>> asm_source = io.StringIO("db 0x77")
        >>> obj1 = asm(asm_source, 'arm')
        >>> c_source = io.StringIO("int main(void) { return 42; }")
        >>> obj2 = cc(c_source, 'arm')
        >>> obj = link([obj1, obj2])
        >>> print(obj)
        CodeObject of 8 bytes
    """

    # 把输入（文件名/文件对象/ObjectFile）统一成目标文件：
    objects = list(map(get_object, objects))
    if not objects:
        raise ValueError("Please provide at least one object as input")

    # 链接脚本：字符串/文件 → Layout 对象：
    if layout:
        layout = get_layout(layout)

    # 架构以第一个输入对象为准：
    march = objects[0].arch

    # 追加编译器运行时库（如软浮点例程），用于补齐运行时符号：
    if use_runtime:
        objects.append(march.runtime)

    libraries = list(map(get_archive, libraries)) if libraries else []

    # 创建链接器并执行链接，返回合并后的目标文件：
    linker = Linker(march, reporter)
    output_obj = linker.link(
        objects,
        layout=layout,
        partial_link=partial_link,
        debug=debug,
        extra_symbols=extra_symbols,
        libraries=libraries,
        entry_symbol_name=entry,
    )
    return output_obj


class Linker:
    """链接器：合并多个目标文件的节，并完成符号解析与重定位。

    Merges the sections of several object files and
    performs relocation"""

    logger = logging.getLogger("linker")

    def __init__(self, arch, reporter=None):
        self.arch = arch
        self.extra_symbols = None
        self.reporter = reporter

    def link(
        self,
        input_objects,
        layout=None,
        partial_link=False,
        debug=False,
        extra_symbols=None,
        libraries=None,
        entry_symbol_name=None,
    ):
        """链接主流程：按给定布局合并输入对象并完成重定位。

        步骤：架构一致性检查 → 建输出对象（debug 时附调试信息）→
        确定入口符号与 extra_symbols → merge_objects 合并全部输入 →
        （非部分链接时）从库补符号 → 布局定地址 → 未定义符号检查 →
        松弛 → 重定位；部分链接（partial_link）则只合并、不做布局与重定位。

        Link together the given object files using the layout"""
        assert isinstance(input_objects, (list, tuple))

        if self.reporter:
            self.reporter.heading(2, "Linking")

        # Check all incoming objects for same architecture:
        # 架构一致性检查：不同架构的目标文件不能链到一起：
        for input_object in input_objects:
            assert input_object.arch == self.arch

        # Create new object file to store output:
        # 新建输出目标文件，承载合并结果：
        self.dst = ObjectFile(self.arch)
        if debug:
            self.dst.debug_info = DebugInfo()

        # Take entry symbol from layout if not specified alreay:
        # 入口符号：命令行未指定时取链接脚本里的 ENTRY(...)：
        if not entry_symbol_name and layout and layout.entry:
            # TODO: what to do if two symbols are defined?
            # for now the symbol given via command line overrides
            # the entry in the linker script.
            entry_symbol_name = layout.entry.symbol_name

        # Define entry symbol:
        # 登记入口符号：标明"程序从哪里开始执行"（未定义，等待合并阶段点亮）：
        if entry_symbol_name:
            self.dst.entry_symbol_id = self.inject_symbol(
                entry_symbol_name, "global", None, None, "object", 0
            ).id

        # Define extra symbols:
        # 注入额外符号（例如裸机程序需要的 _start 地址）：
        extra_symbols = extra_symbols or {}
        for symbol_name, value in extra_symbols.items():
            self.logger.debug("Defining extra symbol %s", symbol_name)
            self.inject_symbol(symbol_name, "global", None, value, "object", 0)

        # First merge all sections into output sections:
        # 第一步：合并全部输入对象（节/符号/重定位/入口/调试信息）：
        self.merge_objects(input_objects, debug)

        if partial_link:
            # 部分链接：只合并，供增量链接使用，不做布局与重定位：
            if layout:
                # Layout makes only sense in the final binary.
                raise ValueError("Can only apply layout in non-partial links")
        else:
            if libraries:
                # Find missing symbols in libraries:
                # 从档案库中拉取定义了未解析符号的成员对象：
                self.add_missing_symbols_from_libraries(libraries)

            # Apply layout rules:
            # 第二步：按链接脚本布局各节并分配运行地址：
            if layout:
                assert isinstance(layout, Layout)
                self.layout_sections(layout)

            # 第三步：仍有未定义符号则报错：
            self.check_undefined_symbols()

            # 第四步：链接期松弛（缩短跳转）：
            self.do_relaxations()
            # 第五步：重定位——把符号地址"锤"进节数据：
            self.do_relocations()

        if self.reporter:
            self.report_link_result()

        return self.dst

    def report_link_result(self):
        """链接完成后把节地址、映像地址与符号表转储到编译报告。

        After linking is complete, this function can be used to dump
        information to a reporter.
        """
        for section in self.dst.sections:
            self.reporter.message(f"{section} at {section.address}")

        for image in self.dst.images:
            self.reporter.message(f"{image} at {image.address}")

        symbols = [
            (s, self.dst.get_symbol_id_value(s.id) if s.defined else -1)
            for s in self.dst.symbols
        ]
        symbols.sort(key=lambda x: x[1])
        for symbol, address in symbols:
            self.reporter.message(
                f"Symbol {symbol.binding} {symbol.name} at 0x{address:X}"
            )

        self.reporter.message("Linking complete")

    def merge_objects(self, input_objects, debug):
        """合并阶段入口：把每个输入对象依次"贴"进输出对象。

        Merge object files into a single object file"""

        for input_object in input_objects:
            self.inject_object(input_object, debug)

    def inject_object(self, obj, debug):
        """把单个对象贴进输出对象：同名节拼接、符号值/编号平移、重定位平移。

        Paste object into destination object."""
        self.logger.debug("Merging %s", obj)

        # 节合并：同名节并入输出节，记录各输入节在输出节中的起始偏移：
        section_offsets = {}
        for input_section in obj.sections:
            # Get or create the output section:
            output_section = self.dst.get_section(
                input_section.name, create=True
            )

            # Alter the minimum section alignment if required:
            # 输出节的对齐要求取所有输入节的最大值：
            if input_section.alignment > output_section.alignment:
                output_section.alignment = input_section.alignment

            # Align section:
            # 按输入节的对齐要求补零，保证节数据从对齐位置开始：
            while output_section.size % input_section.alignment != 0:
                self.logger.debug("Padding output to ensure alignment")
                output_section.add_data(bytes([0]))

            # Add new section:
            # 记录偏移并直接拼接节数据：
            offset = output_section.size
            section_offsets[input_section.name] = offset
            output_section.add_data(input_section.data)
            self.logger.debug(
                "at offset 0x%x section %s",
                section_offsets[input_section.name],
                input_section,
            )

        # 符号合并：已定义符号的值 = 节在输出中的偏移 + 原节内偏移
        # （因为节在输出文件里挪了位置）；全局符号走 merge_global_symbol
        # （未定义引用可被后来的定义"点亮"），局部符号直接注入。
        symbol_id_mapping = {}
        for symbol in obj.symbols:
            # Shift symbol value if required:
            if symbol.defined:
                value = section_offsets[symbol.section] + symbol.value
                section = symbol.section
            else:
                value = section = None

            if symbol.binding == "global":
                new_symbol = self.merge_global_symbol(
                    symbol.name, section, value, symbol.typ, symbol.size
                )
            else:
                new_symbol = self.inject_symbol(
                    symbol.name,
                    symbol.binding,
                    section,
                    value,
                    symbol.typ,
                    symbol.size,
                )

            # 记录旧符号表编号 → 新编号的映射（重定位要用）：
            symbol_id_mapping[symbol.id] = new_symbol.id

        # 重定位条目重映射：offset 加上节的偏移、symbol_id 换成新编号，
        # 让重定位跟着节和符号一起"平移"：
        for reloc in obj.relocations:
            offset = section_offsets[reloc.section] + reloc.offset
            symbol_id = symbol_id_mapping[reloc.symbol_id]
            new_reloc = RelocationEntry(
                reloc.reloc_type,
                symbol_id,
                reloc.section,
                offset,
                reloc.addend,
            )
            self.dst.add_relocation(new_reloc)

        # Merge entry symbol:
        # 入口符号合并：两个对象都带入口 → 报错：
        if obj.entry_symbol_id is not None:
            if self.dst.entry_symbol_id is None:
                self.dst.entry_symbol_id = symbol_id_mapping[
                    obj.entry_symbol_id
                ]
            else:
                # TODO: improve error message?
                raise CompilerError("Multiple entry points defined")

        # Merge debug info:
        # 调试信息复制（复制器同步把符号编号映射到新符号表）：
        if debug and obj.debug_info:
            replicator = SymbolIdAdjustingReplicator(symbol_id_mapping)
            replicator.replicate(obj.debug_info, self.dst.debug_info)

    def merge_global_symbol(self, name, section, value, typ, size):
        """插入或合并一个全局符号：未定义引用被后续定义点亮，重复定义报错。

        Insert or merge a global name."""
        if self.dst.has_symbol(name):
            new_symbol = self.dst.get_symbol(name)
            assert new_symbol.binding == "global"
            if value is not None:  # we define this symbol.
                # We require merging.
                if new_symbol.undefined:
                    new_symbol.value = value
                    new_symbol.section = section
                else:
                    # TODO: accumulate errors..
                    raise CompilerError(f"Multiple defined symbol: {name}")
        else:
            new_symbol = self.inject_symbol(
                name, "global", section, value, typ, size
            )

        return new_symbol

    def inject_symbol(self, name, binding, section, value, typ, size):
        """向输出对象中新增一个符号（编号顺序分配）。

        Generate new symbol into object file."""
        symbol_id = len(self.dst.symbols)
        new_symbol = self.dst.add_symbol(
            symbol_id, name, binding, value, section, typ, size
        )
        return new_symbol

    def layout_sections(self, layout):
        """按链接脚本（Layout）把各节安排进内存映像并确定运行地址。

        遍历每条 MEMORY 的输入条目：Section（取节、对齐、定地址并加入 Image）、
        SectionData（只取节数据放到当前位置）、SymbolDefinition（在当前位置
        定义符号）、Align（地址对齐）；映像超出内存容量则报错。

        Use the given layout to place sections into memories"""
        # Create sections with address:
        # 为每条 MEMORY 建立一个 Image（一段可加载进内存的映像）：
        for mem in layout.memories:
            image = Image(mem.name, mem.location)
            current_address = mem.location
            for memory_input in mem.inputs:
                if isinstance(memory_input, Section):
                    # 取节、按节的对齐要求对齐当前地址，然后确定节地址：
                    section = self.dst.get_section(
                        memory_input.section_name, create=True
                    )
                    while current_address % section.alignment != 0:
                        current_address += 1
                    section.address = current_address
                    self.logger.debug(
                        "Memory: %s Section: %s Address: 0x%x Size: 0x%x",
                        mem.name,
                        section.name,
                        section.address,
                        section.size,
                    )
                    current_address += section.size
                    image.add_section(section)
                elif isinstance(memory_input, SectionData):
                    # SECTIONDATA：只把源节的字节放到当前位置，
                    # 为此新建一个包装节（名字确保唯一）：
                    section_name = f"_${memory_input.section_name}_"
                    # Each section must be unique:
                    assert not self.dst.has_section(section_name)

                    section = self.dst.get_section(section_name, create=True)
                    section.address = current_address
                    section.alignment = 1  # TODO: is this correct alignment?

                    src_section = self.dst.get_section(
                        memory_input.section_name
                    )

                    section.add_data(src_section.data)

                    current_address += section.size
                    image.add_section(section)
                elif isinstance(memory_input, SymbolDefinition):
                    # DEFINESYMBOL：在当前位置定义符号——
                    # 新建一个空节并令符号指向节首：
                    # Create a new section, and place it at current spot:
                    symbol_name = memory_input.symbol_name
                    section_name = f"_${symbol_name}_"

                    # Each section must be unique:
                    assert not self.dst.has_section(section_name)

                    section = self.dst.get_section(section_name, create=True)
                    section.address = current_address
                    section.alignment = 1
                    self.merge_global_symbol(
                        symbol_name, section_name, 0, "object", 0
                    )
                    image.add_section(section)
                elif isinstance(memory_input, Align):
                    # ALIGN(...)：把当前位置抬到指定对齐：
                    while (current_address % memory_input.alignment) != 0:
                        current_address += 1
                else:  # pragma: no cover
                    raise NotImplementedError(str(memory_input))

            # Check that the memory fits!
            # 映像大小超出该内存块容量 → 报错：
            if image.size > mem.size:
                raise CompilerError(
                    f"Memory exceeds size ({image.size} > {mem.size})"
                )
            self.dst.add_image(image)

    def get_symbol_value(self, symbol_id):
        """取符号的最终地址（= 节地址 + 节内偏移），供重定位与松弛使用。

        Get value of a symbol from object or fallback"""
        # Lookup symbol:
        return self.dst.get_symbol_id_value(symbol_id)
        #    raise CompilerError('Undefined reference "{}"'.format(name))

    def add_missing_symbols_from_libraries(self, libraries):
        """档案库解析的经典算法：反复扫描各库，拉取能定义未解析符号的成员对象。

        只要某成员对象定义了当前未定义符号之一，就 inject_object 把它拉进来；
        被拉进来的对象可能带来新的未定义符号，因此循环到不动点为止
        （docstring 自嘲：这可能是个"兔子洞"，因为库自身也可能有未定义符号）。

        Try to fetch extra code from libraries to resolve symbols.

        Note that this can be a rabbit hole, since libraries can have undefined
        symbols as well.
        """
        undefined_symbols = self.get_undefined_symbols()
        if not undefined_symbols:
            self.logger.debug(
                "No undefined symbols, no need to check libraries"
            )
            return

        # Keep adding objects while we have undefined symbols.
        # 循环直到不再有新的对象被拉入（不动点）：
        reloop = True
        while reloop:
            reloop = False
            for library in libraries:
                self.logger.debug("scanning library for symbols %s", library)
                for obj in library:
                    has_sym = any(map(obj.has_symbol, undefined_symbols))
                    if has_sym:
                        self.logger.debug(
                            "Using object file %s from library", obj
                        )
                        self.inject_object(obj, False)
                        undefined_symbols = self.get_undefined_symbols()
                        reloop = True

    def get_undefined_symbols(self):
        """取当前仍未解析的符号名列表。

        Get a list of currently undefined symbols."""
        return self.dst.get_undefined_symbols()

    def check_undefined_symbols(self):
        """最终检查：仍有未定义符号则列出全部并抛 CompilerError（经典链接错误）。

        Find undefined symbols."""
        undefined_symbols = self.get_undefined_symbols()
        for symbol in undefined_symbols:
            self.logger.error("Undefined reference: %s", symbol)

        if undefined_symbols:
            undefined = ", ".join(undefined_symbols)
            raise CompilerError(f"Undefined references: {undefined}")

    def do_relaxations(self):
        """链接期松弛：地址确定后，把保守的远跳转换缩短为近跳转。

        编译器总是生成保守的远跳转（如 32 位偏移），链接后地址已定，
        可把能缩短的跳转换成短形式（如 8 位偏移）。做法：扫描所有重定位，
        用 can_shrink 判断能否缩短，能则 do_shrink 打补丁并记录省下的
        "字节洞"（hole），最后 _apply_relaxation_holes 从符号值、重定位
        偏移、节数据与映像地址中一致地扣除这些洞。

        注意：RISC-V 的重定位类目前只实现 apply、未实现 can_shrink，
        因此该阶段在 RISC-V 上相当于"扫描后空转"。

        Linker relaxation. Just relax ;).

        Linker relaxation is the process of finding shorted opcodes for
        jumps to addresses nearby.

        For example, an instruction set might
        define two jump operations. One with a 32 bits offset, and one with
        an 8 bits offset. Most likely the compiler will generate conservative
        code, so always 32 bits branches. During the relaxation phase, the
        code is scanned for possible replacements of the 32 bits jump by an
        8 bit jump.

        Possible issues that might occur during this phase:

        - alignment of code. Code that was previously aligned might be
          shifted.

        - Linker relaxations might cause the opposite effect on jumps whose
          distance increases due to relaxation. This occurs when jumping over
          a memory whole between sections.

        """

        self.logger.debug("Doing linker relaxations")

        # TODO: general note. Alignment must still be taken into account.
        # A wrong situation occurs, when reducing the image by small amount
        # of bytes. Locations that were aligned before, might become unaligned.

        # First, determine the list of possible optimizations!
        lst = []
        for relocation in self.dst.relocations:
            sym_value = self.get_symbol_value(relocation.symbol_id)
            reloc_section = self.dst.get_section(relocation.section)
            reloc_value = reloc_section.address + relocation.offset
            rcls = self.dst.arch.isa.relocation_map[relocation.reloc_type]
            reloc = rcls(
                None, offset=relocation.offset, addend=relocation.addend
            )
            if reloc.can_shrink(sym_value, reloc_value):
                # Apply code patching:
                begin = relocation.offset
                size = reloc.size()
                end = begin + size
                data = reloc_section.data[begin:end]
                assert len(data) == size

                # Apply code patch:
                self.logger.debug("Applying patch for %s", reloc)
                data, new_relocs = reloc.do_shrink(
                    sym_value, data, reloc_value
                )
                new_size = len(data)
                diff = size - new_size
                assert 0 <= diff <= size
                # assert len(data) == size
                new_end = begin + new_size
                assert new_end + new_size == end
                # Do not shrink the data here, we will do this later on.
                reloc_section.data[begin:new_end] = data

                # Define new memory hole, starting after instruction
                hole = (new_end, diff)

                # Record this reduction occurence:
                lst.append((hole, relocation, reloc, new_relocs))

        if not lst:
            self.logger.debug("No linker relaxations found")
            return

        s = ", ".join(str(x) for x in lst)
        self.logger.debug("Relaxable relocations: %s", s)

        # Define a map with the byte holes:
        holes_map = defaultdict(list)  # section name to list of holes.

        # Remove old relocations by new ones.
        for hole, relocation, _, new_relocs in lst:
            # Remove old relocation which is superceeded:
            self.dst.relocations.remove(relocation)

            # Inject new relocations:
            for new_reloc in new_relocs:
                # TODO: maybe deal with somewhat shifted new relocations?

                # Create fresh relocation entry for patched code.
                new_relocation = RelocationEntry(
                    new_reloc.name,
                    relocation.symbol_id,
                    relocation.section,
                    relocation.offset,
                    relocation.addend,
                )
                self.dst.add_relocation(new_relocation)

            # Register hole:
            assert relocation.section
            holes_map[relocation.section].append(hole)

        for holes in holes_map.values():
            holes.sort(key=lambda x: x[0])

        # TODO: at this point, there can be the situation that we have two
        # sections which become further apart (due to them being in different
        # memory images. In this case, some relative jumps can become
        # unreachable. What should be do in this case?

        # Code has been patched here. Now update all relocations, symbols and
        # section addresses.
        self._apply_relaxation_holes(holes_map)

    def _apply_relaxation_holes(self, hole_map):
        """Punch holes in the destination object file.

        Do adjustments to section addresses, symbol offsets
        and relocation offsets.
        """

        def count_holes(offset, holes):
            """Count how much holes we have until the given offset."""
            diff = 0
            for hole_offset, hole_size in holes:
                if hole_offset < offset:
                    diff += hole_size
                else:
                    break
            return diff

        # Update symbols which are located in sections.
        for symbol in self.dst.symbols:
            # Ignore global section-less symbols.
            if symbol.section is None:
                continue
            holes = hole_map[symbol.section]
            delta = count_holes(symbol.value, holes)
            self.logger.debug(
                "symbol changing %s (id=%s) at %08x with -%08x",
                symbol.name,
                symbol.id,
                symbol.value,
                delta,
            )
            symbol.value -= delta

        # Update relocations (which are always located in a section)
        for relocation in self.dst.relocations:
            assert relocation.section
            holes = hole_map[relocation.section]
            delta = count_holes(relocation.offset, holes)
            self.logger.debug(
                "relocation changing %s at offset %08x with -%08x",
                relocation.symbol_id,
                relocation.offset,
                delta,
            )
            relocation.offset -= delta

        # Update section data:
        for section in self.dst.sections:
            # Loop over holes in reverse, since earlier holes influence later
            # holes.
            holes = hole_map[section.name]
            for hole_offset, hole_size in reversed(holes):
                for _ in range(hole_size):
                    section.data.pop(hole_offset)

        # Calculate total change per section
        section_changes = {
            name: sum(h[1] for h in holes) for name, holes in hole_map.items()
        }

        # Update layout of section in images
        for image in self.dst.images:
            delta = 0
            for section in image.sections:
                self.logger.debug(
                    "sectororchanging %s at %08x with -%08x to %08x",
                    section.name,
                    section.address,
                    delta,
                )
                # TODO: tricky stuff might go wrong here with alignment
                # requirements of sections.
                # Idea: re-do the layout phase?
                section.address -= delta
                delta += section_changes[section.name]

    def do_relocations(self):
        """Perform the correct relocation as listed"""
        self.logger.debug(
            f"Performing {len(self.dst.relocations)} linker relocations"
        )
        for reloc in self.dst.relocations:
            self._do_relocation(reloc)

    def _do_relocation(self, relocation):
        """Perform a single relocation.

        This involves hammering some specific bits in the section data
        according to symbol location and relocation location in the file.
        """
        sym_value = self.get_symbol_value(relocation.symbol_id)
        section = self.dst.get_section(relocation.section)

        # Determine address in memory of reloc patchup position:
        reloc_value = section.address + relocation.offset

        # reloc_function = self.arch.get_reloc(reloc.typ)
        # Construct architecture specific relocation:
        rcls = self.dst.arch.isa.relocation_map[relocation.reloc_type]
        reloc = rcls(None, offset=relocation.offset, addend=relocation.addend)

        begin = relocation.offset
        size = reloc.size()
        end = begin + size
        data = section.data[begin:end]
        assert len(data) == size, f"len({data}) ({begin}-{end}) != {size}"
        data = reloc.apply(sym_value, data, reloc_value)
        assert len(data) == size
        section.data[begin:end] = data
