"""Evaluation harness: answer-key validation, metrics, sweeps, and the score cache."""

import json

import numpy as np
import pytest

from core.profiles import SHOOT_AXES, SHOOT_WEIGHTS
from scripts import eval_shoot as ev

IDS = [f"DSC{i:04d}.JPG" for i in range(1, 11)]


def _key(*sets):
    return {"shoot": "t", "sets": [dict(zip(("first", "last", "pick"), s, strict=True)) for s in sets]}


# ---------------------------------------------------------------------------
# Answer key
# ---------------------------------------------------------------------------

def test_valid_key_resolves_to_full_ids():
    key = _key(("DSC0001", "DSC0004", "DSC0002"), ("DSC0005", "DSC0005", "DSC0005"),
               ("DSC0006", "DSC0010", "DSC0010"))
    key["sets"][0]["acceptable"] = ["DSC0003"]
    key["photo_count"] = 10
    sets = ev.resolve_key(key, IDS)
    assert [len(s["photo_ids"]) for s in sets] == [4, 1, 5]
    assert sets[0]["pick"] == "DSC0002.JPG" and sets[0]["acceptable"] == ["DSC0003.JPG"]
    assert [pid for s in sets for pid in s["photo_ids"]] == IDS


@pytest.mark.parametrize(
    ("key", "message"),
    [
        (_key(("DSC0001", "DSC0004", "DSC0002"), ("DSC0006", "DSC0010", "DSC0007")), "uncovered"),
        (_key(("DSC0001", "DSC0005", "DSC0002"), ("DSC0005", "DSC0010", "DSC0007")), "overlaps"),
        (_key(("DSC0001", "DSC0008", "DSC0002")), "after the last set"),
        (_key(("DSC0001", "DSC0004", "DSC0009"), ("DSC0005", "DSC0010", "DSC0005")), "outside the set"),
        (_key(("DSC0001", "DSC0099", "DSC0002")), "not in the input folder"),
        (_key(("DSC0004", "DSC0001", "DSC0002")), "before 'first'"),
        ({"shoot": "t", "sets": [{"first": "DSC0001", "last": "DSC0010"}]}, "missing"),
        ({**_key(("DSC0001", "DSC0010", "DSC0002")), "photo_count": 180}, "photo_count"),
    ],
)
def test_invalid_keys_are_rejected(key, message):
    with pytest.raises(ValueError, match=message):
        ev.resolve_key(key, IDS)


def test_key_needs_sets(tmp_path):
    path = tmp_path / "k.json"
    path.write_text(json.dumps({"shoot": "t", "sets": []}))
    with pytest.raises(ValueError, match="non-empty"):
        ev.load_key(path)


# ---------------------------------------------------------------------------
# Boundary metrics
# ---------------------------------------------------------------------------

def _split(*sizes):
    out, start = [], 0
    for n in sizes:
        out.append(IDS[start : start + n])
        start += n
    return out


def test_boundary_metrics_perfect():
    m = ev.boundary_metrics(_split(4, 1, 5), _split(4, 1, 5))
    assert (m["precision"], m["recall"], m["f1"], m["corrections"]) == (1.0, 1.0, 1.0, 0)
    assert m["exact_set_match"] == 1.0 and m["tolerant_f1"] == 1.0


def test_boundary_metrics_count_merges_and_splits():
    # Truth: 4 | 3 | 3 (boundaries after photo 4 and 7).
    # Predicted: 2 | 2 | 6: one false boundary (needs a merge), one missed (needs a split).
    m = ev.boundary_metrics(_split(2, 2, 6), _split(4, 3, 3))
    assert (m["false_boundaries"], m["missed_boundaries"], m["corrections"]) == (1, 1, 2)
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["f1"] == 0.5
    assert m["exact_set_match"] == 0.0


def test_tolerant_f1_forgives_one_frame():
    m = ev.boundary_metrics(_split(5, 5), _split(4, 6))
    assert m["f1"] == 0.0 and m["corrections"] == 2
    assert m["tolerant_f1"] == 1.0
    assert ev.boundary_metrics(_split(7, 3), _split(4, 6))["tolerant_f1"] == 0.0


