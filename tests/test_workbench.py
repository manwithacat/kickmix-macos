# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Small, exact format and accounting checks for the read-only workbench."""
import importlib.util
import hashlib
import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

MODULE = Path(__file__).resolve().parents[1] / "tools/circuit-viewer/workbench.py"
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location("workbench", MODULE)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
TRACE = MODULE.with_name("trace_diff.py")
tspec = importlib.util.spec_from_file_location("trace_diff", TRACE)
td = importlib.util.module_from_spec(tspec)
tspec.loader.exec_module(td)
PREFIX = MODULE.with_name("prefix.py")
pspec = importlib.util.spec_from_file_location("prefix", PREFIX)
px = importlib.util.module_from_spec(pspec)
pspec.loader.exec_module(px)
STATE_MAP = MODULE.with_name("state_map.py")
mspec = importlib.util.spec_from_file_location("state_map", STATE_MAP)
mm = importlib.util.module_from_spec(mspec)
mspec.loader.exec_module(mm)
STAGE_CONTRACT = MODULE.with_name("stage_contract.py")
cspec = importlib.util.spec_from_file_location("stage_contract", STAGE_CONTRACT)
contract = importlib.util.module_from_spec(cspec)
cspec.loader.exec_module(contract)


def fixture(path):
    # One top-level stage, a nested stage, and one conditional Toffoli.
    ops = bytes([6, 6, 129, 49, 1, 6, 80, 6])
    tags = [b"@enter:outer", b"@enter:inner", b"@exit:inner", b"@exit:outer"]
    streams = [np.array([0], dtype="<u4"), np.array([1], dtype="<u4"),
               np.array([2], dtype="<u4"), np.array([], dtype="<u4"),
               np.array([], dtype="<u4"), np.array([2], dtype="<u4"),
               np.array([], dtype="<u4"), np.array([0], dtype="<u4")]
    chars = b"".join(tags)
    ends = np.cumsum([len(t) for t in tags]).astype("<u8")
    data = bytearray(w.MAGIC)
    data += struct.pack("<4I", 2, 3, 1, 0)
    data += struct.pack("<9Q", len(ops), 1, 0, 1, 0, 1, 0, len(tags), len(chars))
    data += struct.pack("<2Q", 1, len(ops)) + ops
    for i, stream in enumerate(streams):
        data += struct.pack("<2Q", i + 2, stream.nbytes) + stream.tobytes()
    data += struct.pack("<2Q", 10, 0)
    data += struct.pack("<2Q", 11, 8 * (len(tags) + 1) + len(chars))
    data += struct.pack("<Q", len(tags)) + ends.tobytes() + chars
    path.write_bytes(data)


def test_summary_and_cut(tmp_path):
    src = tmp_path / "tiny.kmb"
    fixture(src)
    c = w.Circuit(src)
    report = w.summarize(c, {"author": "test"})
    assert report["metrics"]["ops"] == 8
    assert report["metrics"]["toffoli"] == 1
    assert report["metrics"]["conditional_toffoli"] == 1
    assert report["metrics"]["hmr"] == 1
    assert [r["id"] for r in report["tag_regions"]] == ["outer#1", "outer/inner#1"]
    assert report["tag_regions"][1]["metrics"]["toffoli"] == 1
    row = w.cut(c, 3, 0)["rows"][0]
    assert row["kind"] == "CCX" and row["qubits"] == [0, 1, 2]
    assert w.compare(report, report)["changed_regions"] == []
    assert w.query(report, "inner", 1, "toffoli", 2)["matched"] == 1
    ctx = {"circuit_sha256": report["circuit"]["sha256"],
           "boundaries": {"mid": {"op": 3, "tier": "local prescreen"}}}
    with_ctx = w.summarize(c, {"author": "test"}, ctx)
    assert w.boundary(with_ctx, "3")["matched"] == 1
    prefix_path = tmp_path / "prefix.kmb"
    prefix_info = px.write_prefix(c, 5, prefix_path)
    assert prefix_info["op"] == 5
    assert len(w.Circuit(prefix_path).ops) == 5


