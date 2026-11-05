# Agent notes for kickmix-macos

This fork packages kickmix (Google Quantum AI's circuit simulator and verifier) for macOS; see
[README.md](README.md) for the build. This file covers the agent-facing **circuit workbench** in
`tools/circuit-viewer/`: read-only, bounded answers about large `.kmb` circuits and exact checks for
bilinear (field multiplier) components. It locates and checks; it does not score. The official verifier
decides correctness and cost.

## Interpreter

Run everything through the launcher from the fork root:

```sh
tools/circuit-viewer/workbench --help          # every verb, grouped by question
tools/circuit-viewer/workbench VERB --help     # flags for one verb
```

It uses the fork's `.venv` (NumPy). In a git worktree without a `.venv`, set `WORKBENCH_PYTHON` to an
interpreter with NumPy, for example another checkout's `.venv/bin/python`. Do not use system Python.
The `bilinear-*` launchers are aliases of `workbench bilinear-*`.

## Question → verb

| Question | Command | Tier |
| --- | --- | --- |
| How big is this circuit; which stages hold the Toffolis? | `workbench summarize C.kmb --out r.json`, then `workbench query r.json --depth 0 --sort toffoli` | exact static counts |
| Which repeated sub-stage costs most, summed over instances? | `workbench query r.json --depth 1 --group path` or `--match '^scan/' --group path` | exact static counts |
| What gates sit at op N? | `workbench cut C.kmb N --radius 4` | exact |
| What changed between two versions? | `workbench compare old.json new.json --out d.json`, then `workbench query d.json` | exact static deltas, not per-shot |
| Is this KMB a correct GF(2^n) multiplier, and how many products? | `workbench bilinear-cert --challenge manifest.json --id gf16-06 --out cert.json --tensor-out tensor.json` | exact (byte certificate) |
| Can local rewrites cut its Toffolis? | `workbench bilinear-corner` / `bilinear-mechanisms` / `bilinear-carrier` with the same target flags and `--candidate-out` | exact moves, greedy choice |
| Minimum products for one output bit? | `workbench bilinear-project cert.json --output-mask 0x1 --out p.json` | exact rank |
| Tensor invariants, zero divisors, isotopy to a field? | `workbench bilinear-structure tensor.json --out s.json` (or `--modulus 0x13`), `workbench bilinear-isotopy` | exact |
| Turn a term list into a KMB | `workbench bilinear-synth terms.json --out c.kmb`, then `bilinear-cert` on it | precheck; certify after |
| Compare sampled DQI cut dumps | `trace-diff`, `trace-query`, `trace-plan`, `state-map*`, `stage-contract` | sampled / heuristic |

Ranked lists (`idle`-style screens, `trace-plan`, `state-map` fits, greedy rewrite order) are heuristics;
a byte certificate or the official verifier is the evidence. `lower_bound_proved: false` means no
optimality claim.

## Output discipline

- Every verb prints one JSON object on stdout. Follow its `next` list; each entry runs on that output.
- Errors are `{"error": ..., "hint": ...}` with a non-zero exit. Read the hint before retrying.
- Never `cat` a report: the frontier DQI report is about 2.8 MB. Use `query` (default 20 rows, at most 100).
- Outputs are never overwritten; pick a new `--out` path.
- Never copy a 500+ MB DQI `.kmb`; pass its path.

## Tests

```sh
.venv/bin/python -m pytest -q tests/test_workbench*.py tests/test_bilinear_*.py
```

`tests/test_workbench_cli.py` runs every README and `--help` example against small fixtures and fails on
an error or an empty result. Set `VQCC_CIRCUITS_DIR` to a folder with `dqi/bdee18489fac.kmb` and
`invsqrt/bce2948ff24e.kmb` to add the frontier-circuit checks.

For VQCC competition work, the cross-tool map (qhdl in the competition repo plus this workbench) is `docs/agent-circuit-tools.md` in the competition repository.
