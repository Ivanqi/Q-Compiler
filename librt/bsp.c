/* 板级支持包（BSP）：把 libc 需要的 putc/exit 接到 Linux 系统调用上。

   ppci 原版 BSP 用 C3 语言编写（module bsp 的 public 函数带 bsp_ 前缀），
   本迁移版改用纯 C 实现同名符号，避免依赖 C3 前端：
   - bsp_putc(c)    —— 把字符写到标准输出（syscall 1 = write，fd=1）
   - bsp_exit(code) —— 退出进程（syscall 60 = exit）
   真正的 syscall 由 start.asm 里的 bsp_syscall 汇编桥接完成。
*/

extern long bsp_syscall(long nr, long a, long b, long c);

void bsp_putc(char c)
{
    bsp_syscall(1, 1, (long)&c, 1);  /* write(1, &c, 1) */
}

void bsp_exit(long code)
{
    bsp_syscall(60, code, 0, 0);  /* exit(code) */
}
