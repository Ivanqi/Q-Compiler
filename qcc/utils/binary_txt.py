"""二进制与可打印文本互转（asc2bin/bin2asc）。

把二进制节数据编码成 ASCII 文本（base64 风格）以便在 JSON/文本
格式中携带，读取时再还原。irutils/io.py 使用。Small helper to convert binary data into text and vice-versa."""

import binascii

from qcc.utils.chunk import chunks


def bin2asc(data: bytes):
    """Encode binary data as ascii. If it is a large data set, then use a
    list of hex characters.
    """
    if len(data) > 30:
        res = []
        for part in chunks(data):
            res.append(binascii.hexlify(part).decode("ascii"))
        return res
    else:
        return binascii.hexlify(data).decode("ascii")


def asc2bin(data) -> bytes:
    """Decode ascii into binary"""
    if isinstance(data, str):
        return bytes(binascii.unhexlify(data.encode("ascii")))
    elif isinstance(data, list):
        res = bytearray()
        for part in data:
            res.extend(binascii.unhexlify(part.encode("ascii")))
        return bytes(res)
    else:  # pragma: no cover
        raise NotImplementedError(str(type(data)))
