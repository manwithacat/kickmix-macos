#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Read-only, agent-first index of large kickmix .kmb circuits.

Usage:
  python workbench.py summarize CIRCUIT.kmb --out report.json [--author NAME --source URL --version V]
  python workbench.py compare BEFORE.json AFTER.json --out comparison.json
  python workbench.py query REPORT.json [--match REGEX] [--depth N] [--sort toffoli] [--limit 20]
  python workbench.py boundary REPORT.json [NAME_OR_OP]
  python workbench.py trace-diff MANIFEST.json --out trace-report.json
  python workbench.py trace-query TRACE-REPORT.json [--cut NAME] [--seed N]
  python workbench.py trace-plan TRACE.json HOST-STATIC.json DONOR-STATIC.json --cohort fixed_weight --seed N
  python workbench.py prefix CIRCUIT.kmb OP_INDEX --out prefix.kmb
  python workbench.py sample-dqi SOURCE.kmb PREFIX.kmb INPUTS.npz --seeds 777 778 --engine-package DIR --out dump.npz
  python workbench.py state-map PINNED-TRACE-MANIFEST.json --cut scan_exit --out map.json
  python workbench.py state-map-query map.json --word 6
  python workbench.py state-map-compare BEFORE-MAP.json BEFORE-MANIFEST.json AFTER-MAP.json AFTER-MANIFEST.json --out comparison.json
  python workbench.py stage-contract PINNED-TRACE-MANIFEST.json entry.json exit.json --out contract.json
  python workbench.py wire-transport CIRCUIT.kmb STATIC.json --out transport.json
  python workbench.py transport-compare HOST-TRANSPORT.json DONOR-TRANSPORT.json STATE-MAP.json --out comparison.json
  python workbench.py cut CIRCUIT.kmb OP_INDEX [--radius 6]
  python workbench.py bilinear-cert CIRCUIT.kmb --a a --b b --target t --modulus 0x13 --out certificate.json
  python workbench.py bilinear-project certificate.json --output-mask 0x80 --out scalar.json

The report uses half-open operation ranges and counts Toffoli-class gates (CCZ,
CCX, CSWAP, including conditional forms). It is static evidence, not a verifier.
Public circuits are parsed as bytes and never imported or executed as code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

import numpy as np

MAGIC = bytes.fromhex("d750c7d5c329d326e3cc9f6834f2b8bf")
NAMES = {0: "NEG", 1: "POP_CONDITION", 6: "INFO_TAG", 16: "Z", 17: "X",
         18: "R", 19: "Z_POW", 32: "CZ", 33: "CX", 34: "SWAP",
         48: "CCZ", 49: "CCX", 50: "CSWAP", 64: "BIT_STORE0",
         65: "BIT_STORE1", 66: "BIT_INVERT", 80: "HMR", 129: "PUSH_CONDITION"}
METRICS = ("ops", "toffoli", "conditional_toffoli", "hmr", "measurements")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


