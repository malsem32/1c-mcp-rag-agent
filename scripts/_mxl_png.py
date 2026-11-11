# -*- coding: utf-8 -*-
"""Крошечный PNG (красный квадрат 4x4) в base64 — для смоуков рисунков макета."""
import base64
import struct
import zlib


def png_b64() -> str:
    def ch(t: bytes, d: bytes) -> bytes:
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    data = b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")
    return base64.b64encode(data).decode()