def test_trace_diff_first_divergence(tmp_path):
    np.savez(tmp_path / "inputs.npz", weights=np.array([0, 1, 2, 3]))
    (tmp_path / "inputs.json").write_text(json.dumps({"metadata": [
        {"slot": i, "cohort": "known_failure" if i == 2 else "fixed_weight", "weight": i}
        for i in range(4)]}))
    cuts = []
    for name, different in (("forward", [2]), ("reverse", [1, 2]), ("final", [])):
        host = np.zeros((2, 12, 4), bool)
        donor = host.copy()
        donor[:, 0, different] = True
        common = dict(phase=np.zeros((2, 4), bool), bits=np.zeros((2, 1, 1), np.uint64),
                      bit_compact=np.array([0]))
        np.savez(tmp_path / f"host-{name}.npz", qubits=host, syndrome=host, **common)
        np.savez(tmp_path / f"donor-{name}.npz", qubits=donor, syndrome=donor, **common)
        cuts.append({"name": name, "host_op": {"forward": 10, "reverse": 30, "final": 50}[name],
                     "donor_op": {"forward": 11, "reverse": 31, "final": 51}[name],
                     "host_dump": f"host-{name}.npz", "donor_dump": f"donor-{name}.npz",
                     "host_dump_sha256": hashlib.sha256((tmp_path / f"host-{name}.npz").read_bytes()).hexdigest(),
                     "donor_dump_sha256": hashlib.sha256((tmp_path / f"donor-{name}.npz").read_bytes()).hexdigest()})
    manifest = {"schema": "kickmix-trace-manifest-v1", "host_sha256": "a", "donor_sha256": "b",
                "input_metadata": "inputs.json", "inputs": "inputs.npz", "seeds": [7, 8], "cuts": cuts,
                "input_metadata_sha256": hashlib.sha256((tmp_path / "inputs.json").read_bytes()).hexdigest(),
                "inputs_sha256": hashlib.sha256((tmp_path / "inputs.npz").read_bytes()).hexdigest()}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    report = td.analyze(path)
    assert [c["rows"][0]["syndrome_mismatch_slots"] for c in report["cuts"]] == [1, 2, 0]
    assert report["first_syndrome_divergence_by_cohort"]["7"]["reverse"]["fixed_weight"] == 1
    assert report["transitions"]["7"][1]["by_cohort"]["fixed_weight"]["resolved"] == 1
    assert td.query(report, "reverse", 7)["rows"][0]["changed_words"] == [0]
    hs = {"circuit": {"sha256": "a"}, "tag_regions": [
        {"id": "middle#1", "depth": 0, "start": 20, "end": 25}]}
    ds = {"circuit": {"sha256": "b"}, "tag_regions": [
        {"id": "middle#1", "depth": 0, "start": 21, "end": 26}]}
    prior = dict(report, cuts=report["cuts"][:2], raw_difference_nonmonotonic=False)
    suggestion = td.plan(prior, hs, ds, "fixed_weight", 7)
    assert suggestion["suggestions"][0]["host_op"] == 20
    assert td.plan(report, hs, ds, "fixed_weight", 7)["status"] == "raw_state_nonmonotonic"
    unpinned = dict(manifest)
    unpinned.pop("inputs_sha256")
    path.write_text(json.dumps(unpinned))
    with pytest.raises(ValueError, match="missing or malformed SHA-256"):
        td.analyze(path)


