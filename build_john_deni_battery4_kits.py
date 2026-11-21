#!/usr/bin/env python3
"""Build sample-linked Battery 4 kits (.nbkt) from a Roland SPD-30 backup.

Reads an SPD-30 backup (BACKUPxx.BIN), finds each active pad layer's WAV under
`<samples>/<NNN Kit>/Layer A|B/`, and writes for every kit

    <out>/<NNN Kit>/<NNN Kit>.nbkt
    <out>/<NNN Kit>/<NNN Kit> Samples/<A|B> <wav name>

with one Battery cell per active layer: key range = the layer's MIDI note
(when the note is OFF on the SPD-30: 120, 121, ... 127 in cell order),
volume = level / 100, tune = 2 ** (semitones / 12), and a zone that links the
copied WAV by relative path.

    python3 build_john_deni_battery4_kits.py            # default folders below
    python3 build_john_deni_battery4_kits.py --help     # all options

Default locations (relative to this script):
    input/BACKUP01.BIN       the SPD-30 backup
    input/samples/           one "NNN Kit" folder per kit, with Layer A/ and Layer B/
    templates/Dummy.nbkt     an empty kit saved by Battery 4
    templates/wrapper.nbkt   a kit saved by Battery 4 with at least one sample
    output/battery4_kits/    where kits and reports are written

Templates (you provide them; see templates/README.md):
  * Dummy.nbkt supplies the 128 clean cell (group) blocks.
  * wrapper.nbkt, a kit saved by Battery 4 *with* samples,
    supplies the surrounding program wrapper, the zone layout and the sample
    file table.  Dummy.nbkt has no zone and no file-table entry to copy, and it
    is a newer program version (0xb2) whose file table differs from every
    sample-bearing kit available (0xaf), so its wrapper cannot be used for
    samples.  Dummy's cell blocks are byte-identical to that kit's empty cells.

Format notes (all checked against the Battery-saved kits by `self_test`):
  preset   = chunk list: 0x28 program, 0x47, 0x4b sample file table
  program  = structured object; children include 0x33 (128 groups = cells)
             and 0x34 (zones: u32 count, then u32 group index + zone object)
  zone     = v0x9a, private 87 bytes, public 82 bytes, children 66 bytes
  0x4b     = 10-byte header, u32 count, one path per file (segments: type 2
             folder, type 4 file name, UTF-16), u64 timestamp per file, u32 0
             per file, then the kit's own file name
  trailer  = info block: timestamp, zone count, group count, total sample
             bytes, CRC32 of the preset chunk list, salted MD5
"""

import argparse
import csv
import hashlib
import os
import shutil
import struct
import sys
import uuid
import wave
import zlib
from collections import Counter

import fastlz2
import nbkt_gen

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- SPD-30 backup ---------------------------------------------------------
KIT_BASE = 0x100 + 16
KIT_SIZE = 654
KIT_COUNT = 100
PAD_BASE = 94
PAD_SIZE = 36
PAD_PREFIXES = [str(i) for i in range(1, 9)] + ["EXT%d" % i for i in range(1, 5)]
PAD_LABELS = ["PAD %d" % i for i in range(1, 9)] + ["EXT %d" % i for i in range(1, 5)]
CAT_OFF = 12
NOTE_OFF = 128
NOTE_OFF_KEYS = list(range(120, 128)) + list(range(119, -1, -1))
# Keys handed out, in this order, to a kit's active layers whose MIDI note is
# OFF: 120..127 for the first eight (in cell order), then 119 downwards for
# any further ones.  Keys already used by a real note in the kit are skipped.

# ---- Battery group (cell) --------------------------------------------------
KEY_LO = nbkt_gen.PRIV_KEY_RANGE            # 1639: u32 low key, u32 high key
ENGINE_GAIN = nbkt_gen.PRIV_ENGINE_GAIN     # 1836: f32
COLUMNS = 16

# ---- Battery zone ----------------------------------------------------------
ZONE_HEAD = "<III HHHH HHHH H fff"   # start, ?, ?, lovel, hivel, lokey, hikey,
#                                      4 fades, root key, volume, pan, tune
ZONE_HEAD_LEN = struct.calcsize(ZONE_HEAD)           # 42
ZONE_TAIL = "<IIIBIIIfBI"            # file id, bytes/sample, rate, channels,
#                                      frames, data offset, 60, 1.0, big-endian
#                                      flag, total sample bytes
ROOT_KEY = 36
ZONE_PRIV_ID = 83                    # u32 zone id in the private block
ZONE_KIDS_LOOP = 14                  # u32 4*(frames//8), u32 frames//8

MAGIC = b"\x12\x90\xa8\x7f"
INFO_MD5 = slice(0xA2, 0xB2)
INFO_CRC = 0xB6
MD5_SALT = b"\x09\xe2"

u16 = lambda b, o: struct.unpack_from("<H", b, o)[0]
u32 = lambda b, o: struct.unpack_from("<I", b, o)[0]
u64 = lambda b, o: struct.unpack_from("<Q", b, o)[0]
f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# NISound container ("hsin" items holding DSIN/4KIN chunks)
# ---------------------------------------------------------------------------

class Chunk:
    def __init__(self, domain, type_, ver, nested, data):
        self.domain, self.type, self.ver, self.nested, self.data = domain, type_, ver, nested, data

    def to_bytes(self):
        inner = (self.nested.to_bytes() if self.nested else b"") + bytes(self.data)
        return struct.pack("<Q4sII", 20 + len(inner), self.domain, self.type, self.ver) + inner


