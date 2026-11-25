"""
Shoot labeller: export validation, edits, page data and images.

The page's logic is scripts/labeller/core.js. These tests run that file under
Node, so they check exactly what the page runs, and compare its key
validation with scripts/eval_shoot.py:resolve_key(), which the evaluation
applies to the exported file.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from core.group import group_photos
from core.profiles import SHOOT_AXES
from scripts import eval_shoot as ev
from scripts import label_shoot as ls

CORE_JS = Path(ls.__file__).parent / "labeller" / "core.js"

# Reads [fn, args] JSON lines, replies {"ok": result} or {"error": text}.
NODE_BRIDGE = r"""
const core = require(process.argv[1]);
const rl = require("readline").createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const [fn, args] = JSON.parse(line);
  let reply;
  try { reply = { ok: core[fn](...args) }; }
  catch (e) { reply = { error: String((e && e.stack) || e) }; }
  process.stdout.write(JSON.stringify(reply === undefined ? null : reply) + "\n");
});
"""


@pytest.fixture(scope="module")
def js():
    """Call a core.js function: js("buildKey", meta, ids, state)."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    proc = subprocess.Popen(
        ["node", "-e", NODE_BRIDGE, str(CORE_JS)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )

    def call(fn, *args):
        proc.stdin.write(json.dumps([fn, list(args)]) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            raise AssertionError("node exited")
        reply = json.loads(line)
        if "error" in reply:
            raise AssertionError(reply["error"])
        return reply["ok"]

    yield call
    proc.stdin.close()
    proc.wait(timeout=5)


def ids(*numbers):
    return [f"DSC{n:05d}.JPG" for n in numbers]


IDS  = ids(*range(1, 11))          # ten photos
META = {"shoot": "t"}
# Sets DSC00001–03, DSC00004–07, DSC00008–10.
THREE_SETS = ids(4, 8)


def _sets(*entries):
    return [dict(zip(("first", "last", "pick"), e, strict=True)) for e in entries]


def _key(*entries, **extra):
    return {"shoot": "t", **extra, "sets": _sets(*entries)}


# ---------------------------------------------------------------------------
# Export validation
# ---------------------------------------------------------------------------

def test_complete_labels_export_a_key_the_harness_accepts(js):
    state  = {"starts": THREE_SETS, "picks": ids(2, 5, 10)}
    result = js("buildKey", {"shoot": "t", "camera": "cam"}, IDS, state)
    assert result["problems"] == []
    assert result["key"] == {
        "shoot": "t", "camera": "cam", "photo_count": 10,
        "sets": [
            {"set": 1, "first": "DSC00001", "last": "DSC00003", "pick": "DSC00002"},
            {"set": 2, "first": "DSC00004", "last": "DSC00007", "pick": "DSC00005"},
            {"set": 3, "first": "DSC00008", "last": "DSC00010", "pick": "DSC00010"},
        ],
    }
    resolved = ev.resolve_key(result["key"], IDS)
    assert [s["photo_ids"] for s in resolved] == [IDS[0:3], IDS[3:7], IDS[7:10]]
    assert [s["pick"] for s in resolved] == ids(2, 5, 10)


def test_camera_is_left_out_when_not_given(js):
    key = js("buildKey", {"shoot": "t", "camera": None}, IDS, {"starts": [], "picks": ids(3)})["key"]
    assert "camera" not in key
    assert key["sets"] == [{"set": 1, "first": "DSC00001", "last": "DSC00010", "pick": "DSC00003"}]


def test_formatted_key_is_the_same_json_one_line_per_set(js):
    key  = js("buildKey", META, IDS, {"starts": THREE_SETS, "picks": ids(2, 5, 10)})["key"]
    text = js("formatKey", key)
    assert json.loads(text) == key
    assert sum(1 for line in text.splitlines() if '"pick"' in line) == 3


def test_a_set_without_a_pick_blocks_export(js):
    result = js("buildKey", META, IDS, {"starts": THREE_SETS, "picks": ids(2, 10)})
    assert [p["set"] for p in result["problems"]] == [2]
    assert "DSC00004–DSC00007" in result["problems"][0]["message"]
    assert result["key"]["sets"][1]["pick"] is None
    with pytest.raises(ValueError):
        ev.resolve_key(result["key"], IDS)


def test_every_unpicked_set_is_reported(js):
    result = js("buildKey", META, IDS, {"starts": THREE_SETS, "picks": []})
    assert [p["set"] for p in result["problems"]] == [1, 2, 3]


def test_two_picks_in_one_set_block_export(js):
    # The UI never makes this, but saved progress could be edited by hand.
    result = js("buildKey", META, IDS, {"starts": THREE_SETS, "picks": ids(2, 3, 5, 10)})
    assert [p["set"] for p in result["problems"]] == [1]
    assert "2 picks" in result["problems"][0]["message"]


def test_a_one_photo_set_exports(js):
    result = js("buildKey", META, IDS, {"starts": ids(4, 5), "picks": ids(1, 4, 9)})
    assert result["problems"] == []
    assert result["key"]["sets"][1] == {"set": 2, "first": "DSC00004", "last": "DSC00004", "pick": "DSC00004"}
    ev.resolve_key(result["key"], IDS)


# Keys the harness must reject. The page's validateKey() must reject each too.
BAD_KEYS = {
    "gap":                _key(("DSC00001", "DSC00003", "DSC00002"), ("DSC00005", "DSC00010", "DSC00006")),
    "overlap":            _key(("DSC00001", "DSC00004", "DSC00002"), ("DSC00004", "DSC00010", "DSC00006")),
    "tail uncovered":     _key(("DSC00001", "DSC00003", "DSC00002"), ("DSC00004", "DSC00008", "DSC00006")),
    "pick outside set":   _key(("DSC00001", "DSC00003", "DSC00005"), ("DSC00004", "DSC00010", "DSC00006")),
    "unknown photo":      _key(("DSC00001", "DSC00010", "DSC00099")),
    "last before first":  _key(("DSC00003", "DSC00001", "DSC00002"), ("DSC00004", "DSC00010", "DSC00006")),
    "photo_count wrong":  _key(("DSC00001", "DSC00010", "DSC00002"), photo_count=9),
    "no sets":            {"shoot": "t", "sets": []},
    "missing pick":       {"shoot": "t", "sets": [{"first": "DSC00001", "last": "DSC00010"}]},
    "null pick":          {"shoot": "t", "sets": [{"first": "DSC00001", "last": "DSC00010", "pick": None}]},
    "acceptable outside": {"shoot": "t", "sets": [
        {"first": "DSC00001", "last": "DSC00005", "pick": "DSC00002", "acceptable": ["DSC00007"]},
        {"first": "DSC00006", "last": "DSC00010", "pick": "DSC00008"},
    ]},
}

GOOD_KEYS = {
    "one set":            _key(("DSC00001", "DSC00010", "DSC00004")),
    "with photo_count":   _key(("DSC00001", "DSC00003", "DSC00001"), ("DSC00004", "DSC00010", "DSC00010"),
                               photo_count=10),
    "one-photo set":      _key(("DSC00001", "DSC00001", "DSC00001"), ("DSC00002", "DSC00010", "DSC00005")),
    "acceptable inside":  {"shoot": "t", "sets": [
        {"first": "DSC00001", "last": "DSC00005", "pick": "DSC00002", "acceptable": ["DSC00003"]},
        {"first": "DSC00006", "last": "DSC00010", "pick": "DSC00008"},
    ]},
}


@pytest.mark.parametrize("name", sorted(BAD_KEYS))
def test_page_and_harness_reject_the_same_bad_keys(js, name):
    assert js("validateKey", BAD_KEYS[name], IDS) != []
    with pytest.raises(ValueError):
        ev.resolve_key(BAD_KEYS[name], IDS)


@pytest.mark.parametrize("name", sorted(GOOD_KEYS))
def test_page_and_harness_accept_the_same_good_keys(js, name):
    assert js("validateKey", GOOD_KEYS[name], IDS) == []
    ev.resolve_key(GOOD_KEYS[name], IDS)


def test_page_and_harness_reject_photos_that_share_a_name(js):
    photos = ["A.JPG", "A.png", "B.JPG"]
    key    = {"shoot": "t", "sets": [{"first": "A", "last": "B", "pick": "B"}]}
    assert "share the name" in js("validateKey", key, photos)[0]["message"]
    with pytest.raises(ValueError):
        ev.resolve_key(key, photos)


def test_problems_point_at_the_set_they_are_about(js):
    problems = js("validateKey", BAD_KEYS["pick outside set"], IDS)
    assert [p["set"] for p in problems] == [1]
    assert js("validateKey", BAD_KEYS["tail uncovered"], IDS)[0]["set"] is None


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------

def test_split_starts_a_new_set_that_needs_its_own_pick(js):
    state = js("split", {"starts": THREE_SETS, "picks": ids(2, 5, 10)}, IDS, "DSC00006.JPG")
    assert state == {"starts": ids(4, 6, 8), "picks": ids(2, 5, 10)}
    assert [p["set"] for p in js("buildKey", META, IDS, state)["problems"]] == [3]
    state = js("choosePick", state, IDS, "DSC00007.JPG")
    assert js("buildKey", META, IDS, state)["problems"] == []


def test_split_on_the_first_photo_or_an_existing_start_changes_nothing(js):
    state = {"starts": THREE_SETS, "picks": []}
    assert js("split", state, IDS, "DSC00001.JPG") == state
    assert js("split", state, IDS, "DSC00004.JPG") == state


def test_merge_keeps_a_lone_pick(js):
    merged = js("mergeWithPrevious", {"starts": THREE_SETS, "picks": ids(2, 10)}, IDS, "DSC00004.JPG")
    assert merged == {"starts": ids(8), "picks": ids(2, 10)}
    assert js("buildKey", META, IDS, merged)["problems"] == []


def test_merge_of_two_picked_sets_asks_for_a_new_pick(js):
    merged = js("mergeWithPrevious", {"starts": THREE_SETS, "picks": ids(2, 5, 10)}, IDS, "DSC00004.JPG")
    assert merged == {"starts": ids(8), "picks": ids(10)}
    assert [p["set"] for p in js("buildKey", META, IDS, merged)["problems"]] == [1]


def test_merge_on_a_photo_that_does_not_start_a_set_changes_nothing(js):
    state = {"starts": THREE_SETS, "picks": ids(2)}
    assert js("mergeWithPrevious", state, IDS, "DSC00005.JPG") == state


def test_choosing_a_pick_replaces_only_that_sets_pick(js):
    state = {"starts": THREE_SETS, "picks": ids(2, 5, 10)}
    assert js("choosePick", state, IDS, "DSC00006.JPG")["picks"] == ids(2, 6, 10)


def test_corrections_count_merges_and_splits_against_the_proposal(js):
    assert js("corrections", THREE_SETS, THREE_SETS) == {"merges": 0, "splits": 0, "total": 0}
    assert js("corrections", THREE_SETS, ids(4, 6)) == {"merges": 1, "splits": 1, "total": 2}


def test_saved_progress_is_cleaned_against_the_current_photos(js):
    saved = {
        "starts": ["DSC00008.JPG", "DSC00001.JPG", "GONE.JPG", "DSC00004.JPG", "DSC00004.JPG"],
        "picks":  ["GONE.JPG", "DSC00002.JPG"],
    }
    assert js("normalize", saved, IDS) == {"starts": THREE_SETS, "picks": ids(2)}
    assert js("normalize", None, IDS) == {"starts": [], "picks": []}
    assert js("normalize", {"starts": "junk"}, IDS) == {"starts": [], "picks": []}


# ---------------------------------------------------------------------------
# Page data and HTML
# ---------------------------------------------------------------------------

def _feature(n, seconds, scene):
    return {
        "photo_id":       f"DSC{n:05d}.JPG",
        "captured_at_ms": 1_000_000 + seconds * 1000,
        "time_source":    "exif",
        "dhash":          "0" * 16 if scene == 0 else "f" * 16,
        "hist":           ("ff" + "00" * 127) if scene == 0 else ("00" * 127 + "ff"),
    }


def _shoot():
    """Two scenes 200 s apart, given out of order."""
    features = [_feature(1, 0, 0), _feature(2, 2, 0), _feature(3, 4, 0),
                _feature(4, 204, 1), _feature(5, 206, 1)]
    return features[::-1]


def _page(shoot="t", has_large=True):
    features = _shoot()
    grouping = group_photos(features)
    sizes    = {Path(f["photo_id"]).stem: (40, 60) for f in features}
    return ls.page_data(grouping, features, shoot, None, sizes, has_large, "2026-11-25 10:00"), grouping


def _keys(obj):
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def test_page_data_follows_shooting_order_and_the_proposal():
    data, grouping = _page()
    assert [p["id"] for p in data["photos"]] == ids(1, 2, 3, 4, 5)
    assert [p["start"] for p in data["proposal"]] == ["DSC00001.JPG", "DSC00004.JPG"]
    assert data["proposal"][1]["reason"] == "time_gap"
    assert [p["gap_s"] for p in data["photos"]] == [None, 2.0, 2.0, 200.0, 2.0]
    assert data["photos"][0]["thumb"] == "thumbs/DSC00001.jpg"
    assert data["photos"][0]["large"] == "large/DSC00001.jpg"
    assert data["photos"][0]["time"] == "00:16:40"
    assert data["order"] == grouping["order"] == "capture_time"


def test_page_data_carries_no_scores_or_rankings():
    data, _ = _page()
    keys = _keys(data)
    assert not keys & {*SHOOT_AXES, "notes", "score_breakdown", "relative_rank"}
    assert not [k for k in keys if "score" in k or "rank" in k]


def test_page_without_large_images_falls_back_to_thumbnails():
    data, _ = _page(has_large=False)
    assert all(p["large"] is None for p in data["photos"])


def test_page_is_self_contained_and_data_cannot_break_out_of_its_script():
    shoot = "</script><script>alert(1)</script>"
    data, _ = _page(shoot=shoot)
    page = ls.render_html(data)

    assert "<script>alert(1)" not in page
    assert page.count("</script>") == 3          # data, core.js, app.js
    match = re.search(r'<script type="application/json" id="labeller-data">(.*?)</script>', page, re.DOTALL)
    assert json.loads(match.group(1)) == data
    assert "<link" not in page
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page)
    assert not re.search(r"/\*__\w+__\*/|__(DATA|TITLE|SHOOT_PATH|GENERATED|GROUPING_VERSION)__", page)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def _jpeg(path, size=(60, 40), orientation=None, taken=None):
    img = Image.new("RGB", size, (0, 0, 255))
    img.paste((255, 0, 0), (0, 0, size[0] // 4, size[1] // 4))     # red block, top left
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    if orientation:
        exif[0x0112] = orientation
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
    img.save(path, exif=exif)
    return path


def test_make_image_applies_orientation_and_writes_no_exif(tmp_path):
    src  = _jpeg(tmp_path / "a.jpg", orientation=6)     # 6: rotate 90° clockwise to display
    dest = tmp_path / "out" / "a.jpg"
    assert ls.make_image(src, dest, 30, 80) == (20, 30)
    with Image.open(dest) as im:
        assert im.size == (20, 30)
        assert len(im.getexif()) == 0 and "exif" not in im.info
        r, _, b = im.convert("RGB").getpixel((im.width - 2, 1))
    assert r > 150 and b < 100                          # top left moved to top right


def test_make_image_never_upscales(tmp_path):
    assert ls.make_image(_jpeg(tmp_path / "a.jpg"), tmp_path / "b.jpg", 400, 80) == (60, 40)


def test_build_images_redoes_only_changed_photos_and_removes_old_ones(tmp_path):
    paths    = [_jpeg(tmp_path / f"p{i}.jpg") for i in range(3)]
    out      = tmp_path / "thumbs"
    manifest = {}
    sizes, written = ls.build_images(paths, out, 32, 80, manifest)
    assert written == 3 and sizes == {"p0": (32, 21), "p1": (32, 21), "p2": (32, 21)}

    assert ls.build_images(paths, out, 32, 80, manifest)[1] == 0
    os.utime(paths[1], ns=(1, 1))
    assert ls.build_images(paths, out, 32, 80, manifest)[1] == 1
    assert ls.build_images(paths, out, 40, 80, manifest)[1] == 3     # new size

    (out / "mine.jpg").write_bytes(b"not ours")
    ls.build_images(paths[:2], out, 40, 80, manifest)
    assert sorted(f.name for f in out.iterdir()) == ["mine.jpg", "p0.jpg", "p1.jpg"]
    assert sorted(manifest) == ["p0", "p1"]


def test_main_builds_the_page(tmp_path):
    shoot = tmp_path / "my_shoot"
    shoot.mkdir()
    for i, taken in enumerate(["2026:01:01 10:00:00", "2026:01:01 10:00:02", "2026:01:01 10:05:00"], 1):
        _jpeg(shoot / f"DSC{i:05d}.JPG", taken=taken)
    out  = tmp_path / "labeller"
    args = ["--input", str(shoot), "--out", str(out), "--cache", str(tmp_path / "cache.json"),
            "--thumb", "40", "--large", "80"]

    assert ls.main(args) == 0
    page = (out / "index.html").read_text()
    data = json.loads(re.search(r'id="labeller-data">(.*?)</script>', page, re.DOTALL).group(1))
    assert data["shoot"] == "my_shoot"
    assert [p["id"] for p in data["photos"]] == ids(1, 2, 3)
    assert len(list((out / "thumbs").glob("*.jpg"))) == 3
    assert len(list((out / "large").glob("*.jpg"))) == 3
    assert "features" in json.loads((tmp_path / "cache.json").read_text())

    assert ls.main([*args[:-2], "--large", "0"]) == 0
    assert list((out / "large").glob("*.jpg")) == []
    page = (out / "index.html").read_text()
    assert '"large":null' in page


def test_main_rejects_photos_that_share_a_name(tmp_path, capsys):
    shoot = tmp_path / "s"
    shoot.mkdir()
    _jpeg(shoot / "A.jpg")
    Image.new("RGB", (8, 8)).save(shoot / "A.png")
    assert ls.main(["--input", str(shoot), "--out", str(tmp_path / "o")]) == 1
    assert "share a name" in capsys.readouterr().err
