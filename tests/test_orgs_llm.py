"""Organisations LLM ablation: reply parsing keeps seed/home/family ids and never drops a group. No network."""
import unittest

from coldstart import orgs_llm
from coldstart.orgs import Orgs


def _rules():
    return Orgs(orgs=[{"id": "org1", "name": "Seed Co", "type": "company", "side": "work", "role": "owner"},
                      {"id": "home", "name": "Home", "type": "residence", "side": "home", "role": "owner"},
                      {"id": "social", "name": "Friends & communities", "type": "other", "side": "home", "role": "member"}],
                group_org={"C1": "org1", "C2": "social", "C3": "social", "C4": "home"},
                kind={"org1": "seed", "home": "home", "social": "social"})


GMAP = {"G1": "C1", "G2": "C2", "G3": "C3", "G4": "C4"}
SMAP = {"S1": "org1"}


class ParseOrgs(unittest.TestCase):
    def test_ids_kept_and_new_org(self):
        rep = {"orgs": [{"id": "S1", "name": "Seed Co", "type": "company", "side": "work", "role": "owner", "groups": ["G1", "G2"]},
                        {"id": "x", "name": "Family", "type": "other", "side": "home", "role": "member", "groups": ["G3"]},
                        {"id": "y", "name": "Kids school", "type": "school", "side": "home", "role": "parent", "groups": []}]}
        o = orgs_llm.parse(rep, GMAP, SMAP, _rules())
        ids = [x["id"] for x in o.orgs]
        self.assertIn("org1", ids)
        self.assertIn("family", ids)  # people.py reads family membership by this id
        self.assertIn("llm1", ids)
        self.assertIn("home", ids)  # always present
        self.assertEqual(o.group_org, {"C1": "org1", "C2": "org1", "C3": "family", "C4": "home"})

    def test_left_out_groups_keep_rules_org(self):
        o = orgs_llm.parse({"orgs": [{"id": "S1", "groups": ["G1"]}]}, GMAP, SMAP, _rules())
        self.assertEqual(o.group_org["C2"], "social")
        self.assertIn("social", [x["id"] for x in o.orgs])
        self.assertEqual(set(o.group_org), {"C1", "C2", "C3", "C4"})

    def test_bad_types_fall_back_and_junk_reply_is_none(self):
        o = orgs_llm.parse({"orgs": [{"id": "S1", "type": "spaceship", "groups": ["G1"]}]}, GMAP, SMAP, _rules())
        self.assertEqual(next(x for x in o.orgs if x["id"] == "org1")["type"], "company")
        self.assertIsNone(orgs_llm.parse({"nope": 1}, GMAP, SMAP, _rules()))
        self.assertIsNone(orgs_llm.parse(None, GMAP, SMAP, _rules()))


if __name__ == "__main__":
    unittest.main()