class Item:
    def __init__(self, ver, flags, pad, uid, chunk, child_ver, children):
        self.ver, self.flags, self.pad, self.uuid = ver, flags, pad, uid
        self.chunk, self.child_ver, self.children = chunk, child_ver, children

    def to_bytes(self):
        body = self.chunk.to_bytes() + struct.pack("<II", self.child_ver, len(self.children))
        for unk, dom, cid, item in self.children:
            body += struct.pack("<I4sI", unk, dom, cid) + item.to_bytes()
        head = struct.pack("<I4sII", self.ver, b"hsin", self.flags, self.pad) + self.uuid
        return struct.pack("<Q", 8 + len(head) + len(body)) + head + body

    def walk(self):
        yield self
        for *_, item in self.children:
            yield from item.walk()

    def find_chunk(self, type_):
        for item in self.walk():
            c = item.chunk
            while c:
                if c.type == type_:
                    return c
                c = c.nested
        raise BuildError("container chunk type %d not found" % type_)


def _parse_chunk(b, o):
    size, dom, type_, ver = u64(b, o), b[o + 8:o + 12], u32(b, o + 12), u32(b, o + 16)
    end, p, nested = o + size, o + 20, None
    if type_ != 1 and p + 20 <= end and b[p + 8:p + 12] in (b"DSIN", b"4KIN"):
        nested = _parse_chunk(b, p)
        p += u64(b, p)
    return Chunk(dom, type_, ver, nested, b[p:end])


def parse_item(b, o=0):
    size = u64(b, o)
    ver, tag, flags, pad = struct.unpack_from("<I4sII", b, o + 8)
    if tag != b"hsin" or o + size > len(b):
        raise BuildError("not an NISound container item")
    uid = b[o + 24:o + 40]
    chunk = _parse_chunk(b, o + 40)
    p = o + 40 + u64(b, o + 40)
    child_ver, n = u32(b, p), u32(b, p + 4)
    p += 8
    children = []
    for _ in range(n):
        unk, dom, cid = struct.unpack_from("<I4sI", b, p)
        p += 12
        children.append((unk, dom, cid, parse_item(b, p)))
        p += u64(b, p)
    if p != o + size:
        raise BuildError("container item size mismatch")
    return Item(ver, flags, pad, uid, chunk, child_ver, children)


# ---------------------------------------------------------------------------
# preset (Kontakt-style program)
# ---------------------------------------------------------------------------

