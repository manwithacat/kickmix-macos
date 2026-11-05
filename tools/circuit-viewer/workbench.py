#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Read-only, agent-first circuit workbench for kickmix .kmb files.

Every verb prints one JSON object on stdout: a bounded summary with a `next`
list of commands that work on its output, or {"error": ..., "hint": ...} with a
non-zero exit. Reports go to --out files and are never overwritten. Counts are
static (netlist) evidence, not per-shot cost; the official verifier decides
correctness and score. Public circuits are parsed as bytes, never executed.

Verbs, by question:
  where do the Toffolis sit?      summarize, query (--depth, --group path), cut, compare
  imported findings               boundary
  sampled DQI traces (diagnostic) prefix, sample-dqi, trace-diff, trace-query, trace-plan,
                                  state-map, state-map-query, state-map-compare,
                                  stage-contract, wire-transport, transport-compare
  bilinear multipliers (exact)    bilinear-cert, bilinear-project, bilinear-structure,
                                  bilinear-isotopy, bilinear-transport, bilinear-synth,
                                  bilinear-sieve
  bilinear rewrites (greedy)      bilinear-corner, bilinear-mechanisms, bilinear-carrier

Query depth is tag nesting: 0 = top-level stages, 1 = their children.

Examples (`workbench` is tools/circuit-viewer/workbench from the fork root):
  workbench summarize circuit.kmb --out report.json
  workbench query report.json --depth 0 --sort toffoli
  workbench query report.json --match '^scan/' --group path
  workbench cut circuit.kmb 3 --radius 4
  workbench bilinear-cert --challenge challenge/manifest.json --id gf16-06 --out cert.json --tensor-out tensor.json
  workbench bilinear-corner gf16.kmb --modulus 0x13 --out corner.json --candidate-out corner.kmb
  workbench bilinear-structure --modulus 0x13 --out structure.json
  workbench bilinear-carrier gf16.kmb --tensor-json tensor.json --out carrier.json

Run `workbench VERB --help` for a verb's flags.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import struct
import sys
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


def query(report: dict, pattern: str, depth: int | None, sort: str, limit: int,
          group: str | None = None) -> dict:
    """Bounded region rows. Depth is tag nesting: 0 = top-level stages, 1 = their children.

    With group="path", region instances sharing a tag path (e.g. 8,050 `scan/...`
    instances) are summed into one row per path before sorting and limiting.
    """
    diff = report["schema"] == "kickmix-workbench-diff-v1"
    rows = report["changed_regions"] if diff else report["tag_regions"]
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"bad --match regular expression {pattern!r}: {exc}") from exc
    selected = [r for r in rows if rx.search(r["id"]) and (depth is None or r["depth"] == depth)]
    key = "delta" if diff else "metrics"
    if group == "path":
        groups: dict[str, dict] = {}
        for r in selected:
            path = r["id"].rsplit("#", 1)[0]
            g = groups.get(path)
            if g is None:
                g = groups[path] = {"path": path, "depth": r["depth"], "instances": 0, key: zero()}
                if not diff:
                    g["first_start"], g["last_end"] = r["start"], r["end"]
            g["instances"] += 1
            add(g[key], r[key])
            if not diff:
                g["first_start"] = min(g["first_start"], r["start"])
                g["last_end"] = max(g["last_end"], r["end"])
        selected = list(groups.values())
    selected.sort(key=lambda r: abs(r[key][sort]), reverse=True)
    result = {"schema": "kickmix-workbench-query-v1", "tier": "static model / projection",
              "report_schema": report["schema"], "matched": len(selected),
              "shown": min(len(selected), limit), "sort": sort, "group": group,
              "rows": selected[:limit]}
    if not selected:
        depths: dict[str, int] = {}
        for r in rows:
            depths[str(r["depth"])] = depths.get(str(r["depth"]), 0) + 1
        result["depths_present"] = depths
        result["hint"] = ("no regions match; depth is tag nesting (0 = top-level stages). "
                          + ("This report has no INFO_TAG regions: use `cut` on op ranges instead."
                             if not rows else f"Depths present: {depths}. Try --depth 0 --sort toffoli."))
    return result


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


