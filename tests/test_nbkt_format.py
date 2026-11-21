"""Battery kit container: serialisation, size fields, CRC32, MD5, zones,
the sample file table and per-cell parameter fields."""

import hashlib
import math
import struct
import unittest
import zlib

import nbkt_fixtures as nf
import build_john_deni_battery4_kits as b
import nbkt_gen


class StructureTest(unittest.TestCase):
    def test_struct_parts_roundtrip(self):
        data = b.struct_bytes(0x95, b"priv", b"public", b"kids!")
        ver, parts, end = b.struct_parts(data)
        self.assertEqual((ver, parts, end), (0x95, [b"priv", b"public", b"kids!"], len(data)))

    def test_chunk_list_roundtrip_and_overrun(self):
        chunks = [[0x28, b"abc"], [0x47, b""], [0x4B, bytes(10)]]
        self.assertEqual(b.read_chunks(b.chunk_bytes(chunks)), chunks)
        with self.assertRaises(b.BuildError):
            b.read_chunks(b.chunk_bytes(chunks)[:-1])

    def test_utf16_strings(self):
        s, end = b.rstr(b.wstr("Pad 1 – Kick") + b"tail", 0)
        self.assertEqual(s, "Pad 1 – Kick")
        self.assertEqual(end, 4 + 2 * len("Pad 1 – Kick"))

    def test_container_roundtrip_and_size_fields(self):
        data = nf.finished_kit(True)
        self.assertEqual(struct.unpack_from("<Q", data, 0)[0], len(data))
        self.assertEqual(b.parse_item(data).to_bytes(), data)
        with self.assertRaises(b.BuildError):
            b.parse_item(data[:-1])

    def test_group_public_block(self):
        g = b.Group(0x95, bytes(10), b.wstr(" ") + struct.pack("<fff", 0.9, 0.0, 1.0) + b"rest", b"")
        g.set_public(name="Snare", volume=0.5, tune=2.0)
        self.assertEqual(g.name, "Snare")
        _, p = b.rstr(g.pub, 0)
        self.assertEqual(struct.unpack_from("<fff", g.pub, p), (0.5, 0.0, 2.0))
        self.assertTrue(g.pub.endswith(b"rest"))


class ChecksumTest(unittest.TestCase):
    def setUp(self):
        self.data = nf.finished_kit(True)
        self.kit = b.Kit(self.data)

    def test_crc_and_md5_valid(self):
        chk = self.kit.checks()
        self.assertTrue(chk["crc_ok"])
        self.assertTrue(chk["md5_ok"])
        self.assertEqual((chk["info_zones"], chk["info_groups"]), (1, 128))
        self.assertEqual(chk["info_sample_bytes"], 441 * 2)

    def test_md5_definition(self):
        info = bytearray(self.kit.info_block())
        md5 = bytes(info[b.INFO_MD5])
        info[b.INFO_MD5] = bytes(16)
        self.assertEqual(hashlib.md5(b"\x09\xe2" + bytes(info)).digest(), md5)
        self.assertEqual(struct.unpack_from("<I", info, b.INFO_CRC)[0],
                         zlib.crc32(self.kit.chunk_region) & 0xFFFFFFFF)

    def test_changed_preset_breaks_crc(self):
        kit = b.Kit(self.data)
        kit.chunk_region = kit.chunk_region[:-1] + bytes([kit.chunk_region[-1] ^ 1])
        self.assertFalse(kit.checks()["crc_ok"])

    def test_payload_is_fastlz_level2(self):
        self.assertEqual(self.kit.compressed[0] >> 5, 1)


