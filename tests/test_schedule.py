import unittest

from app.schedule import (build_groups, compute_schedule, generate_stages, merge_rows,
                          normalize_plan, normalize_scheme, seed_order, seed_label)


def entry(index, name=None):
    return {"id": f"e{index}", "name": name or f"Driver {index}",
            "nick": "", "transponder": str(1000 + index),
            "class_id": "c1", "approved": True, "checked_in": True}


def rows(pairs):
    """pairs: list of (transponder, pos, total_ms, best_ms)."""
    out = []
    for transponder, pos, total, best in pairs:
        out.append({"transponder": transponder, "pos": pos, "total_ms": total,
                    "best_ms": best, "top3_ms": best, "consecutive_ms": best})
    return out


def plan(**overrides):
    data = {"class_id": "c1", "kind": "qualifying", "order": 1,
            "max_group_size": 4, "bump_up": 0, "source_kind": "roster",
            "seeding": {"basis": "roster", "scope": "front", "mode": "forward",
                        "rounds": 1, "drop": 0},
            "duration_ms": 60000, "gap_ms": 300000}
    seeding = dict(data["seeding"])
    seeding.update(overrides.pop("seeding", {}))
    data.update(overrides)
    data["seeding"] = seeding
    return normalize_plan(data)


class NormalizePlanTest(unittest.TestCase):
    def test_defaults_and_limits(self):
        self.assertEqual(normalize_plan({"class_id": "c1"})["max_group_size"], 8)
        self.assertEqual(normalize_plan({"class_id": "c1"})["gap_ms"], 300000)
        with self.assertRaises(ValueError):
            normalize_plan({"class_id": "c1", "max_group_size": 1})
        with self.assertRaises(ValueError):
            normalize_plan({"class_id": "c1", "kind": "unknown"})
        with self.assertRaises(ValueError):
            normalize_plan({"class_id": "c1", "seeding": {"basis": "nope"}})

    def test_scheme_is_validated(self):
        self.assertEqual(normalize_scheme(None)["type"], "sequential")
        self.assertTrue(normalize_scheme({"type": "rounds", "finals_by_group": True})["finals_by_group"])
        with self.assertRaises(ValueError):
            normalize_scheme({"type": "nope"})


class SeedOrderTest(unittest.TestCase):
    def test_roster_keeps_original_order(self):
        entries = [entry(1), entry(2), entry(3)]
        self.assertEqual(seed_order(entries, [], plan()), entries)

    def test_result_basis_orders_by_total_time(self):
        entries = [entry(1), entry(2), entry(3)]
        source = rows([("1001", 1, 90000, 30000), ("1002", 2, 80000, 31000),
                       ("1003", 3, 95000, 29000)])
        ordered = seed_order(entries, source, plan(seeding={"basis": "result"}))
        self.assertEqual([e["transponder"] for e in ordered], ["1002", "1001", "1003"])

    def test_position_basis_orders_by_finish_position(self):
        entries = [entry(1), entry(2), entry(3)]
        source = rows([("1001", 3, 90000, 30000), ("1002", 1, 80000, 31000),
                       ("1003", 2, 95000, 29000)])
        ordered = seed_order(entries, source, plan(seeding={"basis": "position"}))
        self.assertEqual([e["transponder"] for e in ordered], ["1002", "1003", "1001"])

    def test_best_lap_basis_uses_best_ms(self):
        entries = [entry(1), entry(2)]
        source = rows([("1001", 1, 90000, 31000), ("1002", 2, 80000, 30000)])
        ordered = seed_order(entries, source, plan(seeding={"basis": "best"}))
        self.assertEqual([e["transponder"] for e in ordered], ["1002", "1001"])

    def test_drop_removes_the_worst_before_grouping(self):
        entries = [entry(1), entry(2), entry(3), entry(4)]
        source = rows([("1001", 1, 90000, 30000), ("1002", 2, 80000, 31000),
                       ("1003", 3, 95000, 29000), ("1004", 4, 99000, 28000)])
        ordered = seed_order(entries, source, plan(seeding={"basis": "result", "drop": 1}))
        self.assertEqual(len(ordered), 3)
        self.assertNotIn("1004", [e["transponder"] for e in ordered])
        self.assertIn("1003", [e["transponder"] for e in ordered])

    def test_reverse_mode_flips_the_ranking(self):
        entries = [entry(1), entry(2), entry(3)]
        source = rows([("1001", 1, 90000, 30000), ("1002", 2, 80000, 31000),
                       ("1003", 3, 95000, 29000)])
        ordered = seed_order(entries, source, plan(seeding={"basis": "result", "mode": "reverse"}))
        self.assertEqual([e["transponder"] for e in ordered], ["1003", "1001", "1002"])

    def test_merge_rows_keeps_the_best_per_driver(self):
        first = rows([("1001", 1, 90000, 30000)])
        second = rows([("1001", 2, 80000, 31000), ("1002", 1, 70000, 29000)])
        merged = merge_rows([first, second], "result")
        by_tp = {row["transponder"]: row for row in merged}
        self.assertEqual(by_tp["1001"]["total_ms"], 80000)
        self.assertIn("1002", by_tp)