def test_threshold_sweep_finds_the_better_setting():
    ms = [0, 2_000, 4_000, 44_000, 46_000, 48_000]          # one 40 s pause, low visual change
    measured = [
        {"a": IDS[i], "b": IDS[i + 1], "dt": (ms[i + 1] - ms[i]) / 1000, "dh": 8, "dc": 0.1}
        for i in range(5)
    ]
    truth = [IDS[:3], IDS[3:6]]
    sweep = ev.sweep_thresholds(IDS[:6], measured, truth, "capture_time",
                                {"T_HARD": [30, 90], "MIN_GROUP": [1, 2]})
    assert sweep["combos"] == 4 and sweep["best_corrections"] == 0 and sweep["at_best"] == 2
    by_value = {v["value"]: v for v in sweep["marginals"]["T_HARD"]}
    assert by_value[30]["best"] == 0 and by_value[90]["best"] == 1
    assert all(e["T_HARD"] == 30 for e in sweep["best_examples"])
    # Both MIN_GROUP values reach the best; the default (2) changes less.
    assert sweep["closest_best"] == {"T_HARD": 30, "MIN_GROUP": 2}
    assert sweep["closest_changes"] == 1


def _paced_shoot():
    """
    Two sets shot at a steady 2 s pace, split by a 10 s pause with a framing
    change. Too quick for the fixed T_SOFT of 20 s, but 5x the pace.
    """
    steps = [(2, 3, 0.03)] * 4 + [(10, 20, 0.05)] + [(2, 3, 0.03)] * 4
    measured = [{"a": IDS[i], "b": IDS[i + 1], "dt": dt, "dh": dh, "dc": dc}
                for i, (dt, dh, dc) in enumerate(steps)]
    return IDS, measured, [IDS[:5], IDS[5:]]


def test_time_rule_comparison(monkeypatch):
    monkeypatch.setattr(ev, "FIXED_TIME_GRID", {"T_SOFT": [15, 20]})
    monkeypatch.setattr(ev, "ADAPTIVE_TIME_GRID",
                        {"TIME_RULE": ["adaptive"], "T_MIN": [4, 8], "ADAPT_K": [4], "ADAPT_N": [10]})
    monkeypatch.setattr(ev, "TIME_RULE_SHARED", {"H_LOW": [14, 65]})
    ids, measured, truth = _paced_shoot()
    cmp = ev.compare_time_rules(ids, measured, truth, "capture_time")

    assert cmp["default"]["metrics"]["corrections"] == 1
    assert cmp["fixed"]["metrics"]["corrections"] == 1         # no T_SOFT here catches 10 s
    assert cmp["adaptive"]["metrics"]["corrections"] == 0
    assert cmp["adaptive"]["chosen"] == {"H_LOW": 14, "TIME_RULE": "adaptive", "T_MIN": 8,
                                         "ADAPT_K": 4, "ADAPT_N": 10}
    assert cmp["fixed"]["sweep"]["combos"] == 4 and cmp["adaptive"]["sweep"]["combos"] == 4

    lines: list[str] = []
    ev.report_time_rules(lines, cmp, ev.boundaries_of(truth))
    text = "\n".join(lines)
    assert "| Best adaptive | 4 |" in text
    assert "| DSC0005 → DSC0006 | new set | 10 | 20 | 0.050 | missed | missed | ✓ (pause 8 s) |" in text


# ---------------------------------------------------------------------------
# Pick metrics
# ---------------------------------------------------------------------------

def _photo(pid, face=5.0, expression=5.0, rr=1, face_detected=True, blur=300.0):
    row = {axis: 5.0 for axis in SHOOT_AXES}
    row.update(photo_id=pid, face_sharpness=face, expression=expression, relative_rank=rr,
               face_detected=face_detected, blur_raw=blur, notes="n")
    return row


def _truth_and_scores():
    truth = [
        {"set": 1, "photo_ids": ["a1", "a2", "a3"], "pick": "a1", "acceptable": []},
        {"set": 2, "photo_ids": ["b1", "b2", "b3", "b4"], "pick": "b2", "acceptable": ["b1"]},
    ]
    scored = [
        {"photos": [_photo("a1", face=9, expression=3, rr=2), _photo("a2", face=4, expression=9, rr=1),
                    _photo("a3", face=2, expression=2, rr=3)], "gemini_latency_s": 6.0},
        {"photos": [_photo("b1", face=9, expression=9, rr=1), _photo("b2", face=8, expression=8, rr=2),
                    _photo("b3", face=3, expression=3, rr=3, face_detected=False, blur=40.0),
                    _photo("b4", face=2, expression=2, rr=4)], "gemini_latency_s": 9.0},
    ]
    return truth, scored


