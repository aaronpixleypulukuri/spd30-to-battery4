# Architecture

How `build_john_deni_battery4_kits.py` reads an SPD-30 backup and rebuilds Battery 4 kits. Offsets are in bytes; `u16`, `u32` and `u64` are unsigned integers, and `f32` is an IEEE-754 float. All of this was worked out from real files and is checked by the code and tests. None of it comes from official documentation.

```
build_john_deni_battery4_kits.py
  parse_backup()            SPD-30 backup  -> kits -> layers (+ active flag, cell id)
  assign_note_off_keys()    keys for sounding layers with no MIDI note
  find_wav() / wav_info()   WAV lookup and header parsing
  parse_item() / Item       NISound container (outer file and decompressed preset)
  Kit                       decoded kit: groups (cells), zones, sample table, info block
  Builder                   templates -> new kit; make_zone()
  verify_kit() / self_test  read-back checks
nbkt_gen.py                 cell field offsets, read_cells() (independent reader)
fastlz2.py                  FastLZ level-2 codec
```

## 1. SPD-30 backup

The backup starts with `00 FE "SVD2"` and a table of contents of 16-byte entries (`<4-char id>"SP30" <u32 offset> <u32 size>`, big-endian). The `KITa` chunk holds 100 kit records of 654 bytes each, starting at file offset `0x110`. The converter uses that fixed offset and checks the `SVD2` magic and the file size.

```
kit record (654 bytes)
  +0     name, 16 ASCII characters
  +16    kit volume 0-100            (not used)
  +94    12 pad records x 36 bytes: PAD 1-8, EXT 1-4

pad record (36 bytes)
  +0     layer mode: 0 = single (only Layer A plays), 1-4 = both layers in use
  +4     Layer A (16 bytes)
  +20    Layer B (16 bytes)

layer record (16 bytes)
  +0     instrument category (12 = OFF)
  +1     tone number - 1
  +2     volume 0-100
  +4     u16 big-endian tune = cents + 2400   (0..4800 = -24..+24 semitones)
  +10    1 = muted
  +14    MIDI note 0-127, 128 = OFF
  +15    MIDI channel, 0-based
```

`parse_backup()` returns, for every kit, 24 layer dicts with these fields plus:

- `cell = layer_index * 16 + pad_index`. Layer A is row 1, Layer B is row 2; PAD 1-8 are columns 1-8 and EXT 1-4 are columns 9-12.
- `active`: false when Layer B is on a single-mode pad, or when the instrument is OFF, the volume is 0 or the layer is muted. `inactive_reason` says which.

## 2. Samples

For a kit numbered `n`, the sample folder is the first folder in `--samples` whose name starts with `"%03d " % n`. That folder name, such as `001 My Kit`, becomes the Battery kit's name and file name.

`find_wav()` lists `Layer A/` or `Layer B/` and returns every `.wav` (any case) whose name starts with the pad prefix plus a dash: `1-` … `8-`, `EXT1-` … `EXT4-`. If several match, the first by name is used and the duplicate is reported. If none match, the cell is written without a sample and that is reported. WAVs that belong to no active layer are reported as well.

Each used WAV is copied to `<Kit>/<Kit> Samples/<A|B> <original name>`. The `A`/`B` prefix avoids clashes, because both layers of a pad can use the same file name. The copy keeps the modification time. `wav_info()` reads rate, channels, bytes per sample and frame count with the `wave` module, and finds the byte offset of the `data` chunk by walking the RIFF chunks.

## 3. MIDI keys

A cell's key range is `low = high = note`. For sounding layers whose note is OFF, `assign_note_off_keys()` hands out keys in this order:

```
120, 121, ..., 127, 119, 118, ..., 0     skipping keys already used by a real note in the kit
```

Layers are taken in cell order (row 1, then row 2), so a kit's keys are always assigned the same way. The SPD-20X exporter calls the same function, so both outputs agree.

## 4. Battery 4 kit file

```
.nbkt file = NISound container ("hsin" items, each holding DSIN/4KIN data chunks)
  item:  u64 size, u32 version, "hsin", u32 flags, u32 0, 16-byte UUID,
         data chunk, u32 child version, u32 child count, children
  chunk: u64 size, 4-char domain, u32 type, u32 version, [nested chunk], payload

  chunk type 115  preset:      u32, u8, u32 raw size, u32 packed size, FastLZ level-2 data
  chunk type 4    info block:  starts with 12 90 A8 7F
                    "4taB" then u32 timestamp, u16 zones, u16 groups, u32 sample bytes
                    +0xA2  16-byte MD5     +0xB6  u32 CRC32
  chunk type 108  display name (UTF-16)
```

