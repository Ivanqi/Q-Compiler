/* 主题：动静态链接（模块之一）—— 提供跨文件符号的数学模块。
   与 main.c 分别编译成目标文件后链接为一个可执行文件。 */

/* static：本文件私有，与 main.c 中的同名 static 函数互不冲突 */
static int square(int x)
{
    return x * x;
}

static int helper(int x)
{
    return x + 1;
}

/* 公开接口：main.c 通过 extern 声明在链接期解析 */
int math_sum_of_squares(int a, int b)
{
    return square(helper(a)) + square(helper(b));
}

/* 公开全局变量：跨文件共享 */
int math_calls = 0;

int math_counted_add(int a, int b)
{
    math_calls = math_calls + 1;
    return a + b;
}
