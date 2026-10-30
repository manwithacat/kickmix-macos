# kickmix-render: circuit pictures from the command line

`render.py` turns kickmix circuit files (`.kmb`) into explanatory PNGs and animated GIFs, headless, with one
command per picture. It is written for AI agents as much as for people: every run prints one short JSON summary
on stdout, output is deterministic, and errors say how to fix them. It is one file with no dependencies beyond
numpy, matplotlib and Pillow (all preinstalled on Kaggle), so you can copy it anywhere.

It was built with AI assistance (Claude, Anthropic) under the repository owner's direction, as part of this
openly AI-assisted fork, and is Apache-2.0 like the rest of the repository.

## Quick start (6 commands)

```sh
python render.py doctor                                       # dependencies; kickmix is optional
python render.py width A.kmb B.kmb --labels "63 q,62 q"       # live-qubit profiles, stage-by-stage GIF
python render.py diff  A.kmb B.kmb                            # what changed, aligned by stage
python render.py trace A.kmb --set x=161                      # one shot's qubits and registers
python render.py cost  A.kmb B.kmb                            # Q, Toffolis, cost and score ratio dashboard
python render.py ledger A.kmb                                 # bookkeeping: values, phase debts, capital, close
```

Files go to `./render-out/` (`--out DIR`), named `<A>_vs_<B>_<verb>.png|gif|json|csv` (`--name STEM` to choose).
`python render.py <verb> --help` lists every option with copy-paste examples.

## Verbs

