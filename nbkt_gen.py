#!/usr/bin/env python3
"""Generate Native Instruments Battery 4 kits (.nbkt) from a template kit.

Patches per-cell Key Range (MIDI note), Volume and Tune into a clean template
(`Dummy.nbkt`), re-compresses the preset (FastLZ level 2), fixes the CRC32 /
MD5 in the trailer and the chunk lengths in the NISound prefix, then
round-trip verifies the result.

Library use:

    from nbkt_gen import Cell, generate_kit
    generate_kit("MyKit", {
        0: Cell(note=36, volume=100, tune=0),      # row 0, pad 0
        1: Cell(note=38, volume=80,  tune=-2.5),
        17: Cell(note=42),                          # row 1, pad 1; note only
    })

Command line:

    python3 nbkt_gen.py MyKit --cell 0:36:100:0 --cell 1:38:80:-2.5
    python3 nbkt_gen.py MyKit --json cells.json
    python3 nbkt_gen.py --verify "MyKit.nbkt"
    python3 nbkt_gen.py --dump  "MyKit.nbkt"

Cell ID = row * 16 + pad (0..127).  Fields left as None / empty are not
touched and keep the template's value.
"""

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional

import fastlz2

# --- container layout (Dummy.nbkt) -----------------------------------------
PREFIX_LEN = 0x420                       # outer hsin/DSIN/4KIN headers
SUFFIX_LEN = 339                         # trailer: CRC32 + MD5 info block
LEN_FIELDS = (0x0000, 0x00D2, 0x03AB, 0x03D3, 0x03E7)   # uint64 chunk lengths
OFF_DECOMP_SIZE = 0x0418                 # uint32
OFF_COMP_SIZE = 0x041C                   # uint32

# --- trailer info block ------------------------------------------------------
INFO_OFF = 109                           # inside the 339-byte suffix
INFO_LEN = 222
INFO_MD5 = slice(0xA2, 0xB2)
INFO_CRC = 0xB6
MD5_SALT = b"\x09\xe2"

# --- decompressed preset -----------------------------------------------------
OFF_PRESET_LEN = 0xB4                    # uint64 length of the preset chunk
OFF_PRESET = 0xBC                        # CRC32 covers [0xBC : 0xBC + length]

# --- cell (group) blocks -----------------------------------------------------
NUM_CELLS = 128
PRIV_SIZE = 2390
# every group starts: 01 | chunk id 0x95 (u16) | private-block size (u32)
GROUP_HEADER = b"\x01\x95\x00" + struct.pack("<I", PRIV_SIZE)
PRIV_KEY_RANGE = 1639                    # uint32 low_key, uint32 high_key
PRIV_KEY_TAG = PRIV_KEY_RANGE + 8        # followed by 01 07 00 00
KEY_TAG = b"\x01\x07\x00\x00"
PRIV_ENGINE_GAIN = 1836                  # float32 linear gain
# public block = uint32 name_len, UTF-16LE name, f32 volume, f32 pan, f32 tune
# With the usual 1-character name (" ") volume is at pub+6 and tune at pub+14.
# Cells with an empty name (pads 13-16 and rows 5-8 of the template) are two
# bytes shorter, so the offsets are derived from the name length instead.

VOLUME_RANGE = (0.0, 100.0)
TUNE_RANGE = (-36.0, 36.0)               # Battery 4's Tune knob range


class NbktError(Exception):
    pass


@dataclass
class Cell:
    """Parameters to inject into one cell; None leaves the template value."""
    note: Optional[int] = None           # MIDI note 0..127 (low == high key)
    volume: Optional[float] = None       # 0..100  (100 = 1.0 linear = 0 dB)
    tune: Optional[float] = None         # semitones (ratio = 2 ** (st / 12))