def wstr(s):
    return struct.pack("<I", len(s.encode("utf-16-le")) // 2) + s.encode("utf-16-le")


def rstr(d, p):
    n = u32(d, p)
    return bytes(d[p + 4:p + 4 + 2 * n]).decode("utf-16-le"), p + 4 + 2 * n


def struct_parts(d, o=0):
    """Structured object at d[o:] -> (version, [private, public, children], end)."""
    if d[o] != 1:
        raise BuildError("expected a structured object")
    ver, p, parts = u16(d, o + 1), o + 3, []
    for _ in range(3):
        n = u32(d, p)
        parts.append(bytes(d[p + 4:p + 4 + n]))
        p += 4 + n
    return ver, parts, p


def struct_bytes(ver, priv, pub, kids):
    out = struct.pack("<BH", 1, ver)
    for part in (priv, pub, kids):
        out += struct.pack("<I", len(part)) + bytes(part)
    return out


def read_chunks(d):
    p, out = 0, []
    while p < len(d):
        cid, n = u16(d, p), u32(d, p + 2)
        out.append([cid, bytes(d[p + 6:p + 6 + n])])
        p += 6 + n
    if p != len(d):
        raise BuildError("preset chunk list overrun")
    return out


def chunk_bytes(chunks):
    return b"".join(struct.pack("<HI", cid, len(d)) + d for cid, d in chunks)


class Group:
    def __init__(self, ver, priv, pub, kids):
        self.ver, self.priv, self.pub, self.kids = ver, bytearray(priv), bytes(pub), bytes(kids)

    def copy(self):
        return Group(self.ver, self.priv, self.pub, self.kids)

    @property
    def name(self):
        return rstr(self.pub, 0)[0]

    def set_public(self, name=None, volume=None, tune=None):
        """Public block = name, f32 volume, f32 pan, f32 tune, rest."""
        old_name, p = rstr(self.pub, 0)
        vol, pan, tun = struct.unpack_from("<fff", self.pub, p)
        self.pub = (wstr(old_name if name is None else name)
                    + struct.pack("<fff", vol if volume is None else volume, pan,
                                  tun if tune is None else tune)
                    + self.pub[p + 12:])


class Zone:
    def __init__(self, group, ver, priv, pub, kids):
        self.group, self.ver, self.priv, self.pub, self.kids = group, ver, priv, pub, kids

    def fields(self):
        h = struct.unpack_from(ZONE_HEAD, self.pub, 0)
        t = struct.unpack_from(ZONE_TAIL, self.pub, ZONE_HEAD_LEN + 6)
        return dict(lovel=h[3], hivel=h[4], lokey=h[5], hikey=h[6], root=h[11],
                    volume=h[12], pan=h[13], tune=h[14], file=t[0], width=t[1], rate=t[2],
                    channels=t[3], frames=t[4], data_offset=t[5], big_endian=t[8],
                    total_bytes=t[9], zone_id=u32(self.priv, ZONE_PRIV_ID))


class Kit:
    """A decoded kit.  Only what this tool edits is modelled; the rest is kept
    verbatim and re-serialised byte for byte."""

    def __init__(self, data):
        self.root = parse_item(data)
        c115 = self.root.find_chunk(115)
        ver, flag, usize, csize = struct.unpack_from("<IBII", c115.data, 0)
        self._c115_head = (ver, flag)
        self.compressed = bytes(c115.data[13:13 + csize])
        self.raw = fastlz2.decompress(self.compressed, usize)
        self.inner = parse_item(self.raw)
        body = self.inner.find_chunk(109).data
        self._hdr109 = bytearray(body[:20])
        chunks_len = u32(body, 12)
        self.chunk_region = bytes(body[20:20 + chunks_len])
        self._trailer = bytes(body[20 + chunks_len:])
        self._chunks = read_chunks(self.chunk_region)
        prog = self._find(0x28)
        self.prog_ver, (self.prog_priv, self.prog_pub, kids), _ = struct_parts(prog[1])
        self._prog_kids = read_chunks(kids)
        self.groups = self._read_groups()
        self.zones = self._read_zones()
        self.files = self._read_files()

    def _find(self, cid):
        return next(c for c in self._chunks if c[0] == cid)

    def _prog_kid(self, cid):
        return next(c for c in self._prog_kids if c[0] == cid)

    def _read_groups(self):
        d = self._prog_kid(0x33)[1]
        n, p, out = u32(d, 0), 4, []
        for _ in range(n):
            ver, (priv, pub, kids), p = struct_parts(d, p)
            out.append(Group(ver, priv, pub, kids))
        return out

    def _read_zones(self):
        d = self._prog_kid(0x34)[1]
        n, p, out = u32(d, 0), 4, []
        for _ in range(n):
            group = u32(d, p)
            ver, (priv, pub, kids), p = struct_parts(d, p + 4)
            out.append(Zone(group, ver, priv, pub, kids))
        return out

    def _read_files(self):
        """-> list of (folder, file name, timestamp)."""
        d = self._find(0x4B)[1]
        if d[:2] != b"\x02\x00":
            return []                       # newer table layout (Dummy.nbkt): not modelled
        n, p, paths = u32(d, 10), 14, []
        for _ in range(n):
            nseg = u32(d, p)
            p += 4
            segs = []
            for _ in range(nseg):
                kind = d[p]
                s, p = rstr(d, p + 1)
                segs.append((kind, s))
            paths.append(segs)
        stamps = [u64(d, p + 8 * i) for i in range(n)]
        return [("/".join(s for k, s in segs if k == 2), next(s for k, s in segs if k == 4), st)
                for segs, st in zip(paths, stamps)]

    # ---- info block in the outer container
    def info_block(self):
        h = self.root.find_chunk(4).data
        return bytes(h[h.index(MAGIC):])

    @property
    def name(self):
        return rstr(self.prog_pub, 0)[0]

    def sample_bytes(self):
        """Total sample data, counting each distinct file once (the value
        Battery stores in the info block)."""
        per_file = {}
        for z in self.zones:
            f = z.fields()
            per_file[self.files[f["file"]][:2]] = f["total_bytes"]
        return sum(per_file.values())

    def checks(self):
        """Integrity facts Battery depends on."""
        info = bytearray(self.info_block())
        s = info.index(b"4taB") + 4
        md5 = bytes(info[INFO_MD5])
        info[INFO_MD5] = bytes(16)
        return dict(crc_ok=u32(info, INFO_CRC) == zlib.crc32(self.chunk_region) & 0xFFFFFFFF,
                    md5_ok=hashlib.md5(MD5_SALT + bytes(info)).digest() == md5,
                    info_zones=u16(info, s + 8), info_groups=u16(info, s + 10),
                    info_sample_bytes=u32(info, s + 14), timestamp=u32(info, s))

    # ---- serialisation
    def to_bytes(self, kit_file_name, timestamp, kit_uuid_seed):
        groups = struct.pack("<I", len(self.groups)) + b"".join(
            struct_bytes(g.ver, g.priv, g.pub, g.kids) for g in self.groups)
        zones = struct.pack("<I", len(self.zones)) + b"".join(
            struct.pack("<I", z.group) + struct_bytes(z.ver, z.priv, z.pub, z.kids)
            for z in self.zones)
        self._prog_kid(0x33)[1] = groups
        self._prog_kid(0x34)[1] = zones
        self._find(0x28)[1] = struct_bytes(self.prog_ver, self.prog_priv, self.prog_pub,
                                           chunk_bytes(self._prog_kids))

        # sample file table
        old = self._find(0x4B)[1]
        table = bytearray(old[:10]) + struct.pack("<I", len(self.files))
        for folder, name, _ in self.files:
            segs = ([(2, folder)] if folder else []) + [(4, name)]
            table += struct.pack("<I", len(segs)) + b"".join(bytes([k]) + wstr(s) for k, s in segs)
        table += b"".join(struct.pack("<Q", stamp) for *_, stamp in self.files)
        table += bytes(4 * len(self.files))
        table += struct.pack("<II", 1, 1) + b"\x04" + wstr(kit_file_name) + b"\x01\x00"
        self._find(0x4B)[1] = bytes(table)

        self.chunk_region = chunk_bytes(self._chunks)
        struct.pack_into("<I", self._hdr109, 12, len(self.chunk_region))
        self.inner.find_chunk(109).data = bytes(self._hdr109) + self.chunk_region + self._trailer
        self.raw = self.inner.to_bytes()
        self.compressed = fastlz2.compress(self.raw)
        self.root.find_chunk(115).data = struct.pack(
            "<IBII", *self._c115_head, len(self.raw), len(self.compressed)) + self.compressed

        # browser display name
        c108 = self.root.find_chunk(108)
        old_len = u32(c108.data, 16)
        c108.data = c108.data[:16] + wstr(self.name) + c108.data[16 + 4 + 2 * old_len:]

        # info block: timestamp, counts, sample bytes, CRC32, salted MD5
        c4 = self.root.find_chunk(4)
        h = bytearray(c4.data)
        i = h.index(MAGIC)
        s = h.index(b"4taB", i) + 4
        struct.pack_into("<I", h, s, timestamp)
        struct.pack_into("<HH", h, s + 8, len(self.zones), len(self.groups))
        struct.pack_into("<I", h, s + 14, self.sample_bytes())
        struct.pack_into("<I", h, i + INFO_CRC, zlib.crc32(self.chunk_region) & 0xFFFFFFFF)
        h[i + INFO_MD5.start:i + INFO_MD5.stop] = bytes(16)
        h[i + INFO_MD5.start:i + INFO_MD5.stop] = hashlib.md5(MD5_SALT + bytes(h[i:])).digest()
        c4.data = bytes(h)

        for n, item in enumerate(self.root.walk()):
            item.uuid = uuid.uuid5(uuid.NAMESPACE_URL, "%s/%d" % (kit_uuid_seed, n)).bytes
        return self.root.to_bytes()


# ---------------------------------------------------------------------------
# WAV
# ---------------------------------------------------------------------------

def wav_info(path):
    with wave.open(path, "rb") as w:
        info = dict(rate=w.getframerate(), channels=w.getnchannels(),
                    width=w.getsampwidth(), frames=w.getnframes())
    with open(path, "rb") as f:
        head = f.read(1 << 16)
    p, info["data_offset"] = 12, None
    while p + 8 <= len(head):
        cid, n = head[p:p + 4], u32(head, p + 4)
        if cid == b"data":
            info["data_offset"] = p + 8
            break
        p += 8 + n + (n & 1)
    if info["data_offset"] is None:
        raise BuildError("no data chunk found in %s" % path)
    return info


# ---------------------------------------------------------------------------
# SPD-30 backup
# ---------------------------------------------------------------------------

def parse_backup(path):
    """-> list of kits; each kit is a dict with 'name' and 'layers'."""
    with open(path, "rb") as f:
        blob = f.read()
    if blob[2:6] != b"SVD2" or len(blob) < KIT_BASE + KIT_COUNT * KIT_SIZE:
        raise BuildError("%s is not an SPD-30 backup" % path)
    kits = []
    for k in range(KIT_COUNT):
        rec = blob[KIT_BASE + k * KIT_SIZE:KIT_BASE + (k + 1) * KIT_SIZE]
        layers = []
        for p in range(12):
            pad = rec[PAD_BASE + p * PAD_SIZE:PAD_BASE + (p + 1) * PAD_SIZE]
            mode = pad[0]
            for li in range(2):
                o = 4 + 16 * li
                category, tone, volume = pad[o], pad[o + 1], pad[o + 2]
                raw_tune = struct.unpack(">H", pad[o + 4:o + 6])[0]
                muted = pad[o + 10] == 1
                note = pad[o + 14]
                if li == 1 and mode == 0:
                    reason = "layer B disabled (single mode)"
                elif category == CAT_OFF:
                    reason = "instrument OFF"
                elif volume == 0:
                    reason = "zero volume"
                elif muted:
                    reason = "muted"
                else:
                    reason = ""
                layers.append(dict(
                    kit=k + 1, pad_index=p, pad=PAD_LABELS[p], prefix=PAD_PREFIXES[p],
                    layer="AB"[li], layer_index=li, layer_mode=mode, category=category,
                    tone=tone + 1, volume=volume, raw_tune=raw_tune,
                    semitones=(raw_tune - 2400) / 100.0, note=note,
                    active=not reason, inactive_reason=reason,
                    cell=li * COLUMNS + p))
        kits.append(dict(number=k + 1, name=rec[:16].decode("ascii", "replace").rstrip(),
                         volume=rec[16], layers=layers))
    return kits


def assign_note_off_keys(active_layers):
    """-> {cell: key} for the active layers whose MIDI note is OFF."""
    taken = {l["note"] for l in active_layers if l["note"] < NOTE_OFF}
    free = (k for k in NOTE_OFF_KEYS if k not in taken)
    return {l["cell"]: next(free)
            for l in sorted(active_layers, key=lambda l: l["cell"]) if l["note"] >= NOTE_OFF}


def find_wav(kit_dir, layer):
    folder = os.path.join(kit_dir, "Layer %s" % layer["layer"])
    if not os.path.isdir(folder):
        return []
    want = layer["prefix"] + "-"
    return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                  if f.startswith(want) and f.lower().endswith(".wav"))