def test_pick_metrics():
    truth, scored = _truth_and_scores()
    weights = ev.ablation_weights()

    face = ev.pick_metrics(truth, scored, weights["face_sharpness only"])
    assert face["top1"] == 0.5 and face["top3"] == 1.0          # a1 first; b2 second
    assert face["mrr"] == pytest.approx((1 + 0.5) / 2)
    assert face["lenient_top1"] == 1.0                          # b1 is an acceptable near-tie
    assert face["random_top1"] == pytest.approx((1 / 3 + 1 / 4) / 2)

    gemini = ev.pick_metrics(truth, scored, weights["Gemini axes only"])
    assert gemini["top1"] == 0.0                                # a2 wins on expression

    assert ev.pick_metrics(truth, [scored[0], None])["sets"] == 1   # failed set excluded
    assert ev.pick_metrics(truth, [None, None]) == {"sets": 0}


def test_ablation_weights_sum_to_one():
    for weights in ev.ablation_weights().values():
        assert set(weights) == set(SHOOT_AXES)
        assert sum(weights.values()) == pytest.approx(1.0)
    assert ev.ablation_weights()["SHOOT_WEIGHTS (combined)"] == SHOOT_WEIGHTS
    assert ev.ablation_weights()["Gemini axes only"]["face_sharpness"] == 0.0


def test_weight_grid():
    grid = ev.weight_grid(0.5)
    assert grid.shape == (36, 8)                                # C(2 + 7, 7)
    assert np.allclose(grid.sum(axis=1), 1.0)
    assert ev.weight_grid(0.10).shape == (19_448, 8)
    assert len({tuple(row) for row in grid.tolist()}) == 36
    with pytest.raises(ValueError):
        ev.weight_grid(0.3)


def test_weight_sweep_reports_a_plateau_not_a_point():
    truth, scored = _truth_and_scores()
    sweep = ev.sweep_weights(truth, scored, 0.5)
    assert sweep["sets"] == 2 and sweep["candidates"] == 36
    assert sweep["best_top1"] == 0.5 and sweep["best_top3"] == 1.0
    assert sweep["plateau_share"] == 1.0                        # within one set of best: everything
    # Today's weights are scored too, even though 0.05 values are not on the grid.
    assert sweep["current_top1"] == 0.0 and sweep["current_on_plateau"] is True
    assert ev.pick_metrics(truth, scored)["top1"] == sweep["current_top1"]


def test_mean_scores_averages_runs_and_drops_incomplete_sets():
    _, run1 = _truth_and_scores()
    _, run2 = _truth_and_scores()
    run2[0]["photos"][0]["expression"] = 7
    averaged = ev.mean_scores([run1, run2])
    assert averaged[0]["photos"][0]["expression"] == 5.0        # (3 + 7) / 2
    assert ev.mean_scores([run1, [run2[0], None]])[1] is None


def test_diagnostics():
    truth, scored = _truth_and_scores()
    d = ev.diagnostics(truth, scored)
    assert (d["photos"], d["face_missed"], d["picks_without_face"]) == (7, 1, 0)
    assert d["all_face_sets"] == 1 and d["below_gate"] == 1 and d["picks_below_gate"] == 0
    assert d["latency"] == {3: (1, 6.0, 6.0), 4: (1, 9.0, 9.0)}


def test_e2e_counts_hits_and_shortlist_holes():
    truth, scored = _truth_and_scores()
    # Predicted grouping merged both true sets into one group.
    merged = {"photos": scored[0]["photos"] + scored[1]["photos"]}
    m = ev.e2e_metrics(truth, [truth[0]["photo_ids"] + truth[1]["photo_ids"]], [merged])
    assert m["sets"] == 2 and m["hit_rate"] == 0.0              # b1 wins the merged group
    assert m["holes"] == 1                                      # set 1 has no winner of its own

    exact = ev.e2e_metrics(truth, [s["photo_ids"] for s in truth], scored)
    assert exact["holes"] == 0 and exact["hit_rate"] == 0.0     # a2 and b1 win under SHOOT_WEIGHTS
    assert ev.e2e_metrics(truth, [s["photo_ids"] for s in truth], [None, None])["failed_groups"] == 2


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_cache_round_trip_and_version_check(tmp_path):
    path = tmp_path / "scores.json"
    cache = ev.Cache(path)
    assert cache.get_scores("v", 0, ["a", "b"]) is None
    cache.put_scores("v", 0, ["a", "b"], {"photos": [], "gemini_latency_s": 1.0})

    again = ev.Cache(path)
    assert again.get_scores("v", 0, ["a", "b"]) == {"photos": [], "gemini_latency_s": 1.0}
    assert again.get_scores("v", 1, ["a", "b"]) is None          # another run
    assert again.get_scores("w", 0, ["a", "b"]) is None          # another model or prompt
    assert again.get_scores("v", 0, ["a"]) is None               # another set

    path.write_text(json.dumps({"version": 0}))
    with pytest.raises(ValueError, match="cache version"):
        ev.Cache(path)