class Circuit:
    def __init__(self, path: Path):
        self.path = path
        self.raw = np.memmap(path, dtype=np.uint8, mode="r")
        b = memoryview(self.raw)
        if bytes(b[:16]) != MAGIC:
            raise ValueError("not kickmix KMB v2")
        version, self.qubits, self.bits, nreg = struct.unpack_from("<4I", b, 16)
        if version != 2:
            raise ValueError(f"unsupported KMB version {version}")
        keys = ("ops", "qqq", "qq", "q", "b", "c", "angle", "tags", "tag_chars")
        self.count = dict(zip(keys, struct.unpack_from("<9Q", b, 32)))
        pos = 104
        self.registers = {}
        self.register_ids = {}
        for _ in range(nreg):
            packet_start = pos
            typ, size = struct.unpack_from("<2Q", b, pos)
            if typ != 0:
                raise ValueError("unexpected register packet")
            pos += 16
            nl, nr = struct.unpack_from("<2I", b, pos)
            pos += 8
            name = bytes(b[pos:pos + nl]).decode("utf8")
            pos += nl
            vals = np.frombuffer(b, dtype="<u4", count=nr, offset=pos)
            self.registers[name] = {"qubits": int(np.count_nonzero(vals & 0x80000000)),
                                    "bits": int(nr - np.count_nonzero(vals & 0x80000000))}
            self.register_ids[name] = [(bool(int(v) & 0x80000000), int(v) & 0x7fffffff) for v in vals]
            pos += 4 * nr
            if pos != packet_start + 16 + size:
                raise ValueError(f"bad register packet {name}")
        self.offsets = {}
        for kind, name, count, dtype in [(1, "op_types", self.count["ops"], "u1"),
            (2, "qqq0", self.count["qqq"], "<u4"), (3, "qqq1", self.count["qqq"], "<u4"),
            (4, "qqq2", self.count["qqq"], "<u4"), (5, "qq0", self.count["qq"], "<u4"),
            (6, "qq1", self.count["qq"], "<u4"), (7, "q0", self.count["q"], "<u4"),
            (8, "b0", self.count["b"], "<u4"), (9, "bc", self.count["c"], "<u4"),
            (10, "angles", self.count["angle"], "V16")]:
            typ, size = struct.unpack_from("<2Q", b, pos)
            if typ != kind or size != count * np.dtype(dtype).itemsize:
                raise ValueError(f"bad {name} packet")
            self.offsets[name] = (pos + 16, count, dtype)
            pos += 16 + size
        typ, size = struct.unpack_from("<2Q", b, pos)
        ntags = struct.unpack_from("<Q", b, pos + 16)[0]
        if typ != 11 or ntags != self.count["tags"] or size != 8 * (ntags + 1) + self.count["tag_chars"]:
            raise ValueError("bad tag packet")
        ends = np.frombuffer(b, dtype="<u8", count=ntags, offset=pos + 24)
        chars = bytes(b[pos + 24 + 8 * ntags:pos + 24 + 8 * ntags + self.count["tag_chars"]])
        self.tags = []
        start = 0
        for end in ends:
            self.tags.append(chars[start:int(end)].decode("utf8", "replace"))
            start = int(end)
        if start != len(chars) or pos + 16 + size != len(self.raw):
            raise ValueError("trailing or truncated KMB data")

    def stream(self, name: str) -> np.ndarray:
        off, n, dt = self.offsets[name]
        return np.frombuffer(self.raw, dtype=dt, count=n, offset=off)

    @property
    def ops(self) -> np.ndarray:
        return self.stream("op_types")


def counts(t: np.ndarray, depth: int = 0) -> tuple[dict, int]:
    base = t & 0x7f
    tof = np.isin(base, (48, 49, 50))
    push = t == 129
    pop = t == 1
    change = push.astype(np.int32) - pop.astype(np.int32)
    after = np.cumsum(change, dtype=np.int32) + depth
    before = after - change
    if len(before) and int(before.min()) < 0:
        raise ValueError("condition stack underflow")
    result = {"ops": int(len(t)), "toffoli": int(tof.sum()),
            "conditional_toffoli": int(np.count_nonzero(tof & ((before > 0) | ((t & 0x80) != 0)))),
            "hmr": int(np.count_nonzero(base == 80)),
            "measurements": int(np.count_nonzero((base == 80) | (base == 18)))}
    return result, int(after[-1]) if len(after) else depth


def add(dst: dict, inc: dict) -> None:
    for k in METRICS:
        dst[k] += inc[k]


def zero() -> dict:
    return dict.fromkeys(METRICS, 0)


