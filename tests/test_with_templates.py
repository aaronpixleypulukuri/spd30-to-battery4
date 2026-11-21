"""Checks against real kits saved by Battery 4.  SKIPPED unless you provide them.

Put an empty kit saved by Battery 4 at templates/Dummy.nbkt and a kit with at
least one sample at templates/wrapper.nbkt (or point SPD30_TEMPLATES at a
folder holding both).  They are not part of the repository: they are
Native Instruments files.

The backup and WAVs used here are still synthetic, so no SPD-30 data is needed.
"""

import contextlib
import io
import os
import tempfile
import unittest

from fixtures import read, write, ROOT, layer_bytes, make_backup, silent_layer, write_wav
import build_john_deni_battery4_kits as b
import nbkt_gen

TEMPLATES = os.environ.get("SPD30_TEMPLATES", os.path.join(ROOT, "templates"))
CELL = os.path.join(TEMPLATES, "Dummy.nbkt")
WRAP = os.path.join(TEMPLATES, "wrapper.nbkt")
HAVE = os.path.isfile(CELL) and os.path.isfile(WRAP)


@unittest.skipUnless(HAVE, "Battery-saved templates not found in %s" % TEMPLATES)
class RealTemplateTest(unittest.TestCase):
    def test_templates_validate(self):
        for path in (CELL, WRAP):
            data = read(path)
            kit = b.Kit(data)
            chk = kit.checks()
            self.assertTrue(chk["crc_ok"] and chk["md5_ok"], path)
            self.assertEqual(b.parse_item(data).to_bytes(), data, "container re-serialises")
            self.assertEqual(kit.inner.to_bytes(), kit.raw, "preset re-serialises")

    def test_self_test_on_wrapper(self):
        builder = b.Builder(CELL, WRAP)
        refs, zones_ok, zones_total, cells_equal = b.self_test(builder, [WRAP])
        self.assertTrue(refs[0][1])
        self.assertEqual(zones_ok, zones_total)
        self.assertTrue(cells_equal)

    def test_build_with_real_templates(self):
        with tempfile.TemporaryDirectory() as t:
            backup = os.path.join(t, "B.BIN")
            write(backup, make_backup({1: {"name": "T", "pads": {
                0: (1, layer_bytes(note=36, volume=70, cents=-250), layer_bytes(note=128)),
                8: (0, layer_bytes(note=45), silent_layer())}}}))
            k = os.path.join(t, "samples", "001 Template Test")
            write_wav(os.path.join(k, "Layer A", "1-KIK 001.wav"), frames=4410)
            write_wav(os.path.join(k, "Layer B", "1-SNR 001.wav"), frames=2205, channels=2)
            write_wav(os.path.join(k, "Layer A", "EXT1-TOM 001.wav"), frames=1000, width=3)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = b.main(["--backup", backup, "--samples", os.path.join(t, "samples"),
                               "--out-dir", os.path.join(t, "out"),
                               "--cell-template", CELL, "--wrapper-template", WRAP])
            self.assertEqual(code, 0, out.getvalue())
            kit = b.Kit(read(os.path.join(t, "out", "001 Template Test",
                                          "001 Template Test.nbkt")))
            self.assertEqual(len(kit.zones), 3)
            cells = nbkt_gen.read_cells(kit.raw)
            self.assertEqual((cells[0].low_key, cells[16].low_key, cells[8].low_key), (36, 120, 45))

    def test_parameter_only_generator(self):
        with tempfile.TemporaryDirectory() as t:
            cells = {0: nbkt_gen.Cell(60, 100, 0), 1: nbkt_gen.Cell(61, 50, 12)}
            path = nbkt_gen.generate_kit("Param", cells, template=CELL, out_dir=t)
            infos = nbkt_gen.verify_kit(path, template=CELL, cells=cells)
            self.assertAlmostEqual(infos[1].tune_semitones, 12.0, places=4)


if __name__ == "__main__":
    unittest.main()