The decompressed preset is itself an NISound item. Its chunk 109 holds a 20-byte header (`u32` at +12 = length of the chunk list), the **chunk list**, and a 16-byte trailer. The chunk list is a sequence of `u16 id, u32 length, data`:

```
0x28  program   structured object: 01, u16 version, then private, public and child
                blocks, each prefixed by its u32 length.  The children are again a
                chunk list, including:
        0x33  groups: u32 count (128), then 128 structured objects = Battery cells
        0x34  zones:  u32 count, then per zone: u32 group index + structured object
0x47  (kept as is)
0x4B  sample table: 10-byte header, u32 count, one path per file
      (u32 segment count; segments: u8 type 2 = folder / 4 = file name, UTF-16),
      u64 timestamp per file, u32 0 per file, then the kit's own file name
```

**Cell (group) fields** (group version 0x95, 2390-byte private block):

```
private +1639   u32 low key, u32 high key       (followed by 01 07 00 00)
private +1836   f32 engine gain (1.0 = 0 dB)
public          u32 name length, UTF-16 name, f32 volume, f32 pan, f32 tune ratio, ...
```

The public block starts with the cell's name. Volume is therefore at `pub + 4 + 2 * name_length`, not at a fixed offset. In the empty template, some cells have a one-character name and others an empty one.

**Zone** (version 0x9A; private 87, public 82, children 66 bytes):

```
public +0    u32 0, u32 0, u32 FFFFFFFF, u16 low/high velocity, u16 low/high key,
             4 x u16 fades, u16 root key (36), f32 volume, f32 pan, f32 tune
public +48   u32 file index, u32 bytes per sample, u32 rate, u8 channels, u32 frames,
             u32 data offset, u32 60, f32 1.0, u8 big-endian flag, u32 total bytes
private +83  u32 zone id
children +14 u32 4*(frames//8), u32 frames//8
```

## 5. Rebuilding a kit (`Builder`)

Two templates saved by Battery 4 are needed:

| Template | Supplies |
|---|---|
| `Dummy.nbkt` (empty kit) | the 128 clean cell blocks |
| `wrapper.nbkt` (kit with ≥1 sample) | everything around them: program, zone layout, sample table, info block |

The empty kit has no zone or sample-table entry to copy, so it can't be used alone. In the files used during development, the two templates also had different program versions.

When the builder loads, it compares a filled cell of the wrapper with the same empty cell of the cell template. The bytes that differ, apart from the key range, are the flags Battery sets in a cell that holds a sample. They are reapplied to every filled cell. If filled cells disagree about those flags, the build stops.

For each kit, `Builder.build()`:

1. starts from a fresh copy of the wrapper, renames the program and replaces all 128 cells with the clean ones;
2. for each converted layer, writes the key range; in `--params group` mode (the default) it also writes the engine gain, quick-access volume and quick-access tune; for a layer with a WAV it sets the filled-cell flags and the cell name, adds a sample-table entry and builds a zone from the wrapper's zone (in `--params zone` mode volume and tune go to the zone instead);
3. serialises the groups, zones and sample table and rebuilds the chunk list;
4. compresses the preset with FastLZ level 2;
5. updates the info block: timestamp (newest sample's modification time), zone count, group count, sample bytes (each distinct file counted once, as Battery does), `CRC32(chunk list)`, and finally `MD5(09 E2 + info block with the MD5 field zeroed)`;
6. gives every container item a UUID derived from the kit name, so rebuilding the same input gives byte-identical files;
7. serialises the outer container, which recomputes every size field.

`fastlz2.py` is a port of the reference FastLZ C code. Its compressed output differs from Native Instruments' own encoder, but it is a valid level-2 stream, and a parameter-only kit compressed this way loaded in Battery 4.

## 6. Verification

`verify_kit()` reopens each written file from disk and returns a list of problems; an empty list is a pass:

- total size field, outer and inner container re-serialise byte for byte, FastLZ level-2 marker;
- CRC32 and MD5 recomputed and compared; zone, group and sample-byte counts in the info block;
- kit name; 128 groups; one zone and one sample-table entry per linked sample;
- each cell's key range, both volume fields and tune, read with `nbkt_gen.read_cells()`, a separate reader that finds cells by their header bytes rather than by walking the structure;
- each zone's keys, volume, tune and format against the copied WAV on disk, and the sample path against the folder;
- every cell that was not converted is identical to the clean template cell.

`self_test()` runs once per build on the Battery-saved kits in the wrapper's folder. Each must re-serialise byte for byte with valid CRC and MD5, and each unedited 16/24-bit PCM zone in them must come out identical when rebuilt by `make_zone()` from its own fields. This checks the format model against real Battery output before any kit is written.

The unit tests (`tests/`) build synthetic backups, WAVs and kit files to exercise all of this without private data. `tests/test_with_templates.py` repeats the important checks with real templates when they are present.