def summarize(c: Circuit, provenance: dict, context: dict | None = None) -> dict:
    t = c.ops
    tagpos = np.flatnonzero(t == 6)
    if len(tagpos) != len(c.tags):
        raise ValueError("INFO_TAG count differs from tag strings")
    regions, stack, occurrence = [], [], {}
    root = zero()
    prev = 0
    cond_depth = 0
    for i, label in zip(tagpos, c.tags):
        i = int(i)
        delta, cond_depth = counts(t[prev:i], cond_depth)
        add(root, delta)
        for j in stack:
            add(regions[j]["metrics"], delta)
        if label.startswith("@enter:"):
            name = label[7:]
            path = "/".join([regions[j]["name"] for j in stack] + [name])
            occurrence[path] = occurrence.get(path, 0) + 1
            regions.append({"id": f"{path}#{occurrence[path]}", "name": name, "path": path,
                            "depth": len(stack), "start": i + 1, "end": None, "metrics": zero()})
            stack.append(len(regions) - 1)
        elif label.startswith("@exit:"):
            name = label[6:]
            if not stack or regions[stack[-1]]["name"] != name:
                raise ValueError(f"unbalanced tag {label} at op {i}")
            regions[stack.pop()]["end"] = i
        else:
            raise ValueError(f"unrecognised tag {label} at op {i}")
        root["ops"] += 1
        for j in stack:
            regions[j]["metrics"]["ops"] += 1
        prev = i + 1
    delta, cond_depth = counts(t[prev:], cond_depth)
    add(root, delta)
    for j in stack:
        add(regions[j]["metrics"], delta)
    if stack:
        raise ValueError("unclosed tags")
    if cond_depth != 0:
        raise ValueError("unclosed condition stack")
    # Independent whole-stream checks of the non-conditional totals.
    direct_tof = int(np.count_nonzero(np.isin(t & 0x7f, (48, 49, 50))))
    if root["ops"] != len(t) or root["toffoli"] != direct_tof:
        raise ValueError("partition count mismatch")
    circuit_sha = digest(c.path)
    if context:
        pinned = context.get("circuit_sha256")
        if pinned and pinned != circuit_sha:
            raise ValueError("context circuit_sha256 does not match input")
        notes = context.get("regions", {})
        for region in regions:
            if region["id"] in notes or region["path"] in notes:
                region["context"] = notes.get(region["id"], notes.get(region["path"]))
    return {"schema": "kickmix-workbench-v1", "tier": "static model / projection",
            "circuit": {"file": c.path.name, "sha256": circuit_sha, "qubits": c.qubits,
                        "bits": c.bits, "registers": c.registers, "provenance": provenance},
            "metrics": root, "tag_regions": regions,
            "context": None if not context else {k: v for k, v in context.items() if k != "regions"},
            "notes": ["Half-open operation ranges; tag operations excluded from region endpoints.",
                      "Counts describe the netlist, not per-shot conditional execution or correctness."]}


def compare(a: dict, b: dict) -> dict:
    if a["schema"] != b["schema"]:
        raise ValueError("schema mismatch")
    x = {r["id"]: r for r in a["tag_regions"]}
    y = {r["id"]: r for r in b["tag_regions"]}
    rows = []
    for key in sorted(x.keys() | y.keys()):
        u, v = x.get(key), y.get(key)
        before, after = (u or {}).get("metrics", zero()), (v or {}).get("metrics", zero())
        diff = {k: after[k] - before[k] for k in METRICS}
        if any(diff.values()):
            rows.append({"id": key, "depth": (v or u)["depth"],
                         "before": None if u is None else [u["start"], u["end"]],
                         "after": None if v is None else [v["start"], v["end"]], "delta": diff,
                         "context": (v or u).get("context")})
    return {"schema": "kickmix-workbench-diff-v1", "tier": "static model / projection",
            "before": a["circuit"], "after": b["circuit"],
            "total_delta": {k: b["metrics"][k] - a["metrics"][k] for k in METRICS},
            "changed_regions": rows,
            "notes": ["Region ids use tag path and occurrence; changed or retagged paths may not align.",
                      "Deltas are static counts, not per-shot savings."]}


