/* 主题：SSA 优化 —— 典型的 alloc/load/store 模式在 mem2reg 下
   被提升为 SSA 形式（含跨基本块的 phi 节点）。
   运行结果用于验证优化前后语义一致；tests/test_c_programs.py
   还会直接检查优化后 IR 中 phi 的存在与 alloc 的消除。 */

#include <stdio.h>

/* 循环累加器：i 与 s 都会被提升为寄存器变量（mem2reg） */
int sum_to(int n)
{
    int i;
    int s = 0;
    for (i = 1; i <= n; i++)
    {
        s = s + i;
    }
    return s;
}

/* 分支中定义、合并点使用的变量 → 插入 phi 节点 */
int abs_v(int x)
{
    int r;
    if (x < 0)
    {
        r = -x;
    }
    else
    {
        r = x;
    }
    return r;
}

/* 多重嵌套循环 + 分支（更多 phi 与活跃区间重叠） */
int nested(int n)
{
    int i;
    int j;
    int total = 0;
    for (i = 0; i < n; i++)
    {
        for (j = 0; j < n; j++)
        {
            if ((i + j) % 2 == 0)
            {
                total = total + i + j;
            }
        }
    }
    return total;
}

int main(void)
{
    printf("sum_to=%d\n", sum_to(100));
    printf("sum_to=%d\n", sum_to(0));
    printf("abs=%d\n", abs_v(-7));
    printf("abs=%d\n", abs_v(7));
    printf("nested=%d\n", nested(4));
    return 0;
}
