"""
Builds tests/fixtures/grouping_golden.json.

The golden fixture is the contract shared by core/group.py and the JS port
(frontend/src/lib/group.js, Phase B). Both must reproduce it exactly.

Every case below is synthetic and states its expected outcome BY HAND
(`rules`, `groups`, `reasons`). build() runs the Python reference and refuses
to produce a fixture unless it agrees with those hand-written expectations, so
the fixture is never just a snapshot of whatever the code does.

Regenerate after an intended change (and bump GROUPING_VERSION):

    PYTHONPATH=. ./venv/bin/python tests/fixtures/make_grouping_golden.py

Synthetic features: each photo's dhash differs from the previous photo's by
exactly `dh` bits, and its histogram by exactly `dc` (two-bin histograms whose
split moves in steps of 1/200), so every distance is known exactly.
"""

import json
import sys
from pathlib import Path

from core.group import GROUPING_VERSION, THRESHOLDS, group_photos

FIXTURE_PATH = Path(__file__).with_name("grouping_golden.json")

_BASE_MS   = 1_759_480_000_000
_HIST_UNIT = 200          # histogram total; dc moves in steps of 1/200


def _hist(t: int) -> str:
    return bytes([_HIST_UNIT - t, t] + [0] * 126).hex()


def _photos(steps: list[tuple], *, source: str = "exif_subsec", prefix: str = "DSC") -> list[dict]:
    """
    steps[0] is the first photo: (photo_id,). Each later step is
    (photo_id, dt_seconds, dh_bits, dc) relative to the previous photo.
    """
    photos, ms, dhash, t, bit = [], _BASE_MS, 0, 100, 0
    for i, step in enumerate(steps):
        if i > 0:
            _, dt, dh, dc = step
            ms += round(dt * 1000)
            for _ in range(dh):                 # flip dh fresh bits
                dhash ^= 1 << (bit % 64)
                bit += 1
            delta = round(dc * _HIST_UNIT)
            t = t + delta if t + delta <= _HIST_UNIT else t - delta
            assert 0 <= t <= _HIST_UNIT
        photos.append({
            "photo_id":       f"{prefix}{step[0]}.JPG",
            "captured_at_ms": ms,
            "time_source":    source,
            "dhash":          f"{dhash:016x}",
            "hist":           _hist(t),
        })
    return photos


def _ids(*numbers: str, prefix: str = "DSC") -> list[str]:
    return [f"{prefix}{n}.JPG" for n in numbers]