def test_state_map_exact_coordinates_and_holdout(tmp_path):
    weights = np.array([0, 2, 4, 6, 1, -1, 3])
    cohorts = ["fixed_weight"] * 5 + ["natural", "known_failure"]
    np.savez(tmp_path / "inputs.npz", weights=weights)
    (tmp_path / "inputs.json").write_text(json.dumps({"metadata": [
        {"slot": i, "cohort": c, "weight": int(weights[i])}
        for i, c in enumerate(cohorts)]}))
    host = np.array([[0, 0, 1, 1, 1, 0, 1],
                     [0, 1, 0, 1, 0, 1, 1],
                     [0, 0, 0, 0, 0, 0, 0]], dtype=bool)
    donor = np.array([host[1], ~host[0], host[0] ^ host[1]])
    np.savez(tmp_path / "host.npz", syndrome=np.stack([host, host]))
    np.savez(tmp_path / "donor.npz", syndrome=np.stack([donor, donor]))
    digest = lambda name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    manifest = {"schema": "kickmix-trace-manifest-v1", "seeds": [7, 8],
                "input_metadata": "inputs.json", "input_metadata_sha256": digest("inputs.json"),
                "inputs": "inputs.npz", "inputs_sha256": digest("inputs.npz"),
                "cuts": [{"name": "scan", "host_dump": "host.npz", "host_dump_sha256": digest("host.npz"),
                          "donor_dump": "donor.npz", "donor_dump_sha256": digest("donor.npz")}]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    result = mm.screen(path, "scan")
    assert result["variable_donor_bits"] == 3
    assert result["fit_candidate_bits"] == 2
    assert result["full_holdout_pass_bits"] == 2
    assert result["mappings"][1]["host_bit"] == 0
    assert result["mappings"][1]["complement"] == 1
    assert mm.query(result, 0)["fit_candidate_bits"] == 2
    with pytest.raises(ValueError, match="out of range"):
        mm.query(result, 1)
    assert mm.compare_words(result, result, path, path)["common_full_words"] == 0
    report12 = dict(result, syndrome_bits=12, mappings=[
        {"donor_bit": i, "status": "fit_candidate", "host_bit": 24+i,
         "complement": 0, "holdout_errors": {"odd_fixed_weight": 0, "natural": 0}}
        for i in range(12)])
    assert mm.complete_words(report12) == {0: 2}


def test_stage_contract_checks_pinned_exit(tmp_path):
    (tmp_path / "inputs.json").write_text(json.dumps({"metadata": [
        {"slot": i, "cohort": "fixed_weight"} for i in range(4)]}))
    np.savez(tmp_path / "inputs.npz", weights=np.arange(4))
    q = np.zeros((2, 3, 4), bool)
    q[:, 0, 1] = True
    for name in ("entry", "exit"):
        np.savez(tmp_path / f"{name}.npz", qubits=q, syndrome=q[:, :2], phase=np.zeros((2, 4), bool))
    digest = lambda name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    for name, op in (("entry", 5), ("exit", 10)):
        (tmp_path / f"{name}.json").write_text(json.dumps({
            "schema": "kickmix-workbench-sample-v1", "source_sha256": "a", "engine_sha256": "b",
            "qubits": 3, "inputs_sha256": digest("inputs.npz"), "seeds": [7, 8],
            "shots": 4, "cut_op": op, "dump_sha256": digest(f"{name}.npz")}))
    manifest = {"schema": "kickmix-trace-manifest-v1", "host_sha256": "a", "donor_sha256": "c",
                "seeds": [7, 8], "input_metadata": "inputs.json",
                "input_metadata_sha256": digest("inputs.json"),
                "inputs": "inputs.npz", "inputs_sha256": digest("inputs.npz"),
                "cuts": [{"host_op": 10, "host_dump_sha256": digest("exit.npz"),
                          "donor_op": 11, "donor_dump_sha256": "0" * 64}]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    result = contract.run(path, tmp_path / "entry.json", tmp_path / "exit.json")
    assert [r["qubit_mismatch_slots"] for r in result["rows"]] == [0, 0]
    manifest["cuts"][0]["host_op"] = 12
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="not pinned"):
        contract.run(path, tmp_path / "entry.json", tmp_path / "exit.json")