@dataclass
class CellInfo:
    """What is actually stored in a kit for one cell."""
    cell: int
    name: str
    low_key: int
    high_key: int
    engine_gain: float
    volume: float                        # quick-access level, linear
    pan: float
    tune_ratio: float

    @property
    def tune_semitones(self):
        return 12.0 * math.log2(self.tune_ratio) if self.tune_ratio > 0 else float("nan")


@dataclass
class _Group:
    priv: int
    pub: int
    name_len: int

    @property
    def off_volume(self):
        return self.pub + 4 + 2 * self.name_len

    @property
    def off_tune(self):
        return self.off_volume + 8


# ---------------------------------------------------------------------------
# container parsing
# ---------------------------------------------------------------------------

def split_container(blob):
    """Split a .nbkt into (prefix, compressed payload, suffix)."""
    if len(blob) < PREFIX_LEN + SUFFIX_LEN + 16:
        raise NbktError("file too small to be a Battery 4 kit")
    if blob[0x0C:0x10] != b"hsin":
        raise NbktError("missing 'hsin' container tag")
    prefix = blob[:PREFIX_LEN]
    payload = blob[PREFIX_LEN:len(blob) - SUFFIX_LEN]
    suffix = blob[len(blob) - SUFFIX_LEN:]
    comp_size = struct.unpack_from("<I", prefix, OFF_COMP_SIZE)[0]
    if comp_size != len(payload):
        raise NbktError(
            "compressed size field (%d) does not match payload (%d bytes); "
            "this kit's prefix is not laid out like Dummy.nbkt"
            % (comp_size, len(payload)))
    return prefix, payload, suffix


def find_groups(decomp) -> List[_Group]:
    """Locate the 128 cell groups in the decompressed preset."""
    groups = []
    pos = decomp.find(GROUP_HEADER)
    while pos != -1:
        priv = pos + len(GROUP_HEADER)
        pub_size_off = priv + PRIV_SIZE
        pub = pub_size_off + 4
        if (pub + 4 <= len(decomp)
                and decomp[priv + PRIV_KEY_TAG:priv + PRIV_KEY_TAG + 4] == KEY_TAG):
            pub_size = struct.unpack_from("<I", decomp, pub_size_off)[0]
            name_len = struct.unpack_from("<I", decomp, pub)[0]
            if 4 + 2 * name_len + 12 > pub_size or pub + pub_size > len(decomp):
                raise NbktError("group at 0x%x has a malformed public block" % pos)
            groups.append(_Group(priv, pub, name_len))
            pos = decomp.find(GROUP_HEADER, pub + pub_size)
        else:
            pos = decomp.find(GROUP_HEADER, pos + 1)
    if len(groups) != NUM_CELLS:
        raise NbktError("expected %d cell groups, found %d" % (NUM_CELLS, len(groups)))
    return groups


def read_cells(decomp) -> List[CellInfo]:
    out = []
    for i, g in enumerate(find_groups(decomp)):
        low, high = struct.unpack_from("<II", decomp, g.priv + PRIV_KEY_RANGE)
        gain = struct.unpack_from("<f", decomp, g.priv + PRIV_ENGINE_GAIN)[0]
        vol, pan, tune = struct.unpack_from("<fff", decomp, g.off_volume)
        name = bytes(decomp[g.pub + 4:g.pub + 4 + 2 * g.name_len]).decode("utf-16-le", "replace")
        out.append(CellInfo(i, name, low, high, gain, vol, pan, tune))
    return out


def _info_block(suffix):
    return bytearray(suffix[INFO_OFF:INFO_OFF + INFO_LEN])


def _info_md5(info):
    tmp = bytearray(info)
    tmp[INFO_MD5] = bytes(16)
    return hashlib.md5(MD5_SALT + bytes(tmp)).digest()


def _preset_crc(decomp):
    chunk_len = struct.unpack_from("<Q", decomp, OFF_PRESET_LEN)[0]
    if OFF_PRESET + chunk_len > len(decomp):
        raise NbktError("preset chunk length runs past the decompressed stream")
    return zlib.crc32(bytes(decomp[OFF_PRESET:OFF_PRESET + chunk_len])) & 0xFFFFFFFF


