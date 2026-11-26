"""Shoot weights, the Gemini prompt switch, batch_size, and per-group ranking."""

import hashlib

import pytest

from core import profiles, score_vision, shoot
from core.profiles import SHOOT_AXES, SHOOT_WEIGHTS, validate_shoot_weights

# sha256 of _SYSTEM_PROMPT on main before Phase A. Set mode must keep sending
# exactly this prompt.
V1_SYSTEM_PROMPT_SHA256 = "ab4676c6725c16277607731e3c02dad12c01dba21f2cce36776605d1aadbd219"


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

# The plan's initial weights (§6.3), used by the §8.1 worked example below.
INITIAL_SHOOT_WEIGHTS = {
    "face_sharpness": 0.30, "expression": 0.30, "subject_focus": 0.10,
    "composition": 0.10, "camera_engagement": 0.10, "exposure": 0.05,
    "sharpness": 0.05, "face_exposure": 0.00,
}


def test_shoot_weights_match_the_plan():
    # Chosen 2026-11-26 from the Wat Arun evaluation (V2-PLAN §6.3).
    assert SHOOT_WEIGHTS == {
        "face_sharpness": 0.10, "expression": 0.30, "subject_focus": 0.30,
        "composition": 0.30, "camera_engagement": 0.00, "exposure": 0.00,
        "sharpness": 0.00, "face_exposure": 0.00,
    }
    assert set(SHOOT_WEIGHTS) == set(SHOOT_AXES) == set(profiles.SHOOT_AXIS_SOURCES)
    assert sum(SHOOT_WEIGHTS.values()) == pytest.approx(1.0)


def test_shoot_weight_validation():
    validate_shoot_weights(SHOOT_WEIGHTS)
    with pytest.raises(ValueError, match="sum to 1.0"):
        validate_shoot_weights({**SHOOT_WEIGHTS, "expression": 0.35})
    missing = {k: v for k, v in SHOOT_WEIGHTS.items() if k != "face_exposure"}
    with pytest.raises(ValueError, match="missing axes"):
        validate_shoot_weights(missing)
    with pytest.raises(ValueError, match="unknown axes"):
        validate_shoot_weights({**SHOOT_WEIGHTS, "eye_openness": 0.0})
    with pytest.raises(ValueError, match="negative"):
        validate_shoot_weights({**SHOOT_WEIGHTS, "exposure": -0.05, "sharpness": 0.15})


def test_v1_profiles_and_burst_weights_unchanged():
    assert profiles.ALL_AXES == [
        "sharpness", "exposure", "expression", "composition", "subject_focus", "camera_engagement",
    ]
    assert profiles.PROFILES["family"] == {
        "expression": 0.25, "camera_engagement": 0.20, "subject_focus": 0.20,
        "sharpness": 0.19, "composition": 0.10, "exposure": 0.06,
    }
    assert profiles.BURST_WEIGHTS == {
        "face_sharpness": 0.50, "sharpness": 0.20, "face_exposure": 0.20, "exposure": 0.10,
    }
    for weights in profiles.PROFILES.values():
        profiles.validate_weights(weights)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def test_set_mode_prompt_is_byte_identical_to_v1():
    digest = hashlib.sha256(score_vision._SYSTEM_PROMPT.encode()).hexdigest()
    assert digest == V1_SYSTEM_PROMPT_SHA256
    assert score_vision.build_system_instruction("family") == (
        score_vision._SYSTEM_PROMPT + "\n\nScoring profile context: family"
    )
    travel = score_vision.build_system_instruction("travel")
    assert travel.endswith(score_vision._PROFILE_HINTS["travel"])
    assert "Scoring profile context: travel" in travel


def test_shoot_prompt_addendum_and_spread_switch():
    spread_on  = score_vision.build_system_instruction(prompt_mode="shoot", force_spread=True)
    spread_off = score_vision.build_system_instruction(prompt_mode="shoot", force_spread=False)

    for prompt in (spread_on, spread_off):
        assert prompt.endswith(score_vision._SHOOT_ADDENDUM)
        assert "consecutive frames of a single set" in prompt
        assert "Scoring profile context" not in prompt
        assert "Do NOT assess technical sharpness" in prompt       # still never asked
        assert '"relative_rank"' in prompt and "AGAINST EACH OTHER" in prompt

    assert "At least one photo must score below 5" in spread_on
    assert "At least one photo must score" not in spread_off
    assert "Do NOT invent differences" in spread_off
    assert spread_on == score_vision._SYSTEM_PROMPT + "\n\n" + score_vision._SHOOT_ADDENDUM


