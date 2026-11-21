"""Synthetic test data: an SPD-30 backup, WAV files and SPD-20X patches.

Nothing here comes from a real device or sample library, so the public tests
run without any private or proprietary files.
"""

import os
import struct
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

KIT_BASE, KIT_SIZE, KIT_COUNT = 0x110, 654, 100
PAD_BASE, PAD_SIZE = 94, 36
BACKUP_SIZE = 130024                       # size of a real SPD-30 backup
PAD_PREFIXES = [str(i) for i in range(1, 9)] + ["EXT%d" % i for i in range(1, 5)]


def read(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def layer_bytes(category=6, tone=1, volume=100, cents=0, muted=False, note=36, channel=0):
    """One 16-byte SPD-30 layer record."""
    b = bytearray(16)
    b[0] = category
    b[1] = tone - 1
    b[2] = volume
    struct.pack_into(">H", b, 4, 2400 + cents)
    b[8] = 50                              # pan centre
    b[10] = 1 if muted else 0
    b[14] = note
    b[15] = channel
    return bytes(b)


def silent_layer():
    return layer_bytes(category=12, volume=0, note=128)


def make_backup(kits):
    """kits: {kit number: {"name": str, "pads": {pad index: (mode, layerA, layerB)}}}.
    Pads not listed are silent single-mode pads."""
    d = bytearray(BACKUP_SIZE)
    d[0:6] = b"\x00\xfeSVD2"
    for k in range(KIT_COUNT):
        rec = bytearray(KIT_SIZE)
        spec = kits.get(k + 1, {})
        rec[0:16] = spec.get("name", "Kit %d" % (k + 1)).encode("ascii").ljust(16)[:16]
        rec[16] = 100
        for p in range(12):
            mode, a, b = spec.get("pads", {}).get(p, (0, silent_layer(), silent_layer()))
            o = PAD_BASE + p * PAD_SIZE
            rec[o] = mode
            rec[o + 4:o + 20] = a
            rec[o + 20:o + 36] = b
        d[KIT_BASE + k * KIT_SIZE:KIT_BASE + (k + 1) * KIT_SIZE] = rec
    return bytes(d)


def write_wav(path, frames=441, channels=1, width=2, rate=44100, extra_chunk=False):
    """A short silent PCM WAV.  extra_chunk puts a LIST chunk before 'data'."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(frames * channels * width))
    if extra_chunk:
        with open(path, "rb") as f:
            data = f.read()
        info = b"LIST" + struct.pack("<I", 4) + b"INFO"
        body = data[12:]
        data = data[:4] + struct.pack("<I", 4 + len(info) + len(body)) + b"WAVE" + info + body
        with open(path, "wb") as f:
            f.write(data)


def make_spd20x_patch(n, name="Patch"):
    """A 498-byte SPD-20X patch file with every MIDI record on channel 10,
    note OFF and program change OFF, except pad 1 bank A (PC = n)."""
    d = bytearray(498)
    d[0:12] = b"BACKUP      "
    for base in (0x10E, 0x16E):
        for r in range(16):
            o = base + 6 * r
            d[o] = 9
            d[o + 2] = 128
            d[o + 4] = 128
    d[0x10E + 2] = n - 1
    d[0x1E3:0x1EF] = name.encode("ascii").ljust(12)[:12]
    d[0x1F0] = n - 1
    return bytes(d)
