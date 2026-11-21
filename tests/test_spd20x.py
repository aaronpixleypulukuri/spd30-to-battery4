"""SPD-30 -> SPD-20X MIDI export on synthetic files."""

import contextlib
import io
import os
import tempfile
import unittest

from fixtures import layer_bytes, make_backup, make_spd20x_patch, silent_layer
import spd30_to_spd20x_midi as x


class SPD20XTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        cls.backup = os.path.join(t, "BACKUP01.BIN")
        with open(cls.backup, "wb") as f:
            f.write(make_backup({3: {"pads": {
                0: (1, layer_bytes(note=40), layer_bytes(note=40)),          # same note
                1: (1, layer_bytes(note=128), layer_bytes(note=42)),        # no-note A
                2: (0, layer_bytes(note=44), layer_bytes(note=50)),         # single mode
                8: (0, layer_bytes(note=36, channel=9), silent_layer()),    # EXT 1
                11: (0, layer_bytes(note=49), silent_layer()),              # EXT 4
            }}}))
        cls.src = os.path.join(t, "src")
        os.makedirs(cls.src)
        cls.originals = {}
        for n in range(1, 100):
            data = make_spd20x_patch(n, "P%d" % n)
            if n == 3:
                data = bytearray(data)
                data[0x10E + 6 * 5 + 2] = 7            # another pad already sends a PC
                data = bytes(data)
            cls.originals[n] = data
            with open(os.path.join(cls.src, "SPD20X%02d.BIN" % n), "wb") as f:
                f.write(data)
        cls.out = os.path.join(t, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            cls.code = x.main(["--spd30", cls.backup, "--src", cls.src, "--out-dir", cls.out])
        with open(os.path.join(cls.out, "ROLAND", "SPD-20X", "BACKUP", "SPD20X03.BIN"), "rb") as f:
            cls.p3 = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rec(self, bank, r):
        base = 0x10E if bank == "A" else 0x16E
        o = base + 6 * r
        return self.p3[o], self.p3[o + 2], self.p3[o + 4]      # channel, pc, note

    def test_runs_and_sources_untouched(self):
        self.assertEqual(self.code, 0)
        for n, data in self.originals.items():
            with open(os.path.join(self.src, "SPD20X%02d.BIN" % n), "rb") as f:
                self.assertEqual(f.read(), data)

    def test_notes_and_channels(self):
        self.assertEqual(self.rec("A", 0)[::2], (0, 40))
        self.assertEqual(self.rec("B", 0), (x.CH_OFF, 128, 40), "same note: B channel OFF")
        self.assertEqual(self.rec("B", 1)[::2], (0, 42))
        self.assertEqual(self.rec("A", 2)[2], 44)
        self.assertEqual(self.rec("B", 2)[2], 128, "single mode: layer B not sent")
        self.assertEqual(self.rec("A", 8)[::2], (9, 36), "EXT 1 -> record 8, channel 10")
        self.assertEqual(self.rec("A", 14)[2], 49, "EXT 4 -> record 14")
        self.assertEqual(self.rec("A", 9)[2], 128, "rim records untouched")

    def test_no_note_layer_gets_battery_key(self):
        self.assertEqual(self.rec("A", 1)[2], 120)

    def test_program_change(self):
        self.assertEqual(self.rec("A", 0)[1], 2, "patch 3 sends PC 3 (stored 0-based)")
        self.assertEqual(self.rec("A", 5)[1], 2, "existing PC on another pad follows")
        self.assertEqual(self.rec("A", 6)[1], 128, "pads without a PC stay OFF")

    def test_only_midi_bytes_change(self):
        allowed = set()
        for base in (0x10E, 0x16E):
            for r in range(16):
                allowed |= {base + 6 * r, base + 6 * r + 2, base + 6 * r + 4}
        changed = {i for i in range(498) if self.p3[i] != self.originals[3][i]}
        self.assertTrue(changed and changed <= allowed)

    def test_refuses_to_overwrite_source(self):
        root = os.path.join(self.tmp.name, "inplace")
        backup_dir = os.path.join(root, "ROLAND", "SPD-20X", "BACKUP")
        os.makedirs(backup_dir)
        with self.assertRaises(x.ConvertError):
            x.convert(self.backup, backup_dir, root)

if __name__ == "__main__":
    unittest.main()