def test_prompt_mode_is_validated():
    with pytest.raises(ValueError, match="prompt_mode"):
        score_vision.build_system_instruction(prompt_mode="burst")
    with pytest.raises(ValueError, match="force_spread"):
        score_vision.build_system_instruction("family", force_spread=False)


# ---------------------------------------------------------------------------
# batch_size (no network: the Gemini client and batch call are stubbed)
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_gemini(monkeypatch):
    calls = {"batches": [], "system": []}

    def fake_score_batch(client, config, image_paths, photo_ids, retries=2, strict_ids=False):
        calls["batches"].append(list(photo_ids))
        calls.setdefault("strict_ids", []).append(strict_ids)
        calls["system"].append(config.system_instruction)
        return [
            {"photo_id": pid, "subject_1_expression": 7, "subject_2_expression": None,
             "expression": 7.0, "camera_engagement": 8, "composition": 6,
             "subject_focus": 7, "relative_rank": i + 1, "notes": "ok"}
            for i, pid in enumerate(photo_ids)
        ]

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(score_vision, "_score_batch", fake_score_batch)
    monkeypatch.setattr(score_vision.genai, "Client", lambda api_key: object())
    return calls


def _paths(n):
    return [f"/nonexistent/{i:02d}.jpg" for i in range(n)]


def test_default_batch_size_is_still_eight(fake_gemini):
    scores = score_vision.score_photos(_paths(20))
    assert sorted(len(b) for b in fake_gemini["batches"]) == [4, 8, 8]
    assert [s["photo_id"] for s in scores] == [f"{i:02d}.jpg" for i in range(20)]
    assert all(s.endswith("Scoring profile context: family") for s in fake_gemini["system"])


def test_batch_size_puts_a_whole_group_in_one_call(fake_gemini):
    score_vision.score_photos(_paths(20), batch_size=20, prompt_mode="shoot")
    assert [len(b) for b in fake_gemini["batches"]] == [20]
    assert fake_gemini["system"][0].endswith(score_vision._SHOOT_ADDENDUM)

    with pytest.raises(ValueError, match="batch_size"):
        score_vision.score_photos(_paths(3), batch_size=0)


# ---------------------------------------------------------------------------
# Per-group scoring and ranking
# ---------------------------------------------------------------------------

def _scored(pid, face_sharpness=8.0, expression=7.0, relative_rank=1, face_detected=True):
    return {
        "photo_id": pid, "sharpness": 4.0, "exposure": 6.0, "blur_raw": 40.0,
        "tenengrad_raw": 600.0, "face_detected": face_detected,
        "face_sharpness": face_sharpness, "face_exposure": 6.5, "face_blur_raw": 400.0,
        "subject_1_expression": expression, "subject_2_expression": None,
        "expression": expression, "camera_engagement": 9, "composition": 7,
        "subject_focus": 8, "relative_rank": relative_rank, "notes": "n",
    }


def test_rank_group_matches_the_plan_example():
    # docs/V2-PLAN.md §8.1 worked example.
    photo = _scored("DSC01007.JPG", face_sharpness=8.41, expression=8)
    photo.update(sharpness=4.12, exposure=6.80, face_exposure=6.95)
    ranked = shoot.rank_group([photo], INITIAL_SHOOT_WEIGHTS)
    top = ranked[0]
    # 2.523 + 2.400 + 0.800 + 0.700 + 0.900 + 0.340 + 0.206 + 0.000
    assert top["final_score"] == 7.869
    assert top["final_rank"] == 1
    assert list(top["score_breakdown"]) == SHOOT_AXES
    assert top["score_breakdown"]["face_sharpness"] == {
        "raw": 8.41, "weight": 0.30, "effective_weight": 0.30,
        "contribution": 2.523, "source": "deterministic (face crop)",
    }
    assert top["score_breakdown"]["expression"]["source"] == "gemini"
    assert top["score_breakdown"]["face_exposure"]["contribution"] == 0.0
    assert "final_score" not in photo            # input is not modified

    # Today's defaults: 0.841 + 2.400 + 2.400 + 2.100; zero-weight axes add nothing
    # but stay in the breakdown.
    top = shoot.rank_group([photo])[0]
    assert top["final_score"] == 7.741
    assert list(top["score_breakdown"]) == SHOOT_AXES
    assert top["score_breakdown"]["camera_engagement"]["contribution"] == 0.0


