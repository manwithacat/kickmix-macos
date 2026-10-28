# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
# AI-assisted (Claude, Anthropic), under the repository owner's direction.
"""kickmix_tools engine support: wheel choice, hash checks, install_engine(), verify(build="engine").

No real engine, kickmix build or competition data is needed: a stand-in `kickmix_engine` package
emulates the CLI's output contract (summary JSON on stdout, `{"engine_info": ...}` on stderr with
--timing, the cross-check envelope, exit 1 on FAIL).
"""
import base64
import hashlib
import importlib.util
import json
import platform
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kickmix_tools_engine_t", ROOT / "tools" / "kickmix_tools.py")
kt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kt)

MINOR = sys.version_info.minor

FAKE_MAIN = r'''
import json, os, sys
args = sys.argv[1:]
if args[0] == "doctor":
    print(json.dumps({"schema": "kickmix-engine-doctor/1", "ok": True}) if "--json" in args else "kickmix-engine fake")
    sys.exit(0)
task, circuit = args[1], args[2]
def opt(name):
    return args[args.index(name) + 1] if name in args else None
if "FAIL" in os.path.basename(circuit):
    print(json.dumps({"status": "FAIL", "error": "x was not restored."}, indent=2))
    sys.exit(1)
summary = {"status": "PASS", "score": 1.5, "num_qubits": 7, "shot_operation_counts": {"CX": 3},
           "threads": opt("--threads"), "salt": opt("--salt"), "num_shots": opt("--num-shots"),
           "verifier": opt("--verifier"), "pythonpath": os.environ.get("PYTHONPATH", "")}
kernel = {"kernel": "baseline", "selected_by": "auto", "available": ["baseline"]}
print(json.dumps({"engine_info": {"kernel": kernel, "total_s": 0.01}}), file=sys.stderr)
if "--cross-check" in args:
    official = dict(summary, score=2.5) if "MISMATCH" in circuit else summary
    diffs = {"score": {"engine": 1.5, "official": 2.5}} if "MISMATCH" in circuit else {}
    print(json.dumps({"match": not diffs, "differences": diffs, "engine": summary, "official": official,
                      "engine_kernel": kernel, "seconds": {"engine": 0.01, "official": 0.02},
                      "official_returncode": 0, "official_command": ["x"]}, indent=2))
    sys.exit(3 if diffs else 0)
print(json.dumps(summary, indent=2))
'''


def _plat_tag():
    system, arch = kt._platform_want()
    if system == "manylinux":
        return f"manylinux_2_17_{arch}"
    if system == "macosx":
        return f"macosx_11_0_{arch}"
    return "win_amd64"


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_engine_wheel(directory: Path, version="9.9.9") -> Path:
    """A minimal installable kickmix_engine wheel holding the stand-in CLI."""
    name = f"kickmix_engine-{version}-cp312-abi3-{_plat_tag()}.whl"
    info = f"kickmix_engine-{version}.dist-info"
    files = {
        "kickmix_engine/__init__.py": f'__version__ = "{version}"\n'.encode(),
        "kickmix_engine/__main__.py": FAKE_MAIN.encode(),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: kickmix-engine\nVersion: {version}\n".encode(),
        f"{info}/WHEEL": f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: false\nTag: cp312-abi3-{_plat_tag()}\n".encode(),
    }
    record = "".join(f"{p},{_record_hash(d)},{len(d)}\n" for p, d in files.items()) + f"{info}/RECORD,,\n"
    files[f"{info}/RECORD"] = record.encode()
    path = directory / name
    with zipfile.ZipFile(path, "w") as z:
        for p, d in files.items():
            z.writestr(p, d)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- wheel choice


def test_pick_engine_wheel_linux(monkeypatch):
    monkeypatch.setattr(kt.platform, "system", lambda: "Linux")
    monkeypatch.setattr(kt.platform, "machine", lambda: "x86_64")
    names = ["kickmix_engine-0.4.0-cp312-abi3-macosx_11_0_arm64.whl",
             "kickmix_engine-0.4.0-cp312-abi3-manylinux_2_28_x86_64.whl",
             "kickmix_engine-0.10.0-cp312-abi3-manylinux_2_28_x86_64.whl",
             "kickmix_engine-0.4.0-cp312-abi3-manylinux_2_28_aarch64.whl",
             "kickmix-0.3.0-cp313-cp313-manylinux_2_28_x86_64.whl"]
    if MINOR >= 12:
        assert kt._pick_engine_wheel(names) == "kickmix_engine-0.10.0-cp312-abi3-manylinux_2_28_x86_64.whl"


