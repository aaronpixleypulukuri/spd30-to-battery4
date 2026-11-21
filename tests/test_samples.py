"""Locating WAV files for pad layers and reading their format."""

import os
import tempfile
import unittest

from fixtures import write_wav
import build_john_deni_battery4_kits as b


def layer(prefix, which):
    return dict(prefix=prefix, layer=which)


class FindWavTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kit = os.path.join(self.tmp.name, "001 Demo")
        for name in ("Layer A/1-KIK 001.wav", "Layer A/EXT1-SNR 002.wav",
                     "Layer A/10-not a pad.wav", "Layer B/2-TOM 004.WAV",
                     "Layer B/3-HH 001.wav", "Layer B/3-HH 002.wav", "Layer A/4-notes.txt"):
            path = os.path.join(self.kit, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").close()

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, prefix, which):
        return [os.path.basename(p) for p in b.find_wav(self.kit, layer(prefix, which))]

    def test_pad_prefix(self):
        self.assertEqual(self.names("1", "A"), ["1-KIK 001.wav"])

    def test_ext_prefix_is_not_confused_with_pad(self):
        self.assertEqual(self.names("EXT1", "A"), ["EXT1-SNR 002.wav"])

    def test_upper_case_extension(self):
        self.assertEqual(self.names("2", "B"), ["2-TOM 004.WAV"])

    def test_layer_folder_is_respected(self):
        self.assertEqual(self.names("2", "A"), [])

    def test_missing(self):
        self.assertEqual(self.names("5", "A"), [])
        self.assertEqual(self.names("4", "A"), [], "non-WAV files are ignored")
        self.assertEqual(b.find_wav(os.path.join(self.tmp.name, "nope"), layer("1", "A")), [])

    def test_duplicates_are_all_returned_sorted(self):
        self.assertEqual(self.names("3", "B"), ["3-HH 001.wav", "3-HH 002.wav"])


class WavInfoTest(unittest.TestCase):
    def test_plain_and_extra_chunk(self):
        with tempfile.TemporaryDirectory() as d:
            plain = os.path.join(d, "a.wav")
            write_wav(plain, frames=1000, channels=2, width=2, rate=48000)
            info = b.wav_info(plain)
            self.assertEqual(info, dict(rate=48000, channels=2, width=2, frames=1000,
                                        data_offset=44))
            extra = os.path.join(d, "b.wav")
            write_wav(extra, frames=10, extra_chunk=True)
            self.assertEqual(b.wav_info(extra)["data_offset"], 44 + 12)
            self.assertEqual(b.wav_info(extra)["frames"], 10)


if __name__ == "__main__":
    unittest.main()