def test_rank_group_orders_by_score_then_relative_rank():
    scored = [
        _scored("soft.JPG",  face_sharpness=5.0, relative_rank=1),
        _scored("tie_b.JPG", face_sharpness=8.0, relative_rank=3),
        _scored("tie_a.JPG", face_sharpness=8.0, relative_rank=2),
    ]
    ranked = shoot.rank_group(scored)
    assert [r["photo_id"] for r in ranked] == ["tie_a.JPG", "tie_b.JPG", "soft.JPG"]
    assert [r["final_rank"] for r in ranked] == [1, 2, 3]

    only_face = {axis: 0.0 for axis in SHOOT_AXES} | {"face_sharpness": 1.0}
    assert shoot.rank_group(scored, only_face)[0]["final_score"] == 8.0
    with pytest.raises(ValueError):
        shoot.rank_group(scored, {**only_face, "expression": 0.5})


def test_group_result_shape():
    ranked = shoot.rank_group([_scored("a.JPG"), _scored("b.JPG", 9.0, face_detected=False)])
    result = shoot.group_result("g03", ranked)
    assert result["group_id"] == "g03" and result["status"] == "scored"
    assert result["photo_count"] == 2 and result["face_detected_count"] == 1
    assert result["winner_photo_id"] == "b.JPG" == result["ranked"][0]["photo_id"]


def _stub_burst(monkeypatch):
    from core import score_burst

    def fake_burst(path, photo_id=None):
        row = _scored(photo_id)
        return {k: row[k] for k in ("photo_id", *shoot._BURST_FIELDS)} | {"path": path}

    monkeypatch.setattr(score_burst, "compute_burst_scores", fake_burst)


def test_score_group_is_one_gemini_call_without_spread_for_a_single_photo(monkeypatch, fake_gemini):
    _stub_burst(monkeypatch)
    photos = [{"photo_id": f"{i}.JPG", "path": f"/x/{i}.jpg"} for i in range(11)]

    scored = shoot.score_group(photos, force_spread=True)
    assert [len(b) for b in fake_gemini["batches"]] == [11]
    assert "At least one photo must score below 5" in fake_gemini["system"][0]
    assert [p["photo_id"] for p in scored["photos"]] == [p["photo_id"] for p in photos]
    assert "path" not in scored["photos"][0] and "gemini_latency_s" in scored
    assert set(SHOOT_AXES) <= set(scored["photos"][0])

    shoot.score_group(photos[:1], force_spread=True)
    assert "At least one photo must score" not in fake_gemini["system"][1]


def test_score_group_fails_whole_group_when_gemini_drops_a_photo(monkeypatch, fake_gemini):
    _stub_burst(monkeypatch)
    real = score_vision._score_batch
    monkeypatch.setattr(
        score_vision, "_score_batch",
        lambda *a, **k: real(*a, **k)[:-1],
    )
    photos = [{"photo_id": f"{i}.JPG", "path": f"/x/{i}.jpg"} for i in range(3)]
    with pytest.raises(RuntimeError, match="2 of 3"):
        shoot.score_group(photos)


def test_score_group_labels_photos_by_position_and_maps_them_back(monkeypatch, fake_gemini):
    _stub_burst(monkeypatch)
    real = score_vision._score_batch

    def reply_in_rank_order(*args, **kwargs):
        rows = real(*args, **kwargs)
        rows[0]["notes"] = "warmer smile than photo_3 and photo_2"
        return rows[::-1]                     # Gemini often lists the best photo first

    monkeypatch.setattr(score_vision, "_score_batch", reply_in_rank_order)
    photos = [{"photo_id": f"DSC0000{n}.JPG", "path": f"/x/{n}.jpg"} for n in range(5, 10)]
    scored = shoot.score_group(photos)

    assert fake_gemini["batches"] == [["photo_1", "photo_2", "photo_3", "photo_4", "photo_5"]]
    assert fake_gemini["strict_ids"] == [True]
    assert [p["photo_id"] for p in scored["photos"]] == [p["photo_id"] for p in photos]
    assert [p["relative_rank"] for p in scored["photos"]] == [1, 2, 3, 4, 5]
    assert scored["photos"][0]["notes"] == "warmer smile than DSC00007.JPG and DSC00006.JPG"


_ROW = ('{{"photo_id": "{}", "subject_1_expression": 7, "subject_2_expression": null, '
        '"camera_engagement": 8, "composition": 6, "subject_focus": 7, "relative_rank": {}, '
        '"notes": "ok"}}')


def _reply(*ids):
    return "[" + ", ".join(_ROW.format(pid, i) for i, pid in enumerate(ids, 1)) + "]"


