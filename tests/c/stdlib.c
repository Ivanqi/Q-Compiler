/* 主题：标准库 —— 使用随编译器提供的 libc（librt/）：
   printf 各格式（%d/%x/%c/%s）、itoa、strlen、memcpy、
   malloc/free（基于 brk 系统调用的堆实现）。 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    char buf[20];

    /* 格式化输出 */
    printf("fmt=%d,%u,%x,%c,%s\n", -42, 42, 255, 'Q', "libc");

    /* itoa：整数转字符串（支持任意进制） */
    itoa(255, buf, 16);
    printf("hex=%s\n", buf);
    itoa(-123, buf, 10);
    printf("dec=%s\n", buf);

    /* 字符串函数 */
    printf("strlen=%d\n", (int)strlen("hello world"));

    /* memcpy */
    {
        char src[6] = "copy!";
        char dst[6];
        memcpy(dst, src, 6);
        printf("memcpy=%s\n", dst);
    }

    /* 堆内存：malloc / free（brk 系统调用实现） */
    {
        int *nums = malloc(4 * sizeof(int));
        int i;
        int s = 0;
        if (nums == 0)
        {
            printf("malloc=failed\n");
            return 1;
        }
        for (i = 0; i < 4; i++)
        {
            nums[i] = i * 10;
        }
        for (i = 0; i < 4; i++)
        {
            s = s + nums[i];
        }
        printf("heap_sum=%d\n", s);
        free(nums);
    }

    return 0;
}
