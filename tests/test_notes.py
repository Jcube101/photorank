"""
The note on a photo without a Gemini note (frontend/src/lib/notes.js), run
under Node. Every burst-mode card shows one, and each must be true for its
photo: only №1 is the best at anything in the set; the rest are described
against №1. Values are compared as the breakdown shows them (one decimal).
"""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from core.profiles import BURST_WEIGHTS

NOTES = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "notes.js"
LABELS = {"face_sharpness": "Face sharpness", "sharpness": "Sharpness",
          "face_exposure": "Face exposure", "exposure": "Exposure",
          "expression": "Expression", "composition": "Composition"}

NODE_SCRIPT = """
const { noteClaims, fallbackNotes } = await import(process.argv[1]);
const fs = await import("node:fs");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
console.log(JSON.stringify(cases.map((c) => ({
  claims: noteClaims(c.photos),
  notes: fallbackNotes(c.photos, { subset: c.subset }),
}))));
"""


def _run(cases: list[dict]) -> list[dict]:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, str(NOTES)],
        input=json.dumps(cases), capture_output=True, text=True, check=True, timeout=60,
    )
    return json.loads(proc.stdout)


def _axes(raws: dict, weights: dict = BURST_WEIGHTS) -> list[dict]:
    """One photo's axes, as buildBreakdown returns them."""
    return [{"key": k, "label": LABELS[k], "raw": raws[k], "weight": w} for k, w in weights.items()]


def _burst(*raws: tuple, subset: bool = False) -> dict:
    """Photos given as (face_sharpness, sharpness, face_exposure, exposure), best first."""
    return {"photos": [_axes(dict(zip(BURST_WEIGHTS, r))) for r in raws], "subset": subset}


def _notes(case: dict) -> list[str]:
    return _run([case])[0]["notes"]


def test_only_the_winner_is_called_strongest():
    # The owner's burst (2026-11-27): face sharpness is every photo's largest
    # contribution (50% weight), so the old note said "Strongest on face
    # sharpness in this set." on all five cards.
    notes = _notes(_burst(
        (8.9, 7.6, 6.1, 6.4),
        (8.4, 7.7, 6.1, 6.4),
        (8.1, 7.6, 6.3, 6.4),
        (7.2, 7.1, 6.0, 6.2),
        (6.9, 7.0, 6.1, 6.6),
    ))
    assert notes == [
        "Sharpest face in this set.",
        "Sharper overall than №1, but softer face.",
        "Better-exposed face than №1, but softer face.",
        "Softer face than №1.",
        "Better exposure than №1, but softer face.",
    ]


def test_ties_and_display_precision():
    # 7.44 and 7.36 both show as 7.4: level, as the user sees them.
    assert _notes(_burst((7.44, 7.0, 6.0, 6.0), (7.36, 7.0, 6.0, 6.0))) == [
        "Joint sharpest face in this set.",
        "Level with №1 on every measure.",
    ]
    # №1 leads on nothing alone; it wins on the combination.
    assert _notes(_burst((8.0, 6.0, 6.0, 6.0), (8.0, 6.0, 6.0, 6.0), (7.0, 7.0, 6.0, 6.0)))[0] == (
        "Joint sharpest face in this set."
    )
    # A lead on a lighter axis beats a tie on a heavier one.
    assert _notes(_burst((8.0, 7.0, 6.0, 6.0), (8.0, 6.0, 6.0, 6.0)))[0] == (
        "Sharpest overall in this set."
    )


def test_best_combined_when_the_winner_tops_no_axis():
    weights = {"face_sharpness": 0.5, "sharpness": 0.5}
    photos = [_axes({"face_sharpness": 8, "sharpness": 8}, weights),
              _axes({"face_sharpness": 9, "sharpness": 5}, weights),
              _axes({"face_sharpness": 5, "sharpness": 9}, weights)]
    assert _notes({"photos": photos, "subset": False})[0] == "Best combined score in this set."


def test_scope_when_the_blur_gate_left_photos_out():
    notes = _notes(_burst((8.9, 7.6, 6.1, 6.4), (8.4, 7.7, 6.1, 6.4), subset=True))
    assert notes[0] == "Sharpest face of the 2 ranked."
    assert _notes(_burst((8.9, 7.6, 6.1, 6.4)))[0] == "The only photo ranked."


def test_set_mode_axes_read_generically():
    weights = {"expression": 0.6, "composition": 0.4}
    photos = [_axes({"expression": 8, "composition": 6}, weights),
              _axes({"expression": 6, "composition": 7}, weights)]
    assert _notes({"photos": photos, "subset": False}) == [
        "Highest expression in this set.",
        "Higher composition than №1, but lower expression.",
    ]


def _check_claims(raws: list[dict], claims: list[dict]) -> None:
    """Every claim holds for its photo, at one decimal."""
    shown = [{k: round(v, 1) for k, v in r.items()} for r in raws]
    top, rest = shown[0], shown[1:]
    first = claims[0]
    if first["kind"] == "lead":
        axis = first["axis"]
        assert all(top[axis] > p[axis] for p in rest)
    elif first["kind"] == "joint":
        axis = first["axis"]
        assert all(top[axis] >= p[axis] for p in rest) and any(top[axis] == p[axis] for p in rest)
        assert not any(all(top[k] > p[k] for p in rest) for k in top)
    else:
        assert first["kind"] == "overall"
        assert not any(all(top[k] >= p[k] for p in rest) for k in top)
    for p, claim in zip(rest, claims[1:]):
        assert claim["kind"] == "vs_top"
        gaps = {k: (p[k] - top[k]) * BURST_WEIGHTS[k] for k in p}
        if claim["worse"] is None:
            assert all(g >= 0 for g in gaps.values())
        else:
            assert p[claim["worse"]] < top[claim["worse"]]
            assert gaps[claim["worse"]] == min(gaps.values())
        if claim["better"] is None:
            assert all(g <= 0 for g in gaps.values())
        else:
            assert p[claim["better"]] > top[claim["better"]]
            assert gaps[claim["better"]] == max(gaps.values())


def test_every_claim_is_true_on_random_bursts():
    rng = random.Random(20261127)
    sets = []
    for _ in range(300):
        n = rng.randint(2, 6)
        coarse = rng.random() < 0.3  # whole numbers, so ties happen
        raws = [{k: float(rng.randint(5, 9)) if coarse else round(rng.uniform(4, 10), 2)
                 for k in BURST_WEIGHTS} for _ in range(n)]
        raws.sort(key=lambda r: -round(sum(r[k] * w for k, w in BURST_WEIGHTS.items()), 3))
        sets.append(raws)
    results = _run([{"photos": [_axes(r) for r in raws], "subset": False} for raws in sets])
    for raws, result in zip(sets, results):
        _check_claims(raws, result["claims"])
        assert result["notes"][0].endswith(" in this set.")
        assert not any("in this set" in note for note in result["notes"][1:])
