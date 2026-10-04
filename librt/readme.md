
This directory contains mandatory header files for a C compiler.

# Freestanding

A C-compiler without standard library is called a freestanding compiler.
A freestanding compiler only contains the following header files:

- iso646.h
- limits.h
- stdalign.h
- stdarg.h
- stdbool.h
- stddef.h
- stdint.h

# Hosted

Combining a C-compiler and a C standard library gives a hosted C
implementation.

# 说明
librt/ 是编译器自带的 C 运行时库（libc），它的作用是让 python3 -m qcc --run 编译出的程序能真正在 Linux x86_64
  上跑起来——没有它，程序连 printf 都没有实现。

## 目录结构
```
  librt/
  ├── start.asm    # 启动代码：程序入口 start → 调 main → bsp_exit 退出；
  │                #   另提供 bsp_syscall（把参数搬进寄存器后执行 syscall 指令）
  ├── bsp.c        # 板级支持包（BSP）：bsp_putc(写stdout)/bsp_exit(进程退出)，
  │                #   经 bsp_syscall 桥接到 Linux 系统调用（原版 ppci 用 C3 写，这里改写为纯 C）
  ├── layout.mmap  # 链接脚本：code 段放 0x40000、data 段放 0x20000000，入口 start
  ├── lib.c        # printf/itoa/reverse —— 格式化输出（经 bsp_putc 逐字符输出）
  ├── include/     # C 标准头文件（stdio.h/stdlib.h/string.h/fcntl.h/unistd.h…）
  └── src/         # 独立的 libc 模块（每个都能用 qcc 单独编译）
      ├── syscall/    syscall.c(内联汇编 syscall)、brk.c(堆基元)
      ├── malloc.c    malloc/free/memcpy（基于 brk 系统调用）
      ├── unistd.c    read/write/close（文件 IO 依赖它）
      ├── fcntl.c     open（文件 IO 依赖它）
      ├── stat.c      fstat/f_size
      └── string/     strlen 等
```
  在编译流水线中的角色

  --run 模式（qcc/__main__.py 的 build_executable）会自动把这些文件连同用户程序一起编译、链接：

  start.asm（入口） + bsp.c + lib.c + malloc.c + unistd.c + fcntl.c ...
       │ 全部由 qcc 自己编译（cc()）
       ▼
  link() 合并 → 写 ELF 可执行文件 → 真机执行

  这也正是 11 个 C 测试程序能覆盖标准库（printf/malloc）、文件 IO（open/read/close）和网络 IO（内联汇编 syscall）的原因——整个运行时都是"自举"的：用
  qcc 编译它自己的 C 库，再链进用户程序。

  对照关系：
  - tests/test_c_programs.py 里的 build_executable() 和 qcc/__main__.py 的 RUNTIME_SOURCES 是这份运行时的两个消费者；
  - 如果不走 --run，普通编译（-m riscv 交叉编译等）不需要 librt，只有包含 printf 等符号的用户程序链接时才用到。