def cut(c: Circuit, op: int, radius: int) -> dict:
    t = c.ops
    if op < 0 or op >= len(t):
        raise ValueError("operation index out of range")
    a, b = max(0, op - radius), min(len(t), op + radius + 1)
    qcls = t & 0x30
    # Stream positions are counts of prior operations of the appropriate arity.
    q3, q2, q1 = [int(np.count_nonzero(qcls[:a] == n)) for n in (0x30, 0x20, 0x10)]
    b0 = int(np.count_nonzero((t[:a] & 0x40) != 0))
    bc = int(np.count_nonzero((t[:a] & 0x80) != 0))
    ti = int(np.count_nonzero(t[:a] == 6))
    s = {n: c.stream(n) for n in ("qqq0", "qqq1", "qqq2", "qq0", "qq1", "q0", "b0", "bc")}
    sha = digest(c.path)
    rows = []
    for i in range(a, b):
        typ = int(t[i]); cls = typ & 0x30
        qs = []
        if cls == 0x30:
            qs = [int(s[n][q3]) for n in ("qqq0", "qqq1", "qqq2")]; q3 += 1
        elif cls == 0x20:
            qs = [int(s[n][q2]) for n in ("qq0", "qq1")]; q2 += 1
        elif cls == 0x10:
            qs = [int(s["q0"][q1])]; q1 += 1
        row = {"op": i, "address": f"op@{sha[:12]}:{i}",
               "kind": NAMES.get(typ & 0x7f if typ & 0x80 and typ != 129 else typ, hex(typ)),
               "qubits": qs}
        if typ & 0x40:
            row["bit"] = int(s["b0"][b0]); b0 += 1
        if typ & 0x80:
            row["condition"] = int(s["bc"][bc]); bc += 1
        if typ == 6:
            row["tag"] = c.tags[ti]; ti += 1
        rows.append(row)
    return {"schema": "kickmix-workbench-cut-v1", "tier": "static model / projection",
            "circuit_sha256": sha, "range": [a, b], "rows": rows}


def query(report: dict, pattern: str, depth: int | None, sort: str, limit: int) -> dict:
    diff = report["schema"] == "kickmix-workbench-diff-v1"
    rows = report["changed_regions"] if diff else report["tag_regions"]
    rx = re.compile(pattern, re.IGNORECASE)
    selected = [r for r in rows if rx.search(r["id"]) and (depth is None or r["depth"] == depth)]
    selected.sort(key=lambda r: abs((r["delta"] if diff else r["metrics"])[sort]), reverse=True)
    return {"schema": "kickmix-workbench-query-v1", "tier": "static model / projection",
            "report_schema": report["schema"], "matched": len(selected),
            "shown": min(len(selected), limit), "sort": sort, "rows": selected[:limit]}