# ---------------------------------------------------------------------------
# kit builder
# ---------------------------------------------------------------------------

class Builder:
    def __init__(self, cell_template, wrapper_template, params="group"):
        if params not in ("group", "zone"):
            raise BuildError("params must be 'group' or 'zone'")
        self.params = params
        with open(wrapper_template, "rb") as f:
            self.wrapper_bytes = f.read()
        with open(cell_template, "rb") as f:
            cells = Kit(f.read())
        wrapper = Kit(self.wrapper_bytes)
        if len(cells.groups) != 128 or len(wrapper.groups) != 128:
            raise BuildError("templates must have 128 cells")
        if not all(wrapper.checks()[k] for k in ("crc_ok", "md5_ok")):
            raise BuildError("wrapper template fails its own CRC/MD5 check")
        self.empty_groups = cells.groups
        self.default_keys = [u32(g.priv, KEY_LO) for g in cells.groups]

        # Bytes Battery sets in a cell's private block once it holds a sample:
        # the difference between a filled cell of the wrapper kit and the same
        # (empty) cell of the cell template, ignoring the key range.
        used = sorted({z.group for z in wrapper.zones})
        if not used:
            raise BuildError("wrapper template has no sample-bearing cell")
        flags = None
        for gi in used:
            a, b = cells.groups[gi].priv, wrapper.groups[gi].priv
            if len(a) != len(b):
                raise BuildError("cell block size differs between templates")
            cur = {o: b[o] for o in range(len(a))
                   if a[o] != b[o] and not KEY_LO <= o < KEY_LO + 8}
            if flags is not None and cur != flags:
                raise BuildError("filled cells of the wrapper template disagree")
            flags = cur
        self.fill_flags = flags

        # zone template: a mono 16-bit zone if there is one
        zones = sorted(wrapper.zones, key=lambda z: (z.fields()["channels"] != 1,
                                                    z.fields()["width"] != 2))
        self.zone_template = zones[0]

    def make_zone(self, group, zone_id, file_id, key, wav, volume=1.0, tune=1.0):
        t = self.zone_template
        head = struct.pack(ZONE_HEAD, 0, 0, 0xFFFFFFFF, 1, 127, key, key, 0, 0, 0, 0,
                           ROOT_KEY, volume, 0.0, tune)
        mid = t.pub[ZONE_HEAD_LEN:ZONE_HEAD_LEN + 6]
        total = wav["frames"] * wav["channels"] * wav["width"]
        tail = struct.pack(ZONE_TAIL, file_id, wav["width"], wav["rate"], wav["channels"],
                           wav["frames"], wav["data_offset"], 60, 1.0, 0, total)
        pub = head + mid + tail
        priv = bytearray(t.priv)
        priv[22] = 1                    # value of every unedited zone in real kits
        priv[64:79] = bytes(15)         # per-zone edit data, zero when unedited
        struct.pack_into("<I", priv, ZONE_PRIV_ID, zone_id)
        kids = bytearray(t.kids)
        loop = wav["frames"] // 8
        struct.pack_into("<II", kids, ZONE_KIDS_LOOP, 4 * loop, loop)
        if len(pub) != len(t.pub):
            raise BuildError("zone public block has the wrong size")
        return Zone(group, t.ver, bytes(priv), pub, bytes(kids))

    def build(self, kit_name, cells, timestamp):
        """cells: list of dicts with cell, key, volume (linear), tune_ratio,
        label, and (optional) sample_name / wav / stamp."""
        kit = Kit(self.wrapper_bytes)
        old_name, p = rstr(kit.prog_pub, 0)
        kit.prog_pub = wstr(kit_name) + kit.prog_pub[p:]
        kit.groups = [g.copy() for g in self.empty_groups]
        kit.zones, kit.files = [], []
        in_group = self.params == "group"
        for c in sorted(cells, key=lambda c: c["cell"]):
            g = kit.groups[c["cell"]]
            struct.pack_into("<II", g.priv, KEY_LO, c["key"], c["key"])
            if in_group:
                struct.pack_into("<f", g.priv, ENGINE_GAIN, c["volume"])
                g.set_public(volume=c["volume"], tune=c["tune_ratio"])
            if c.get("wav"):
                for o, v in self.fill_flags.items():
                    g.priv[o] = v
                g.set_public(name=c["label"])
                kit.files.append(("%s Samples" % kit_name, c["sample_name"], c["stamp"]))
                kit.zones.append(self.make_zone(
                    c["cell"], len(kit.zones), len(kit.files) - 1, c["key"], c["wav"],
                    volume=1.0 if in_group else c["volume"],
                    tune=1.0 if in_group else c["tune_ratio"]))
        return kit.to_bytes(kit_name + ".nbkt", timestamp, kit_name)


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify_kit(path, cells, builder):
    """Re-open a written kit from disk and check everything Battery relies on.
    Returns a list of error strings (empty = all checks passed)."""
    errs = []
    with open(path, "rb") as f:
        data = f.read()
    if u64(data, 0) != len(data):
        errs.append("total size field != file size")
    try:
        kit = Kit(data)                         # parses sizes, FastLZ-decodes
    except (BuildError, fastlz2.FastLZError, struct.error) as e:
        return ["does not parse: %s" % e]
    if parse_item(data).to_bytes() != data:
        errs.append("outer container does not re-serialise identically")
    if kit.inner.to_bytes() != kit.raw:
        errs.append("preset container does not re-serialise identically")
    if kit.compressed[0] >> 5 != 1:
        errs.append("payload is not FastLZ level 2")
    chk = kit.checks()
    if not chk["crc_ok"]:
        errs.append("preset CRC32 mismatch")
    if not chk["md5_ok"]:
        errs.append("trailer MD5 mismatch")

    kit_name = os.path.splitext(os.path.basename(path))[0]
    if kit.name != kit_name:
        errs.append("kit name is %r" % kit.name)
    want = sorted(cells, key=lambda c: c["cell"])
    linked = [c for c in want if c.get("wav")]
    if len(kit.groups) != 128:
        errs.append("%d groups" % len(kit.groups))
    if (len(kit.zones), len(kit.files)) != (len(linked), len(linked)):
        errs.append("zones/files %d/%d, expected %d" % (len(kit.zones), len(kit.files), len(linked)))
        return errs
    if (chk["info_zones"], chk["info_groups"]) != (len(linked), 128):
        errs.append("info block zone/group counts wrong")
    if chk["info_sample_bytes"] != kit.sample_bytes():
        errs.append("info block sample byte total wrong")

    # cell parameters, read with the independent nbkt_gen reader
    infos = nbkt_gen.read_cells(kit.raw)
    in_group = builder.params == "group"
    zone_of = {z.group: z for z in kit.zones}
    samples_dir = os.path.join(os.path.dirname(path), "%s Samples" % kit_name)
    for c in want:
        info, tag = infos[c["cell"]], "cell %d" % c["cell"]
        vol, tune = f32(c["volume"]), f32(c["tune_ratio"])
        if (info.low_key, info.high_key) != (c["key"], c["key"]):
            errs.append("%s: key range %d-%d" % (tag, info.low_key, info.high_key))
        if in_group:
            if info.engine_gain != vol or info.volume != vol:
                errs.append("%s: volume engine=%r quick=%r" % (tag, info.engine_gain, info.volume))
            if info.tune_ratio != tune:
                errs.append("%s: tune ratio %r" % (tag, info.tune_ratio))
        z = zone_of.get(c["cell"])
        if not c.get("wav"):
            if z is not None:
                errs.append("%s: unexpected zone" % tag)
            continue
        if z is None:
            errs.append("%s: no zone" % tag)
            continue
        f = z.fields()
        if (f["lokey"], f["hikey"]) != (c["key"], c["key"]):
            errs.append("%s: zone key %d-%d" % (tag, f["lokey"], f["hikey"]))
        zv, zt = (1.0, 1.0) if in_group else (vol, tune)
        if f["volume"] != zv or f["tune"] != zt:
            errs.append("%s: zone volume/tune %r/%r" % (tag, f["volume"], f["tune"]))
        folder, name, _ = kit.files[f["file"]]
        if folder != "%s Samples" % kit_name or name != c["sample_name"]:
            errs.append("%s: sample path %s/%s" % (tag, folder, name))
            continue
        on_disk = os.path.join(samples_dir, name)
        if not os.path.isfile(on_disk):
            errs.append("%s: linked sample missing on disk" % tag)
            continue
        w = wav_info(on_disk)
        if any(f[k] != w[k] for k in ("rate", "channels", "width", "frames", "data_offset")):
            errs.append("%s: zone format does not match the WAV" % tag)
        if info.name != c["label"]:
            errs.append("%s: cell name %r" % (tag, info.name))

    # every other cell must be exactly the clean template cell
    used = {c["cell"] for c in want}
    for i, (g, e) in enumerate(zip(kit.groups, builder.empty_groups)):
        if i not in used and (g.priv, g.pub, g.kids) != (e.priv, e.pub, e.kids):
            errs.append("cell %d differs from the template but was not requested" % i)
    return errs


