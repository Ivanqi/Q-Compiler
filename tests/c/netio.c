/* 主题：网络 IO —— 用 6 参数 syscall（内联汇编）做 UDP 回环通信：
   socket → bind 127.0.0.1:44444 → sendto → recvfrom → 校验数据 → close。

   Linux x86_64 syscall 编号：socket=41, bind=49, sendto=44,
   recvfrom=45, close=3。
   sockaddr_in（16 字节）：family(2) + port(2, 网络序) + addr(4) + 填充。
*/

#include <stdio.h>

/* 通用 4 参数 syscall（libc 提供） */
extern long syscall(long nr, long a, long b, long c);

/* 6 参数 syscall：内联汇编，参数 4-6 走 r10/r8/r9 */
long syscall6(long nr, long a, long b, long c, long d, long e, long f)
{
    long ret;
    asm(
        "mov rax, %0 \n"
        "mov rdi, %1 \n"
        "mov rsi, %2 \n"
        "mov rdx, %3 \n"
        "mov r10, %4 \n"
        "mov r8, %5 \n"
        "mov r9, %6 \n"
        "syscall \n"
        : "=r" (ret)
        : "r" (nr), "r" (a), "r" (b), "r" (c), "r" (d), "r" (e), "r" (f)
        : "rax", "rdi", "rsi", "rdx", "r10", "r8", "r9"
    );
    return ret;
}

int main(void)
{
    char addr[16]; /* struct sockaddr_in */
    char msg[16] = "net-io-ok";
    char buf[16];
    long addrlen = 16;
    int fd;
    long n;

    /* 构造 127.0.0.1:44444（端口网络字节序 0xAD9C） */
    addr[0] = 2;        /* family = AF_INET */
    addr[1] = 0;
    addr[2] = 0xAD;     /* port = htons(44444) */
    addr[3] = 0x9C;
    addr[4] = 127;      /* 127.0.0.1 */
    addr[5] = 0;
    addr[6] = 0;
    addr[7] = 1;

    fd = (int)syscall6(41, 2, 2, 0, 0, 0, 0); /* socket(AF_INET, SOCK_DGRAM, 0) */
    if (fd < 0)
    {
        printf("socket=fail\n");
        return 1;
    }
    printf("socket=ok\n");

    n = syscall6(49, fd, (long)&addr[0], 16, 0, 0, 0); /* bind */
    if (n < 0)
    {
        printf("bind=fail\n");
        return 1;
    }
    printf("bind=ok\n");

    n = syscall6(44, fd, (long)&msg[0], 9, 0, (long)&addr[0], 16); /* sendto */
    if (n != 9)
    {
        printf("sendto=fail\n");
        return 1;
    }
    printf("sendto=ok\n");

    n = syscall6(45, fd, (long)&buf[0], 16, 0, (long)&addr[0], (long)&addrlen); /* recvfrom */
    if (n != 9)
    {
        printf("recvfrom=fail\n");
        return 1;
    }
    printf("recvfrom=ok\n");

    {
        int ok = 1;
        int i;
        for (i = 0; i < 9; i++)
        {
            if (buf[i] != msg[i])
            {
                ok = 0;
            }
        }
        printf("udp_echo=%s\n", ok ? "ok" : "mismatch");
    }

    syscall(3, fd, 0, 0); /* close */
    return 0;
}
