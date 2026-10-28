# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
# AI-assisted (Claude, Anthropic), under the repository owner's direction.
"""kickmix_tools: version-agnostic wheel choice from a directory, provenance, latest-release check."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kickmix_tools", ROOT / "tools" / "kickmix_tools.py")
kt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kt)


def _tag():
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def test_wheel_in_dir_picks_this_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(kt.platform, "system", lambda: "Linux")
    monkeypatch.setattr(kt.platform, "machine", lambda: "x86_64")
    t = _tag()
    for name in (f"kickmix-0.3.0-cp312-cp312-manylinux_2_28_x86_64.whl",
                 f"kickmix-0.3.0-{t}-{t}-manylinux_2_28_x86_64.whl",
                 f"kickmix-0.3.0-{t}-{t}-manylinux_2_28_aarch64.whl"):
        (tmp_path / name).write_bytes(b"")
    got = kt._wheel_in_dir(tmp_path)
    assert got is not None and f"-{t}-{t}-" in got.name and "x86_64" in got.name


def test_wheel_in_dir_none_when_no_match(tmp_path):
    (tmp_path / "kickmix-0.3.0-cp27-cp27-manylinux_2_28_x86_64.whl").write_bytes(b"")
    assert kt._wheel_in_dir(tmp_path) is None


def test_install_from_empty_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        kt.install(str(tmp_path), target=tmp_path / "out")


def test_provenance_reads_dist_info(tmp_path, monkeypatch):
    (tmp_path / "kickmix-0.3.0.dist-info").mkdir()
    monkeypatch.setitem(kt._STATE, "fork_build", tmp_path)
    monkeypatch.setitem(kt._STATE, "fork_how", "local wheel x")
    p = kt.provenance()
    assert p["fork_version"] == "0.3.0" and p["fork_install"] == "local wheel x"
    assert p["kickmix_tools_version"] == kt.__version__


def test_check_latest(tmp_path, monkeypatch):
    (tmp_path / "kickmix-0.2.0.dist-info").mkdir()
    monkeypatch.setitem(kt._STATE, "fork_build", tmp_path)
    monkeypatch.setattr(kt, "_release", lambda tag, repo: {"tag_name": "v0.3.0"})
    assert kt.check_latest(quiet=True) == {"installed": "0.2.0", "latest": "0.3.0", "up_to_date": False}

    def offline(tag, repo):
        raise OSError("no network")
    monkeypatch.setattr(kt, "_release", offline)
    assert kt.check_latest(quiet=True)["up_to_date"] is None
