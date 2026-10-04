/* 主题：动静态链接（主程序）—— extern 符号在链接期解析（动态解析
   跨目标文件的引用），static 符号在各编译单元内隔离（静态绑定）。
   与 math.c 分别编译 → 链接器合并 → 生成可执行文件。 */

#include <stdio.h>

/* extern：引用 math.c 中的公开符号（链接时解析） */
extern int math_sum_of_squares(int a, int b);
extern int math_counted_add(int a, int b);
extern int math_calls;

/* static：本文件私有——与 math.c 的 static int helper 同名但互不影响 */
static int helper(int x)
{
    return x * 10;
}

static int local_add(int a, int b)
{
    return a + b;
}

int main(void)
{
    printf("squares=%d\n", math_sum_of_squares(3, 4)); /* (3+1)^2 + (4+1)^2 = 41 */
    printf("counted=%d\n", math_counted_add(10, 20));
    printf("counted=%d\n", math_counted_add(1, 2));
    printf("calls=%d\n", math_calls);
    printf("local=%d\n", local_add(1, 2));
    printf("helper=%d\n", helper(4)); /* 40：本文件的 static 版本 */
    return 0;
}
