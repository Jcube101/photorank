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

## Running

```bash
PYTHONPATH=. ./venv/bin/python scripts/eval_shoot.py \
  --input input/wat_arun --key eval/wat_arun/answer_key.json --stage all
```

| Stage | Calls Gemini? | What it reports |
|---|---|---|
| `grouping` | No | Boundary metrics, distance distributions, threshold sweep |
| `oracle` | Yes: sets × `--runs` × spread variants | Pick accuracy on the true sets, ablations, weight sweep, diagnostics |
| `e2e` | Yes: once per predicted group | Predicted groups, then predicted winners |
| `all` | Yes | Everything |

Raw scores are cached in `output/eval_<shoot>_scores.json` as each set
finishes, so re-running with other weights, or after an interruption, makes no
new Gemini calls. Delete that file to re-score from scratch. The report goes to
`output/eval_<shoot>_report.md`. Both are git-ignored.

Start with `--stage grouping`: it is free and validates the key.