class JsonArgumentParser(argparse.ArgumentParser):
    """Argument errors become one JSON object on stdout, never a usage dump."""

    def error(self, message: str) -> None:  # type: ignore[override]
        fail(message, "run `workbench --help` to list every verb" if self.prog == "workbench"
             else f"run `{self.prog} --help` for its flags")


def fail(message: str, hint: str, code: int = 2) -> None:
    print(json.dumps({"error": message, "hint": hint}))
    raise SystemExit(code)


def q(value) -> str:
    return shlex.quote(str(value))


def read_report(path: Path, what: str = "a workbench JSON report") -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found")
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UserInputError(f"{path} is not JSON ({exc})", f"expected {what}") from exc


class UserInputError(ValueError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


DEPTH_HELP = "tag nesting level: 0 = top-level stages, 1 = their children; omit for all levels"

VERB_HELP = {
    "summarize": "index a KMB: totals and per-INFO_TAG-region static counts, SHA-pinned report",
    "query": "bounded rows of a report or diff (default 20, max 100); --group path aggregates",
    "cut": "op addresses and operands around one operation",
    "compare": "static count deltas between two reports (not per-shot savings)",
    "boundary": "imported boundary findings from a report's context JSON",
    "prefix": "write an unchanged KMB prefix at a depth-zero cut",
    "sample-dqi": "sample a DQI prefix with a compatible engine package (diagnostic)",
    "trace-diff": "compare pinned sampled cut dumps by cohort (sampled)",
    "trace-query": "bounded view of one cut and seed of a trace report",
    "trace-plan": "plan the next sampled bisection between two cuts (heuristic)",
    "state-map": "fit a bit-to-bit coordinate map at a pinned cut (sampled, heuristic)",
    "state-map-query": "one 12-bit donor word of a state map",
    "state-map-compare": "compare two state maps on their holdouts",
    "stage-contract": "check one stage of one circuit preserves basis state (sampled)",
    "wire-transport": "follow unconditional SWAPs through synthesis/convert (static screen)",
    "transport-compare": "compare static wire transport with a sampled state map",
}


def build_parser() -> argparse.ArgumentParser:
    import bilinear_cli
    p = JsonArgumentParser(prog="workbench", description=__doc__,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True, metavar="VERB")
    s = sp.add_parser("summarize", help=VERB_HELP["summarize"])
    s.add_argument("circuit", type=Path); s.add_argument("--out", type=Path, required=True)
    s.add_argument("--author", default=""); s.add_argument("--source", default="")
    s.add_argument("--version", default=""); s.add_argument("--licence", default="")
    s.add_argument("--context", type=Path, help="optional pinned JSON notes keyed by tag path or region id")
    u = sp.add_parser("query", help=VERB_HELP["query"])
    u.add_argument("report", type=Path)
    u.add_argument("--match", default=".", help="case-insensitive regex on region id (path#occurrence)")
    u.add_argument("--depth", type=int, help=DEPTH_HELP)
    u.add_argument("--sort", choices=METRICS, default="toffoli")
    u.add_argument("--group", choices=("path",), help="sum all instances of each tag path into one row")
    u.add_argument("--limit", type=int, default=20, help="rows to show, 1..100")
    q_ = sp.add_parser("cut", help=VERB_HELP["cut"])
    q_.add_argument("circuit", type=Path); q_.add_argument("op", type=int)
    q_.add_argument("--radius", type=int, default=6)
    c = sp.add_parser("compare", help=VERB_HELP["compare"])
    c.add_argument("before", type=Path); c.add_argument("after", type=Path)
    c.add_argument("--out", type=Path, required=True)
    v = sp.add_parser("boundary", help=VERB_HELP["boundary"])
    v.add_argument("report", type=Path); v.add_argument("address", nargs="?")
    px = sp.add_parser("prefix", help=VERB_HELP["prefix"])
    px.add_argument("circuit", type=Path); px.add_argument("op", type=int)
    px.add_argument("--out", type=Path, required=True)
    sm = sp.add_parser("sample-dqi", help=VERB_HELP["sample-dqi"])
    sm.add_argument("source", type=Path); sm.add_argument("prefix", type=Path)
    sm.add_argument("inputs", type=Path); sm.add_argument("--seeds", type=int, nargs="+", required=True)
    sm.add_argument("--engine-package", type=Path, required=True)
    sm.add_argument("--out", type=Path, required=True)
    td = sp.add_parser("trace-diff", help=VERB_HELP["trace-diff"])
    td.add_argument("manifest", type=Path); td.add_argument("--out", type=Path, required=True)
    tq = sp.add_parser("trace-query", help=VERB_HELP["trace-query"])
    tq.add_argument("report", type=Path); tq.add_argument("--cut"); tq.add_argument("--seed", type=int)
    tp = sp.add_parser("trace-plan", help=VERB_HELP["trace-plan"])
    tp.add_argument("report", type=Path); tp.add_argument("host_static", type=Path)
    tp.add_argument("donor_static", type=Path); tp.add_argument("--cohort", required=True)
    tp.add_argument("--seed", type=int, required=True)
    mp = sp.add_parser("state-map", help=VERB_HELP["state-map"])
    mp.add_argument("manifest", type=Path); mp.add_argument("--cut", required=True)
    mp.add_argument("--out", type=Path, required=True)
    mq = sp.add_parser("state-map-query", help=VERB_HELP["state-map-query"])
    mq.add_argument("report", type=Path); mq.add_argument("--word", type=int, required=True)
    mc = sp.add_parser("state-map-compare", help=VERB_HELP["state-map-compare"])
    mc.add_argument("before", type=Path); mc.add_argument("before_manifest", type=Path)
    mc.add_argument("after", type=Path); mc.add_argument("after_manifest", type=Path)
    mc.add_argument("--out", type=Path, required=True)
    sc = sp.add_parser("stage-contract", help=VERB_HELP["stage-contract"])
    sc.add_argument("manifest", type=Path); sc.add_argument("entry", type=Path)
    sc.add_argument("exit", type=Path); sc.add_argument("--out", type=Path, required=True)
    wt = sp.add_parser("wire-transport", help=VERB_HELP["wire-transport"])
    wt.add_argument("circuit", type=Path); wt.add_argument("static", type=Path)
    wt.add_argument("--out", type=Path, required=True)
    wc = sp.add_parser("transport-compare", help=VERB_HELP["transport-compare"])
    wc.add_argument("host", type=Path); wc.add_argument("donor", type=Path)
    wc.add_argument("sampled", type=Path); wc.add_argument("--out", type=Path, required=True)
    p.set_defaults(bilinear_handlers=bilinear_cli.register(sp))
    return p


def run(a: argparse.Namespace) -> dict:
    """Execute one parsed verb and return its JSON summary (with a `next` list)."""
    if a.cmd in a.bilinear_handlers:
        return a.bilinear_handlers[a.cmd](a)
    if a.cmd == "summarize":
        context = read_report(a.context, "a context JSON") if a.context else None
        if a.out.exists():
            raise FileExistsError(f"refusing to overwrite {a.out}")
        obj = summarize(Circuit(a.circuit), {k: getattr(a, k) for k in ("author", "source", "version", "licence")}, context)
        if context:
            obj["context"]["context_sha256"] = digest(a.context)
        write_json(a.out, obj)
        regions = obj["tag_regions"]
        if regions:
            nxt = [f"workbench query {q(a.out)} --depth 0 --sort toffoli"]
            if any(r["depth"] == 1 for r in regions):
                nxt.append(f"workbench query {q(a.out)} --depth 1 --group path --sort toffoli")
            nxt.append(f"workbench cut {q(a.circuit)} {regions[0]['start']} --radius 4")
        else:
            nxt = [f"workbench cut {q(a.circuit)} 0 --radius 6"]
        out = {"report": str(a.out), "sha256": obj["circuit"]["sha256"],
               "qubits": obj["circuit"]["qubits"], "metrics": obj["metrics"],
               "regions": len(regions), "next": nxt}
        if not regions:
            out["note"] = "no INFO_TAG regions: query returns nothing; use cut on op ranges"
        return out
    if a.cmd == "compare":
        obj = compare(read_report(a.before), read_report(a.after))
        write_json(a.out, obj)
        return {"report": str(a.out), "total_delta": obj["total_delta"],
                "changed_regions": len(obj["changed_regions"]),
                "next": [f"workbench query {q(a.out)} --sort toffoli --limit 10"]
                if obj["changed_regions"] else []}
    if a.cmd == "query":
        if a.limit < 1 or a.limit > 100:
            raise UserInputError("--limit must be 1..100", "the full table is in the report file")
        report = read_report(a.report)
        if report.get("schema") not in ("kickmix-workbench-v1", "kickmix-workbench-diff-v1"):
            raise UserInputError(f"{a.report} is not a workbench report or diff",
                                 "query reads the JSON written by `summarize` or `compare`")
        result = query(report, a.match, a.depth, a.sort, a.limit, a.group)
        nxt = []
        if result["rows"]:
            top = result["rows"][0]
            path = top.get("path") or top["id"].rsplit("#", 1)[0]
            all_rows = report.get("tag_regions") or report.get("changed_regions") or []
            if a.group:
                nxt.append(f"workbench query {q(a.report)} --match {q('^' + re.escape(path) + '#')} --limit 5")
            if any(r["id"].startswith(path + "/") for r in all_rows):
                nxt.append(f"workbench query {q(a.report)} --match {q('^' + re.escape(path) + '/')} "
                           f"--group path --sort {a.sort}")
        result["next"] = nxt
        return result
    if a.cmd == "boundary":
        result = boundary(read_report(a.report), a.address)
        result["next"] = []
        return result
    if a.cmd == "cut":
        c = Circuit(a.circuit)
        if not 0 <= a.op < len(c.ops):
            raise UserInputError(f"op {a.op} is outside the circuit (0..{len(c.ops) - 1})",
                                 "op indices are 0-based; `summarize` gives region start/end")
        result = cut(c, a.op, a.radius)
        end = result["range"][1]
        result["next"] = ([f"workbench cut {q(a.circuit)} {end + a.radius} --radius {a.radius}"]
                          if end + a.radius < len(c.ops) else [])
        return result
    if a.cmd == "trace-diff":
        from trace_diff import analyze
        result = analyze(a.manifest)
        write_json(a.out, result)
        cuts = [c["name"] for c in result["cuts"]]
        return {"report": str(a.out), "tier": result["tier"], "shots": result["shots"],
                "seeds": result["seeds"], "cuts": [{"name": c["name"],
                "mismatches": [r["syndrome_mismatch_slots"] for r in c["rows"]]}
                for c in result["cuts"]],
                "next": [f"workbench trace-query {q(a.out)} --cut {q(cuts[0])}"] if cuts else []}
    if a.cmd == "trace-query":
        from trace_diff import query as trace_query
        return {**trace_query(read_report(a.report), a.cut, a.seed), "next": []}
    if a.cmd == "trace-plan":
        from trace_diff import plan
        return {**plan(read_report(a.report), read_report(a.host_static),
                       read_report(a.donor_static), a.cohort, a.seed), "next": []}
    if a.cmd == "prefix":
        from prefix import write_prefix
        return {**write_prefix(Circuit(a.circuit), a.op, a.out), "next": []}
    if a.cmd == "sample-dqi":
        from sample_cut import sample
        return {**sample(Circuit(a.source), Circuit(a.prefix), a.inputs, a.seeds,
                         a.engine_package, a.out), "next": []}
    if a.cmd == "state-map":
        from state_map import screen
        result = screen(a.manifest, a.cut)
        write_json(a.out, result)
        return {**{k: result[k] for k in ("schema", "tier", "cut", "fit_shots",
                "variable_donor_bits", "fit_candidate_bits", "full_holdout_pass_bits",
                "ambiguous_fit_bits", "holdout_bit_errors")},
                "next": [f"workbench state-map-query {q(a.out)} --word 0"]}
    if a.cmd == "state-map-query":
        from state_map import query as map_query
        return {**map_query(read_report(a.report), a.word), "next": []}
    if a.cmd == "state-map-compare":
        from state_map import compare_words
        result = compare_words(read_report(a.before), read_report(a.after),
                               a.before_manifest, a.after_manifest)
        write_json(a.out, result)
        return {**{k: result[k] for k in ("schema", "tier", "before_cut", "after_cut",
                "before_full_words", "after_full_words", "common_full_words",
                "same_full_words", "added_words", "removed_words")}, "next": []}
    if a.cmd == "stage-contract":
        from stage_contract import run as contract_run
        result = contract_run(a.manifest, a.entry, a.exit)
        write_json(a.out, result)
        return {"schema": result["schema"], "tier": result["tier"],
                "source_sha256": result["source_sha256"], "rows": result["rows"], "next": []}
    if a.cmd == "wire-transport":
        from wire_transport import analyze
        result = analyze(Circuit(a.circuit), read_report(a.static))
        write_json(a.out, result)
        return {"schema": result["schema"], "tier": result["tier"],
                "circuit_sha256": result["circuit_sha256"],
                "stages": result["stages"], "aligned_words": result["aligned_words"], "next": []}
    if a.cmd == "transport-compare":
        from wire_transport import compare as transport_compare
        result = transport_compare(read_report(a.host), read_report(a.donor), read_report(a.sampled))
        write_json(a.out, result)
        return {"schema": result["schema"], "tier": result["tier"], "status": result["status"],
                "testable_words": result["testable_words"], "agree_words": result["agree_words"],
                "next": []}
    raise UserInputError(f"unknown verb {a.cmd}", "run `workbench --help`")


def hint_for(exc: BaseException, verb: str | None) -> str:
    text = str(exc)
    if isinstance(exc, FileExistsError):
        return "outputs are never overwritten; choose a new --out / --candidate-out path"
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, NotADirectoryError)):
        return "check the path; run from the fork root or give an absolute path"
    if isinstance(exc, KeyError):
        return f"input JSON lacks field {text}; is this the right kind of file for `{verb}`?"
    if "tensor mismatch" in text:
        return ("the circuit does not multiply under this target: check --modulus (0x13 for GF(16), "
                "0x43 or 0x49 for GF(64)) and the register layout, or pass --challenge MANIFEST --id ID")
    if "modulus" in text:
        from bilinear_cli import MODULUS_HINT
        return MODULUS_HINT
    if "register" in text:
        from bilinear_cli import LAYOUT_HINT
        return LAYOUT_HINT
    if "KMB" in text or "packet" in text or "mmap" in text:
        return "expected a kickmix KMB v2 circuit file (.kmb); convert .kmx with kickmix first"
    return f"run `workbench {verb or ''} --help`".replace("  ", " ")


def main(argv: list[str] | None = None) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from bilinear_cli import UserError
    parser = build_parser()
    a = parser.parse_args(argv)
    try:
        result = run(a)
    except (UserError, UserInputError) as exc:
        fail(str(exc), exc.hint or hint_for(exc, a.cmd))
    except (OSError, ValueError, KeyError, TypeError, IndexError, struct.error,
            UnicodeDecodeError) as exc:
        fail(str(exc) if not isinstance(exc, KeyError) else f"missing field {exc}",
             hint_for(exc, a.cmd))
    print(json.dumps(result, indent=2 if a.cmd == "cut" else None))


if __name__ == "__main__":
    main()