def test_cached_sets_are_not_rescored(tmp_path, monkeypatch):
    from core import ingest, shoot

    calls = []

    def fake_score_group(photos, force_spread=True):
        calls.append([p["photo_id"] for p in photos])
        if photos[0]["photo_id"] == "bad1":
            raise RuntimeError("Gemini scoring failed")
        return {"photos": [_photo(p["photo_id"]) for p in photos], "gemini_latency_s": 2.0}

    monkeypatch.setattr(shoot, "score_group", fake_score_group)
    monkeypatch.setattr(
        ingest, "ingest",
        lambda paths: ([{"photo_id": p.name, "path": str(p)} for p in paths], tmp_path / "none"),
    )
    cache = ev.Cache(tmp_path / "scores.json")
    sets = [["a1", "a2"], ["bad1"], ["c1"]]

    first = ev.score_sets(tmp_path, sets, cache, True, 0)
    assert first[0]["photo_count"] == 2 and first[1] is None and first[2] is not None
    assert len(calls) == 3

    second = ev.score_sets(tmp_path, sets, ev.Cache(tmp_path / "scores.json"), True, 0)
    assert second[0] == first[0] and second[1] is None
    assert calls[3:] == [["bad1"]]                               # only the failed set is retried

    ev.score_sets(tmp_path, sets[:1], cache, False, 0)           # spread off is a separate variant
    assert calls[4:] == [["a1", "a2"]]


def test_cached_only_never_scores_and_slices_split_sets(tmp_path, monkeypatch):
    from core import shoot

    def no_gemini(*args, **kwargs):
        raise AssertionError("cached_only must not score")

    monkeypatch.setattr(shoot, "score_group", no_gemini)
    cache = ev.Cache(tmp_path / "scores.json")
    variant = ev.variant_key(True)
    whole = ["p1", "p2", "p3", "p4", "p5"]
    cache.put_scores(variant, 0, whole, {"photos": [_photo(p, rr=i) for i, p in enumerate(whole, 1)],
                                         "gemini_latency_s": 3.0, "photo_count": 5})

    # The key later split the set at p3; p9 was never scored.
    sets = [["p1", "p2"], ["p3", "p4", "p5"], ["p9"], ["p2", "p4"]]
    got = ev.score_sets(tmp_path, sets, cache, True, 0, cached_only=True)
    assert [p["photo_id"] for p in got[1]["photos"]] == ["p3", "p4", "p5"]
    assert got[1]["sliced_from"] == 5 and "gemini_latency_s" not in got[1]
    assert got[0]["photo_count"] == 2
    assert got[2] is None and got[3] is None                    # not contiguous: no slice
    assert ev.score_sets(tmp_path, [whole], cache, True, 0, cached_only=True)[0]["gemini_latency_s"] == 3.0
    assert ev.score_sets(tmp_path, sets[:1], cache, True, 1, cached_only=True) == [None]   # other run


def test_weight_candidates_prefer_the_simplest_within_range(monkeypatch):
    fs, ex = "face_sharpness", "expression"
    monkeypatch.setattr(ev, "WEIGHT_CANDIDATES", [
        ev._weights(face_sharpness=0.5, expression=0.5),
        ev._weights(expression=1.0),
        ev._weights(face_sharpness=1.0),
    ])
    # Set 1: the pick has the best expression. Set 2: the best face. Set 3: both.
    truth = [{"set": i, "pick": f"s{i}a", "acceptable": []} for i in (1, 2, 3)]

    def group(i, a, b):
        return {"photos": [
            {**_photo(f"s{i}a", rr=1), fs: a[0], ex: a[1]},
            {**_photo(f"s{i}b", rr=2), fs: b[0], ex: b[1]},
        ]}

    run = [group(1, (5, 9), (6, 5)), group(2, (9, 5), (5, 6)), group(3, (9, 9), (5, 5))]
    c = ev.candidate_metrics(truth, [run, run])
    assert c["sets"] == 3
    top1 = [r["top1"][0] for r in c["rows"]]
    assert top1 == [pytest.approx(1.0), pytest.approx(2 / 3), pytest.approx(2 / 3)]
    assert [r["within_range"] for r in c["rows"]] == [True, False, False]
    assert c["simplest"]["weights"] == ev.WEIGHT_CANDIDATES[0]
    assert c["relative_rank"]["top1"] == [1.0, 1.0]           # rr=1 is always the pick here