def self_test(builder, reference_kits):
    """Check the format model against kits saved by Battery 4 itself:
    1. each parses and re-serialises byte for byte, CRC and MD5 validate;
    2. every unedited 16/24-bit PCM WAV zone in them is reproduced exactly by
       make_zone() (other sample formats are outside what this tool writes);
    3. Dummy's cells equal the empty cells of the wrapper kit."""
    results = []
    zones_ok = zones_total = 0
    for path in reference_kits:
        with open(path, "rb") as f:
            data = f.read()
        kit = Kit(data)
        chk = kit.checks()
        ok = (parse_item(data).to_bytes() == data and kit.inner.to_bytes() == kit.raw
              and chk["crc_ok"] and chk["md5_ok"]
              and chk["info_zones"] == len(kit.zones)
              and chk["info_groups"] == len(kit.groups)
              and chk["info_sample_bytes"] == kit.sample_bytes())
        results.append((os.path.basename(path), ok, len(kit.zones)))
        for z in kit.zones:
            f = z.fields()
            if z.priv[22] != 1 or any(z.priv[64:79]) or f["big_endian"] or u32(z.pub, 0) \
                    or f["lokey"] != f["hikey"] or f["width"] not in (2, 3):
                continue                       # edited in Battery / AIFF / non-PCM
            zones_total += 1
            saved, builder.zone_template = builder.zone_template, z
            made = builder.make_zone(z.group, f["zone_id"], f["file"], f["lokey"], f,
                                     volume=f["volume"], tune=f["tune"])
            builder.zone_template = saved
            same_pub = made.pub == z.pub
            same_kids = made.kids == z.kids
            priv = bytearray(z.priv)
            mpriv = bytearray(made.priv)
            for o in (12, 13, 14, 15, 38):     # per-zone settings unrelated to the sample
                priv[o] = mpriv[o] = 0
            zones_ok += same_pub and same_kids and priv == mpriv
    wrapper = Kit(builder.wrapper_bytes)
    used = {z.group for z in wrapper.zones}
    cells_equal = all((a.priv, a.pub, a.kids) == (b.priv, b.pub, b.kids)
                      for i, (a, b) in enumerate(zip(builder.empty_groups, wrapper.groups))
                      if i not in used)
    return results, zones_ok, zones_total, cells_equal


