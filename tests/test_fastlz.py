import os
import random
import unittest

import fixtures  # noqa: F401  (puts the project root on sys.path)
import fastlz2


class FastLZTest(unittest.TestCase):
    def roundtrip(self, data):
        packed = fastlz2.compress(data)
        if data:
            self.assertEqual(packed[0] >> 5, 1, "stream must be marked level 2")
        self.assertEqual(fastlz2.decompress(packed, len(data)), data)
        return packed

    def test_empty_and_short(self):
        for n in range(0, 20):
            self.roundtrip(bytes(range(n)))

    def test_repetitive_data_compresses(self):
        data = b"abc" * 20000
        self.assertLess(len(self.roundtrip(data)), len(data) // 10)

    def test_random_data(self):
        rnd = random.Random(1)
        for _ in range(50):
            n = rnd.randrange(0, 5000)
            data = bytes(rnd.choice(b"ab\x00\x01") if rnd.random() < 0.9 else rnd.randrange(256)
                         for _ in range(n))
            self.roundtrip(data)

    def test_far_matches(self):
        # matches more than 8191 bytes back use the long level-2 distance form
        block = os.urandom(3000)
        data = block + os.urandom(20000) + block + b"xyz" * 5000 + block
        self.roundtrip(data)

    def test_long_runs(self):
        self.roundtrip(bytes(100000))
        self.roundtrip(b"\x01\x02" * 40000)

    def test_wrong_expected_size(self):
        packed = fastlz2.compress(b"hello world" * 10)
        with self.assertRaises(fastlz2.FastLZError):
            fastlz2.decompress(packed, 5)

    def test_truncated_stream(self):
        packed = fastlz2.compress(os.urandom(2000))
        with self.assertRaises(fastlz2.FastLZError):
            fastlz2.decompress(packed[:len(packed) // 2], 2000)

    def test_bad_back_reference(self):
        # level-2 marker, then a match opcode before any output exists
        with self.assertRaises(fastlz2.FastLZError):
            fastlz2.decompress(bytes([0x20 | 0, 0x41, 0x40, 0x05]))


if __name__ == "__main__":
    unittest.main()
