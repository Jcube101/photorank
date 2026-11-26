# Answer keys for full shoot evaluation

One folder per labelled shoot: `eval/<shoot>/answer_key.json`. A key holds
filenames only, never image data. The photos themselves stay in git-ignored
`input/`. See `docs/V2-PLAN.md` §12 for the metrics.

## Format

```json
{
  "shoot": "wat_arun_2026",
  "camera": "Sony A7 III, 85mm f/1.4",
  "photo_count": 180,
  "sets": [
    { "set": 1, "first": "DSC01001", "last": "DSC01012", "pick": "DSC01007" },
    { "set": 2, "first": "DSC01013", "last": "DSC01020", "pick": "DSC01016",
      "acceptable": ["DSC01018"] }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `shoot` | yes | Short name. Used for the default cache and report filenames. |
| `camera` | no | Free text, for the record. |
| `photo_count` | no | If present, must equal the number of photos in the input folder. |
| `sets[].set` | no | Your own label for the set. Used in error messages. |
| `sets[].first`, `sets[].last` | yes | First and last photo of the set, as filenames without the extension. |
| `sets[].pick` | yes | The one frame you would post. Must be inside the set. |
| `sets[].acceptable` | no | Near-ties you would also accept. Only feeds the separate "lenient top-1" metric. |

## Labelling rules

- Sets must cover every photo in the folder, in shooting order, with no gaps
  and no overlaps. The harness checks this and lists every problem it finds.
- Exactly one `pick` per set.
- A new set is any photo you might post alongside the others as its own
  photo. The same spot with a framing change (full-length to headshot) is a
  **new** set.
- A set may be a single photo (`first` equals `last`).

"Shooting order" is capture time when every photo has an EXIF time, otherwise
filename order. The report states which was used.

## Building a key with the labeller

Labelling a whole shoot from scratch is the chore PhotoRank exists to remove.
The labeller starts from the engine's proposed sets instead, so you only fix
what it got wrong and choose each set's pick.

```bash
PYTHONPATH=. ./venv/bin/python scripts/label_shoot.py \
  --input input/wat_arun --out output/labeller/ --camera "Sony A7 III, 85mm f/1.4"
python3 -m http.server 8765 --bind 127.0.0.1 --directory output/labeller
```

Open `http://127.0.0.1:8765/`. Over VS Code Remote-SSH the port is forwarded
to your laptop. If it isn't, add 8765 in the Ports panel.

- One row per proposed set, in shooting order, with the reason the engine
  started it (`time_gap`, `time+visual`, …) and the gap to the previous photo.
- Tap a photo to make it the set's pick. **✂ Split** starts a new set at a
  photo. **Merge with previous** joins a set to the one above it. **⤢** opens a
  large view (← → move, P pick, S split, Esc close). Undo is Ctrl+Z.
- No AI scores or rankings are on the page, so they can't bias the picks.
- The header counts your corrections to the proposal: merges plus splits,
  the same "corrections needed" the evaluation reports.
- Progress is kept in the browser's localStorage, so a reload loses nothing.
- **Export** checks that the sets are contiguous, that they cover every photo,
  and that each set has one pick. It then downloads `answer_key.json`. Put it
  at `eval/<shoot>/answer_key.json`: drag it into VS Code's Explorer, or use
  Copy JSON.

The shoot name defaults to the input folder's name (`wat_arun`). The
labeller reads and saves grouping features in `output/eval_<shoot>_scores.json`.
The evaluation uses the same file when the key's `shoot` matches, so the shoot
is fingerprinted only once. Thumbnails (400 px) and viewer images (1600 px) are
written to `output/labeller/` with orientation applied and no EXIF. `output/`
is git-ignored. Delete `output/labeller/` when the key is done.

The labeller can't mark `acceptable` near-ties. Add them by hand if you want
the lenient metric.

## Running

```bash
PYTHONPATH=. ./venv/bin/python scripts/eval_shoot.py \
  --input input/wat_arun --key eval/wat_arun/answer_key.json --stage all
```

| Stage | Calls Gemini? | What it reports |
|---|---|---|
| `grouping` | No | Boundary metrics, distance distributions, threshold sweep, fixed vs adaptive time rule |
| `oracle` | Yes: sets × `--runs` × spread variants | Pick accuracy on the true sets, ablations, weight candidates, weight sweep, diagnostics |
| `e2e` | Yes: once per predicted group | Predicted groups, then predicted winners |
| `all` | Yes | Everything |

`--spread` defaults to `off`, the shoot-mode default since Q6. `--spread both`
scores both prompt variants and lists `off` first; the sweep, weight
candidates, diagnostics and end-to-end sections use that first variant.

Raw scores are cached in `output/eval_<shoot>_scores.json` as each set
finishes, so re-running with other weights, or after an interruption, makes no
new Gemini calls. Delete that file to re-score from scratch. The report goes to
`output/eval_<shoot>_report.md`. Both are git-ignored.

Start with `--stage grouping`: it is free and validates the key.

### Without Gemini: `--cached-only`

```bash
PYTHONPATH=. ./venv/bin/python scripts/eval_shoot.py \
  --input input/wat_arun --key eval/wat_arun/answer_key.json \
  --stage oracle --spread off --cached-only
```

`--cached-only` never calls Gemini, so weights can be compared on the scores
already cached. A set the key splits after it was scored is re-ranked from the
cached group that holds it, the same way the app re-ranks a split (V2-PLAN
§10.4). Any other set that isn't cached is left out, and the report lists it.

The weight-candidates table scores a few round weight sets from
`WEIGHT_CANDIDATES` in `scripts/eval_shoot.py`. It uses the sets that have
scores in every run. A candidate is "within range" when its min–max over runs
overlaps the best candidate's on both top-1 and top-3. The report names the
simplest such candidate. With about 30 sets, one set is worth 3 points, which
is less than Gemini's run-to-run range, so prefer that candidate over the
sweep's single best.