class ZoneAndFileTableTest(unittest.TestCase):
    def setUp(self):
        self.kit = b.Kit(nf.finished_kit(True))

    def test_read_zone_and_file(self):
        self.assertEqual(len(self.kit.zones), 1)
        f = self.kit.zones[0].fields()
        self.assertEqual((f["lokey"], f["hikey"], f["frames"], f["rate"]), (36, 36, 441, 44100))
        self.assertEqual(self.kit.files[0][:2], ("Kit Samples", "kick.wav"))

    def test_make_zone_fields(self):
        builder = b.Builder.__new__(b.Builder)
        builder.zone_template = self.kit.zones[0]
        wav = dict(rate=48000, channels=2, width=3, frames=8000, data_offset=68)
        z = builder.make_zone(5, 7, 3, 61, wav, volume=0.5, tune=2.0)
        f = z.fields()
        self.assertEqual(z.group, 5)
        self.assertEqual((f["lokey"], f["hikey"], f["root"]), (61, 61, b.ROOT_KEY))
        self.assertEqual((f["volume"], f["tune"]), (0.5, 2.0))
        self.assertEqual((f["file"], f["zone_id"]), (3, 7))
        self.assertEqual((f["rate"], f["channels"], f["width"], f["frames"], f["data_offset"]),
                         (48000, 2, 3, 8000, 68))
        self.assertEqual(f["total_bytes"], 8000 * 2 * 3)
        self.assertEqual(struct.unpack_from("<II", z.kids, b.ZONE_KIDS_LOOP), (4000, 1000))

    def test_files_and_zones_survive_rewrite(self):
        kit = self.kit
        kit.files.append(("Kit Samples", "snare.wav", 1700000001))
        kit.zones.append(nf.b.Zone(1, kit.zones[0].ver, kit.zones[0].priv,
                                   kit.zones[0].pub, kit.zones[0].kids))
        again = b.Kit(kit.to_bytes("Kit.nbkt", 1, "seed"))
        self.assertEqual([f[1] for f in again.files], ["kick.wav", "snare.wav"])
        self.assertEqual([z.group for z in again.zones], [0, 1])
        self.assertTrue(again.checks()["crc_ok"] and again.checks()["md5_ok"])
        self.assertEqual(again.checks()["info_zones"], 2)


class CellParameterTest(unittest.TestCase):
    """nbkt_gen reads and writes the per-cell fields in the decompressed preset."""

    def setUp(self):
        self.raw = bytearray(b.Kit(nf.finished_kit(False)).raw)

    def test_find_128_groups(self):
        self.assertEqual(len(nbkt_gen.find_groups(self.raw)), 128)

    def test_patch_and_read_back(self):
        cells = {0: nbkt_gen.Cell(note=60, volume=50, tune=12),
                 17: nbkt_gen.Cell(note=121, volume=100, tune=-12),
                 127: nbkt_gen.Cell(volume=25)}
        nbkt_gen.patch_cells(self.raw, cells)
        info = nbkt_gen.read_cells(self.raw)
        self.assertEqual((info[0].low_key, info[0].high_key), (60, 60))
        self.assertEqual((info[0].engine_gain, info[0].volume), (0.5, 0.5))
        self.assertAlmostEqual(info[0].tune_ratio, 2.0)
        self.assertAlmostEqual(info[17].tune_semitones, -12.0, places=5)
        self.assertEqual(info[127].volume, 0.25)
        self.assertEqual(info[127].low_key, 36 + 127 % 92, "note left untouched")
        self.assertAlmostEqual(info[1].volume, 0.9880964, places=6, msg="other cells untouched")

    def test_tune_formula(self):
        for st in (-24, -7, 0, 0.4, 6.2, 24):
            raw = bytearray(self.raw)
            nbkt_gen.patch_cells(raw, {3: nbkt_gen.Cell(tune=st)})
            self.assertAlmostEqual(nbkt_gen.read_cells(raw)[3].tune_ratio, 2 ** (st / 12), places=6)

    def test_out_of_range_values_rejected(self):
        for cell in ({128: nbkt_gen.Cell(note=1)}, {0: nbkt_gen.Cell(note=128)},
                     {0: nbkt_gen.Cell(volume=101)}, {0: nbkt_gen.Cell(tune=40)},
                     {0: nbkt_gen.Cell(volume=math.nan)}):
            with self.assertRaises(nbkt_gen.NbktError):
                nbkt_gen.patch_cells(bytearray(self.raw), cell)


if __name__ == "__main__":
    unittest.main()
