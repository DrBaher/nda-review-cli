import unittest

from rule_engine import clause_hit, red_flag_hits


class RuleEngineTests(unittest.TestCase):
    def test_clause_hit_returns_patterns(self):
        hit, hits = clause_hit("governing law and exclusive jurisdiction", [r"governing law", r"courts of"])
        self.assertTrue(hit)
        self.assertIn(r"governing law", hits)

    def test_red_flag_hits(self):
        hits = red_flag_hits("term_and_survival", "obligations survive indefinitely")
        self.assertTrue(hits)
        self.assertIn(hits[0]["match"].lower(), {"indefinite", "indefinitely"})


if __name__ == "__main__":
    unittest.main()