def test_strict_ids_reject_a_reply_that_names_the_wrong_photos():
    expected = ["photo_1", "photo_2", "photo_3"]
    assert len(score_vision._parse_scores(_reply("photo_3", "photo_1", "photo_2"), 3, expected)) == 3
    with pytest.raises(ValueError, match=r"missing \['photo_3'\], unexpected \['DSC00003.JPG'\]"):
        score_vision._parse_scores(_reply("photo_1", "photo_2", "DSC00003.JPG"), 3, expected)
    with pytest.raises(ValueError, match="2 objects for 3 photos"):
        score_vision._parse_scores(_reply("photo_1", "photo_2"), 2, expected)
    with pytest.raises(ValueError, match="wrong photos"):
        score_vision._parse_scores(_reply("photo_1", "photo_1", "photo_2"), 3, expected)
    # Without expected ids (set mode) the reply is not checked, as before.
    assert len(score_vision._parse_scores(_reply("photo_1", "x"), 2)) == 2


class _FakeClient:
    """Returns the given reply texts in turn."""

    def __init__(self, *texts):
        self.texts, self.calls = list(texts), 0
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls += 1
        return type("Response", (), {"text": self.texts.pop(0)})()


def test_strict_ids_retry_a_mislabelled_reply(monkeypatch, tmp_path):
    monkeypatch.setattr(score_vision.time, "sleep", lambda s: None)
    image = tmp_path / "a.jpg"
    image.write_bytes(b"\xff\xd8")
    paths, ids = [image, image], ["photo_1", "photo_2"]

    client = _FakeClient(_reply("photo_1", "DSC00002.JPG"), _reply("photo_2", "photo_1"))
    rows = score_vision._score_batch(client, None, paths, ids, strict_ids=True)
    assert client.calls == 2 and sorted(r["photo_id"] for r in rows) == ids

    client = _FakeClient(*[_reply("photo_1", "DSC00002.JPG")] * 3)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        score_vision._score_batch(client, None, paths, ids, strict_ids=True)

    # Set mode keeps today's behaviour: one call, reply accepted as it is.
    client = _FakeClient(_reply("photo_1", "DSC00002.JPG"))
    assert len(score_vision._score_batch(client, None, paths, ids)) == 2
    assert client.calls == 1


def test_run_shoot_keeps_going_when_one_group_fails(monkeypatch, fake_gemini):
    from core import group as group_module

    _stub_burst(monkeypatch)
    features = [
        {"photo_id": f"DSC{i:04d}.JPG", "captured_at_ms": ms, "time_source": "exif",
         "dhash": "0" * 16 if i < 3 else "f" * 16, "hist": "ff" + "00" * 127}
        for i, ms in enumerate([0, 1000, 2000, 400_000, 401_000, 402_000])
    ]
    monkeypatch.setattr(group_module, "extract_features", lambda photos: features)

    real = score_vision._score_batch

    def flaky(client, config, image_paths, photo_ids, retries=2, strict_ids=False):
        if len(image_paths) == 3 and image_paths[0] == "/x3":
            raise RuntimeError("Gemini scoring failed after 3 attempts")
        return real(client, config, image_paths, photo_ids, retries, strict_ids)

    monkeypatch.setattr(score_vision, "_score_batch", flaky)

    photos = [{"photo_id": f["photo_id"], "path": f"/x{i}", "original_path": "/y"}
              for i, f in enumerate(features)]
    output = shoot.run_shoot(photos)

    assert output["mode"] == "shoot" and output["weights"] == SHOOT_WEIGHTS
    assert (output["groups_total"], output["groups_scored"], output["groups_failed"]) == (2, 1, 1)
    assert "pairs" not in output["grouping"]
    assert output["grouping"]["groups"][1]["boundary_before"]["reason"] == "time_gap"

    ok, failed = output["groups"]
    assert ok["status"] == "scored" and ok["winner_photo_id"] == "DSC0000.JPG"
    assert failed["status"] == "failed" and "ranked" not in failed       # no default scores
    assert failed["error"]["code"] == "scoring_failed"
    shoot.print_summary(output)


def test_shoot_mode_drops_forced_spread_by_default(monkeypatch, fake_gemini):
    # V2-PLAN Q6, decided 2026-11-26: off in shoot mode, opt-in with --force-spread.
    _stub_burst(monkeypatch)
    photos = [{"photo_id": f"{i}.JPG", "path": f"/x/{i}.jpg"} for i in range(4)]
    shoot.score_group(photos)
    shoot.score_group(photos, force_spread=True)
    assert "At least one photo must score" not in fake_gemini["system"][0]
    assert "Do NOT invent differences" in fake_gemini["system"][0]
    assert "At least one photo must score below 5" in fake_gemini["system"][1]
    # Set mode always forces spread.
    score_vision.score_photos(_paths(2))
    assert "At least one photo must score below 5" in fake_gemini["system"][2]
