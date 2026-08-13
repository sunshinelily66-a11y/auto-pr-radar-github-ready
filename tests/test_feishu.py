import json
import unittest

from src.feishu import build_card, make_signature, _fit_payload


class FeishuTest(unittest.TestCase):
    def test_signature_stable(self):
        sig1 = make_signature(1234567890, "secret")
        sig2 = make_signature(1234567890, "secret")
        self.assertEqual(sig1, sig2)
        self.assertTrue(len(sig1) > 10)

    def test_payload_under_limit(self):
        payload = build_card("测试", "中文内容" * 10000)
        payload = _fit_payload(payload)
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertLess(size, 20 * 1024)


if __name__ == "__main__":
    unittest.main()
