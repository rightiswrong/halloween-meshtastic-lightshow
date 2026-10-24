import unittest

from stormshow import protocol as P


class RequestGrammar(unittest.TestCase):
    def test_round_trip(self):
        for req in (
            P.Request("00af", "STRIKE", 0),
            P.Request("beef", "STRIKE", 100),
            P.Request("1234", "STORM"),
            P.Request("abcd", "FIRE"),
            P.Request("abcd", "HEART"),
            P.Request("abcd", "PING"),
        ):
            self.assertEqual(P.parse_request(req.encode()), req)

    def test_rejects_everything_else(self):
        bad = [
            None,
            42,
            b"SB1 abcd STORM",
            "",
            "SB1",
            "sb1 abcd STORM",
            "SB1 ABCD STORM",
            "SB1 abc STORM",
            "SB1 abcde STORM",
            "SB1 abcd storm",
            "SB1 abcd STORM ",
            " SB1 abcd STORM",
            "SB1  abcd STORM",
            "SB1 abcd STORM 5",
            "SB1 abcd STRIKE",
            "SB1 abcd STRIKE 101",
            "SB1 abcd STRIKE 007",
            "SB1 abcd STRIKE -1",
            "SB1 abcd STRIKE 5.0",
            "SB1 abcd STRIKE 80\n",
            "SB1 abcd RM -RF",
            "SB1 abcd OK 3",
            "SB2 abcd STORM",
            "SB1 abcd STORM\x00",
            "SB1 abcd STRIKE ٨٠",
            "SB1 abcd " + "STORM" * 20,
        ]
        for text in bad:
            with self.subTest(text=text):
                self.assertIsNone(P.parse_request(text))

    def test_encode_validates(self):
        with self.assertRaises(ValueError):
            P.Request("xyz1", "STORM").encode()
        with self.assertRaises(ValueError):
            P.Request("abcd", "STRIKE", 101).encode()
        with self.assertRaises(ValueError):
            P.Request("abcd", "STORM", 5).encode()
        with self.assertRaises(ValueError):
            P.Request("abcd", "DANCE").encode()


class ReplyGrammar(unittest.TestCase):
    def test_round_trip(self):
        for rep in (
            P.Reply("abcd", "OK", 0),
            P.Reply("abcd", "OK", 347),
            P.Reply("abcd", "CLOSED"),
            P.Reply("abcd", "BUSY"),
            P.Reply("abcd", "ERR"),
            P.Reply("abcd", "PONG"),
        ):
            self.assertEqual(P.parse_reply(rep.encode()), rep)

    def test_rejects(self):
        for text in ("SB1 abcd OK", "SB1 abcd OK 01", "SB1 abcd FINE", "SB1 abcd STORM", "SB1 abcd OK -1"):
            with self.subTest(text=text):
                self.assertIsNone(P.parse_reply(text))

    def test_messages_fit_one_packet(self):
        self.assertLessEqual(len(P.Reply("ffff", "OK", 9_999_999).encode()), P.MAX_LEN)
        self.assertLessEqual(len(P.Request("ffff", "STRIKE", 100).encode()), P.MAX_LEN)


if __name__ == "__main__":
    unittest.main()
