/* 主题：指针 —— 指针运算、多级指针、void*、函数指针、NULL、字符串遍历。 */

#include <stdio.h>

int sum_array(int *p, int n)
{
    int s = 0;
    int i;
    for (i = 0; i < n; i++)
    {
        s = s + p[i];
    }
    return s;
}

/* 通过指针交换两个变量 */
void swap(int *a, int *b)
{
    int t = *a;
    *a = *b;
    *b = t;
}

/* 指针运算：p + n 与下标等价 */
int sum_pointer_arith(int *p, int n)
{
    int s = 0;
    int *end = p + n;
    while (p < end)
    {
        s = s + *p;
        p = p + 1;
    }
    return s;
}

int main(void)
{
    int a[5] = {1, 2, 3, 4, 5};
    int x = 7;
    int y = 9;
    int *p = a;
    int **pp = &p; /* 多级指针 */

    printf("sum_array=%d\n", sum_array(a, 5));
    printf("sum_arith=%d\n", sum_pointer_arith(a, 5));

    /* 指针运算与解引用 */
    printf("p2=%d\n", *(a + 2));
    printf("pp=%d\n", **pp);
    *pp = a + 3; /* 通过二级指针改一级指针 */
    printf("pp3=%d\n", *p);

    /* 交换 */
    swap(&x, &y);
    printf("swap=%d,%d\n", x, y);

    /* 字符串遍历（char* 与 ++） */
    {
        char *msg = "pointer";
        int n = 0;
        while (*msg)
        {
            n = n + 1;
            msg = msg + 1;
        }
        printf("strlen=%d\n", n);
    }

    /* NULL 检查与 void* */
    {
        void *vp = 0;
        if (vp == 0)
        {
            printf("null=ok\n");
        }
        vp = &x;
        printf("voidptr=%d\n", *(int *)vp);
    }

    /* 函数指针 */
    {
        int (*f)(int *, int) = sum_array;
        printf("fnptr=%d\n", f(a, 5));
    }

    /* 指针比较 */
    {
        int *q = a;
        int *r = a + 4;
        printf("ptrdiff=%d\n", (int)(r - q));
    }
    return 0;
}
