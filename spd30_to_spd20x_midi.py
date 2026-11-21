#!/usr/bin/env python3
"""Copy MIDI notes, MIDI channels and program changes from an SPD-30 backup
(BACKUPxx.BIN) into a set of SPD-20X patch files.

    python3 spd30_to_spd20x_midi.py --src <SD card>/ROLAND/SPD-20X/BACKUP

The source SPD-20X folder is only read.  The edited copies are written to
    output/spd20x/ROLAND/SPD-20X/BACKUP/SPD20X01.BIN ... SPD20X99.BIN
(copy the ROLAND folder to the root of the SD card to restore them).

Mapping, SPD-30 kit n -> SPD20Xnn.BIN (kits 1-99; SPD-30 kit 100 has no slot):
    SPD-30 PAD 1-8   -> SPD-20X pad 1-8      (MIDI records 0-7)
    SPD-30 EXT 1-4   -> SPD-20X trigger records 8, 10, 12, 14
    Layer A          -> SPD-20X bank A record
    Layer B          -> SPD-20X bank B record; note OFF when the SPD-30 pad is
                        in single mode (layer B is not used there)
    No-note layers   -> an active layer (sounding: volume > 0, instrument not
                        OFF, not muted, layer B in use) whose SPD-30 note is OFF
                        gets the same key as its cell in the Battery 4 kits:
                        120..127 in cell order, then 119 downwards
                        (build_john_deni_battery4_kits.assign_note_off_keys)
    MIDI channel     -> copied as stored (SPD-30 layer byte 15, 0-based);
                        bank B channel is set to OFF when bank A and bank B of
                        the pad send the same note, so the note is sent once
    Program change   -> kit n sends PC n.  The SPD-30 backup has no program
                        change table: its kits are selected by PC = kit number.
                        Written to pad 1 bank A (where the SPD-20X patches
                        already carry it), and to any other record that already
                        sends a program change, so no stale value is left.

SPD-20X patch file (498 bytes, no checksum):
    0x10E + 6*r   bank A MIDI record r (0-15): channel (0-based), gate,
                  program change (0-based, 128 = OFF), 0, note (128 = OFF), 0
    0x16E + 6*r   bank B MIDI record r, same layout
    0x1E3         patch name, 12 ASCII characters (not changed)
Everything except the note, channel and program change bytes is left as is.
"""

import argparse
import csv
import os
import sys

import build_john_deni_battery4_kits as battery

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "output", "spd20x")

# SPD-30
KIT_BASE, KIT_SIZE, KIT_COUNT = 0x110, 654, 100
PAD_BASE, PAD_SIZE = 94, 36
L_NOTE, L_CHANNEL = 14, 15
SPD30_PADS = ["PAD %d" % i for i in range(1, 9)] + ["EXT %d" % i for i in range(1, 5)]

# SPD-20X
FILE_SIZE = 498
MIDI_A, MIDI_B, REC = 0x10E, 0x16E, 6
R_CH, R_PC, R_NOTE = 0, 2, 4
NAME = slice(0x1E3, 0x1EF)
OFF = 128
CH_OFF = 16             # channel OFF (the only value above 15 found in real patches)
PATCHES = 99
SPD20X_RECORD = list(range(8)) + [8, 10, 12, 14]      # per SPD-30 pad index


class ConvertError(Exception):
    pass


def read_spd30(path):
    with open(path, "rb") as f:
        d = f.read()
    if d[2:6] != b"SVD2" or len(d) < KIT_BASE + KIT_COUNT * KIT_SIZE:
        raise ConvertError("%s is not an SPD-30 backup" % path)
    kits = []
    for k in range(KIT_COUNT):
        rec = d[KIT_BASE + k * KIT_SIZE:KIT_BASE + (k + 1) * KIT_SIZE]
        pads = []
        for p in range(12):
            pad = rec[PAD_BASE + p * PAD_SIZE:PAD_BASE + (p + 1) * PAD_SIZE]
            layers = []
            for li in range(2):
                lay = pad[4 + 16 * li:20 + 16 * li]
                layers.append(dict(note=lay[L_NOTE], channel=lay[L_CHANNEL] & 0x0F,
                                   volume=lay[2], category=lay[0], muted=lay[10] == 1))
            pads.append(dict(mode=pad[0], layers=layers))
        kits.append(dict(name=rec[:16].decode("ascii", "replace").rstrip(), pads=pads))
    return kits


