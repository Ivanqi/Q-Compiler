/* 主题：文件 IO —— 用 libc 的 open/read/close（Linux syscall 包装）
   读取同目录下的 example.txt 并输出其内容。 */

#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>

int main(void)
{
    int fd = open("example.txt", O_RDONLY);
    char buf[64];
    long n;

    if (fd < 0)
    {
        printf("open failed fd=%d\n", fd);
        return 1;
    }

    n = read(fd, buf, 63);
    if (n < 0)
    {
        printf("read failed\n");
        return 1;
    }
    buf[n] = 0; /* 结束字符串 */
    printf("file=<%s>\n", buf);
    close(fd);
    return 0;
}