def _check_container(blob, label):
    """Structural + checksum validation shared by template loading and verify.

    Returns (prefix, suffix, decompressed preset)."""
    prefix, payload, suffix = split_container(blob)
    if struct.unpack_from("<Q", prefix, 0)[0] != len(blob):
        raise NbktError("%s: total-size field does not match file size" % label)
    decomp_size = struct.unpack_from("<I", prefix, OFF_DECOMP_SIZE)[0]
    try:
        decomp = fastlz2.decompress(payload, decomp_size)
    except fastlz2.FastLZError as e:
        raise NbktError("%s: payload does not decompress: %s" % (label, e)) from None
    if payload[0] >> 5 != 1:
        raise NbktError("%s: payload is not a FastLZ level 2 stream" % label)
    info = _info_block(suffix)
    crc = _preset_crc(decomp)
    stored_crc = struct.unpack_from("<I", info, INFO_CRC)[0]
    if crc != stored_crc:
        raise NbktError("%s: preset CRC32 mismatch (stored %08x, actual %08x)"
                        % (label, stored_crc, crc))
    if _info_md5(info) != bytes(info[INFO_MD5]):
        raise NbktError("%s: trailer MD5 mismatch" % label)
    return prefix, suffix, decomp


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

def default_template():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "templates", "Dummy.nbkt"),
                 os.path.join(here, "Dummy.nbkt"),
                 "Dummy.nbkt"):
        if os.path.isfile(cand):
            return cand
    raise NbktError("Dummy.nbkt not found; pass template=/--template")


def _validate(cell_id, c):
    if not isinstance(cell_id, int) or not 0 <= cell_id < NUM_CELLS:
        raise NbktError("cell id %r out of range 0..127" % (cell_id,))
    if c.note is not None and (int(c.note) != c.note or not 0 <= c.note <= 127):
        raise NbktError("cell %d: MIDI note %r out of range 0..127" % (cell_id, c.note))
    for label, val, (lo, hi) in (("volume", c.volume, VOLUME_RANGE),
                                 ("tune", c.tune, TUNE_RANGE)):
        if val is not None and not (math.isfinite(val) and lo <= val <= hi):
            raise NbktError("cell %d: %s %r out of range %g..%g"
                            % (cell_id, label, val, lo, hi))


def patch_cells(decomp, cells: Dict[int, Cell]):
    """Inject parameters into a decompressed preset (in place).

    Returns {offset: length} of every byte range that was written."""
    groups = find_groups(decomp)
    written = {}
    for cell_id, c in sorted(cells.items()):
        _validate(cell_id, c)
        g = groups[cell_id]
        if c.note is not None:
            off = g.priv + PRIV_KEY_RANGE
            struct.pack_into("<II", decomp, off, int(c.note), int(c.note))
            written[off] = 8
        if c.volume is not None:
            vol = c.volume / 100.0
            for off in (g.priv + PRIV_ENGINE_GAIN, g.off_volume):
                struct.pack_into("<f", decomp, off, vol)
                written[off] = 4
        if c.tune is not None:
            struct.pack_into("<f", decomp, g.off_tune, 2.0 ** (c.tune / 12.0))
            written[g.off_tune] = 4
    return written