def note_off_keys(spd30_path):
    """-> {(kit number, cell): key}, exactly as the Battery 4 kits assign them."""
    keys = {}
    for kit in battery.parse_backup(spd30_path):
        active = [l for l in kit["layers"] if l["active"]]
        for cell, key in battery.assign_note_off_keys(active).items():
            keys[(kit["number"], cell)] = key
    return keys


def targets(n, kit, keys):
    """-> {(pad index, layer index): (note, channel, remark)} for patch n."""
    out = {}
    for p, pad in enumerate(kit["pads"]):
        notes = []
        for li in range(2):
            lay = pad["layers"][li]
            used = li == 0 or pad["mode"] != 0
            note, remark = (lay["note"] if used else OFF), ""
            if used and note >= OFF and (n, li * 16 + p) in keys:
                note = keys[(n, li * 16 + p)]
                remark = "no note on SPD-30 - Battery key %d" % note
            notes.append(note)
            out[(p, li)] = [note, lay["channel"], remark]
        if notes[0] < OFF and notes[0] == notes[1]:
            out[(p, 1)][1] = CH_OFF
            out[(p, 1)][2] = "same note as bank A - channel OFF"
    return out


def plan_patch(n, kit, data, keys):
    """-> list of (offset, new value, description) for SPD20Xnn."""
    writes = []
    for (p, li), (note, ch, _) in targets(n, kit, keys).items():
        o = (MIDI_A, MIDI_B)[li] + REC * SPD20X_RECORD[p]
        writes.append((o + R_NOTE, note, "%s %s note" % (SPD30_PADS[p], "AB"[li])))
        writes.append((o + R_CH, ch, "%s %s channel" % (SPD30_PADS[p], "AB"[li])))
    for r in range(16):
        for base in (MIDI_A, MIDI_B):
            o = base + REC * r + R_PC
            if (base, r) == (MIDI_A, 0) or data[o] != OFF:
                writes.append((o, n - 1, "program change"))
    return writes


