import unittest

from stratgauge.ml._hashing import canonical_json, sha256_bytes, sha256_json


class HashingTests(unittest.TestCase):
    def test_canonical_json_preserves_non_ascii_and_is_compact(self) -> None:
        value = {"问候": "世界", "value": [1, 2]}
        expected = '{"value":[1,2],"问候":"世界"}'.encode()
        self.assertEqual(canonical_json(value), expected)

    def test_canonical_json_sorts_nested_keys(self) -> None:
        value = {"outer": {"z": 1, "a": {"d": 4, "c": 3}}}
        self.assertEqual(canonical_json(value), b'{"outer":{"a":{"c":3,"d":4},"z":1}}')

    def test_sha256_bytes_is_deterministic(self) -> None:
        digest = sha256_bytes(b"hello")
        self.assertEqual(
            digest,
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )
        self.assertEqual(digest, sha256_bytes(b"hello"))

    def test_sha256_json_is_deterministic(self) -> None:
        value = {"z": "汉", "a": {"b": 2, "a": 1}}
        self.assertEqual(sha256_json(value), sha256_json(value))
        self.assertEqual(sha256_json(value), sha256_bytes(canonical_json(value)))
