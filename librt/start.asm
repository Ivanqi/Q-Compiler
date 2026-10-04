; 启动代码（Linux x86_64）：程序入口 start → 调用 C 的 main → bsp_exit 退出。
; 同时提供 bsp_syscall：把 4 个参数搬到寄存器后执行 Linux syscall 指令。
; 从 ppci/test/lang/c/test_c_test_suite.py 的 STARTERCODE 迁移而来。

global bsp_exit
global bsp_syscall
global main
global start

start:
    call main
    mov rdi, rax
    call bsp_exit

bsp_syscall:
    mov rax, rdi ; abi param 1
    mov rdi, rsi ; abi param 2
    mov rsi, rdx ; abi param 3
    mov rdx, rcx ; abi param 4
    syscall
    ret