def convert(spd30_path, src_dir, out_dir):
    kits = read_spd30(spd30_path)
    keys = note_off_keys(spd30_path)
    dest = os.path.join(out_dir, "ROLAND", "SPD-20X", "BACKUP")
    if os.path.realpath(dest) == os.path.realpath(src_dir):
        raise ConvertError("output folder is the source folder")
    os.makedirs(dest, exist_ok=True)
    rows, results = [], []
    for n in range(1, PATCHES + 1):
        fname = "SPD20X%02d.BIN" % n
        with open(os.path.join(src_dir, fname), "rb") as f:
            orig = f.read()
        if len(orig) != FILE_SIZE:
            raise ConvertError("%s is %d bytes, expected %d" % (fname, len(orig), FILE_SIZE))
        if orig[0x1F0] != n - 1:
            raise ConvertError("%s holds patch %d" % (fname, orig[0x1F0] + 1))
        kit = kits[n - 1]
        writes = plan_patch(n, kit, orig, keys)
        data = bytearray(orig)
        for o, v, _ in writes:
            data[o] = v
        out = os.path.join(dest, fname)
        with open(out, "wb") as f:
            f.write(data)
        results.append(verify(out, orig, n, kit, keys))
        tg = targets(n, kit, keys)
        name = bytes(orig[NAME]).decode("ascii", "replace").strip()
        for p, pad in enumerate(kit["pads"]):
            r = SPD20X_RECORD[p]
            for li, base in enumerate((MIDI_A, MIDI_B)):
                o = base + REC * r
                lay = pad["layers"][li]
                rows.append({
                    "patch": n, "spd20x_name": name, "spd30_kit_name": kit["name"],
                    "spd30_pad": SPD30_PADS[p], "layer_bank": "AB"[li], "spd20x_record": r,
                    "spd30_layer_used": "yes" if (li == 0 or pad["mode"]) else "no (single mode)",
                    "spd30_note": "OFF" if lay["note"] >= OFF else lay["note"],
                    "spd30_channel": lay["channel"] + 1,
                    "old_note": "OFF" if orig[o + R_NOTE] >= OFF else orig[o + R_NOTE],
                    "new_note": "OFF" if data[o + R_NOTE] >= OFF else data[o + R_NOTE],
                    "old_channel": orig[o + R_CH] + 1, "new_channel": "OFF" if data[o + R_CH] == CH_OFF else data[o + R_CH] + 1,
                    "old_pc": "OFF" if orig[o + R_PC] >= OFF else orig[o + R_PC] + 1,
                    "new_pc": "OFF" if data[o + R_PC] >= OFF else data[o + R_PC] + 1,
                    "remark": tg[(p, li)][2],
                })
    with open(os.path.join(out_dir, "spd20x_midi_changes.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return kits, rows, results


def verify(path, orig, n, kit, keys):
    """Re-read a written file; return (changed byte count, list of errors)."""
    with open(path, "rb") as f:
        data = f.read()
    errs = []
    if len(data) != FILE_SIZE:
        errs.append("size %d" % len(data))
    allowed = set()
    for p, pad in enumerate(kit["pads"]):
        r = SPD20X_RECORD[p]
        notes = []
        for li, base in enumerate((MIDI_A, MIDI_B)):
            o = base + REC * r
            lay = pad["layers"][li]
            used = li == 0 or pad["mode"] != 0
            want = lay["note"] if used else OFF
            if used and want >= OFF and (n, li * 16 + p) in keys:
                want = keys[(n, li * 16 + p)]
            if data[o + R_NOTE] != want:
                errs.append("%s %s note %d != %d" % (SPD30_PADS[p], "AB"[li], data[o + R_NOTE], want))
            notes.append(data[o + R_NOTE])
            allowed |= {o + R_NOTE, o + R_CH}
        dup = notes[0] < OFF and notes[0] == notes[1]
        if data[MIDI_A + REC * r + R_CH] != pad["layers"][0]["channel"]:
            errs.append("%s A channel" % SPD30_PADS[p])
        want_b = CH_OFF if dup else pad["layers"][1]["channel"]
        if data[MIDI_B + REC * r + R_CH] != want_b:
            errs.append("%s B channel %d != %d" % (SPD30_PADS[p], data[MIDI_B + REC * r + R_CH], want_b))
    if data[MIDI_A + R_PC] != n - 1:
        errs.append("pad 1 program change %d" % data[MIDI_A + R_PC])
    for r in range(16):
        for base in (MIDI_A, MIDI_B):
            o = base + REC * r + R_PC
            allowed.add(o)
            if data[o] not in (OFF, n - 1):
                errs.append("record %d stale program change %d" % (r, data[o]))
            if (orig[o] == OFF) != (data[o] == OFF) and (base, r) != (MIDI_A, 0):
                errs.append("record %d program change switched on/off" % r)
    changed = [i for i in range(FILE_SIZE) if data[i] != orig[i]]
    stray = [i for i in changed if i not in allowed]
    if stray:
        errs.append("unexpected bytes changed at %s" % stray[:8])
    return len(changed), errs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spd30", default=os.path.join(HERE, "input", "BACKUP01.BIN"),
                    help="SPD-30 backup file (default: input/BACKUP01.BIN)")
    ap.add_argument("--src", required=True,
                    help="folder holding SPD20X01.BIN ... SPD20X99.BIN (only read)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    try:
        kits, rows, results = convert(args.spd30, args.src, args.out_dir)
    except (ConvertError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    bad = [(i + 1, e) for i, (_, e) in enumerate(results) if e]
    changed = [c for c, _ in results]
    print("patches written: %d, verified: %d, failed: %d"
          % (len(results), len(results) - len(bad), len(bad)))
    print("bytes changed per patch: %d-%d" % (min(changed), max(changed)))
    for n, e in bad[:10]:
        print("  patch %02d: %s" % (n, "; ".join(e[:4])))
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
