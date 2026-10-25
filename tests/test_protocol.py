import unittest

from stormshow import protocol as P
from tests.helpers import KEY, OTHER_KEY


class RequestGrammar(unittest.TestCase):
    def test_round_trip(self):
        for req in (
            P.Request(1, "STRIKE", 0),
            P.Request(4_294_967_295, "STRIKE", 100),
            P.Request(42, "STORM"),
            P.Request(43, "FIRE"),
            P.Request(44, "HEART"),
            P.Request(45, "PING"),
        ):
            line = req.encode(KEY)
            self.assertLessEqual(len(line), P.MAX_LEN)
            self.assertEqual(P.parse_request(line, KEY), req)

    def test_wrong_key_is_rejected(self):
        line = P.Request(7, "STRIKE", 80).encode(KEY)
        self.assertIsNone(P.parse_request(line, OTHER_KEY))

    def test_any_change_breaks_the_tag(self):
        """F-01: bit-flipping a captured command must not yield a valid one."""
        line = P.Request(1042, "STRIKE", 80).encode(KEY)
        for i, ch in enumerate(line):
            for alt in {"0", "9", "A", "z", "-", " "} - {ch}:
                forged = line[:i] + alt + line[i + 1 :]
                parsed = P.parse_request(forged, KEY)
                self.assertTrue(parsed is None or parsed == P.parse_request(line, KEY), forged)
        self.assertIsNone(P.parse_request(line.replace("STRIKE 80", "STRIKE 99"), KEY))
        self.assertIsNone(P.parse_request(line.replace("1042", "1043"), KEY))

    def test_reply_is_not_a_request(self):
        """Direction labels: a signed reply can't be replayed as a request."""
        reply = P.Reply(5, "PONG").encode(KEY)
        self.assertIsNone(P.parse_request(reply, KEY))
        self.assertIsNone(P.parse_reply(P.Request(5, "PING").encode(KEY), KEY))

    def test_rejects_everything_else(self):
        good = P.Request(12, "STORM").encode(KEY)
        tag = good.rsplit(" ", 1)[1]
        bad = [
            None,
            42,
            b"SB2 12 STORM x",
            "",
            "SB2",
            good.lower(),
            good + " ",
            " " + good,
            good.replace("SB2", "SB1"),
            f"SB2 012 STORM {tag}",
            f"SB2 0 STORM {tag}",
            f"SB2 4294967296 STORM {tag}",
            f"SB2 12 STORM {tag}X",
            f"SB2 12 STORM {tag[:-1]}",
            f"SB2 12 STRIKE {tag}",
            f"SB2 12 STRIKE 101 {tag}",
            f"SB2 12 RM -RF {tag}",
            f"SB2 12 STORM\n{tag}",
            "SB1 abcd STRIKE 80",
            "SB2 12 STORM " + "A" * 40,
        ]
        for text in bad:
            with self.subTest(text=text):
                self.assertIsNone(P.parse_request(text, KEY))

    def test_encode_validates(self):
        for req in (P.Request(0, "STORM"), P.Request(1, "STRIKE", 101), P.Request(1, "STORM", 5), P.Request(1, "X")):
            with self.assertRaises(ValueError):
                req.encode(KEY)

    def test_secret_must_be_long_enough(self):
        with self.assertRaises(ValueError):
            P.LinkKey(b"short")
        with self.assertRaises(ValueError):
            P.LinkKey.from_base64("not base64!")
        self.assertNotIn(str(bytes(range(32))), repr(KEY))


class ReplyGrammar(unittest.TestCase):
    def test_round_trip(self):
        for rep in (
            P.Reply(1, "OK", 0),
            P.Reply(4_294_967_295, "OK", 9_999_999),
            P.Reply(3, "CLOSED"),
            P.Reply(3, "BUSY"),
            P.Reply(3, "ERR"),
            P.Reply(3, "PONG"),
            P.Reply(4_294_967_295, "STALE", 4_294_967_295),
        ):
            line = rep.encode(KEY)
            self.assertLessEqual(len(line), P.MAX_LEN)
            self.assertEqual(P.parse_reply(line, KEY), rep)

    def test_forged_reply_rejected(self):
        """F-18: a reply without the secret is ignored."""
        self.assertIsNone(P.parse_reply(P.Reply(3, "OK", 999).encode(OTHER_KEY), KEY))


class Window(unittest.TestCase):
    def test_replay_window(self):
        w = P.ReplayWindow(size=4)
        for seq in (1, 2, 5):
            self.assertTrue(w.is_fresh(seq))
            w.accept(seq)
        self.assertFalse(w.is_fresh(5))  # exact replay
        self.assertTrue(w.is_fresh(4))  # late but inside the window
        w.accept(4)
        self.assertFalse(w.is_fresh(4))
        self.assertFalse(w.is_fresh(1))  # too old
        w.accept(100)
        self.assertFalse(w.is_fresh(90))
        self.assertTrue(w.is_fresh(101))


if __name__ == "__main__":
    unittest.main()
