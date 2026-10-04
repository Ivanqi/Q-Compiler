/* 主题：逻辑控制 —— if/else、switch、while/do-while/for、
   goto、break/continue、三元运算符、短路求值。 */

#include <stdio.h>

int classify(int x)
{
    switch (x)
    {
    case 1:
        return 10;
    case 2:
    case 3:
        return 20;
    default:
        return 99;
    }
}

/* 短路求值：右侧副作用不应发生 */
int short_circuit(void)
{
    int a = 0;
    if (0 && (a = 5))
    {
        a = 100;
    }
    if (1 || (a = 6))
    {
        /* do nothing */
    }
    return a;
}

/* goto 与标签 */
int goto_loop(int n)
{
    int i = 0;
    int s = 0;
loop:
    if (i >= n)
    {
        return s;
    }
    s = s + i;
    i = i + 1;
    goto loop;
}

int main(void)
{
    int i;

    printf("classify=%d,%d,%d\n",
           classify(1), classify(3), classify(7));

    printf("short_circuit=%d\n", short_circuit());

    /* while + break/continue */
    i = 0;
    while (1)
    {
        i = i + 1;
        if (i % 2 == 0)
        {
            continue; /* 跳过偶数 */
        }
        if (i > 10)
        {
            break;
        }
        printf("odd=%d\n", i);
    }

    /* do-while：至少执行一次 */
    i = 0;
    do
    {
        i = i + 1;
    } while (i < 3);
    printf("dowhile=%d\n", i);

    /* for 循环 */
    {
        int s = 0;
        for (i = 0; i < 5; i++)
        {
            s = s + i;
        }
        printf("forsum=%d\n", s);
    }

    /* 三元运算符 */
    printf("ternary=%d\n", (3 > 2) ? 111 : 222);

    printf("goto_loop=%d\n", goto_loop(10));
    return 0;
}