def build_kit(template_blob, cells: Dict[int, Cell]):
    """Return the bytes of a new .nbkt built from `template_blob`."""
    prefix, suffix, tdecomp = _check_container(template_blob, "template")
    template_comp_size = len(template_blob) - PREFIX_LEN - SUFFIX_LEN

    decomp = bytearray(tdecomp)
    patch_cells(decomp, cells)

    comp = fastlz2.compress(decomp)

    # trailer: CRC32 of the preset chunk, then MD5 over the info block
    suffix = bytearray(suffix)
    info = _info_block(suffix)
    struct.pack_into("<I", info, INFO_CRC, _preset_crc(decomp))
    info[INFO_MD5] = _info_md5(info)
    suffix[INFO_OFF:INFO_OFF + INFO_LEN] = info

    # prefix: shift every enclosing chunk length by the payload size change
    prefix = bytearray(prefix)
    diff = len(comp) - template_comp_size
    for off in LEN_FIELDS:
        old = struct.unpack_from("<Q", prefix, off)[0]
        struct.pack_into("<Q", prefix, off, old + diff)
    struct.pack_into("<I", prefix, OFF_DECOMP_SIZE, len(decomp))
    struct.pack_into("<I", prefix, OFF_COMP_SIZE, len(comp))

    return bytes(prefix) + comp + bytes(suffix)


def generate_kit(kit_name, cells: Dict[int, Cell], template=None, out_dir="."):
    """Write `<out_dir>/<kit_name>.nbkt`, verify it, and return its path."""
    template = template or default_template()
    with open(template, "rb") as f:
        template_blob = f.read()
    blob = build_kit(template_blob, cells)
    if kit_name.lower().endswith(".nbkt"):
        kit_name = kit_name[:-5]
    path = os.path.join(out_dir, kit_name + ".nbkt")
    if os.path.abspath(path) == os.path.abspath(template):
        raise NbktError("refusing to overwrite the template")
    with open(path, "wb") as f:
        f.write(blob)
    verify_kit(path, template=template, cells=cells)
    return path


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def _close(a, b):
    return struct.pack("<f", a) == struct.pack("<f", b)


def verify_kit(path, template=None, cells: Optional[Dict[int, Cell]] = None):
    """Round-trip parse a generated kit; raise NbktError on any inconsistency.

    Always checked:  container tags, total size, compressed/decompressed size
    fields, FastLZ level-2 decode, preset CRC32, trailer MD5, 128 cell groups.
    With `template`: chunk-length fields moved by exactly the payload size
    delta, prefix/suffix otherwise untouched, and the preset identical to the
    template outside the injectable parameter fields.
    With `cells`:    every requested value reads back exactly.
    Returns the list of CellInfo read from the file."""
    with open(path, "rb") as f:
        blob = f.read()
    prefix, suffix, decomp = _check_container(blob, os.path.basename(path))
    infos = read_cells(decomp)

    if template is not None:
        with open(template, "rb") as f:
            tblob = f.read()
        tprefix, tsuffix, tdecomp = _check_container(tblob, "template")
        diff = (len(blob) - len(tblob))
        for off in LEN_FIELDS:
            want = struct.unpack_from("<Q", tprefix, off)[0] + diff
            got = struct.unpack_from("<Q", prefix, off)[0]
            if got != want:
                raise NbktError("length field at 0x%04x is %d, expected %d" % (off, got, want))

        def mask(buf, ranges):
            buf = bytearray(buf)
            for off, n in ranges:
                buf[off:off + n] = bytes(n)
            return buf

        var_prefix = [(o, 8) for o in LEN_FIELDS] + [(OFF_DECOMP_SIZE, 8)]
        if mask(prefix, var_prefix) != mask(tprefix, var_prefix):
            raise NbktError("prefix differs from template outside the length fields")
        var_suffix = [(INFO_OFF + INFO_MD5.start, 16), (INFO_OFF + INFO_CRC, 4)]
        if mask(suffix, var_suffix) != mask(tsuffix, var_suffix):
            raise NbktError("trailer differs from template outside CRC/MD5")
        if len(decomp) != len(tdecomp):
            raise NbktError("decompressed size differs from template")
        var_preset = []
        for g in find_groups(tdecomp):
            var_preset += [(g.priv + PRIV_KEY_RANGE, 8), (g.priv + PRIV_ENGINE_GAIN, 4),
                           (g.off_volume, 4), (g.off_tune, 4)]
        if mask(decomp, var_preset) != mask(tdecomp, var_preset):
            raise NbktError("preset differs from template outside the injected fields")

    for cell_id, c in sorted((cells or {}).items()):
        info = infos[cell_id]
        if c.note is not None and (info.low_key, info.high_key) != (c.note, c.note):
            raise NbktError("cell %d: key range reads back %d-%d, expected %d"
                            % (cell_id, info.low_key, info.high_key, c.note))
        if c.volume is not None:
            vol = c.volume / 100.0
            if not (_close(info.engine_gain, vol) and _close(info.volume, vol)):
                raise NbktError("cell %d: volume reads back engine=%g fader=%g, expected %g"
                                % (cell_id, info.engine_gain, info.volume, vol))
        if c.tune is not None and not _close(info.tune_ratio, 2.0 ** (c.tune / 12.0)):
            raise NbktError("cell %d: tune ratio reads back %g" % (cell_id, info.tune_ratio))
    return infos


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_cell_arg(text):
    parts = text.split(":")
    if not 2 <= len(parts) <= 4:
        raise argparse.ArgumentTypeError("expected CELL:NOTE[:VOLUME[:TUNE]], got %r" % text)
    parts += [""] * (4 - len(parts))
    try:
        return int(parts[0]), Cell(
            note=int(parts[1]) if parts[1] else None,
            volume=float(parts[2]) if parts[2] else None,
            tune=float(parts[3]) if parts[3] else None)
    except ValueError:
        raise argparse.ArgumentTypeError("bad number in %r" % text) from None


