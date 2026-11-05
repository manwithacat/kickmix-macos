# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""The workbench CLI as an agent meets it: documented examples, `next` hints and JSON errors.

Every README and `workbench --help` example runs against small fixtures and must succeed with a
non-empty answer; examples that need sampled DQI dumps or an agent's decisions are parsed only.
Set VQCC_CIRCUITS_DIR (a folder holding dqi/bdee18489fac.kmb and invsqrt/bce2948ff24e.kmb) to add
checks on the frontier circuits; they are read in place, never copied.
"""
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/circuit-viewer"
WORKBENCH = TOOLS / "workbench.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(TOOLS))
import workbench as wb  # noqa: E402
from bilinear_basis_explorer import apply  # noqa: E402
from bilinear_cli import VERBS  # noqa: E402
from bilinear_synth import field_mul  # noqa: E402

# Verbs whose examples need sampled DQI dumps, an engine package or an agent's
# decisions file: their examples are checked for valid verbs and flags only.
PARSE_ONLY = {"trace-diff", "trace-query", "trace-plan", "prefix", "sample-dqi", "state-map",
              "state-map-query", "state-map-compare", "stage-contract", "wire-transport",
              "transport-compare"}
PLACEHOLDERS = ("CORE.kmb", "DECISIONS.json")


def tagged_kmb(path: Path, extra_ccx: int = 0) -> None:
    """Three qubits, tag tree synthesis | scan/step x3 | unextract/chart, CCX payloads."""
    plan = [("synthesis", 2), ("scan", [("step", 1)] * 3), ("unextract", [("chart", 5 + extra_ccx)])]
    ops, tags = [], []

    def emit(name, body):
        ops.append(6); tags.append(f"@enter:{name}".encode())
        if isinstance(body, int):
            ops.extend([49] * body)
        else:
            for child, inner in body:
                emit(child, inner)
        ops.append(6); tags.append(f"@exit:{name}".encode())

    for name, body in plan:
        emit(name, body)
    nccx = ops.count(49)
    chars = b"".join(tags)
    ends = []
    total = 0
    for t in tags:
        total += len(t); ends.append(total)
    data = bytearray(wb.MAGIC)
    data += struct.pack("<4I", 2, 3, 0, 0)
    data += struct.pack("<9Q", len(ops), nccx, 0, 0, 0, 0, 0, len(tags), len(chars))
    data += struct.pack("<2Q", 1, len(ops)) + bytes(ops)
    for kind, wire in ((2, 0), (3, 1), (4, 2)):
        data += struct.pack("<2Q", kind, 4 * nccx) + struct.pack(f"<{nccx}I", *([wire] * nccx))
    for kind in range(5, 11):
        data += struct.pack("<2Q", kind, 0)
    data += struct.pack("<2Q", 11, 8 * (len(tags) + 1) + len(chars))
    data += struct.pack("<Q", len(tags)) + struct.pack(f"<{len(ends)}Q", *ends) + chars
    path.write_bytes(bytes(data))


def run(args, cwd: Path, env=None):
    proc = subprocess.run([sys.executable, str(WORKBENCH), *args], cwd=cwd, capture_output=True,
                          text=True, env=env)
    assert "Traceback" not in proc.stderr, proc.stderr
    try:
        obj = json.loads(proc.stdout)
    except json.JSONDecodeError:
        obj = None              # --help text
    return proc.returncode, obj, proc


def stage(tmp: Path) -> Path:
    """Put every file the documented examples name into one folder."""
    tagged_kmb(tmp / "circuit.kmb")
    sha = wb.digest(tmp / "circuit.kmb")
    (tmp / "context.json").write_text(json.dumps({
        "circuit_sha256": sha,
        "boundaries": {"reverse_entry": {"op": 3, "tier": "test fixture"}}}))
    tagged_kmb(tmp / "older.kmb")
    tagged_kmb(tmp / "newer.kmb", extra_ccx=2)
    for name in ("older", "newer"):
        rc, out, proc = run(["summarize", f"{name}.kmb", "--out", f"{name}.json"], tmp)
        assert rc == 0, proc.stdout
    shutil.copyfile(FIXTURES / "gf16_schoolbook.kmb", tmp / "gf16.kmb")
    shutil.copyfile(FIXTURES / "gf16_karatsuba.kmb", tmp / "core.kmb")
    (tmp / "challenge").mkdir()
    shutil.copyfile(FIXTURES / "gf16_schoolbook.kmb", tmp / "challenge/gf16-06.kmb")
    (tmp / "challenge/manifest.json").write_text(json.dumps({
        "schema": "bilinear-challenge-v1", "instances": [{
            "id": "gf16-06", "kmb": "gf16-06.kmb", "sha256": wb.digest(tmp / "challenge/gf16-06.kmb"),
            "width": 4, "modulus": 0x13, "qubits": 12, "start_ccx": 16,
            "proven_bilinear_target": 9}]}))
    a, b, out = [3, 2, 4, 8], [1, 6, 4, 8], [1, 2, 12, 8]
    table = [apply(out, field_mul(apply(a, 1 << i), apply(b, 1 << j), 4, 0x13))
             for i in range(4) for j in range(4)]
    (tmp / "target.json").write_text(json.dumps({"width": 4, "modulus": 0x13, "basis_products": table}))
    (tmp / "candidates").mkdir()
    for name in ("gf16_schoolbook.kmb", "gf16_karatsuba.kmb"):
        shutil.copyfile(FIXTURES / name, tmp / "candidates" / name)
    return tmp


def examples(text: str, prefixes: tuple[str, ...]) -> list[list[str]]:
    joined = re.sub(r"\\\n\s*", " ", text)
    found = []
    for line in joined.splitlines():
        line = line.strip()
        for prefix in prefixes:
            if line.startswith(prefix + " "):
                found.append(shlex.split(line[len(prefix):]))
    return found


def readme_examples() -> list[list[str]]:
    text = (TOOLS / "README.md").read_text()
    blocks = "\n".join(re.findall(r"```sh\n(.*?)```", text, re.S))
    return examples(blocks, ("tools/circuit-viewer/workbench",))


def help_examples() -> list[list[str]]:
    rc, _, proc = run(["--help"], ROOT)
    assert rc == 0
    return examples(proc.stdout, ("workbench",))


def check_answer(argv, rc, out, proc):
    assert rc == 0, f"{argv}: {proc.stdout}"
    assert isinstance(out, dict) and "error" not in out, f"{argv}: {out}"
    if "rows" in out:
        assert out["rows"], f"{argv} returned zero rows"
    if "matched" in out:
        assert out["matched"] > 0, f"{argv} matched nothing"
    assert isinstance(out.get("next", []), list)


def run_examples(argvs, tmp: Path) -> int:
    """Run each example in tmp/work; run its `next` hints at once in a snapshot of that state."""
    work = stage(tmp / "work")
    parser = wb.build_parser()
    followed = 0
    assert argvs
    for i, argv in enumerate(argvs):
        parser.parse_args(argv)          # valid verb and flags, or SystemExit
        if argv[0] in PARSE_ONLY or argv[:2] == ["bilinear-sieve", "finalize"]:
            continue
        rc, out, proc = run(argv, work)
        check_answer(argv, rc, out, proc)
        if out.get("next"):
            snap = tmp / f"next-{i}"
            shutil.copytree(work, snap)
            followed += run_next(out["next"], snap)
    return followed


def run_next(hints, tmp: Path) -> int:
    ran = 0
    for cmd in dict.fromkeys(hints):
        if any(p in cmd for p in PLACEHOLDERS):
            continue
        argv = shlex.split(cmd)
        assert argv[0] == "workbench", cmd
        rc, out, proc = run(argv[1:], tmp)
        check_answer(argv, rc, out, proc)
        ran += 1
    return ran


def test_readme_examples_run_and_their_next_hints_work(tmp_path):
    argvs = readme_examples()
    verbs = {argv[0] for argv in argvs}
    assert {"summarize", "query", "cut", "bilinear-cert", "bilinear-structure",
            "bilinear-carrier", "bilinear-isotopy", "bilinear-synth"} <= verbs
    (tmp_path / "work").mkdir()
    assert run_examples(argvs, tmp_path) >= 10


def test_help_examples_run_and_their_next_hints_work(tmp_path):
    argvs = help_examples()
    assert len(argvs) >= 6
    (tmp_path / "work").mkdir()
    assert run_examples(argvs, tmp_path) >= 5


def test_help_lists_every_verb_including_folded_bilinear_tools():
    rc, _, proc = run(["--help"], ROOT)
    assert rc == 0
    for verb in [*VERBS, "summarize", "query", "cut", "compare", "boundary", "trace-diff"]:
        assert verb in proc.stdout, verb
    for launcher in TOOLS.glob("bilinear-*"):
        assert launcher.name in VERBS, launcher.name


def test_query_group_path_and_depth_semantics(tmp_path):
    tagged_kmb(tmp_path / "c.kmb")
    run(["summarize", "c.kmb", "--out", "r.json"], tmp_path)
    rc, out, _ = run(["query", "r.json", "--match", "^scan/", "--group", "path"], tmp_path)
    assert rc == 0 and out["matched"] == 1
    row = out["rows"][0]
    assert row["path"] == "scan/step" and row["instances"] == 3 and row["metrics"]["toffoli"] == 3
    rc, out, _ = run(["query", "r.json", "--depth", "0"], tmp_path)
    assert [r["name"] for r in out["rows"]] == ["unextract", "scan", "synthesis"]
    rc, out, _ = run(["query", "r.json", "--match", "^unextract", "--depth", "2"], tmp_path)
    assert rc == 0 and out["matched"] == 0
    assert out["depths_present"] == {"0": 3, "1": 4} and "hint" in out


def test_field_case_structure_and_carrier_without_hand_written_tensor(tmp_path):
    stage(tmp_path)
    rc, out, _ = run(["bilinear-structure", "--modulus", "0x13", "--out", "s.json"], tmp_path)
    assert rc == 0 and out["zero_pairs"] == 0 and out["minimum_left_rank"] == 4
    rc, out, _ = run(["bilinear-carrier", "gf16.kmb", "--modulus", "0x13", "--out", "c.json"], tmp_path)
    assert rc == 0 and out["input_ccx"] == 16
    rc, out, _ = run(["bilinear-cert", "--challenge", "challenge", "--id", "gf16-06",
                      "--out", "cert.json", "--tensor-out", "t.json"], tmp_path)
    assert rc == 0 and out["layout_source"] == "KMB registers a, b, t"
    tensor = json.loads((tmp_path / "t.json").read_text())
    assert tensor["basis_products"] == [field_mul(1 << i, 1 << j, 4, 0x13)
                                        for i in range(4) for j in range(4)]
    rc, out, _ = run(["bilinear-structure", "t.json", "--out", "s2.json"], tmp_path)
    assert rc == 0 and out["flattening_ranks"] == {"left": 4, "right": 4, "output": 4}


def test_challenge_layout_without_named_registers(tmp_path):
    """Contract layout (a, b, t contiguous) is read from the manifest, not guessed."""
    stage(tmp_path)
    manifest = json.loads((tmp_path / "challenge/manifest.json").read_text())
    manifest["instances"][0]["registers"] = {"a": [0, 1, 2, 3], "b": "4:8", "target": "8:12"}
    (tmp_path / "challenge/manifest.json").write_text(json.dumps(manifest))
    rc, out, _ = run(["bilinear-cert", "--challenge", "challenge/manifest.json", "--id", "gf16-06",
                      "--out", "cert.json"], tmp_path)
    assert rc == 0 and out["registers"] == {"a": "0:4", "b": "4:8", "target": "8:12"}


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    return stage(tmp_path_factory.mktemp("errors"))


@pytest.mark.parametrize("argv,needle", [
    (["bilinear-cert", "gf16.kmb", "--modulus", "0x19", "--out", "x.json"], "tensor mismatch"),
    (["bilinear-cert", "gf16.kmb", "--modulus", "0x7", "--out", "x.json"], "degree"),
    (["bilinear-cert", "gf16.kmb", "--modulus", "zz", "--out", "x.json"], "--modulus"),
    (["bilinear-cert", "gf16.kmb", "--out", "x.json"], "modulus unknown"),
    (["bilinear-cert", "missing.kmb", "--modulus", "0x13", "--out", "x.json"], "not found"),
    (["bilinear-cert", "gf16.kmb", "--modulus", "0x13", "--a", "0:4", "--b", "4:8",
      "--target", "8:99", "--out", "x.json"], "bad register range"),
    (["bilinear-cert", "gf16.kmb", "--modulus", "0x13", "--a", "x:y", "--b", "4:8",
      "--target", "8:12", "--out", "x.json"], "bad register range"),
    (["bilinear-cert", "--challenge", "challenge", "--id", "nope", "--out", "x.json"], "nope"),
    (["bilinear-cert", "--challenge", "challenge", "--id", "gf16-06", "--modulus", "0x19",
      "--out", "x.json"], "differs"),
    (["bilinear-structure", "--out", "x.json"], "modulus unknown"),
    (["bilinear-structure", "missing.json", "--out", "x.json"], "not found"),
    (["bilinear-project", "older.json", "--output-mask", "0x1", "--out", "x.json"], "certificate"),
    (["query", "missing.json"], "not found"),
    (["query", "gf16.kmb"], "not JSON"),
    (["query", "older.json", "--limit", "1000"], "limit"),
    (["query", "older.json", "--match", "("], "regular expression"),
    (["summarize", "older.json", "--out", "x.json"], "KMB"),
    (["summarize", "circuit.kmb", "--out", "older.json"], "overwrite"),
    (["cut", "circuit.kmb", "99999"], "outside"),
    (["frobnicate"], "invalid choice"),
    (["bilinear-sieve", "scan", "--manifest", "missing.json", "--id", "x", "--pool", "candidates",
      "--out", "scan"], "not found"),
])
def test_user_errors_are_json_not_tracebacks(staged, argv, needle):
    rc, out, proc = run(argv, staged)
    assert rc != 0
    assert set(out) == {"error", "hint"} and out["hint"], out
    assert needle in out["error"], out
    assert not (staged / "x.json").exists()


@pytest.mark.skipif(not (ROOT / ".venv/bin/python").exists() and "WORKBENCH_PYTHON" not in os.environ,
                    reason="launcher needs the fork .venv or WORKBENCH_PYTHON")
def test_old_bilinear_launchers_are_aliases(tmp_path):
    env = {**os.environ, "WORKBENCH_PYTHON": os.environ.get("WORKBENCH_PYTHON", sys.executable)}
    proc = subprocess.run([str(TOOLS / "bilinear-structure"), "--modulus", "0x13", "--out", "s.json"],
                          cwd=tmp_path, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["minimum_left_rank"] == 4
    shutil.copyfile(FIXTURES / "gf16_schoolbook.kmb", tmp_path / "gf16.kmb")
    proc = subprocess.run([str(TOOLS / "bilinear-corner"), "gf16.kmb", "--modulus", "0x13",
                           "--out", "c.json"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert proc.returncode == 0 and json.loads(proc.stdout)["final_terms"] == 13


CIRCUITS = Path(os.environ["VQCC_CIRCUITS_DIR"]) if os.environ.get("VQCC_CIRCUITS_DIR") else None


@pytest.mark.skipif(CIRCUITS is None or not (CIRCUITS / "dqi/bdee18489fac.kmb").exists(),
                    reason="set VQCC_CIRCUITS_DIR for frontier-circuit checks")
def test_frontier_dqi_query_group_and_next(tmp_path):
    rc, out, _ = run(["summarize", str(CIRCUITS / "dqi/bdee18489fac.kmb"), "--out", "dqi.json"], tmp_path)
    assert rc == 0 and out["sha256"].startswith("bdee18489fac") and out["regions"] > 8000
    rc, top, proc = run(["query", "dqi.json", "--depth", "0", "--sort", "toffoli"], tmp_path)
    check_answer("depth0", rc, top, proc)
    assert top["rows"][0]["name"] == "unextract"
    rc, grouped, _ = run(["query", "dqi.json", "--match", "^scan/", "--group", "path"], tmp_path)
    assert rc == 0 and grouped["rows"][0]["instances"] == 8050
    assert run_next(out["next"] + top["next"] + grouped["next"], tmp_path) >= 4


@pytest.mark.skipif(CIRCUITS is None or not (CIRCUITS / "invsqrt/bce2948ff24e.kmb").exists(),
                    reason="set VQCC_CIRCUITS_DIR for frontier-circuit checks")
def test_frontier_invsqrt_has_no_regions_and_points_at_cut(tmp_path):
    rc, out, _ = run(["summarize", str(CIRCUITS / "invsqrt/bce2948ff24e.kmb"), "--out", "inv.json"],
                     tmp_path)
    assert rc == 0 and out["regions"] == 0 and out["metrics"]["toffoli"] == 1495
    assert all(cmd.startswith("workbench cut ") for cmd in out["next"])
    assert run_next(out["next"], tmp_path) == 1
