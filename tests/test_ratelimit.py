import unittest

from stormshow.ratelimit import GuestLimiter, TokenBucket


class Buckets(unittest.TestCase):
    def test_token_bucket(self):
        b = TokenBucket(rate=1.0, burst=2, now=0)
        self.assertTrue(b.take(0))
        self.assertTrue(b.take(0))
        self.assertFalse(b.take(0))
        self.assertAlmostEqual(b.retry_after(0), 1.0)
        self.assertTrue(b.take(1.0))

    def test_guest_cooldown_and_refund(self):
        g = GuestLimiter(cooldown_s=30, global_rate_per_min=60, global_burst=10, now=0)
        self.assertEqual(g.take("a", 0), 0)
        wait = g.take("a", 5)
        self.assertAlmostEqual(wait, 25, places=3)
        self.assertEqual(g.take("b", 5), 0)  # other guests unaffected
        g.refund("a")
        self.assertEqual(g.take("a", 6), 0)  # refunded summon is usable again
        self.assertEqual(g.take("a", 37), 0)

    def test_global_limit(self):
        g = GuestLimiter(cooldown_s=1, global_rate_per_min=6, global_burst=2, now=0)
        self.assertEqual(g.take("a", 0), 0)
        self.assertEqual(g.take("b", 0), 0)
        self.assertGreaterEqual(g.take("c", 0), 1)  # global bucket empty
        self.assertEqual(g.take("c", 10), 0)  # 6/min = one every 10 s

    def test_global_refusal_does_not_charge_guest(self):
        g = GuestLimiter(cooldown_s=30, global_rate_per_min=6, global_burst=1, now=0)
        self.assertEqual(g.take("a", 0), 0)
        self.assertGreater(g.take("b", 0), 0)
        self.assertEqual(g.take("b", 10), 0)  # b's own cooldown never started

    def test_keys_are_bounded(self):
        g = GuestLimiter(cooldown_s=30, global_rate_per_min=120, global_burst=50, now=0, max_keys=3)
        for i in range(10):
            g.take(f"k{i}", i * 100)
        self.assertLessEqual(len(g._keys), 3)


if __name__ == "__main__":
    unittest.main()