def _cells_from_json(path):
    """Accepts {"0": {"note": 36, "volume": 100, "tune": 0}, ...} or a list of
    {"cell": 0, ...} / {"row": 0, "pad": 0, ...} objects."""
    with open(path) as f:
        data = json.load(f)
    items = data.items() if isinstance(data, dict) else [
        (d["cell"] if "cell" in d else d["row"] * 16 + d["pad"], d) for d in data]
    return {int(k): Cell(v.get("note"), v.get("volume"), v.get("tune")) for k, v in items}


def _dump(infos):
    print("cell row pad  key      engine  level   pan    tune(st)  name")
    for i in infos:
        print("%4d %3d %3d  %3d-%-3d  %6.3f  %6.3f  %5.2f  %+7.2f   %r"
              % (i.cell, i.cell // 16, i.cell % 16, i.low_key, i.high_key,
                 i.engine_gain, i.volume, i.pan, i.tune_semitones, i.name))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate / verify Battery 4 .nbkt kits.")
    ap.add_argument("kit_name", nargs="?", help="output kit name (writes <name>.nbkt)")
    ap.add_argument("--cell", action="append", type=_parse_cell_arg, default=[],
                    metavar="CELL:NOTE[:VOL[:TUNE]]",
                    help="cell 0-127, MIDI note, volume 0-100, tune in semitones; "
                         "leave a field empty to keep the template value")
    ap.add_argument("--json", help="JSON file with cell parameters")
    ap.add_argument("--template", help="template kit (default: Dummy.nbkt)")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--verify", metavar="FILE", help="verify an existing kit and exit")
    ap.add_argument("--dump", metavar="FILE", help="verify a kit and print its cells")
    args = ap.parse_args(argv)
    try:
        if args.verify or args.dump:
            infos = verify_kit(args.verify or args.dump, template=args.template)
            if args.dump:
                _dump(infos)
            print("OK: container, FastLZ payload, CRC32 and MD5 are consistent")
            return 0
        if not args.kit_name:
            ap.error("kit_name is required")
        cells = _cells_from_json(args.json) if args.json else {}
        cells.update(dict(args.cell))
        path = generate_kit(args.kit_name, cells, args.template, args.out_dir)
        print("wrote %s (%d cells patched, verified)" % (path, len(cells)))
        return 0
    except (NbktError, OSError, KeyError, json.JSONDecodeError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