def boundary(report: dict, address: str | None) -> dict:
    items = (report.get("context") or {}).get("boundaries", {})
    if address is None:
        selected = items
    else:
        selected = {name: row for name, row in items.items()
                    if name == address or str(row.get("op")) == address}
    return {"schema": "kickmix-workbench-boundary-v1", "circuit_sha256": report["circuit"]["sha256"],
            "matched": len(selected), "boundaries": selected,
            "note": "Imported sampled evidence; inspect the cited card for inputs and method. No algebraic proof is implied."}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("summarize")
    s.add_argument("circuit", type=Path); s.add_argument("--out", type=Path, required=True)
    s.add_argument("--author", default=""); s.add_argument("--source", default="")
    s.add_argument("--version", default=""); s.add_argument("--licence", default="")
    s.add_argument("--context", type=Path, help="optional pinned JSON notes keyed by tag path or region id")
    c = sp.add_parser("compare")
    c.add_argument("before", type=Path); c.add_argument("after", type=Path)
    c.add_argument("--out", type=Path, required=True)
    u = sp.add_parser("query")
    u.add_argument("report", type=Path); u.add_argument("--match", default=".")
    u.add_argument("--depth", type=int); u.add_argument("--sort", choices=METRICS, default="toffoli")
    u.add_argument("--limit", type=int, default=20)
    v = sp.add_parser("boundary")
    v.add_argument("report", type=Path); v.add_argument("address", nargs="?")
    td = sp.add_parser("trace-diff")
    td.add_argument("manifest", type=Path); td.add_argument("--out", type=Path, required=True)
    tq = sp.add_parser("trace-query")
    tq.add_argument("report", type=Path); tq.add_argument("--cut"); tq.add_argument("--seed", type=int)
    tp = sp.add_parser("trace-plan")
    tp.add_argument("report", type=Path); tp.add_argument("host_static", type=Path)
    tp.add_argument("donor_static", type=Path); tp.add_argument("--cohort", required=True)
    tp.add_argument("--seed", type=int, required=True)
    px = sp.add_parser("prefix")
    px.add_argument("circuit", type=Path); px.add_argument("op", type=int)
    px.add_argument("--out", type=Path, required=True)
    sm = sp.add_parser("sample-dqi")
    sm.add_argument("source", type=Path); sm.add_argument("prefix", type=Path)
    sm.add_argument("inputs", type=Path); sm.add_argument("--seeds", type=int, nargs="+", required=True)
    sm.add_argument("--engine-package", type=Path, required=True)
    sm.add_argument("--out", type=Path, required=True)
    mp = sp.add_parser("state-map")
    mp.add_argument("manifest", type=Path); mp.add_argument("--cut", required=True)
    mp.add_argument("--out", type=Path, required=True)
    mq = sp.add_parser("state-map-query")
    mq.add_argument("report", type=Path); mq.add_argument("--word", type=int, required=True)
    mc = sp.add_parser("state-map-compare")
    mc.add_argument("before", type=Path); mc.add_argument("before_manifest", type=Path)
    mc.add_argument("after", type=Path); mc.add_argument("after_manifest", type=Path)
    mc.add_argument("--out", type=Path, required=True)
    sc = sp.add_parser("stage-contract")
    sc.add_argument("manifest", type=Path); sc.add_argument("entry", type=Path)
    sc.add_argument("exit", type=Path); sc.add_argument("--out", type=Path, required=True)
    wt = sp.add_parser("wire-transport")
    wt.add_argument("circuit", type=Path); wt.add_argument("static", type=Path)
    wt.add_argument("--out", type=Path, required=True)
    wc = sp.add_parser("transport-compare")
    wc.add_argument("host", type=Path); wc.add_argument("donor", type=Path)
    wc.add_argument("sampled", type=Path); wc.add_argument("--out", type=Path, required=True)
    q = sp.add_parser("cut")
    q.add_argument("circuit", type=Path); q.add_argument("op", type=int)
    q.add_argument("--radius", type=int, default=6)
    bc = sp.add_parser("bilinear-cert")
    bc.add_argument("circuit", type=Path)
    bc.add_argument("--a", required=True, help="qubit register name or half-open range START:END")
    bc.add_argument("--b", required=True, help="qubit register name or half-open range START:END")
    bc.add_argument("--target", required=True, help="qubit register name or half-open range START:END")
    bc.add_argument("--modulus", type=lambda value: int(value, 0), required=True)
    bc.add_argument("--tensor-json", type=Path, help="JSON with explicit basis_products in row-major (a_bit,b_bit) order")
    bc.add_argument("--out", type=Path, required=True)
    bp = sp.add_parser("bilinear-project")
    bp.add_argument("certificate", type=Path)
    bp.add_argument("--output-mask", type=lambda value: int(value, 0), required=True)
    bp.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.cmd == "summarize":
        context = json.loads(a.context.read_text()) if a.context else None
        obj = summarize(Circuit(a.circuit), {k: getattr(a, k) for k in ("author", "source", "version", "licence")}, context)
        if context:
            obj["context"]["context_sha256"] = digest(a.context)
        write_json(a.out, obj)
        print(json.dumps({"report": str(a.out), "sha256": obj["circuit"]["sha256"],
                          "qubits": obj["circuit"]["qubits"], "metrics": obj["metrics"],
                          "regions": len(obj["tag_regions"])}))
    elif a.cmd == "compare":
        obj = compare(json.loads(a.before.read_text()), json.loads(a.after.read_text()))
        write_json(a.out, obj)
        print(json.dumps({"report": str(a.out), "total_delta": obj["total_delta"],
                          "changed_regions": len(obj["changed_regions"])}))
    elif a.cmd == "query":
        if a.limit < 1 or a.limit > 100:
            raise ValueError("limit must be 1..100")
        print(json.dumps(query(json.loads(a.report.read_text()), a.match, a.depth, a.sort, a.limit)))
    elif a.cmd == "boundary":
        print(json.dumps(boundary(json.loads(a.report.read_text()), a.address)))
    elif a.cmd == "trace-diff":
        from trace_diff import analyze
        result = analyze(a.manifest)
        write_json(a.out, result)
        print(json.dumps({"report": str(a.out), "tier": result["tier"], "shots": result["shots"],
                          "seeds": result["seeds"], "cuts": [{"name": c["name"],
                          "mismatches": [r["syndrome_mismatch_slots"] for r in c["rows"]]}
                          for c in result["cuts"]]}))
    elif a.cmd == "trace-query":
        from trace_diff import query as trace_query
        print(json.dumps(trace_query(json.loads(a.report.read_text()), a.cut, a.seed)))
    elif a.cmd == "trace-plan":
        from trace_diff import plan
        print(json.dumps(plan(json.loads(a.report.read_text()), json.loads(a.host_static.read_text()),
                              json.loads(a.donor_static.read_text()), a.cohort, a.seed)))
    elif a.cmd == "prefix":
        from prefix import write_prefix
        print(json.dumps(write_prefix(Circuit(a.circuit), a.op, a.out)))
    elif a.cmd == "sample-dqi":
        from sample_cut import sample
        result = sample(Circuit(a.source), Circuit(a.prefix), a.inputs, a.seeds, a.engine_package, a.out)
        print(json.dumps(result))
    elif a.cmd == "state-map":
        from state_map import screen
        result = screen(a.manifest, a.cut)
        write_json(a.out, result)
        print(json.dumps({k: result[k] for k in ("schema", "tier", "cut", "fit_shots",
                        "variable_donor_bits", "fit_candidate_bits", "full_holdout_pass_bits",
                        "ambiguous_fit_bits", "holdout_bit_errors")}))
    elif a.cmd == "state-map-query":
        from state_map import query as map_query
        print(json.dumps(map_query(json.loads(a.report.read_text()), a.word)))
    elif a.cmd == "state-map-compare":
        from state_map import compare_words
        result = compare_words(json.loads(a.before.read_text()), json.loads(a.after.read_text()),
                               a.before_manifest, a.after_manifest)
        write_json(a.out, result)
        print(json.dumps({k: result[k] for k in ("schema", "tier", "before_cut", "after_cut",
                        "before_full_words", "after_full_words", "common_full_words",
                        "same_full_words", "added_words", "removed_words")}))
    elif a.cmd == "stage-contract":
        from stage_contract import run
        result = run(a.manifest, a.entry, a.exit)
        write_json(a.out, result)
        print(json.dumps({"schema": result["schema"], "tier": result["tier"],
                          "source_sha256": result["source_sha256"], "rows": result["rows"]}))
    elif a.cmd == "wire-transport":
        from wire_transport import analyze
        result = analyze(Circuit(a.circuit), json.loads(a.static.read_text()))
        write_json(a.out, result)
        print(json.dumps({"schema": result["schema"], "tier": result["tier"],
                          "circuit_sha256": result["circuit_sha256"],
                          "stages": result["stages"], "aligned_words": result["aligned_words"]}))
    elif a.cmd == "transport-compare":
        from wire_transport import compare as transport_compare
        result = transport_compare(json.loads(a.host.read_text()), json.loads(a.donor.read_text()),
                                   json.loads(a.sampled.read_text()))
        write_json(a.out, result)
        print(json.dumps({"schema": result["schema"], "tier": result["tier"], "status": result["status"],
                          "testable_words": result["testable_words"], "agree_words": result["agree_words"]}))
    elif a.cmd == "bilinear-cert":
        from bilinear_cert import analyze
        tensor = json.loads(a.tensor_json.read_text()) if a.tensor_json else None
        basis_products = tensor["basis_products"] if isinstance(tensor, dict) else tensor
        result = analyze(Circuit(a.circuit), a.a, a.b, a.target, a.modulus,
                         expected_basis_products=basis_products)
        write_json(a.out, result)
        print(json.dumps({"report": str(a.out), "decomposition_length": result["decomposition_length"],
                          "lower_bound_proved": result["lower_bound_proved"],
                          "gate_counts": result["gate_counts"], "tensor_identity": result["tensor_identity"]}))
    elif a.cmd == "bilinear-project":
        from bilinear_project import analyze
        result = analyze(json.loads(a.certificate.read_text()), a.output_mask)
        write_json(a.out, result)
        print(json.dumps({"report": str(a.out), "existing_terms_selected": result["existing_terms_selected"],
                          "exact_scalar_bilinear_rank": result["exact_scalar_bilinear_rank"],
                          "factorization_verified": result["factorization_verified"]}))
    else:
        print(json.dumps(cut(Circuit(a.circuit), a.op, a.radius), indent=2))


if __name__ == "__main__":
    main()
