/* 主题：类型系统 —— 整型各宽度、无符号、浮点、枚举、结构体/联合体、
   typedef、位域、sizeof、类型转换。 */

#include <stdio.h>

typedef struct Point
{
    int x;
    int y;
} Point;

union Value
{
    int i;
    char c;
    float f;
};

enum Color
{
    RED = 1,
    GREEN = 2,
    BLUE = 4
};

struct Flags
{
    unsigned int ready : 1;
    unsigned int mode : 3;
    unsigned int id : 4;
};

int main(void)
{
    char ch = 'A';
    short s = -123;
    int i = 123456;
    long l = 123456789;
    unsigned int u = 0xFFFFFFFF;
    float f = 1.5;
    double d = 2.25;

    printf("ch=%c\n", ch);
    printf("s=%d\n", (int)s);
    printf("i=%d\n", i);
    printf("l=%d\n", (int)l);
    printf("u=%u\n", u);

    /* 浮点比较（%f 只打印整数部分，这里用比较产生确定输出） */
    if (f > 1.0 && f < 2.0)
    {
        printf("frange=ok\n");
    }
    printf("sum=%d\n", (int)(f + d)); /* 1.5 + 2.25 = 3 */

    /* 类型转换 */
    printf("cast=%d\n", (int)d);

    /* 结构体 */
    Point p;
    p.x = 3;
    p.y = 4;
    printf("p=%d,%d\n", p.x, p.y);
    printf("sizeof(Point)=%d\n", (int)sizeof(Point));

    /* 联合体 */
    union Value v;
    v.i = 42;
    printf("v.i=%d\n", v.i);
    v.c = 'Z';
    printf("v.c=%c\n", v.c);

    /* 枚举 */
    enum Color c = GREEN;
    printf("enum=%d\n", (int)c);

    /* 位域 */
    struct Flags fl;
    fl.ready = 1;
    fl.mode = 5;
    fl.id = 9;
    printf("bitfield=%d,%d,%d\n", (int)fl.ready, (int)fl.mode, (int)fl.id);

    /* sizeof 各类型 */
    printf("sizes=%d,%d,%d,%d\n",
           (int)sizeof(char), (int)sizeof(short),
           (int)sizeof(int), (int)sizeof(long));
    return 0;
}