| Verb | Writes | What it shows | Needs |
| --- | --- | --- | --- |
| `width` | PNG, GIF | Live qubits after every op (the certified width), one or two circuits. B is aligned to A stage by stage. Ticks under the curves mark ops at the circuit's full width. The GIF tours the stages with zooms; `--zoom STAGE` or `--zoom a:b` zooms the PNG too. | numpy, matplotlib, Pillow |
| `diff` | PNG, GIF | Stage-aligned structural diff A → B: stages identical up to qubit renaming, changed modules (tagged circuits, compared recursively), and op-level insertions and replacements. The GIF zooms into each edit with op-class barcodes of A and B. | same |
| `trace` | PNG, GIF | One shot in the computational basis: every qubit's value along the circuit, qubits holding 1 against the static width, the phase, and register values (`--show`). `--watch 'S*'` tracks registers that hit 0 (DQI chance-zero failures). | python engine: same. kickmix engine: kickmix |
| `cost` | PNG | Dashboard: Q, Toffolis per shot, cost and score ratio from the official formulas, Toffolis and peak width per stage, Toffolis accumulated along the circuit. | same |
| `ledger` | PNG, CSV | Double-entry view (see [Ledger](#ledger)): T-accounts per register, trial balance at each stage cut, aged liabilities, ANDs paid for twice, closing balance. `--compare A B` reconciles two circuits. | numpy, matplotlib, Pillow |
| `doctor` | none | Python, numpy, matplotlib, Pillow, kickmix, a test render, the cache. | none |

`--spec jobs.json` runs several renders and prints one summary (see [Batch runs](#batch-runs)).

### Stages

Stages come from `--stages`: `auto` (default) uses the circuit's top-level `INFO_TAG "@enter:..."` regions (DQI
circuits have them), and otherwise *rotation-anchored* stages: `pre`, `rot1`, `mid1`, `rot2`, ..., `post`, cut
at clusters of non-Clifford, non-T `Z_POW` rotations (invsqrt's phase steps). Also `tags`, `rot`, `equal:N`, or a
JSON file with `[{"name", "start", "end"}]` (the viewer's `stages.json` works for 9a8627).

### Trace engines

- `python` (default up to 5,000,000 ops): pure Python, any machine. Measurement (HMR) outcomes are `zero` by
  default (the deterministic branch) or seeded random with `--outcomes SEED`. The phase is exact, in units of
  2^-128 turn. Its states match kickmix's at every snapshot on random circuits without measurements (test
  `test_python_tracer_matches_kickmix`), and its final phase matched kickmix on invsqrt x = 161.
- `kickmix`: the kickmix Simulator, run segment by segment on one simulator so every qubit and the phase can be
  read between segments. Required for DQI-size circuits (60 M ops: about 2 s for the shot plus the rendering). If
  the current interpreter lacks kickmix, pass `--kickmix-python /path/to/python` (or set `KICKMIX_PYTHON`): the
  simulation then runs there and only needs kickmix and numpy.

Register inputs: `--set x=161` (repeatable) or `--set-json shot.json` with `{"x": 161}` or
`{"S*": [70 ints], "E*": [4095 ints]}` (`NAME*` fills `NAME0`, `NAME1`, ...). Unset registers start at 0. An
invsqrt circuit with no input defaults to x = 161.

## Output contract (stdout)

Exactly one JSON object per run, at most 2 KB and 20 rows (`--all` prints every row). Keys:

| Key | Meaning |
| --- | --- |
| `ok` | `true`, or `false` with `error` and `hint` (exit code 2 for usage errors, 1 for internal ones) |
| `verb` | `width`, `diff`, `trace`, `cost`, `ledger`, `doctor` or `spec` |
| `tier` | evidence tier of the numbers: `static` (read from the op stream, nothing run), `sampled(n)` (n simulated shots), `exact-all-x` (bit-parallel run over every legal invsqrt input) |
| `basis` | one line on how the numbers were obtained |
| `files` | `[{path, bytes, kind: png|gif|json|csv, frames}]`; the `json` file holds the full table |
| `circuits` | `[{path, sha12, ops, qubits}]` |
| `facts` | key numbers (certified width, Q, Toffolis, cost, score ratio, edits, ...) |
| `rows` / `rows_shown` / `rows_total` | per-stage or per-edit rows, trimmed to fit |
| `rerun` | the command that reproduces this output |
| `tool` | `kickmix-render <version> (<sha12 of render.py>)` |
| `next` | suggested follow-up commands |

Example (`width`, trimmed):

```json
{"ok": true, "verb": "width", "tier": "static", "basis": "lifetime intervals from the op stream ...",
 "files": [{"path": "render-out/9a8627c81283_vs_6dbb98b34c3b_width.png", "bytes": 59821, "kind": "png"}, ...],
 "facts": {"certified_width": [63, 62], "header_qubits": [63, 62], "ops": [45576, 47201], "stages": "rot"},
 "rows": [{"stage": "pre", "ops": 16698, "peak": 63, "at_full_width": 9488, "peak_b": 62, ...}, ...]}
```

## What the numbers mean, and limits

- **Certified width** (`width`, `diff`, `cost`): a value's lifetime starts at a wire's first touch, or its first
  touch after an unconditional `R`/`HMR`, and ends at such a reset or at the wire's last touch; register qubits
  are live throughout. This is the interval rule of the certified lifetime allocator, and the profile equals that
  allocator's profile op for op on 9a8627 (63), 6dbb98 (62) and 40fa4ebf (888). Its maximum is the number of wires
  a lifetime-respecting relabelling needs; Q is the circuit's header `num_qubits`. Conditional resets are not
  credited, so the width can be an overestimate for circuits that release qubits inside conditions.
- **diff**: two ranges are "same" when their op types, angles and the pattern of value and bit reuse match: qubit
  ids, bit ids and the wire a value lives on after a reset may differ. Op-level alignment is a sequence diff of
  relabel-invariant op tokens, so moved blocks show as a deletion plus an insertion; regions with no tags larger
  than `--op-limit` ops are reported whole. Edit counts depend on these choices; Toffoli and HMR deltas over all
  edits add up to the circuit totals.
- **cost**: invsqrt `Q × (Toffolis + (T + rotations × rot_t) / 4)`, DQI `R × Q² × Toffolis`, generic `Q ×
  Toffolis`; score ratio = official reference cost ÷ cost (invsqrt 2,076,774.23, DQI 24,439,463,583,672.93). For
  invsqrt, Toffolis are a sampled per-shot mean (200 random x, python tracer, `--sample`), tier `sampled(200)`;
  `rot_t` defaults to 25 T per rotation, whereas the verifier derives it from the error slack (`--rot-t`). For DQI,
  Toffolis are the static count, an upper bound on the per-shot mean (`--mean-toffolis` to supply a measured one),
  and `R` defaults to 1.1913 (zero failures in 102,400 shots; `--dqi-r`). None of this replaces the official
  verifier.
- **trace** follows one basis-state shot, not a quantum state; with `zero` outcomes it shows the deterministic
  branch. A trace is evidence about that one shot (tier `sampled(1)`).
- **Size and speed** (Apple M2 Pro): invsqrt (45 k ops) every verb in 0.3 to 10 s. DQI (60 M ops): `width` of
  two circuits about 30 s and 2.5 GB peak the first time, then about 2 s (width profiles are cached in
  `~/.cache/kickmix-render`, `--cache DIR`, `--no-cache`); `diff` about 40 s and 7 GB; `trace` (kickmix) about 7 s.
- **GIFs** stay under `--max-mb` (default 5): frames share one palette; if a GIF is too big, it is scaled down,
  then frames are dropped. `--max-frames`, `--frames`, `--tween`, `--hold` and `--px` control length and size.
- Circuit files are untrusted data: `render.py` parses them with bounds checks and never executes anything from
  them. The kickmix engine writes temporary segment files to a private temporary directory.
- Not packaged as a console script: it is one file on purpose, so it can be copied into a notebook.

## Ledger

The user's idea: a reversible circuit has to close its books. Inputs are restored, scratch goes back to |0⟩ and
the phase is right, so the circuit can be read as double-entry bookkeeping.

| Bookkeeping | Circuit, as `ledger` computes it (all static unless marked) |
| --- | --- |
| account | a wire; register wires grouped by register (`S0`..`S69` → `S*`), scratch wires one row each (or 16 bands) |
| entry | an op touching the wire: Toffoli (CCX/CSWAP target), data (CX/X/SWAP target), phase (Z/CZ/CCZ/Z_POW), control, settle (R/HMR) |
| value ("invoice") | one lifetime of a non-register wire, the `width` interval: first touch after an unconditional reset → reset or last touch |
| contra | value closed at its last touch, i.e. erased by unitary uncompute (presumed statically; exact close check for invsqrt) |
| accrual | value closed by HMR whose record later conditions ops (the phase fix-ups); the record is a liability until the last one, followed through `BIT_INVERT_IF`/`BIT_STORE_IF` copies |
| dead tail | value closed by R, or by an HMR whose record nothing reads |
| capital | width priced at the exchange rate: invsqrt (Toffolis + T/4)/Q, DQI 2T/Q (d(R Q² T)/dQ), generic T/Q; `--rate` overrides |
| expense | Toffoli-class ops (static count; invsqrt: exact mean over all x; `--mean-toffolis` to supply one) |
| trial balance | at each stage cut: values open across it, phase debts open across it, live width, plus per stage the values closed by kind, HMR records, fix-ups, the densest fix-up window, Toffolis and capital used |
| aged liabilities | values ranked by their longest stretch with no entry (capital tied up idle), with the ops at full width inside it; the longest-open debts |
| paying twice | a CCX computing the same AND as an earlier CCX. Exact (invsqrt): equal on every x in every outcome model (`--models`, default `zero,7,11`, the outcome RNG of the competition's `qhdl_bitsim`). Static: both controls hold the same wire versions |
| closing balance | exact (invsqrt): x restored and every scratch wire zero on every x, per model; static: records never repaid, values closed by contra |
| reconciliation | `--compare`: stages aligned by name; values matched by signature (settlement, entry counts, span); capital (ΔQ × A's rate) against expense; a one-line reading |

Limits. Balances are per basis trajectory: kickmix runs one basis trajectory per shot, so this is literal there,
but it says nothing about superpositions. A lifetime can hold several logical values when a wire is uncomputed and
reused without a reset (common in DQI), so "value" means lifetime. Phase is not tracked. Non-Clifford rotations
act like revaluations, not exact entries, and the official verifier decides phase within tolerance. The static
paying-twice test only sees repeats whose controls were not written in between, so it is a lower bound. The
signature match in `--compare` is a multiset match, not an alignment (use `diff` for that).

What it found on known circuits (the gate in the design card; 4 of 5 reproduced with no hand analysis):

- invsqrt 9a8627c81283: 300 groups, 317 recomputed ANDs (exact, all x, models zero/7/11), the same numbers as
  the hand-built extra-qubit ledger; mean 1,329.50 Toffolis per shot; the books close in all three models.
  a568bc793481 (yoyobar): 273 groups, 294 recomputed ANDs.
- DQI 40fa4ebf25b2: the top aged value is q847, idle for 44.7 M ops (synthesis → unsynthesis, 4.76 M of them at
  full width), about 749 T-eq of capital tied up. 2ed7ccf189f1 shows the same q847 hold.
- `--compare 7f3d64c1b4aa 40fa4ebf25b2`: "accrual: B releases values by HMR (+256 records, repaid by +208
  fix-ups) and pays +496 T-eq to recompute, to hold 1 wire less"; all of it is in `unextract`.
- `--compare 9a8627c81283 a568bc793481`: re-packing. In `pre`, a568 opens 28 accumulating values (at least two
  Toffoli entries, still open at the cut) before 9a8627 opens its first (op 1,792): 27 lookup outputs built in
  place at ops 348–1,724, plus one front-end wire. It then repays 4 records with 392 fix-ups in one window at
  op 14,401, where 9a8627 has at most 24.
- Not reproduced: the DQI reverse-pass scalar-chain repayments (42,825 T). The ledger lists the 3,521 records
  carried from `extract` to `unextract`, but it cannot price the recomputation behind them. That needs the source
  map.

Cost (Apple M2 Pro): invsqrt about 10 s, of which the exact run is about 5 s (`--no-exact` skips it; the T-account
panel is most of the rest). DQI
(60 M ops) about 18 s and a 3.2 GB peak footprint; `--compare` of two DQI circuits about 25 s, one circuit at a
time. On macOS the script re-executes itself once with `MallocLargeCache=0`, because libmalloc otherwise keeps
about 5 GB of freed large blocks and the footprint reaches 7.6 GB; set `MallocLargeCache` yourself to skip this.

## Batch runs

```json
{"defaults": ["--out", "figs", "--px", "800"],
 "jobs": [["width", "a.kmb", "b.kmb"], "cost a.kmb b.kmb", {"argv": ["trace", "a.kmb", "--set", "x=161"]}]}
```

`python render.py --spec jobs.json` runs every job (one failing job does not stop the others) and prints one
summary whose rows give each job's status, tier and files. `defaults` are appended to every job. `examples/jobs.json`
is the spec that made the examples below: run it from a folder holding `circuits/<sha12>.kmb` and
`dqi_shot_sweep5.json` (with `KICKMIX_PYTHON` set if this Python lacks kickmix); about 2 minutes in all.

## On Kaggle

`render.py` needs only what Kaggle preinstalls. In a notebook cell (internet on, or attach a dataset containing
`render.py` and the first branch picks it up):

```python
import glob, shutil, subprocess, sys
found = glob.glob('/kaggle/input/**/render.py', recursive=True)
if found:
    shutil.copy(found[0], 'render.py')
else:  # pin a release tag or commit instead of main for reproducible figures
    subprocess.run(['wget', '-q', 'https://raw.githubusercontent.com/manwithacat/kickmix-macos/main/'
                    'tools/circuit-viewer/render.py'], check=True)
!python render.py doctor
!python render.py width /kaggle/input/my-circuits/a.kmb /kaggle/input/my-circuits/b.kmb --out /kaggle/working/figs
from IPython.display import Image
Image('/kaggle/working/figs/a_vs_b_width.gif')
```

`trace --engine kickmix` also needs kickmix in the notebook: for example attach the dataset
`manwithacat/kickmix-fork-wheels` and the `kickmix-tools` utility script and run `kickmix_tools.install(...)`
(see the main [README](../../README.md) and the [Kaggle notes](../../kaggle/README.md)).

## Examples

Made with `examples/jobs.json` from public circuits (the `.kmb` files are not in the repository; they are in the
public VQCC circuits datasets on Kaggle):

| Files | Circuits |
| --- | --- |
| `invsqrt_63_vs_62_width.*`, `_diff.*`, `_cost.*` | invsqrt 9a8627c81283 (busyaprime, 63 q) vs 6dbb98b34c3b (Ahmed Berat Ozer, 62 q) |
| `invsqrt_x161_trace.*` | 9a8627c81283, x = 161, python engine |
| `dqi_889_vs_888_width.*`, `_diff.*`, `_cost.*` | DQI 7f3d64c1b4aa (889 q) vs 40fa4ebf25b2 (888 q) |
| `invsqrt_9a8627_ledger.*` | 9a8627c81283 ledger (PNG, per-value CSV, JSON) |
| `invsqrt_63_vs_a568_ledger.*` | reconciliation 9a8627c81283 → a568bc793481 (yoyobar, 62 q) |
| `dqi_40fa4ebf_ledger.*` | 40fa4ebf25b2 ledger (no CSV: 2.9 MB) |
| `dqi_889_vs_888_ledger.*` | reconciliation 7f3d64c1b4aa → 40fa4ebf25b2 |
| `dqi_fail_sweep5_trace.*` | 40fa4ebf25b2 on the inputs of a failing shot (salt `sweep-5`, `dqi_shot_sweep5.json`), kickmix engine, seed 1 |

What they show, briefly: the 62-qubit invsqrt circuit is one wire narrower in every stage, and its diff against
9a8627 is spread through the lookup and the final stage (+104 static Toffolis; the sampled per-shot mean is
almost equal, 1,330.9 vs 1,328.7). The DQI pair differs only in `unconvert` and `unextract`: in each of eight
reverse epochs 40fa4ebf inserts HMR releases and a recomputation (+496 static Toffolis, +256 HMR in all),
which removes the 889-wide stretches.
