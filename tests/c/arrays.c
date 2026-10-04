/* 主题：数组 —— 一维/二维数组、初始化列表、数组退化为指针、
   字符串数组、sizeof 求元素个数。 */

#include <stdio.h>

/* 数组参数退化为指针 */
int max_of(int a[], int n)
{
    int m = a[0];
    int i;
    for (i = 1; i < n; i++)
    {
        if (a[i] > m)
        {
            m = a[i];
        }
    }
    return m;
}

/* 二维数组 */
int trace(int m[3][3])
{
    return m[0][0] + m[1][1] + m[2][2];
}

int main(void)
{
    int a[5] = {3, 7, 1, 9, 4};
    int m[3][3] = {{1, 2, 3}, {4, 5, 6}, {7, 8, 9}};
    char word[6] = "array"; /* 含结尾 '\0' */
    char *names[2] = {"alpha", "beta"};
    int i;

    printf("max=%d\n", max_of(a, 5));
    printf("trace=%d\n", trace(m));
    printf("m[2][1]=%d\n", m[2][1]);
    printf("word=%s\n", word);
    printf("names=%s,%s\n", names[0], names[1]);

    /* 部分初始化：其余补 0 */
    {
        int p[4] = {10, 20};
        printf("partial=%d,%d,%d\n", p[0], p[1], p[3]);
    }

    /* 下标遍历 + sizeof 求长度 */
    {
        int n = (int)(sizeof(a) / sizeof(a[0]));
        int s = 0;
        for (i = 0; i < n; i++)
        {
            s = s + a[i];
        }
        printf("sum=%d\n", s);
    }

    /* 数组名即首地址 */
    {
        int *p = a;
        printf("decay=%d\n", p[1]);
    }
    return 0;
}
