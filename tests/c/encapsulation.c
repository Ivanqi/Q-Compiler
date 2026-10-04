/* 主题：代码封装 —— 用 static 函数/变量隐藏实现细节，
   以结构体 + 操作函数组织模块（计数器、二维向量两个小模块）。 */

#include <stdio.h>

/* ---- 模块 1：计数器（struct + 操作函数） ---- */
typedef struct Counter
{
    int value;
} Counter;

static Counter global_counter; /* static 全局：本文件私有 */

static void counter_reset(Counter *c)
{
    c->value = 0;
}

void counter_inc(Counter *c)
{
    c->value = c->value + 1;
}

int counter_get(Counter *c)
{
    return c->value;
}

/* ---- 模块 2：二维向量 ---- */
typedef struct Vec2
{
    int x;
    int y;
} Vec2;

static int vec_dot_impl(Vec2 a, Vec2 b)
{
    return a.x * b.x + a.y * b.y;
}

int vec_length_sq(Vec2 v)
{
    return vec_dot_impl(v, v); /* 通过 static 辅助函数实现 */
}

int main(void)
{
    Counter c;
    Vec2 v;

    counter_reset(&c);
    counter_inc(&c);
    counter_inc(&c);
    counter_inc(&c);
    printf("counter=%d\n", counter_get(&c));

    counter_reset(&global_counter);
    counter_inc(&global_counter);
    printf("global_counter=%d\n", counter_get(&global_counter));

    v.x = 3;
    v.y = 4;
    printf("len_sq=%d\n", vec_length_sq(v)); /* 3^2 + 4^2 = 25 */
    return 0;
}