class BuildGroupsTest(unittest.TestCase):
    def test_groups_split_by_max_size(self):
        entries = [entry(i) for i in range(1, 7)]
        groups = build_groups(entries, 4)
        self.assertEqual([len(group) for group in groups], [4, 2])

    def test_bump_up_lifts_a_short_group_into_the_previous_one(self):
        entries = [entry(i) for i in range(1, 10)]
        groups = build_groups(entries, 4, bump_up=1)
        self.assertEqual([len(group) for group in groups], [4, 5])
        self.assertEqual(groups[1][-1]["transponder"], "1009")

    def test_bump_up_zero_keeps_the_short_group(self):
        entries = [entry(i) for i in range(1, 10)]
        groups = build_groups(entries, 4)
        self.assertEqual([len(group) for group in groups], [4, 4, 1])

    def test_bump_up_two_lifts_two_drivers(self):
        entries = [entry(i) for i in range(1, 11)]
        groups = build_groups(entries, 4, bump_up=2)
        self.assertEqual([len(group) for group in groups], [4, 6])

    def test_even_scope_spreads_the_field(self):
        entries = [entry(i) for i in range(1, 9)]
        groups = build_groups(entries, 4, scope="even")
        self.assertEqual([len(group) for group in groups], [4, 4])
        self.assertEqual(groups[0][0]["transponder"], "1001")
        self.assertEqual(groups[1][0]["transponder"], "1002")


class GenerateStagesTest(unittest.TestCase):
    def event(self, entries):
        return {"id": "ev1", "classes": [{"id": "c1", "name": "Modelfix"}],
                "entries": entries, "stages": [], "plans": []}

    def test_generate_splits_entries_into_groups(self):
        entries = [entry(i) for i in range(1, 6)]
        stages = generate_stages(self.event(entries), plan(max_group_size=4))
        self.assertEqual(len(stages), 2)
        self.assertEqual(stages[0]["group"], "Grup A")
        self.assertEqual(stages[1]["group"], "Grup B")
        self.assertEqual(len(stages[0]["entry_ids"]), 4)
        self.assertEqual(stages[0]["status"], "planned")
        self.assertEqual(stages[0]["seed_source"], "Roster")

    def test_unapproved_entries_are_not_scheduled(self):
        entries = [entry(1)]
        entries[0]["approved"] = False
        with self.assertRaises(ValueError):
            generate_stages(self.event(entries), plan())

    def test_seed_label_mentions_source_and_drop(self):
        label = seed_label(plan(seeding={"basis": "result", "drop": 1, "mode": "reverse"}),
                           "Hasil Kualifikasi")
        self.assertIn("Hasil Kualifikasi", label)
        self.assertIn("total waktu", label)
        self.assertIn("balik grid", label)
        self.assertIn("buang 1 terburuk", label)


class ComputeScheduleTest(unittest.TestCase):
    def event(self, stages, scheme=None, start_at=None):
        return {"id": "ev1", "classes": [{"id": "c1", "name": "Modelfix"}],
                "entries": [entry(1)], "stages": stages, "plans": [],
                "schedule_scheme": scheme or {"type": "sequential"}, "start_at": start_at}

    def stage(self, sid, sequence, kind="qualifying", class_id="c1", group="Semua"):
        return {"id": sid, "class_id": class_id, "kind": kind, "name": sid,
                "group": group, "status": "planned", "sequence": sequence,
                "entry_ids": ["e1"], "cfg": {"target": "time", "target_time_ms": 60000}}

    def test_sequential_schedule_uses_start_time_and_gap(self):
        event = self.event([self.stage("s1", 1), self.stage("s2", 2)], start_at=1000)
        rows_out = compute_schedule(event, event["stages"])
        self.assertEqual(rows_out[0]["start_ms"], 1000000)
        self.assertEqual(rows_out[1]["start_ms"], 1000000 + (60000 + 300000))

    def test_without_start_time_no_clock_is_shown(self):
        event = self.event([self.stage("s1", 1)])
        rows_out = compute_schedule(event, event["stages"])
        self.assertIsNone(rows_out[0]["start_ms"])

    def test_rounds_scheme_interleaves_classes(self):
        stages = [self.stage("s1", 1, class_id="c1"), self.stage("s2", 1, class_id="c2"),
                  self.stage("s3", 2, class_id="c1")]
        event = {"id": "ev1", "classes": [{"id": "c1", "name": "A"}, {"id": "c2", "name": "B"}],
                 "entries": [entry(1)], "stages": stages,
                 "schedule_scheme": {"type": "rounds"},
                 "plans": [{"id": "p1", "order": 1, "gap_ms": 0, "duration_ms": 1000},
                           {"id": "p2", "order": 2, "gap_ms": 0, "duration_ms": 1000}]}
        for stage in stages:
            stage["plan_id"] = "p1" if stage["sequence"] == 1 else "p2"
        rows_out = compute_schedule(event, stages)
        self.assertEqual([row["stage_id"] for row in rows_out], ["s1", "s2", "s3"])

    def test_finals_by_group_orders_finals_before_other_kinds(self):
        stages = [self.stage("s1", 1, kind="qualifying", class_id="c1"),
                  self.stage("s2", 1, kind="final", class_id="c1", group="Grup A"),
                  self.stage("s3", 1, kind="final", class_id="c2", group="Grup B")]
        event = {"id": "ev1", "classes": [{"id": "c1", "name": "A"}, {"id": "c2", "name": "B"}],
                 "entries": [entry(1)], "stages": stages,
                 "schedule_scheme": {"type": "rounds", "finals_by_group": True},
                 "plans": [{"id": "p1", "order": 1, "gap_ms": 0, "duration_ms": 1000}]}
        for stage in stages:
            stage["plan_id"] = "p1"
        rows_out = compute_schedule(event, stages)
        self.assertEqual(rows_out[0]["kind"], "final")


if __name__ == "__main__":
    unittest.main()