def test_pick_engine_wheel_abi3_floor_and_exact_tags(monkeypatch):
    monkeypatch.setattr(kt.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(kt.platform, "machine", lambda: "arm64")
    newer = f"kickmix_engine-1.0.0-cp3{MINOR + 1}-abi3-macosx_11_0_arm64.whl"
    assert kt._pick_engine_wheel([newer]) is None  # abi3 floor above this interpreter
    other_exact = f"kickmix_engine-1.0.0-cp3{MINOR - 1}-cp3{MINOR - 1}-macosx_11_0_arm64.whl"
    assert kt._pick_engine_wheel([other_exact]) is None
    exact = f"kickmix_engine-1.0.0-cp3{MINOR}-cp3{MINOR}-macosx_11_0_arm64.whl"
    assert kt._pick_engine_wheel([exact]) == exact


def test_find_wheel_dir_separates_fork_and_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(kt.platform, "system", lambda: "Linux")
    monkeypatch.setattr(kt.platform, "machine", lambda: "x86_64")
    t = f"cp3{MINOR}"
    fork = tmp_path / "kickmix-fork-wheels"
    engine = tmp_path / "kickmix-engine-wheels" / "nested"
    fork.mkdir()
    engine.mkdir(parents=True)
    (fork / f"kickmix-0.3.0-{t}-{t}-manylinux_2_28_x86_64.whl").write_bytes(b"")
    (engine / "kickmix_engine-0.4.0-cp312-abi3-manylinux_2_28_x86_64.whl").write_bytes(b"")
    monkeypatch.setenv("KICKMIX_TOOLS_INPUT_DIR", str(tmp_path))
    assert kt.find_wheel_dir("fork") == fork
    assert kt.find_wheel_dir("engine") == (engine if MINOR >= 12 else None)
    assert kt.find_wheel_dir("engine", tmp_path / "missing") is None


# --------------------------------------------------------------------------- hash checks


def test_check_engine_wheel(tmp_path):
    w = tmp_path / "kickmix_engine-0.4.0-cp312-abi3-manylinux_2_28_x86_64.whl"
    w.write_bytes(b"wheel bytes")
    assert "not checked" in kt._check_engine_wheel(w)
    (tmp_path / "SHA256SUMS.txt").write_text(f"{_sha(w)}  {w.name}\n")
    (tmp_path / "release-manifest.json").write_text(json.dumps({"wheels": [{"filename": w.name, "sha256": _sha(w)}]}))
    msg = kt._check_engine_wheel(w)
    assert "SHA256SUMS.txt" in msg and "release-manifest.json" in msg
    (tmp_path / "release-manifest.json").write_text(json.dumps({"wheels": [{"filename": w.name, "sha256": "0" * 64}]}))
    with pytest.raises(RuntimeError, match="mismatch"):
        kt._check_engine_wheel(w)
    (tmp_path / "release-manifest.json").unlink()
    (tmp_path / "SHA256SUMS.txt").write_text(f"{'0' * 64}  other.whl\n")
    with pytest.raises(RuntimeError, match="does not list"):
        kt._check_engine_wheel(w)


# --------------------------------------------------------------------------- install and verify


@pytest.fixture
def engine_env(tmp_path, monkeypatch):
    """Installed stand-in engine, a stand-in kickmix build dir and a competition dir with verifier files."""
    for k in ("KICKMIX_TOOLS_ENGINE_BUILD", "KICKMIX_TOOLS_FORK_BUILD", "KICKMIX_TOOLS_OFFICIAL_BUILD",
              "KICKMIX_ENGINE_VERIFIERS"):
        monkeypatch.delenv(k, raising=False)
    for k in ("engine_build", "engine_how", "engine_manifest", "fork_build", "fork_how"):
        monkeypatch.delitem(kt._STATE, k, raising=False)
    wheels = tmp_path / "input" / "kickmix-engine-wheels"
    wheels.mkdir(parents=True)
    w = make_engine_wheel(wheels)
    (wheels / "SHA256SUMS.txt").write_text(f"{_sha(w)}  {w.name}\n")
    (wheels / "release-manifest.json").write_text(json.dumps({"wheels": [{"filename": w.name, "sha256": _sha(w)}]}))
    comp = tmp_path / "comp"
    for task in ("dqi", "invsqrt"):
        (comp / "verifiers" / task).mkdir(parents=True)
        (comp / "verifiers" / task / f"verify_submission_{task}.py").write_text("")
    (comp / "kickmix").mkdir()
    (comp / "kickmix" / "kickmix.so").write_bytes(b"")  # stand-in: the fake engine never imports it
    monkeypatch.setenv("KICKMIX_TOOLS_COMPETITION_DIR", str(comp))
    monkeypatch.setenv("KICKMIX_TOOLS_INPUT_DIR", str(tmp_path / "input"))
    target = kt.install_engine(target=tmp_path / "engine")
    return {"tmp": tmp_path, "comp": comp, "target": target, "wheel": w}


def test_install_engine_auto_from_input(engine_env):
    assert engine_env["target"] == engine_env["tmp"] / "engine"
    p = kt.provenance()
    assert p["engine_version"] == "9.9.9"
    assert "SHA256SUMS.txt" in p["engine_install"] and "release-manifest.json" in p["engine_install"]
    assert p["engine_build_dir"] == str(engine_env["target"])
    # reinstalling over the same target replaces the package
    kt.install_engine(str(engine_env["wheel"]), target=engine_env["target"])
    assert kt.provenance()["engine_version"] == "9.9.9"


def test_install_engine_refuses_tampered_wheel(engine_env):
    w = engine_env["wheel"]
    (w.parent / "SHA256SUMS.txt").write_text(f"{'0' * 64}  {w.name}\n")
    with pytest.raises(RuntimeError, match="mismatch"):
        kt.install_engine(str(w.parent), target=engine_env["tmp"] / "engine2")


def test_install_engine_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        kt.install_engine(str(tmp_path), target=tmp_path / "out")
    with pytest.raises(ValueError):
        kt.install_engine(str(tmp_path / "nope.txt"), target=tmp_path / "out")


def test_verify_engine_result_shape(engine_env):
    circuit = engine_env["tmp"] / "dqi.kmb"
    circuit.write_bytes(b"")
    r = kt.verify("dqi", circuit, build="engine", workers=3, salt="s1", dqi_shots=512)
    for key in ("task", "build", "result", "wall_seconds", "peak_rss_sum_mb", "peak_pss_sum_mb",
                "max_single_process_mb", "kickmix_module", "workers", "provenance"):
        assert key in r
    assert r["build"] == "engine" and r["workers"] == 3
    res = r["result"]
    assert res["status"] == "PASS" and res["threads"] == "3" and res["salt"] == "s1" and res["num_shots"] == "512"
    assert res["verifier"] == str(engine_env["comp"] / "verifiers" / "dqi" / "verify_submission_dqi.py")
    # the engine dir first, then the kickmix build used for the verifier imports (official when present)
    path = res["pythonpath"].split(kt.os.pathsep)
    assert path[:2] == [str(engine_env["target"]), str(engine_env["comp"] / "kickmix")]
    prov = r["provenance"]
    assert prov["engine_version"] == "9.9.9" and prov["engine_kernel"] == "baseline"
    assert prov["engine_kernel_selected_by"] == "auto"
    assert prov["kickmix_for_verifier_imports"] == str(engine_env["comp"] / "kickmix")
    assert r["kickmix_module"].endswith("kickmix.so")
    assert r["engine_info"]["kernel"]["kernel"] == "baseline"
    assert "cross_check" not in r


def test_verify_engine_cross_check(engine_env):
    ok = engine_env["tmp"] / "inv.kmb"
    ok.write_bytes(b"")
    r = kt.verify("invsqrt", ok, build="engine", workers=2, cross_check=True)
    assert r["cross_check"]["match"] is True and r["result"]["status"] == "PASS"
    bad = engine_env["tmp"] / "MISMATCH.kmb"
    bad.write_bytes(b"")
    r = kt.verify("invsqrt", bad, build="engine", workers=2, cross_check=True)
    assert r["cross_check"]["match"] is False and "score" in r["cross_check"]["differences"]
    assert r["engine_returncode"] == 3


def test_verify_engine_fail_and_bad_task(engine_env):
    bad = engine_env["tmp"] / "FAIL.kmb"
    bad.write_bytes(b"")
    with pytest.raises(RuntimeError, match="x was not restored"):
        kt.verify("dqi", bad, build="engine", workers=1)
    with pytest.raises(ValueError):
        kt.verify("dfthcsos", engine_env["tmp"], build="engine")
    with pytest.raises(ValueError):
        kt.verify("dqi", bad, build="fork", cross_check=True)


def test_engine_kickmix_fallback_to_fork(engine_env, monkeypatch):
    (engine_env["comp"] / "kickmix" / "kickmix.so").unlink()
    fork = engine_env["tmp"] / "fork"
    fork.mkdir()
    (fork / "kickmix.so").write_bytes(b"")
    monkeypatch.setitem(kt._STATE, "fork_build", fork)
    assert kt._engine_kickmix_dir(None) == fork
    circuit = engine_env["tmp"] / "dqi.kmb"
    circuit.write_bytes(b"")
    r = kt.verify("dqi", circuit, build="engine", workers=1)
    assert r["provenance"]["kickmix_for_verifier_imports"] == str(fork)


def test_engine_doctor(engine_env):
    assert kt.engine_doctor(as_json=True) == {"schema": "kickmix-engine-doctor/1", "ok": True}
    assert "fake" in kt.engine_doctor()


def test_compare_builds_identical(engine_env, monkeypatch):
    def fake_verify(task, circuit, build="fork", workers=None, **kw):
        res = {"status": "PASS", "score": 2.0, "counts": {"CX": 1}}
        if build == "fork" and kw.get("salt") == "diff":
            res["score"] = 3.0
        return {"task": task, "build": build, "result": res, "wall_seconds": 1.0 + (build == "official"),
                "peak_rss_sum_mb": 10.0, "peak_pss_sum_mb": None, "workers": workers, "provenance": {}}
    monkeypatch.setattr(kt, "verify", fake_verify)
    c = kt.compare_builds("dqi", "x.kmb", workers={"official": 2, "fork": 4, "engine": 4}, quiet=True)
    assert c["identical"] and c["n_fields"] == 3 and c["runs"]["official"]["workers"] == 2
    assert "IDENTICAL" in kt.format_comparison(c)
    c = kt.compare_builds("dqi", "x.kmb", builds=("official", "fork"), salt="diff", quiet=True)
    assert not c["identical"] and c["n_different"] == 1 and "differing fields" in kt.format_comparison(c)