def _cases() -> list[dict]:
    cases = []

    # -- Every row of the §7.3 table, including exact-threshold values. -----
    cases.append({
        "name": "decision_table_rows",
        "description": "One pair per table row and per threshold edge. MIN_GROUP=1 so "
                       "the post-pass leaves the table's decisions untouched.",
        "thresholds": {"MIN_GROUP": 1},
        "photos": _photos([
            ("0001",),
            ("0002", 5,      3,  0.02),   # 5: quick, similar
            ("0003", 120,    30, 0.40),   # 1: long pause, different
            ("0004", 150,    4,  0.05),   # 2: long pause, near-identical -> same set
            ("0005", 90,     7,  0.05),   # 1: dt == T_HARD, dh just over H_SAME
            ("0006", 45,     14, 0.05),   # 3: medium pause, dh == H_LOW
            ("0007", 45,     5,  0.25),   # 3: medium pause, colour change only
            ("0008", 45,     13, 0.15),   # 5: medium pause, both below LOW
            ("0009", 20,     20, 0.05),   # 3: dt == T_SOFT counts as medium
            ("0010", 19.999, 20, 0.05),   # 5: just under T_SOFT, not a strong change
            ("0011", 3,      30, 0.40),   # 4: quick, strong on both signals
            ("0012", 3,      30, 0.10),   # 5: quick, structure only (FAST_RULE=and)
            ("0013", 3,      10, 0.45),   # 5: quick, colour only (FAST_RULE=and)
        ]),
        "rules":   ["5", "1", "2", "1", "3", "3", "5", "3", "5", "4", "5", "5"],
        "groups":  [_ids("0001", "0002"), _ids("0003", "0004"), _ids("0005"),
                    _ids("0006"), _ids("0007", "0008"), _ids("0009", "0010"),
                    _ids("0011", "0012", "0013")],
        "reasons": ["start", "time_gap", "time_gap", "time+visual", "time+visual",
                    "time+visual", "visual_change"],
    })

    # -- FAST_RULE variants of rule 4 (review G3). ---------------------------
    fast = [
        ("0001",),
        ("0002", 3, 30, 0.10),   # structure only
        ("0003", 3, 10, 0.45),   # colour only
        ("0004", 3, 10, 0.10),   # neither
    ]
    cases.append({
        "name": "fast_rule_or",
        "description": "FAST_RULE=or: a quick follow-up splits on either strong signal.",
        "thresholds": {"MIN_GROUP": 1, "FAST_RULE": "or"},
        "photos": _photos(fast),
        "rules":   ["4", "4", "5"],
        "groups":  [_ids("0001"), _ids("0002"), _ids("0003", "0004")],
        "reasons": ["start", "visual_change", "visual_change"],
    })
    cases.append({
        "name": "fast_rule_dh",
        "description": "FAST_RULE=dh: a quick follow-up splits on structure alone.",
        "thresholds": {"MIN_GROUP": 1, "FAST_RULE": "dh"},
        "photos": _photos(fast),
        "rules":   ["4", "5", "5"],
        "groups":  [_ids("0001"), _ids("0002", "0003", "0004")],
        "reasons": ["start", "visual_change"],
    })

    # -- MIN_GROUP post-pass. -------------------------------------------------
    cases.append({
        "name": "min_group_merges_into_nearer_neighbour",
        "description": "0004 is a singleton between two medium pauses. Distance to the "
                       "previous group is 20 + 64*0.25 = 36, to the next 15 + 64*0.05 = "
                       "18.2, so it joins the next group. 0008 is a singleton with equal "
                       "distances on both sides; the tie goes to the previous group.",
        "thresholds": {},
        "photos": _photos([
            ("0001",),
            ("0002", 2,  3,  0.02),
            ("0003", 2,  3,  0.02),
            ("0004", 45, 20, 0.25),
            ("0005", 45, 15, 0.05),
            ("0006", 2,  3,  0.02),
            ("0007", 2,  3,  0.02),
            ("0008", 45, 16, 0.10),
            ("0009", 45, 16, 0.10),
            ("0010", 2,  3,  0.02),
        ]),
        "rules":   ["5", "5", "3", "3", "5", "5", "3", "3", "5"],
        "groups":  [_ids("0001", "0002", "0003"),
                    _ids("0004", "0005", "0006", "0007", "0008"),
                    _ids("0009", "0010")],
        "reasons": ["start", "time+visual", "time+visual"],
    })
    cases.append({
        "name": "min_group_keeps_isolated_frames",
        "description": "0001 is a first frame followed by a hard gap: kept. 0004 has "
                       "hard gaps on both sides: kept. 0007 is a last frame after a "
                       "medium pause: merged into the previous group.",
        "thresholds": {},
        "photos": _photos([
            ("0001",),
            ("0002", 300, 30, 0.40),
            ("0003", 2,   3,  0.02),
            ("0004", 200, 30, 0.40),
            ("0005", 200, 30, 0.40),
            ("0006", 2,   3,  0.02),
            ("0007", 45,  20, 0.25),
        ]),
        "rules":   ["1", "5", "1", "1", "5", "3"],
        "groups":  [_ids("0001"), _ids("0002", "0003"), _ids("0004"),
                    _ids("0005", "0006", "0007")],
        "reasons": ["start", "time_gap", "time_gap", "time_gap"],
    })

    # -- MAX_GROUP post-pass. -------------------------------------------------
    cases.append({
        "name": "max_group_split_respects_min_group",
        "description": "Nine quick frames, MAX_GROUP=6. The largest distance is at the "
                       "first pair, but cutting there would leave one photo, so it is "
                       "not a candidate. The cut goes to the next largest (before 0007).",
        "thresholds": {"MAX_GROUP": 6},
        "photos": _photos([
            ("0001",),
            ("0002", 2, 22, 0.30),
            ("0003", 2, 3,  0.02),
            ("0004", 2, 3,  0.02),
            ("0005", 2, 3,  0.02),
            ("0006", 2, 3,  0.02),
            ("0007", 2, 12, 0.10),
            ("0008", 2, 3,  0.02),
            ("0009", 2, 3,  0.02),
        ]),
        "rules":   ["5"] * 8,
        "groups":  [_ids("0001", "0002", "0003", "0004", "0005", "0006"),
                    _ids("0007", "0008", "0009")],
        "reasons": ["start", "max_size"],
    })
    cases.append({
        "name": "max_group_split_tie_goes_to_middle",
        "description": "Eight frames with identical distances, MAX_GROUP=6: the tie is "
                       "broken toward the middle of the group, giving 4 + 4.",
        "thresholds": {"MAX_GROUP": 6},
        "photos": _photos([("0001",)] + [(f"{n:04d}", 2, 3, 0.02) for n in range(2, 9)]),
        "rules":   ["5"] * 7,
        "groups":  [_ids("0001", "0002", "0003", "0004"),
                    _ids("0005", "0006", "0007", "0008")],
        "reasons": ["start", "max_size"],
    })
    cases.append({
        "name": "max_group_split_repeats",
        "description": "Fourteen identical-distance frames, MAX_GROUP=4: split in the "
                       "middle (7 + 7), then each half again. A 7 can only be cut 4 + 3 "
                       "or 3 + 4; both are equally near the middle, so the earlier cut "
                       "wins (3 + 4).",
        "thresholds": {"MAX_GROUP": 4},
        "photos": _photos([("0001",)] + [(f"{n:04d}", 2, 3, 0.02) for n in range(2, 15)]),
        "rules":   ["5"] * 13,
        "groups":  [_ids("0001", "0002", "0003"), _ids("0004", "0005", "0006", "0007"),
                    _ids("0008", "0009", "0010"), _ids("0011", "0012", "0013", "0014")],
        "reasons": ["start", "max_size", "max_size", "max_size"],
    })

    # -- Missing data (§7.4, review G1). --------------------------------------
    no_time = _photos([
        ("0001",),
        ("0002", 0, 14, 0.21),   # visual_only: dh == H_LOW, dc just over C_LOW -> boundary
        ("0003", 0, 20, 0.15),   # colour below C_LOW -> no boundary
        ("0004", 0, 13, 0.40),   # structure below H_LOW -> no boundary
        ("0005", 0, 30, 0.40),   # boundary
    ], source="none")
    for photo in no_time:
        photo["captured_at_ms"] = None
    cases.append({
        "name": "no_capture_times",
        "description": "No photo has a capture time: filename order, visual_only rule "
                       "(needs dh >= H_LOW and dc >= C_LOW).",
        "thresholds": {"MIN_GROUP": 1},
        "photos": no_time,
        "order":   "filename",
        "rules":   ["visual_only"] * 4,
        "groups":  [_ids("0001"), _ids("0002", "0003", "0004"), _ids("0005")],
        "reasons": ["start", "visual_only", "visual_only"],
    })

    mixed = _photos([
        ("0001",),
        ("0002", 2,   3,  0.02),
        ("0003", 2,   20, 0.25),   # becomes file_mtime below
        ("0004", 2,   10, 0.10),   # becomes file_mtime below
        ("0005", 2,   20, 0.25),
        ("0006", 120, 30, 0.40),
    ])
    for photo, offset_ms in ((mixed[2], 86_400_000), (mixed[3], 86_400_000 + 200_000)):
        photo["time_source"]    = "file_mtime"
        photo["captured_at_ms"] = _BASE_MS + offset_ms    # a day later: copy time
    cases.append({
        "name": "mixed_clocks_fall_back_to_filename_order",
        "description": "0003 and 0004 only have file mtimes, a day later than the EXIF "
                       "times. Sorting by time would move them to the end. Order is by "
                       "filename instead, and dt is used only where both photos share a "
                       "clock: 0003-0004 (both mtime, 200 s apart -> time_gap) and "
                       "0005-0006 (both EXIF). Pairs across clocks use visual_only.",
        "thresholds": {"MIN_GROUP": 1},
        "photos": mixed,
        "order":   "filename",
        "rules":   ["5", "visual_only", "1", "visual_only", "1"],
        "groups":  [_ids("0001", "0002"), _ids("0003"), _ids("0004"), _ids("0005"),
                    _ids("0006")],
        "reasons": ["start", "visual_only", "time_gap", "visual_only", "time_gap"],
    })

    no_print = _photos([
        ("0001",),
        ("0002", 2,   3, 0.02),
        ("0003", 30,  3, 0.02),    # fingerprint removed below
        ("0004", 120, 3, 0.02),
        ("0005", 2,   3, 0.02),
        ("0006", 2,   3, 0.02),    # fingerprint and time removed below
        ("0007", 2,   3, 0.02),
    ])
    no_print[2]["dhash"] = no_print[2]["hist"] = None
    no_print[5]["dhash"] = no_print[5]["hist"] = None
    no_print[5]["captured_at_ms"] = None
    no_print[5]["time_source"]    = "none"
    cases.append({
        "name": "missing_fingerprints",
        "description": "0003 has no fingerprint, so its pairs are decided on time alone "
                       "(30 s: no boundary; 120 s: time_gap). 0006 has neither time nor "
                       "fingerprint, so its pairs carry no data and never split. One "
                       "missing EXIF time puts the shoot in filename order.",
        "thresholds": {"MIN_GROUP": 1},
        "photos": no_print,
        "order":   "filename",
        "rules":   ["5", "time_only", "time_only", "5", "no_data", "no_data"],
        "groups":  [_ids("0001", "0002", "0003"), _ids("0004", "0005", "0006", "0007")],
        "reasons": ["start", "time_gap"],
    })

    # -- Ordering. ------------------------------------------------------------
    wrap = _photos([
        ("09998",),
        ("09999", 1,   3,  0.02),
        ("00001", 0,   3,  0.02),   # same second as 09999: filename breaks the tie
        ("00002", 1,   3,  0.02),
        ("00003", 100, 30, 0.40),
        ("00004", 1,   3,  0.02),
    ], source="exif")
    shuffled = [wrap[i] for i in (4, 1, 5, 0, 3, 2)]
    cases.append({
        "name": "capture_time_order_survives_counter_wrap",
        "description": "Input arrives shuffled, and the DSC counter wraps from 09999 to "
                       "00001. Capture time is the primary key, so 09998 and 09999 stay "
                       "ahead of 00002. 09999 and 00001 share a timestamp (1 s EXIF "
                       "resolution); the filename breaks that tie, putting 00001 first.",
        "thresholds": {},
        "photos": shuffled,
        "ordered_ids": _ids("09998", "00001", "09999", "00002", "00003", "00004"),
        "groups":  [_ids("09998", "00001", "09999", "00002"), _ids("00003", "00004")],
        "reasons": ["start", "time_gap"],
    })

    return cases