# ---------------------------------------------------------------------------
# main conversion
# ---------------------------------------------------------------------------

def copy_sample(src, dst):
    if os.path.exists(dst):
        os.remove(dst)
    shutil.copy2(src, dst)                     # keeps the modification time


def convert(args):
    builder = Builder(args.cell_template, args.wrapper_template, args.params)
    refs = [os.path.join(os.path.dirname(args.wrapper_template), f)
            for f in sorted(os.listdir(os.path.dirname(args.wrapper_template)))
            if f.lower().endswith(".nbkt") and os.path.join(
                os.path.dirname(args.wrapper_template), f) != args.cell_template]
    test = self_test(builder, refs)

    kits = parse_backup(args.backup)
    kit_dirs = {}
    for name in sorted(os.listdir(args.samples)):
        if os.path.isdir(os.path.join(args.samples, name)) and name[:3].isdigit() and name[3:4] == " ":
            kit_dirs.setdefault(int(name[:3]), name)

    os.makedirs(args.out_dir, exist_ok=True)
    rows, kit_rows, problems = [], [], []
    for kit in kits:
        active = [l for l in kit["layers"] if l["active"]]
        folder = kit_dirs.get(kit["number"])
        if not active:
            kit_rows.append((kit["number"], folder or kit["name"], 0, 0, "no active layer - not written"))
            continue
        if folder is None:
            problems.append("kit %03d: no sample folder" % kit["number"])
        kit_name = folder or "%03d %s" % (kit["number"], kit["name"].replace("/", "-"))
        kit_dir = os.path.join(args.samples, folder) if folder else None
        dest = os.path.join(args.out_dir, kit_name)
        samples_dest = os.path.join(dest, "%s Samples" % kit_name)
        os.makedirs(samples_dest, exist_ok=True)

        cells, used_wavs = [], set()
        off_keys = assign_note_off_keys(active)
        for l in active:
            hits = find_wav(kit_dir, l) if kit_dir else []
            key = l["note"] if l["note"] < NOTE_OFF else off_keys[l["cell"]]
            cell = dict(cell=l["cell"], key=key, volume=l["volume"] / 100.0,
                        tune_ratio=2.0 ** (l["semitones"] / 12.0), layer=l)
            if len(hits) > 1:
                problems.append("kit %03d %s %s: %d WAVs match, using %s"
                                % (kit["number"], l["pad"], l["layer"], len(hits),
                                   os.path.basename(hits[0])))
            if hits:
                src = hits[0]
                used_wavs.add(src)
                cell["sample_name"] = "%s %s" % (l["layer"], os.path.basename(src))
                cell["label"] = os.path.splitext(os.path.basename(src))[0]
                dst = os.path.join(samples_dest, cell["sample_name"])
                copy_sample(src, dst)
                cell["wav"] = wav_info(dst)
                cell["stamp"] = int(os.path.getmtime(dst))
                cell["dst"] = dst
            else:
                problems.append("kit %03d %s %s: active layer has no WAV (cell written without a sample)"
                                % (kit["number"], l["pad"], l["layer"]))
            cells.append(cell)
        if kit_dir:
            for layer_name in ("Layer A", "Layer B"):
                d = os.path.join(kit_dir, layer_name)
                for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                    if f.lower().endswith(".wav") and os.path.join(d, f) not in used_wavs:
                        problems.append("kit %03d: %s/%s matches no active layer (not linked)"
                                        % (kit["number"], layer_name, f))

        stamp = max([c["stamp"] for c in cells if c.get("wav")], default=0)
        path = os.path.join(dest, kit_name + ".nbkt")
        with open(path, "wb") as f:
            f.write(builder.build(kit_name, cells, stamp))
        errs = verify_kit(path, cells, builder)
        linked = sum(1 for c in cells if c.get("wav"))
        kit_rows.append((kit["number"], kit_name, len(cells), linked,
                         "verified" if not errs else "FAILED: " + "; ".join(errs[:4])))
        bad_cells = {int(e.split(":")[0][5:]) for e in errs if e.startswith("cell ")}
        kit_level = [e for e in errs if not e.startswith("cell ")]
        for c in cells:
            l = c["layer"]
            rows.append({
                "kit_num": kit["number"], "kit_name": kit_name, "roland_pad": l["pad"],
                "layer": l["layer"], "layer_mode": l["layer_mode"],
                "battery_cell": c["cell"], "battery_row": c["cell"] // COLUMNS + 1,
                "battery_column": c["cell"] % COLUMNS + 1,
                "midi_note": "OFF" if l["note"] >= NOTE_OFF else l["note"],
                "key_written": c["key"],
                "note_remark": "note OFF on SPD-30 - key %d assigned" % c["key"] if l["note"] >= NOTE_OFF else "",
                "volume_level": l["volume"], "volume_float": "%.9g" % f32(c["volume"]),
                "tune_semitones": "%+.2f" % l["semitones"],
                "tune_ratio": "%.9g" % f32(c["tune_ratio"]),
                "sample": c.get("sample_name", ""),
                "sample_frames": c["wav"]["frames"] if c.get("wav") else "",
                "verification": "FAIL" if (c["cell"] in bad_cells or kit_level) else "PASS",
            })

    write_outputs(args, rows, kit_rows, problems, test, kits, builder)
    return rows, kit_rows, problems, test


