/* 主题：函数功能 —— 递归、静态函数、函数指针、嵌套调用、可变参数(printf)。 */

#include <stdio.h>

/* 静态函数：只在本编译单元可见（代码封装的一环） */
static int add(int a, int b)
{
    return a + b;
}

/* 递归：斐波那契 */
int fib(int n)
{
    if (n < 2)
    {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

/* 高阶函数：通过函数指针调用 */
int apply(int (*f)(int, int), int x, int y)
{
    return f(x, y);
}

int mul(int a, int b)
{
    return a * b;
}

/* 嵌套调用与传参 */
int poly(int x)
{
    return add(mul(x, x), mul(x, 3)) + 1; /* x^2 + 3x + 1 */
}

int main(void)
{
    printf("add=%d\n", add(3, 4));
    printf("fib=%d\n", fib(10));
    printf("apply_add=%d\n", apply(add, 5, 6));
    printf("apply_mul=%d\n", apply(mul, 5, 6));
    printf("poly=%d\n", poly(2));
    printf("poly=%d\n", poly(-3));
    return 0;
}
