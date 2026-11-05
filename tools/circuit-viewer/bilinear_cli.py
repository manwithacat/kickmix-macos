# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""`workbench bilinear-*` verbs: one invocation style for the bilinear tools.

Every verb that needs a multiplication target resolves it the same way:

* register layout: explicit `--a/--b/--target`, else the challenge row's
  `registers`, else KMB registers named a, b, t, else three contiguous
  equal-width ranges (the benchmark contract);
* modulus: `--modulus`, else the challenge row, else the tensor JSON;
* tensor: `--tensor-json`, else the challenge row's `basis_products`, else the
  standard field tensor synthesised from the modulus.

The standalone module scripts (`bilinear_carrier.py` and so on) keep their own
`main()` because hosted benchmark packages copy them next to a different,
stdlib-only `workbench.py`; this module is only used by the fork's workbench.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path


class UserError(ValueError):
    """A bad input that should be reported as JSON with a hint, not a traceback."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


MODULUS_HINT = ("the modulus is a degree-n binary polynomial with constant term 1, "
                "written with its leading bit: 0x13 for GF(16), 0x43 or 0x49 for GF(64); "
                "--challenge MANIFEST --id ID reads it from the benchmark manifest")
LAYOUT_HINT = ("registers are KMB register names (a, b, t) or half-open qubit ranges "
               "START:END such as 0:4; --challenge MANIFEST reads the layout")


def parse_int(value: str) -> int:
    """Integer in any Python base prefix (0x13, 19, 0b10011)."""
    return int(value, 0)


parse_int.__name__ = "integer (e.g. 0x13)"


def quote(value) -> str:
    return shlex.quote(str(value))


def sibling(path: Path, suffix: str) -> Path:
    """A not-yet-existing output path next to `path`, for runnable `next` hints."""
    stem = path.name[:-len(path.suffix)] if path.suffix else path.name
    candidate = path.with_name(f"{stem}-{suffix}")
    n = 2
    while candidate.exists():
        base, dot, ext = suffix.rpartition(".")
        candidate = path.with_name(f"{stem}-{base}{n}{dot}{ext}" if dot else f"{stem}-{suffix}{n}")
        n += 1
    return candidate


def field_table(width: int, modulus: int) -> list[int]:
    from bilinear_synth import field_mul
    return [field_mul(1 << i, 1 << j, width, modulus) for i in range(width) for j in range(width)]


def check_modulus(modulus: int, width: int | None = None) -> int:
    if modulus < 3 or not modulus & 1:
        raise UserError(f"modulus {modulus:#x} is not a binary polynomial with constant term 1",
                        MODULUS_HINT)
    degree = modulus.bit_length() - 1
    if width is not None and degree != width:
        raise UserError(f"modulus {modulus:#x} has degree {degree}, but the registers are "
                        f"{width} qubits wide", MODULUS_HINT)
    return degree


# --------------------------------------------------------------------------- challenge

def load_challenge(spec: Path) -> tuple[Path, dict]:
    path = spec / "manifest.json" if spec.is_dir() else spec
    if not path.exists():
        raise UserError(f"challenge manifest {path} not found",
                        "pass the benchmark manifest.json or the folder that holds it")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise UserError(f"{path} is not JSON: {exc}", "pass the benchmark manifest.json") from exc
    if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
        raise UserError(f"{path} has no 'instances' list", "pass a bilinear challenge manifest")
    return path, data


def challenge_row(manifest: Path, data: dict, row_id: str | None,
                  circuit: Path | None) -> dict:
    rows = data["instances"]
    ids = [r.get("id") for r in rows]
    if row_id is not None:
        for row in rows:
            if row.get("id") == row_id:
                return row
        raise UserError(f"challenge id {row_id!r} is not in {manifest}",
                        f"known ids: {', '.join(map(str, ids[:30]))}")
    if circuit is not None and circuit.is_file():
        sha = hashlib.sha256(circuit.read_bytes()).hexdigest()
        for row in rows:
            if row.get("sha256") == sha:
                return row
        for row in rows:
            if Path(str(row.get("kmb", ""))).name == circuit.name:
                return row
    if len(rows) == 1:
        return rows[0]
    raise UserError("cannot tell which challenge row applies",
                    f"add --id; known ids: {', '.join(map(str, ids[:30]))}")


# --------------------------------------------------------------------------- target

@dataclass
class Target:
    circuit: Path | None
    width: int | None
    modulus: int
    table: list[int] | None          # None means the standard field tensor
    a: str = "a"
    b: str = "b"
    t: str = "t"
    layout_source: str = "default register names a, b, t"
    tensor_source: str = "field"
    flags: list[str] = field(default_factory=list)   # reusable CLI flags for `next`
    circuit_from_row: bool = False

    def circuit_arg(self) -> str:
        """The circuit as a `next` argument; empty when --challenge --id names it."""
        return "" if self.circuit_from_row else quote(self.circuit)

    def full_table(self) -> list[int]:
        if self.table is not None:
            return self.table
        return field_table(self.width, self.modulus)

    def describe(self) -> dict:
        return {"registers": {"a": self.a, "b": self.b, "target": self.t},
                "layout_source": self.layout_source, "modulus": self.modulus,
                "modulus_hex": hex(self.modulus), "tensor": self.tensor_source}

    def tensor_json(self) -> dict:
        return {"schema": "kickmix-bilinear-tensor-v1", "width": self.width,
                "modulus": self.modulus, "kind": "field" if self.table is None else "explicit",
                "source": self.tensor_source, "basis_products": self.full_table()}


def add_target_args(p, *, circuit: str = "required", registers: bool = True) -> None:
    if circuit == "required":
        p.add_argument("circuit", type=Path, nargs="?",
                       help="KMB file; optional with --challenge --id, which names it")
    if registers:
        for flag, default in (("--a", "a"), ("--b", "b"), ("--target", "t")):
            p.add_argument(flag, help=f"register name or half-open range START:END "
                                      f"(default: challenge layout, else KMB register '{default}')")
    p.add_argument("--modulus", type=parse_int,
                   help="field polynomial with leading bit, e.g. 0x13 (default: challenge row)")
    p.add_argument("--tensor-json", type=Path,
                   help="explicit target JSON with width, modulus and row-major basis_products")
    p.add_argument("--challenge", type=Path,
                   help="bilinear challenge manifest.json (or its folder): supplies layout, "
                        "modulus, tensor and, with --id, the KMB")
    p.add_argument("--id", help="challenge row id, e.g. gf16-06")


def _contiguous(width: int) -> tuple[str, str, str]:
    return f"0:{width}", f"{width}:{2 * width}", f"{2 * width}:{3 * width}"


def resolve(args, *, need_circuit: bool = True) -> Target:
    from workbench import Circuit

    circuit: Path | None = getattr(args, "circuit", None)
    row, manifest = None, None
    flags: list[str] = []
    if args.challenge is not None:
        manifest, data = load_challenge(args.challenge)
        row = challenge_row(manifest, data, args.id, circuit)
        flags += ["--challenge", quote(args.challenge), "--id", quote(row.get("id"))]
        if circuit is None and row.get("kmb"):
            circuit = manifest.parent / row["kmb"]
    elif args.id is not None:
        raise UserError("--id needs --challenge MANIFEST", "add --challenge path/to/manifest.json")
    if need_circuit and circuit is None:
        raise UserError("no circuit given",
                        "pass CIRCUIT.kmb, or --challenge MANIFEST --id ID to use the row's KMB")
    if circuit is not None and not circuit.is_file():
        raise UserError(f"circuit {circuit} not found", "check the path to the .kmb file")

    tensor = None
    if args.tensor_json is not None:
        if not args.tensor_json.is_file():
            raise UserError(f"tensor JSON {args.tensor_json} not found",
                            "write one with `workbench bilinear-cert ... --tensor-out FILE`")
        try:
            tensor = json.loads(args.tensor_json.read_text())
        except json.JSONDecodeError as exc:
            raise UserError(f"{args.tensor_json} is not JSON: {exc}",
                            "expected {width, modulus, basis_products}") from exc
        flags += ["--tensor-json", quote(args.tensor_json)]

    modulus = args.modulus
    for source in (row, tensor if isinstance(tensor, dict) else None):
        if source and source.get("modulus") is not None:
            if modulus is None:
                modulus = int(source["modulus"])
            elif int(source["modulus"]) != modulus:
                raise UserError(f"--modulus {modulus:#x} differs from {int(source['modulus']):#x} "
                                f"in the {'challenge row' if source is row else 'tensor JSON'}",
                                "drop --modulus to use the stated one, or check the file")
    if modulus is None:
        raise UserError("modulus unknown", MODULUS_HINT)
    if args.modulus is not None and args.challenge is None:
        flags += ["--modulus", hex(args.modulus)]

    table = None
    tensor_source = "field"
    if isinstance(tensor, dict):
        table, tensor_source = tensor.get("basis_products"), f"tensor JSON {args.tensor_json}"
    elif isinstance(tensor, list):
        table, tensor_source = tensor, f"tensor JSON {args.tensor_json}"
    elif row is not None and row.get("basis_products") is not None:
        table, tensor_source = row["basis_products"], f"challenge row {row.get('id')}"
    if tensor is not None and table is None:
        raise UserError(f"{args.tensor_json} has no basis_products", "expected {width, modulus, basis_products}")

    width = None
    if row is not None and row.get("width") is not None:
        width = int(row["width"])
    elif isinstance(tensor, dict) and tensor.get("width") is not None:
        width = int(tensor["width"])
    target = Target(circuit=circuit, width=width, modulus=modulus, table=table,
                    tensor_source=tensor_source, flags=flags,
                    circuit_from_row=getattr(args, "circuit", None) is None)

    explicit = [getattr(args, k, None) for k in ("a", "b", "target")]
    c = Circuit(circuit) if circuit is not None else None
    if any(v is not None for v in explicit):
        if not all(v is not None for v in explicit):
            raise UserError("give all of --a, --b and --target, or none", LAYOUT_HINT)
        target.a, target.b, target.t = explicit
        target.layout_source = "command line"
        target.flags += ["--a", quote(explicit[0]), "--b", quote(explicit[1]),
                         "--target", quote(explicit[2])]
    elif row is not None and isinstance(row.get("registers"), dict):
        regs = row["registers"]
        values = [regs.get("a"), regs.get("b"), regs.get("target", regs.get("t"))]
        values = [f"{v[0]}:{v[-1] + 1}" if isinstance(v, list) and v else v for v in values]
        if any(not isinstance(v, str) for v in values):
            raise UserError("challenge row registers must give a, b and target", LAYOUT_HINT)
        target.a, target.b, target.t = values
        target.layout_source = f"challenge row {row.get('id')} registers"
    elif c is not None and all(name in c.registers for name in ("a", "b", "t")):
        target.layout_source = "KMB registers a, b, t"
    elif c is not None:
        n = width or (c.qubits // 3 if c.qubits % 3 == 0 else None)
        if n is None:
            raise UserError(f"{c.qubits} qubits do not split into three equal registers",
                            LAYOUT_HINT)
        target.a, target.b, target.t = _contiguous(n)
        target.layout_source = ("challenge contract: a, b, t contiguous" if row is not None
                                else "three contiguous equal-width ranges (no named registers)")
    if c is not None:
        n = c.qubits // 3 if c.qubits % 3 == 0 else None
        if target.width is None:
            target.width = n
    if target.width is None:
        target.width = modulus.bit_length() - 1
    check_modulus(modulus, target.width)
    if table is not None and (len(table) != target.width ** 2):
        raise UserError(f"basis_products has {len(table)} entries; width {target.width} needs "
                        f"{target.width ** 2}", "expected row-major (a_bit, b_bit) products")
    return target


# --------------------------------------------------------------------------- helpers

def _out_free(*paths: Path | None) -> None:
    for path in paths:
        if path is not None and path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")


def _cert(target: Target, circuit: Path | None = None) -> dict:
    from bilinear_cert import analyze
    from workbench import Circuit
    return analyze(Circuit(circuit or target.circuit), target.a, target.b, target.t,
                   target.modulus, expected_basis_products=target.table)


def _write(path: Path, obj: dict) -> None:
    from workbench import write_json
    write_json(path, obj)


# --------------------------------------------------------------------------- verbs

def cmd_cert(args) -> dict:
    target = resolve(args)
    _out_free(args.out, args.tensor_out)
    result = _cert(target)
    result["target_resolution"] = target.describe()
    _write(args.out, result)
    summary = {"report": str(args.out), "decomposition_length": result["decomposition_length"],
               "bilinear_rank_upper_bound": result["bilinear_rank_upper_bound"],
               "lower_bound_proved": result["lower_bound_proved"],
               "gate_counts": result["gate_counts"], "tensor_identity": result["tensor_identity"],
               **target.describe()}
    target.width = result["width"]
    if args.tensor_out is not None:
        tensor = target.tensor_json()
        tensor["certified_circuit_sha256"] = result["circuit_sha256"]
        _write(args.tensor_out, tensor)
        summary["tensor"] = str(args.tensor_out)
    flags = " ".join(target.flags)
    kmb = target.circuit_arg()
    nxt = [f"workbench bilinear-corner {kmb} {flags} --out {quote(sibling(args.out, 'corner.json'))} "
           f"--candidate-out {quote(sibling(args.out, 'corner.kmb'))}",
           f"workbench bilinear-project {quote(args.out)} --output-mask 0x1 "
           f"--out {quote(sibling(args.out, 'scalar.json'))}"]
    tensor_arg = quote(args.tensor_out) if args.tensor_out is not None else f"--modulus {hex(target.modulus)}" \
        if target.table is None else None
    if tensor_arg:
        nxt.append(f"workbench bilinear-structure {tensor_arg} --out {quote(sibling(args.out, 'structure.json'))}")
    summary["next"] = [" ".join(s.split()) for s in nxt]
    return summary


def cmd_project(args) -> dict:
    from bilinear_project import analyze
    certificate = _read_json(args.certificate, "a bilinear-cert report")
    if args.modulus is not None and certificate.get("modulus") != args.modulus:
        raise UserError(f"--modulus {args.modulus:#x} differs from the certificate's "
                        f"{certificate.get('modulus')!r}", "drop --modulus; the certificate pins it")
    _out_free(args.out)
    result = analyze(certificate, args.output_mask)
    _write(args.out, result)
    n = result["width"]
    following = (args.output_mask << 1) & ((1 << n) - 1) or 1
    return {"report": str(args.out), "existing_terms_selected": result["existing_terms_selected"],
            "exact_scalar_bilinear_rank": result["exact_scalar_bilinear_rank"],
            "factorization_verified": result["factorization_verified"],
            "next": [f"workbench bilinear-project {quote(args.certificate)} --output-mask {hex(following)} "
                     f"--out {quote(sibling(args.out, hex(following) + '.json'))}"]}


def _rewrite(args, verb: str) -> dict:
    target = resolve(args)
    _out_free(args.out, args.candidate_out)
    if verb == "corner":
        from bilinear_corner import run
        result = run(target.circuit, target.a, target.b, target.t, target.modulus,
                     args.tensor_json, args.out, args.candidate_out, args.max_moves,
                     table=target.table)
    elif verb == "mechanisms":
        from bilinear_mechanisms import run
        result = run(target.circuit, target.a, target.b, target.t, target.modulus,
                     args.tensor_json, args.out, args.candidate_out, table=target.table)
    else:
        from bilinear_carrier import run
        result = run(target.circuit, args.tensor_json, args.out, args.candidate_out,
                     modulus=target.modulus, table=target.full_table(),
                     registers=(target.a, target.b, target.t))
    summary = {"report": str(args.out), "input_ccx": result["input_ccx"],
               "initial_opportunities": len(result["initial_opportunities"]),
               "final_terms": result["final_terms"],
               "candidate_sha256": result.get("candidate_sha256"), **target.describe()}
    if verb == "carrier":
        summary["opportunities"] = summary["initial_opportunities"]
    flags = " ".join(target.flags)
    others = [o for o in ("corner", "carrier", "mechanisms") if o != verb]
    if args.candidate_out is not None:
        subject = quote(args.candidate_out)
        summary["next"] = [f"workbench bilinear-cert {subject} {flags} "
                           f"--out {quote(sibling(args.out, 'cert.json'))}"]
    elif summary["initial_opportunities"]:
        subject = target.circuit_arg()
        summary["next"] = [f"workbench bilinear-{verb} {subject} {flags} "
                           f"--out {quote(sibling(args.out, 'applied.json'))} "
                           f"--candidate-out {quote(sibling(args.out, 'applied.kmb'))}"]
    else:
        subject = target.circuit_arg()
        summary["next"] = []
        summary["note"] = f"no exact {verb} move applies; try the other rewrite verbs"
    summary["next"] += [f"workbench bilinear-{o} {subject} {flags} "
                        f"--out {quote(sibling(args.out, o + '.json'))}" for o in others]
    summary["next"] = [" ".join(cmd.split()) for cmd in summary["next"]][:3]
    return summary


def cmd_corner(args) -> dict:
    return _rewrite(args, "corner")


def cmd_mechanisms(args) -> dict:
    return _rewrite(args, "mechanisms")


def cmd_carrier(args) -> dict:
    return _rewrite(args, "carrier")


def _tensor_target(args) -> tuple[dict, str, str | None]:
    """Explicit target JSON, or the field tensor from --modulus / --challenge."""
    if args.target is not None:
        if not args.target.is_file():
            raise UserError(f"tensor JSON {args.target} not found",
                            "write one with `workbench bilinear-cert ... --tensor-out FILE`, "
                            "or pass --modulus to use the standard field tensor")
        data = _read_json(args.target, "a tensor JSON {width, modulus, basis_products}")
        if not isinstance(data, dict) or "basis_products" not in data:
            raise UserError(f"{args.target} has no basis_products",
                            "expected {width, modulus, basis_products}")
        if args.modulus is not None and data.get("modulus") not in (None, args.modulus):
            raise UserError(f"--modulus {args.modulus:#x} differs from {data.get('modulus')!r} "
                            f"in {args.target}", "drop --modulus")
        data = dict(data)
        if data.get("modulus") is None and args.modulus is not None:
            data["modulus"] = args.modulus
        return data, hashlib.sha256(args.target.read_bytes()).hexdigest(), quote(args.target)
    args.tensor_json = None
    args.circuit = None
    target = resolve(args, need_circuit=False)
    flag = " ".join(target.flags) or f"--modulus {hex(target.modulus)}"
    data = target.tensor_json()
    serial = json.dumps(data["basis_products"], separators=(",", ":")).encode()
    return data, hashlib.sha256(serial).hexdigest(), flag


def cmd_structure(args) -> dict:
    from bilinear_structure import analyze
    data, sha, arg = _tensor_target(args)
    _out_free(args.out)
    result = analyze(data["basis_products"], data["width"], witness_limit=args.witness_limit)
    result["source_sha256"] = sha
    result["tensor_source"] = data.get("source", "tensor JSON")
    _write(args.out, result)
    return {"report": str(args.out), "flattening_ranks": result["flattening_ranks"],
            "minimum_left_rank": result["sides"]["left"]["minimum_rank"],
            "minimum_right_rank": result["sides"]["right"]["minimum_rank"],
            "zero_pairs": result["nonzero_annihilating_input_pairs"],
            "split_witness": result["split_witness"] is not None,
            "next": [f"workbench bilinear-isotopy {arg} --out {quote(sibling(args.out, 'isotopy.json'))}"]}


def cmd_isotopy(args) -> dict:
    from bilinear_basis_explorer import recover
    data, _, arg = _tensor_target(args)
    if data.get("modulus") is None:
        raise UserError("modulus unknown for the field-isotopy search", MODULUS_HINT)
    _out_free(args.out)
    result = recover(data["width"], int(data["modulus"]), data["basis_products"])
    _write(args.out, result)
    return {"report": str(args.out), "verified_pairs": result["verified_pairs"],
            "verification_mode": result["verification_mode"],
            "tensor_sha256": result["tensor_sha256"],
            "next": [f"workbench bilinear-transport {arg} --core CORE.kmb "
                     f"--candidate-out {quote(sibling(args.out, 'transported.kmb'))} "
                     f"--out {quote(sibling(args.out, 'transport.json'))}",
                     f"workbench bilinear-structure {arg} --out {quote(sibling(args.out, 'structure.json'))}"]}


def cmd_transport(args) -> dict:
    from bilinear_transport import run
    if not args.core.is_file():
        raise UserError(f"core KMB {args.core} not found",
                        "the core is a certified standard-field multiplier for the target's modulus")
    data, _, arg = _tensor_target(args)
    if args.target is None:
        tmp = args.out.with_name(args.out.name + ".tensor.json")
        _out_free(tmp)
        _write(tmp, data)
        target_path = tmp
    else:
        target_path = args.target
    _out_free(args.out, args.candidate_out)
    report = run(target_path, args.core, args.candidate_out, args.out)
    return {"report": str(args.out), "candidate_sha256": report["candidate_sha256"],
            "candidate_gate_counts": report["candidate_gate_counts"],
            "verified_pairs": report["witness"]["verified_pairs"],
            "next": [" ".join(f"workbench bilinear-corner {quote(args.candidate_out)} "
                              f"--modulus {hex(int(data['modulus']))} --tensor-json {quote(target_path)} "
                              f"--out {quote(sibling(args.out, 'corner.json'))}".split())]}


def cmd_synth(args) -> dict:
    from bilinear_synth import kmb_bytes, synthesize, validated_terms
    data = _read_json(args.terms, "a JSON with width, modulus and terms (a bilinear-cert report works)")
    if not isinstance(data, dict):
        raise UserError(f"{args.terms} is not a JSON object", "expected {width, modulus, terms}")
    data = dict(data)
    if args.modulus is not None:
        if data.get("modulus") not in (None, args.modulus):
            raise UserError(f"--modulus {args.modulus:#x} differs from {data['modulus']!r} in {args.terms}",
                            "drop --modulus")
        data["modulus"] = args.modulus
    if data.get("modulus") is None:
        raise UserError("modulus unknown", MODULUS_HINT)
    if data.get("width") is None:
        data["width"] = int(data["modulus"]).bit_length() - 1
    for key in ("terms",):
        if key not in data:
            raise UserError(f"{args.terms} has no '{key}' list",
                            "give rank-one terms [{a_mask, b_mask, output_mask}, ...]")
    if "normalized_terms" in data and data.get("schema", "").startswith("kickmix-bilinear-certificate"):
        data["terms"] = data["normalized_terms"]
    width, modulus, terms = validated_terms(data)
    _out_free(args.out)
    ops, streams = synthesize(width, terms)
    raw = kmb_bytes(width, ops, streams)
    with args.out.open("xb") as fh:
        fh.write(raw)
    tensor = ""
    if data.get("basis_products") is not None:
        tensor_path = args.out.with_name(args.out.name + ".tensor.json")
        if not tensor_path.exists():
            _write(tensor_path, {"schema": "kickmix-bilinear-tensor-v1", "width": width,
                                 "modulus": modulus, "kind": "explicit",
                                 "basis_products": data["basis_products"]})
        tensor = f" --tensor-json {quote(tensor_path)}"
    return {"schema": "bilinear-synth-v1", "tier": "static model / projection",
            "out": str(args.out), "sha256": hashlib.sha256(raw).hexdigest(),
            "width": width, "modulus": modulus, "tensor_precheck": True, "ccx": len(terms),
            "cx": len(streams["qq0"]),
            "next": [f"workbench bilinear-cert {quote(args.out)} --modulus {hex(modulus)}{tensor} "
                     f"--out {quote(sibling(args.out, 'cert.json'))}"]}


def cmd_sieve(args) -> dict:
    from bilinear_sieve import finalize, scan
    if args.sieve == "scan":
        for path, what in ((args.manifest, "challenge manifest"), (args.pool, "candidate pool folder")):
            if not path.exists():
                raise UserError(f"{what} {path} not found", "check the path")
        result = scan(args.manifest, args.id, args.pool, args.out, args.review_size)
        result = dict(result) if isinstance(result, dict) else {"result": result}
        result["next"] = [f"workbench bilinear-sieve finalize --audit {quote(args.out / 'audit.json')} "
                          f"--packet {quote(args.out / 'review_packet.json')} --decisions DECISIONS.json "
                          f"--out {quote(sibling(args.out, 'shortlist'))}"]
        return result
    for path in (args.audit, args.packet, args.decisions):
        if not path.exists():
            raise UserError(f"{path} not found", "finalize needs the scan's audit.json, "
                            "review_packet.json and the reviewer's decisions JSON")
    result = finalize(args.audit, args.packet, args.decisions, args.out, args.max_shortlist)
    return dict(result)


def _read_json(path: Path, what: str):
    if not path.is_file():
        raise UserError(f"{path} not found", f"expected {what}")
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UserError(f"{path} is not JSON: {exc}", f"expected {what}") from exc


# --------------------------------------------------------------------------- argparse

VERBS = {
    "bilinear-cert": "certify a bilinear multiplier KMB from gate bytes (exact)",
    "bilinear-project": "exact scalar bilinear rank of one output parity (exact)",
    "bilinear-corner": "exact 3-to-2 L-corner rewrites; greedy application (heuristic search)",
    "bilinear-mechanisms": "exact same-parity rank compressions; greedy application (heuristic search)",
    "bilinear-carrier": "exact carrier-extension rewrites; greedy application (heuristic search)",
    "bilinear-structure": "rank, annihilator and split invariants of a tensor (exact)",
    "bilinear-isotopy": "field-isotopy witness F(a,b)=H(A(a)*B(b)) (exact; was bilinear_basis_explorer.py)",
    "bilinear-transport": "carry a field core onto an isotopic explicit tensor (exact)",
    "bilinear-synth": "write an ancilla-free KMB from rank-one terms (tensor precheck)",
    "bilinear-sieve": "screen a pool of candidate KMBs against a challenge row (exact checks)",
}


def register(sp) -> dict:
    handlers = {}

    bc = sp.add_parser("bilinear-cert", help=VERBS["bilinear-cert"])
    add_target_args(bc)
    bc.add_argument("--out", type=Path, required=True)
    bc.add_argument("--tensor-out", type=Path,
                    help="also write the certified target tensor JSON (for structure/isotopy/carrier)")
    handlers["bilinear-cert"] = cmd_cert

    bp = sp.add_parser("bilinear-project", help=VERBS["bilinear-project"])
    bp.add_argument("certificate", type=Path)
    bp.add_argument("--output-mask", type=parse_int, required=True)
    bp.add_argument("--modulus", type=parse_int, help="optional check against the certificate")
    bp.add_argument("--out", type=Path, required=True)
    handlers["bilinear-project"] = cmd_project

    for verb, fn in (("bilinear-corner", cmd_corner), ("bilinear-mechanisms", cmd_mechanisms),
                     ("bilinear-carrier", cmd_carrier)):
        r = sp.add_parser(verb, help=VERBS[verb])
        add_target_args(r)
        r.add_argument("--out", type=Path, required=True)
        r.add_argument("--candidate-out", type=Path, help="write and byte-certify the reduced KMB")
        if verb == "bilinear-corner":
            r.add_argument("--max-moves", type=int, default=32)
        handlers[verb] = fn

    for verb, fn in (("bilinear-structure", cmd_structure), ("bilinear-isotopy", cmd_isotopy),
                     ("bilinear-transport", cmd_transport)):
        r = sp.add_parser(verb, help=VERBS[verb])
        r.add_argument("target", type=Path, nargs="?",
                       help="explicit tensor JSON; omit to use the field tensor from --modulus "
                            "or --challenge")
        add_target_args(r, circuit="none", registers=False)
        if verb == "bilinear-structure":
            r.add_argument("--witness-limit", type=int, default=8)
        if verb == "bilinear-transport":
            r.add_argument("--core", type=Path, required=True,
                           help="certified standard multiplication KMB for the target's modulus")
            r.add_argument("--candidate-out", type=Path, required=True)
        r.add_argument("--out", type=Path, required=True)
        handlers[verb] = fn

    sy = sp.add_parser("bilinear-synth", help=VERBS["bilinear-synth"])
    sy.add_argument("terms", type=Path, help="JSON with width, modulus and terms, or a bilinear-cert report")
    sy.add_argument("--modulus", type=parse_int, help="supply or check the modulus")
    sy.add_argument("--out", type=Path, required=True)
    handlers["bilinear-synth"] = cmd_synth

    sv = sp.add_parser("bilinear-sieve", help=VERBS["bilinear-sieve"])
    ss = sv.add_subparsers(dest="sieve", required=True)
    start = ss.add_parser("scan")
    start.add_argument("--manifest", type=Path, required=True)
    start.add_argument("--id", required=True)
    start.add_argument("--pool", type=Path, required=True)
    start.add_argument("--out", type=Path, required=True)
    start.add_argument("--review-size", type=int, default=6)
    end = ss.add_parser("finalize")
    end.add_argument("--audit", type=Path, required=True)
    end.add_argument("--packet", type=Path, required=True)
    end.add_argument("--decisions", type=Path, required=True)
    end.add_argument("--out", type=Path, required=True)
    end.add_argument("--max-shortlist", type=int, default=6)
    handlers["bilinear-sieve"] = cmd_sieve
    return handlers
