"""Identificadores UUID v7: únicos entre mesas y locales, y ordenables por tiempo."""

import os
import time
import uuid


def uuid7(ts_ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if ts_ms is None else ts_ms
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= ((rand >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return str(uuid.UUID(int=value))
