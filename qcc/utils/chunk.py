"""Chunk：可变的字节块（数据 + 引用列表）。

二进制序列化基元：块内可含"引用"（指向其他 chunk 的偏移），
序列化时统一解析。objectfile 的节序列化等使用。"""
def chunks(data, size=30):
    """Split iterable thing into n-sized chunks"""
    for i in range(0, len(data), size):
        yield data[i : i + size]
