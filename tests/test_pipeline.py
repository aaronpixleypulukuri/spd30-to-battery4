"""End-to-end run of the converter on a synthetic backup, synthetic WAVs and
synthetic templates.  Checks the kits, the copied samples and the reports."""

import contextlib
import csv
import io
import os
import tempfile
import unittest

from fixtures import read, write, layer_bytes, make_backup, silent_layer, write_wav
import nbkt_fixtures as nf
import build_john_deni_battery4_kits as b
import nbkt_gen


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        os.makedirs(os.path.join(t, "templates"))
        cls.cell_tpl, cls.wrap_tpl = nf.write_templates(os.path.join(t, "templates"))
        cls.backup = os.path.join(t, "BACKUP01.BIN")
        with open(cls.backup, "wb") as f:
            f.write(make_backup({
                1: {"name": "Demo One", "pads": {
                    0: (0, layer_bytes(note=36, volume=80, cents=300), silent_layer()),
                    1: (1, layer_bytes(note=128, volume=100), layer_bytes(note=38, volume=50,
                                                                          cents=-1200)),
                    2: (1, layer_bytes(note=128), layer_bytes(note=128)),
                    3: (0, layer_bytes(note=40, volume=0), layer_bytes(note=41)),
                    8: (0, layer_bytes(note=33, muted=True), silent_layer()),
                    9: (0, layer_bytes(note=45), silent_layer()),       # no WAV on disk
                }},
                2: {"name": "Demo Two", "pads": {
                    0: (0, layer_bytes(note=42), silent_layer()),
                }},
            }))
        cls.samples = os.path.join(t, "samples")
        k1 = os.path.join(cls.samples, "001 Demo One")
        write_wav(os.path.join(k1, "Layer A", "1-KIK 001.wav"), frames=1000)
        write_wav(os.path.join(k1, "Layer A", "2-SNR 004.wav"), frames=500, channels=2)
        write_wav(os.path.join(k1, "Layer B", "2-TOM 001.wav"), frames=300, extra_chunk=True)
        write_wav(os.path.join(k1, "Layer A", "3-HH 001.wav"))
        write_wav(os.path.join(k1, "Layer B", "3-HH 002.wav"))
        write_wav(os.path.join(k1, "Layer A", "7-unused.wav"))
        write_wav(os.path.join(cls.samples, "002 Demo Two", "Layer A", "1-HH 003.wav"),
                  width=3)
        cls.out = os.path.join(t, "out")
        with contextlib.redirect_stdout(io.StringIO()) as cls.stdout:
            cls.code = b.main(["--backup", cls.backup, "--samples", cls.samples,
                               "--out-dir", cls.out, "--cell-template", cls.cell_tpl,
                               "--wrapper-template", cls.wrap_tpl])
        with open(os.path.join(cls.out, "kit_cell_report.csv")) as f:
            cls.rows = list(csv.DictReader(f))
        with open(os.path.join(cls.out, "build_report.md")) as f:
            cls.report = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def row(self, kit, pad, layer):
        return next(r for r in self.rows if r["kit_num"] == str(kit) and r["roland_pad"] == pad
                    and r["layer"] == layer)

    def test_exit_code_and_kits(self):
        self.assertEqual(self.code, 0, self.stdout.getvalue())
        for name in ("001 Demo One", "002 Demo Two"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name, name + ".nbkt")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "003 Kit 3")),
                         "kits without an active layer are not written")

    def test_only_active_layers_become_cells(self):
        kit1 = [(r["roland_pad"], r["layer"]) for r in self.rows if r["kit_num"] == "1"]
        self.assertEqual(sorted(kit1), sorted([("PAD 1", "A"), ("PAD 2", "A"), ("PAD 2", "B"),
                                               ("PAD 3", "A"), ("PAD 3", "B"), ("EXT 2", "A")]))

    def test_keys_volume_tune(self):
        r = self.row(1, "PAD 1", "A")
        self.assertEqual((r["battery_cell"], r["key_written"]), ("0", "36"))
        self.assertAlmostEqual(float(r["volume_float"]), 0.8, places=6)
        self.assertAlmostEqual(float(r["tune_ratio"]), 2 ** 0.25, places=6)
        r = self.row(1, "PAD 2", "B")
        self.assertEqual((r["battery_cell"], r["key_written"]), ("17", "38"))
        self.assertEqual(float(r["tune_ratio"]), 0.5)
        # no-note layers get 120, 121, ... in cell order (cells 1, 2, 18)
        self.assertEqual(self.row(1, "PAD 2", "A")["key_written"], "120")
        self.assertEqual(self.row(1, "PAD 3", "A")["key_written"], "121")
        self.assertEqual(self.row(1, "PAD 3", "B")["key_written"], "122")

    def test_written_kit_reads_back(self):
        path = os.path.join(self.out, "001 Demo One", "001 Demo One.nbkt")
        kit = b.Kit(read(path))
        chk = kit.checks()
        self.assertTrue(chk["crc_ok"] and chk["md5_ok"])
        self.assertEqual(kit.name, "001 Demo One")
        self.assertEqual(len(kit.zones), 5)
        self.assertEqual(len(kit.files), 5)
        self.assertEqual(chk["info_zones"], 5)
        cells = nbkt_gen.read_cells(kit.raw)
        self.assertEqual(cells[0].low_key, 36)
        self.assertEqual(cells[0].engine_gain, b.f32(0.8))
        self.assertEqual(cells[0].volume, b.f32(0.8))
        self.assertEqual(cells[0].name, "1-KIK 001")
        self.assertEqual(cells[9].name, " ", "EXT 2 has no WAV, so its cell has no sample")
        zone = next(z for z in kit.zones if z.group == 1).fields()
        self.assertEqual((zone["channels"], zone["frames"]), (2, 500))

    def test_samples_copied_byte_for_byte(self):
        src = os.path.join(self.samples, "001 Demo One", "Layer B", "2-TOM 001.wav")
        dst = os.path.join(self.out, "001 Demo One", "001 Demo One Samples", "B 2-TOM 001.wav")
        self.assertEqual(read(src), read(dst))
        kit = b.Kit(read(os.path.join(self.out, "001 Demo One", "001 Demo One.nbkt")))
        for folder, name, _ in kit.files:
            self.assertEqual(folder, "001 Demo One Samples")
            self.assertTrue(os.path.isfile(os.path.join(self.out, "001 Demo One", folder, name)))

    def test_problems_reported(self):
        self.assertIn("kit 001 EXT 2 A: active layer has no WAV", self.report)
        self.assertIn("Layer A/7-unused.wav matches no active layer", self.report)
        self.assertNotIn("kit 003: no sample folder", self.report,
                         "kits with nothing to convert are not reported")
        self.assertIn("**not tested**", self.report)

    def test_all_cells_verified(self):
        self.assertTrue(all(r["verification"] == "PASS" for r in self.rows))
        self.assertIn("| Kits written | 2 (2 verified, 0 failed) |", self.report)

    def test_verify_detects_tampering(self):
        path = os.path.join(self.out, "002 Demo Two", "002 Demo Two.nbkt")
        good = read(path)
        builder = b.Builder(self.cell_tpl, self.wrap_tpl)
        kit = b.Kit(good)
        cells = [dict(cell=0, key=42, volume=1.0, tune_ratio=1.0, label="1-HH 003",
                      sample_name="A 1-HH 003.wav",
                      wav=b.wav_info(os.path.join(os.path.dirname(path),
                                                  "002 Demo Two Samples", "A 1-HH 003.wav")))]
        self.assertEqual(b.verify_kit(path, cells, builder), [])
        wrong = dict(cells[0], key=43)
        self.assertTrue(b.verify_kit(path, [wrong], builder))
        bad = bytearray(good)
        bad[-5] ^= 0xFF
        with tempfile.TemporaryDirectory() as d:
            p2 = os.path.join(d, "002 Demo Two.nbkt")
            write(p2, bytes(bad))
            self.assertTrue(b.verify_kit(p2, cells, builder))
        self.assertEqual(kit.zones[0].fields()["width"], 3)

    def test_zone_params_option(self):
        out = os.path.join(self.tmp.name, "out_zone")
        with contextlib.redirect_stdout(io.StringIO()):
            code = b.main(["--backup", self.backup, "--samples", self.samples, "--out-dir", out,
                           "--cell-template", self.cell_tpl, "--wrapper-template",
                           self.wrap_tpl, "--params", "zone"])
        self.assertEqual(code, 0)
        kit = b.Kit(read(os.path.join(out, "001 Demo One", "001 Demo One.nbkt")))
        z = next(z for z in kit.zones if z.group == 0).fields()
        self.assertAlmostEqual(z["volume"], 0.8, places=6)
        self.assertAlmostEqual(z["tune"], 2 ** 0.25, places=6)
        self.assertAlmostEqual(nbkt_gen.read_cells(kit.raw)[0].volume, 0.9880964, places=6)


class SelfTestTest(unittest.TestCase):
    def test_reference_kit_checks(self):
        with tempfile.TemporaryDirectory() as d:
            cell, wrap = nf.write_templates(d)
            builder = b.Builder(cell, wrap)
            refs, zones_ok, zones_total, cells_equal = b.self_test(builder, [wrap])
            self.assertEqual(refs, [("wrapper.nbkt", True, 1)])
            self.assertEqual((zones_ok, zones_total), (1, 1))
            self.assertTrue(cells_equal)

    def test_bad_wrapper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            cell, wrap = nf.write_templates(d)
            data = bytearray(read(wrap))
            i = data.index(b.MAGIC)
            data[i + b.INFO_CRC] ^= 1
            write(wrap, bytes(data))
            with self.assertRaises(b.BuildError):
                b.Builder(cell, wrap)


if __name__ == "__main__":
    unittest.main()
