"""SPD-30 backup parsing, layer activation, pad-to-cell mapping and MIDI keys."""

import os
import tempfile
import unittest

from fixtures import layer_bytes, make_backup, silent_layer
import build_john_deni_battery4_kits as b


def parse(kits):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "BACKUP01.BIN")
        with open(path, "wb") as f:
            f.write(make_backup(kits))
        return b.parse_backup(path)


def layer(kit, pad, which):
    return next(l for l in kit["layers"] if l["pad"] == pad and l["layer"] == which)


class ParseTest(unittest.TestCase):
    def test_counts_and_names(self):
        kits = parse({1: {"name": "First"}, 100: {"name": "Last"}})
        self.assertEqual(len(kits), 100)
        self.assertEqual(kits[0]["name"], "First")
        self.assertEqual(kits[99]["name"], "Last")
        self.assertEqual(kits[99]["number"], 100)
        self.assertTrue(all(len(k["layers"]) == 24 for k in kits))

    def test_layer_fields(self):
        kits = parse({7: {"pads": {2: (1, layer_bytes(category=3, tone=12, volume=74,
                                                       cents=620, note=38, channel=9),
                                       layer_bytes(volume=50, cents=-1200, note=40))}}})
        a = layer(kits[6], "PAD 3", "A")
        self.assertEqual((a["category"], a["tone"], a["volume"], a["note"]), (3, 12, 74, 38))
        self.assertEqual(a["raw_tune"], 3020)
        self.assertAlmostEqual(a["semitones"], 6.2)
        bl = layer(kits[6], "PAD 3", "B")
        self.assertAlmostEqual(bl["semitones"], -12.0)
        self.assertEqual(bl["layer_mode"], 1)

    def test_kit_offsets_do_not_bleed(self):
        # a pad set in kit 2 must not show up in kit 1 or kit 3
        kits = parse({2: {"pads": {0: (0, layer_bytes(note=55), silent_layer())}}})
        self.assertEqual(layer(kits[1], "PAD 1", "A")["note"], 55)
        self.assertEqual(layer(kits[0], "PAD 1", "A")["note"], 128)
        self.assertEqual(layer(kits[2], "PAD 1", "A")["note"], 128)

    def test_not_a_backup(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.bin")
            with open(path, "wb") as f:
                f.write(b"\x00" * 200000)
            with self.assertRaises(b.BuildError):
                b.parse_backup(path)


class ActivationTest(unittest.TestCase):
    def setUp(self):
        self.kit = parse({1: {"pads": {
            0: (0, layer_bytes(), layer_bytes()),                    # single mode
            1: (1, layer_bytes(), layer_bytes()),                    # both layers
            2: (1, layer_bytes(category=12), layer_bytes(volume=0)),
            3: (2, layer_bytes(muted=True), layer_bytes()),
        }}})[0]

    def check(self, pad, which, active, reason=""):
        l = layer(self.kit, pad, which)
        self.assertEqual(l["active"], active, (pad, which))
        self.assertEqual(l["inactive_reason"], reason, (pad, which))

    def test_rules(self):
        self.check("PAD 1", "A", True)
        self.check("PAD 1", "B", False, "layer B disabled (single mode)")
        self.check("PAD 2", "A", True)
        self.check("PAD 2", "B", True)
        self.check("PAD 3", "A", False, "instrument OFF")
        self.check("PAD 3", "B", False, "zero volume")
        self.check("PAD 4", "A", False, "muted")
        self.check("PAD 4", "B", True)
        self.check("PAD 5", "A", False, "instrument OFF")


class CellMappingTest(unittest.TestCase):
    def test_pad_to_cell(self):
        kit = parse({})[0]
        cells = {(l["pad"], l["layer"]): l["cell"] for l in kit["layers"]}
        self.assertEqual(cells[("PAD 1", "A")], 0)
        self.assertEqual(cells[("PAD 8", "A")], 7)
        self.assertEqual(cells[("EXT 1", "A")], 8)
        self.assertEqual(cells[("EXT 4", "A")], 11)
        self.assertEqual(cells[("PAD 1", "B")], 16)
        self.assertEqual(cells[("EXT 4", "B")], 27)
        self.assertEqual(len(set(cells.values())), 24)


class NoteOffKeyTest(unittest.TestCase):
    def layers(self, notes):
        return [dict(cell=c, note=n) for c, n in notes]

    def test_first_eight_use_120_to_127_in_cell_order(self):
        keys = b.assign_note_off_keys(self.layers([(c, 128) for c in (16, 0, 3, 1)]))
        self.assertEqual(keys, {0: 120, 1: 121, 3: 122, 16: 123})

    def test_more_than_eight_continue_downward(self):
        keys = b.assign_note_off_keys(self.layers([(c, 128) for c in range(11)]))
        self.assertEqual([keys[c] for c in range(11)],
                         [120, 121, 122, 123, 124, 125, 126, 127, 119, 118, 117])

    def test_keys_used_by_real_notes_are_skipped(self):
        keys = b.assign_note_off_keys(self.layers([(0, 121), (1, 128), (2, 128), (3, 128)]))
        self.assertEqual(keys, {1: 120, 2: 122, 3: 123})

    def test_layers_with_a_note_get_no_key(self):
        self.assertEqual(b.assign_note_off_keys(self.layers([(0, 36), (1, 38)])), {})


if __name__ == "__main__":
    unittest.main()