def build() -> dict:
    """Run every case through the reference and check it against its hand-written expectation."""
    out_cases = []
    for case in _cases():
        name   = case["name"]
        result = group_photos(case["photos"], case["thresholds"])

        got_groups  = [g["photo_ids"] for g in result["groups"]]
        got_reasons = [g["boundary_before"]["reason"] for g in result["groups"]]
        got_order   = [pid for ids in got_groups for pid in ids]
        if got_groups != case["groups"]:
            raise AssertionError(f"{name}: groups {got_groups} != expected {case['groups']}")
        if got_reasons != case["reasons"]:
            raise AssertionError(f"{name}: reasons {got_reasons} != expected {case['reasons']}")
        if result["order"] != case.get("order", "capture_time"):
            raise AssertionError(f"{name}: order {result['order']}")
        if "rules" in case and [p["rule"] for p in result["pairs"]] != case["rules"]:
            raise AssertionError(
                f"{name}: rules {[p['rule'] for p in result['pairs']]} != expected {case['rules']}"
            )
        if "ordered_ids" in case and got_order != case["ordered_ids"]:
            raise AssertionError(f"{name}: order {got_order} != expected {case['ordered_ids']}")

        out_cases.append({
            "name":        name,
            "description": case["description"],
            "thresholds":  case["thresholds"],
            "photos":      case["photos"],
            "expected": {
                "order":       result["order"],
                "ordered_ids": got_order,
                "pairs": [
                    {
                        "a": p["a"], "b": p["b"],
                        "dt_s": p["dt"], "dh": p["dh"], "dc": p["dc"],
                        "boundary": p["boundary"], "reason": p["reason"], "rule": p["rule"],
                        "final_boundary": p["final_boundary"],
                        "final_reason":   p["final_reason"],
                    }
                    for p in result["pairs"]
                ],
                "groups": [
                    {
                        "group_id":        g["group_id"],
                        "photo_ids":       g["photo_ids"],
                        "boundary_before": g["boundary_before"],
                    }
                    for g in result["groups"]
                ],
            },
        })

    return {
        "_comment": "Generated by tests/fixtures/make_grouping_golden.py. Do not edit by "
                    "hand. core/group.py and frontend/src/lib/group.js must both reproduce "
                    "every case exactly, and their constants must equal the thresholds "
                    "and grouping_version below. dc is compared to 1e-9.",
        "grouping_version": GROUPING_VERSION,
        "thresholds":       THRESHOLDS,
        "cases":            out_cases,
    }


if __name__ == "__main__":
    FIXTURE_PATH.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {FIXTURE_PATH} ({len(_cases())} cases)", file=sys.stderr)
