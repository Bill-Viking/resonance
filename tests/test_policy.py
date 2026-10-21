import unittest
import policy


class BoundaryTests(unittest.TestCase):
    def codes(self, text, **account):
        return {s["code"] for s in policy.check(text, account)}

    def test_named_accounts_cannot_relax_sfw_at_any_daring_level(self):
        for daring in ("careful", "balanced", "bold"):
            self.assertIn("sfw-profanity", self.codes("That is fucking stupid", identity_mode="named", daring=daring, sfw_only=False))

    def test_alias_with_explicit_permission_can_be_more_daring(self):
        text = "That is fucking stupid. Never!!!"
        self.assertEqual(set(), self.codes(text, identity_mode="alias", daring="bold", sfw_only=False))
        self.assertIn("sfw-profanity", self.codes(text, identity_mode="alias", daring="bold", sfw_only=True))

    def test_unknown_identity_defaults_conservatively(self):
        self.assertIn("sfw-explicit", self.codes("Look at my nudes", identity_mode="unclassified", sfw_only=False))

    def test_professional_text_not_flagged_for_political_topic(self):
        self.assertEqual(set(), self.codes("I disagree with the immigration policy because it separates families.", identity_mode="named", daring="balanced"))
        self.assertEqual(set(), self.codes("I disagree with the tax policy because costs are too high.", identity_mode="named", daring="balanced"))

    def test_risk_can_remain_on_bold_alias(self):
        self.assertIn("group-attack", self.codes("Women are assholes", identity_mode="alias", daring="bold", sfw_only=False))
        self.assertIn("targeted-threat", self.codes("I will kill you", identity_mode="alias", daring="bold", sfw_only=False))

    def test_offsets_match_javascript_strings(self):
        signal = next(s for s in policy.check("😀 shit", {}) if s["code"] == "sfw-profanity")
        self.assertEqual((3, 7), (signal["start"], signal["end"]))

    def test_self_criticism_is_not_presented_as_a_claim_to_accept_as_true(self):
        signals = policy.check("I am a failure. I always get this wrong.")
        codes = {signal["code"] for signal in signals}
        self.assertIn("self-criticism", codes)
        self.assertNotIn("sweeping-claim", codes)
        self.assertFalse(any("if it is accurate" in s["detail"] for s in signals))
        self.assertIn("sweeping-claim", self.codes("Everyone can use this service."))

    def test_flags_only_examine_available_excerpts_and_change_on_boundary_change(self):
        account = {"id": "a", "identity_mode": "named", "daring": "careful", "sfw_only": True}
        reviews = [{"id": "r", "account_id": "a", "excerpt": "shit", "finding": "not evaluated", "updated_at": "2026-09-14T00:00:00Z"},
                   {"id": "s", "account_id": "a", "excerpt": "", "finding": "fuck"}]
        flags = policy.account_flags([account], reviews)
        self.assertEqual(1, len(flags))
        self.assertEqual(flags[0]["id"], policy.account_flags([account], reviews)[0]["id"])
        account["daring"] = "balanced"
        self.assertNotEqual(flags[0]["id"], policy.account_flags([account], reviews)[0]["id"])


if __name__ == "__main__":
    unittest.main()
