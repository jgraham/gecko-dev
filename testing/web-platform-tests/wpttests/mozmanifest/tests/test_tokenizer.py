import unittest

import sys
import os
sys.path.insert(0, os.path.abspath(".."))
from cStringIO import StringIO
import itertools

import parser
from parser import token_types

class TokenizerTest(unittest.TestCase):
    def setUp(self):
        self.tokenize = parser.Tokenizer().tokenize

    def run_test(self, input_text, expected):
        expected = expected + [(token_types.eof, None)]
        actual = itertools.islice(self.tokenize(StringIO(input_text)), len(expected))
        self.assertEquals(list(actual), expected)

    def test_heading_0(self):
        self.run_test("""[Heading text]""",
                      [(token_types.paren, "["),
                       (token_types.string, "Heading text"),
                       (token_types.paren, "]")])

    def test_heading_1(self):
        self.run_test("""[Heading \[text\]]""",
                      [(token_types.paren, "["),
                       (token_types.string, "Heading [text]"),
                       (token_types.paren, "]")])

    def test_heading_2(self):
        self.run_test("""[Heading \#text]""",
                      [(token_types.paren, "["),
                       (token_types.string, "Heading #text"),
                       (token_types.paren, "]")])

if __name__ == "__main__":
    unittest.main()