def write_outputs(args, rows, kit_rows, problems, test, kits, builder):
    if rows:
        with open(os.path.join(args.out_dir, "kit_cell_report.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    refs, zones_ok, zones_total, cells_equal = test
    layers = [l for k in kits for l in k["layers"]]
    reasons = Counter(l["inactive_reason"] for l in layers if not l["active"])
    written = [k for k in kit_rows if k[2]]
    out = []
    w = out.append
    w("# SPD-30 to Battery 4 build report\n")
    w("| Check | Result |")
    w("|---|---|")
    w("| Kits written | %d (%d verified, %d failed) |"
      % (len(written), sum(k[4] == "verified" for k in written),
         sum(k[4] != "verified" for k in written)))
    w("| Cells written | %d (%d pass read-back) |"
      % (len(rows), sum(r["verification"] == "PASS" for r in rows)))
    w("| Cells with a linked sample | %d |" % sum(bool(r["sample"]) for r in rows))
    w("| Battery-saved reference kits re-serialise byte for byte, CRC and MD5 valid | %s |"
      % ", ".join("%s: %s" % (n, "yes" if ok else "NO") for n, ok, _ in refs))
    w("| Unedited PCM WAV zones of those kits reproduced exactly by the zone builder | %d of %d |"
      % (zones_ok, zones_total))
    w("| Cell template's cells identical to the wrapper kit's empty cells | %s |"
      % ("yes" if cells_equal else "NO"))
    w("| Loaded and played in Battery 4 | **not tested** |")
    w("")
    w("Volume and tune are written to the %s." % (
        "cell (engine gain, quick-access level and quick-access tune); the zone stays at 1.0"
        if builder.params == "group" else "zone; the cell fields keep the template values"))
    w("")
    w("## Layers in the backup\n")
    w("| | Count |")
    w("|---|---|")
    w("| Layers (100 kits x 12 pads x 2) | %d |" % len(layers))
    w("| Active | %d |" % sum(l["active"] for l in layers))
    for reason, n in sorted(reasons.items()):
        w("| Inactive: %s | %d |" % (reason, n))
    w("| Active layers with MIDI note OFF (keys 120-127 assigned in cell order, then 119 downwards) | %d |"
      % sum(1 for r in rows if r["midi_note"] == "OFF"))
    w("")
    w("## Problems\n")
    w("\n".join("- " + p for p in problems) if problems else "None.")
    w("")
    w("## Kits\n")
    w("| Kit | Folder | Cells | Samples linked | Result |")
    w("|---|---|---|---|---|")
    for num, name, n, linked, result in kit_rows:
        w("| %03d | %s | %d | %d | %s |" % (num, name, n, linked, result))
    with open(os.path.join(args.out_dir, "build_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main(argv=None):
    tpl = os.path.join(HERE, "templates")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backup", default=os.path.join(HERE, "input", "BACKUP01.BIN"),
                    help="SPD-30 backup file (default: input/BACKUP01.BIN)")
    ap.add_argument("--samples", default=os.path.join(HERE, "input", "samples"),
                    help="folder with one 'NNN Kit' folder per kit (default: input/samples)")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "output", "battery4_kits"),
                    help="where kits and reports go (default: output/battery4_kits)")
    ap.add_argument("--cell-template", default=os.path.join(tpl, "Dummy.nbkt"),
                    help="empty kit saved by Battery 4 (default: templates/Dummy.nbkt)")
    ap.add_argument("--wrapper-template", default=os.path.join(tpl, "wrapper.nbkt"),
                    help="kit saved by Battery 4 with samples (default: templates/wrapper.nbkt)")
    ap.add_argument("--params", choices=("group", "zone"), default="group",
                    help="where volume and tune go: the cell's group fields (default) "
                         "or the sample zone, as Battery-saved kits do")
    args = ap.parse_args(argv)
    missing = [(what, path) for what, path, ok in (
        ("SPD-30 backup (--backup)", args.backup, os.path.isfile),
        ("sample folder (--samples)", args.samples, os.path.isdir),
        ("empty Battery kit (--cell-template)", args.cell_template, os.path.isfile),
        ("Battery kit with a sample (--wrapper-template)", args.wrapper_template, os.path.isfile),
    ) if not ok(path)]
    if missing:
        for what, path in missing:
            print("error: %s not found: %s" % (what, path), file=sys.stderr)
        print("See README.md, 'Prepare your files'.", file=sys.stderr)
        return 1
    try:
        rows, kit_rows, problems, test = convert(args)
    except (BuildError, nbkt_gen.NbktError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    refs, zones_ok, zones_total, cells_equal = test
    written = [k for k in kit_rows if k[2]]
    failed = [k for k in written if k[4] != "verified"]
    print("format self-test: reference kits %s; zones reproduced %d/%d; cell template matches: %s"
          % ("OK" if all(ok for _, ok, _ in refs) else "FAILED", zones_ok, zones_total, cells_equal))
    print("kits written: %d, verified: %d, failed: %d"
          % (len(written), len(written) - len(failed), len(failed)))
    print("cells: %d, with linked sample: %d, read-back PASS: %d"
          % (len(rows), sum(bool(r["sample"]) for r in rows),
             sum(r["verification"] == "PASS" for r in rows)))
    print("problems: %d" % len(problems))
    for p in problems[:20]:
        print("  - " + p)
    for k in failed[:10]:
        print("  FAILED %03d %s: %s" % (k[0], k[1], k[4]))
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
