"""Synthetic Battery-style kit files for the public tests.

These follow the container layout the converter reads (NISound items, a
FastLZ-compressed preset, 128 cell groups, a zone list, a sample file table
and an info block with CRC32 and salted MD5), but every byte is made up here.
They test that the converter's parser, writer and verifier agree with each
other.  They do NOT prove that Battery 4 accepts a file: that needs real
Battery-saved templates (see tests/test_with_templates.py).
"""

import struct
import uuid

import fixtures  # noqa: F401
import fastlz2
import build_john_deni_battery4_kits as b

GROUP_VER = 0x95
ZONE_VER = 0x9A
PROG_VER = 0xAF
FILLED_FLAGS = {4: 4, 40: 4, 52: 64, 64: 4}     # bytes set in a cell holding a sample


def group_bytes(index, filled=False):
    priv = bytearray(2390)
    struct.pack_into("<II", priv, b.KEY_LO, 36 + index % 92, 36 + index % 92)
    priv[b.KEY_LO + 8:b.KEY_LO + 12] = b"\x01\x07\x00\x00"
    struct.pack_into("<f", priv, b.ENGINE_GAIN, 1.0)
    if filled:
        for o, v in FILLED_FLAGS.items():
            priv[o] = v
    name = "Kick" if filled else " "
    pub = b.wstr(name) + struct.pack("<fff", 0.9880964, 0.0, 1.0) + bytes(20)
    return b.struct_bytes(GROUP_VER, priv, pub, bytes(30))


def zone_bytes(group, file_id, frames=441):
    priv = bytearray(87)
    priv[22] = 1
    struct.pack_into("<I", priv, b.ZONE_PRIV_ID, 0)
    head = struct.pack(b.ZONE_HEAD, 0, 0, 0xFFFFFFFF, 1, 127, 36, 36, 0, 0, 0, 0,
                       b.ROOT_KEY, 1.0, 0.0, 1.0)
    tail = struct.pack(b.ZONE_TAIL, file_id, 2, 44100, 1, frames, 44, 60, 1.0, 0, frames * 2)
    pub = head + b"\xff\xff\xff\xff\x00\x00" + tail
    kids = bytearray(66)
    struct.pack_into("<II", kids, b.ZONE_KIDS_LOOP, 4 * (frames // 8), frames // 8)
    return struct.pack("<I", group) + b.struct_bytes(ZONE_VER, bytes(priv), pub, bytes(kids))


def file_table(files):
    out = bytearray(b"\x02\x00" + bytes(8)) + struct.pack("<I", len(files))
    for folder, name in files:
        out += struct.pack("<I", 2) + b"\x02" + b.wstr(folder) + b"\x04" + b.wstr(name)
    out += b"".join(struct.pack("<Q", 1700000000) for _ in files)
    out += bytes(4 * len(files))
    out += struct.pack("<II", 1, 1) + b"\x04" + b.wstr("Kit.nbkt") + b"\x01\x00"
    return bytes(out)


def raw_kit(with_sample):
    """Kit bytes before checksums are filled in (Kit.to_bytes fixes them)."""
    groups = struct.pack("<I", 128) + b"".join(
        group_bytes(i, filled=with_sample and i == 0) for i in range(128))
    zones = struct.pack("<I", 1 if with_sample else 0)
    files = []
    if with_sample:
        zones += zone_bytes(0, 0)
        files = [("Kit Samples", "kick.wav")]
    prog = b.struct_bytes(PROG_VER, bytes(113), b.wstr("Kit") + bytes(40),
                          b.chunk_bytes([[0x33, groups], [0x34, zones]]))
    chunks = b.chunk_bytes([[0x28, prog], [0x47, bytes(18)], [0x4B, file_table(files)]])
    hdr = bytearray(20)
    struct.pack_into("<I", hdr, 12, len(chunks))
    inner = b.Item(1, 0, 0, uuid.uuid4().bytes,
                   b.Chunk(b"DSIN", 109, 1, None, bytes(hdr) + chunks + bytes(16)), 1, [])
    raw = inner.to_bytes()
    packed = fastlz2.compress(raw)
    c115 = b.Chunk(b"DSIN", 115, 1, None, struct.pack("<IBII", 1, 1, len(raw), len(packed)) + packed)
    info = bytearray(222)
    info[0:4] = b.MAGIC
    info[0x14:0x18] = b"4taB"
    c4 = b.Chunk(b"4KIN", 4, 1, None, bytes(info))
    c108 = b.Chunk(b"DSIN", 108, 1, None, bytes(16) + b.wstr("Kit") + bytes(4))
    child = lambda chunk: b.Item(1, 0, 0, uuid.uuid4().bytes, chunk, 1, [])
    root = b.Item(1, 0, 0, uuid.uuid4().bytes, c108, 1,
                  [(1, b"DSIN", 1, child(c4)), (1, b"DSIN", 2, child(c115))])
    return root.to_bytes()


def finished_kit(with_sample):
    """A kit with valid size fields, CRC32 and MD5."""
    kit = b.Kit(raw_kit(with_sample))
    return kit.to_bytes("Kit.nbkt", 1700000000, "fixture")


def write_templates(folder):
    """Write Dummy.nbkt (empty) and wrapper.nbkt (one sample) into folder."""
    import os
    paths = {}
    for name, with_sample in (("Dummy.nbkt", False), ("wrapper.nbkt", True)):
        paths[name] = os.path.join(folder, name)
        with open(paths[name], "wb") as f:
            f.write(finished_kit(with_sample))
    return paths["Dummy.nbkt"], paths["wrapper.nbkt"]
