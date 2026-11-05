"""Recover a bilinear multiplier certificate from KMB gates, without tags.

This deliberately narrow proof engine accepts unconditional X/CX/SWAP/CCX and
INFO_TAG only. Every CCX must multiply a pure linear form of register a by a
pure linear form of register b; all other circuits fail closed. The final
affine map and every product's propagated output mask are checked directly.
"""

from __future__ import annotations

from collections import Counter


def _register(circuit, spec: str) -> list[int]:
    if ":" in spec:
        left, right = spec.split(":", 1)
        if not (left.isdigit() and right.isdigit()) or not (
                int(left) < int(right) <= circuit.qubits):
            raise ValueError(f"bad register range {spec!r}: expected START:END with "
                             f"0 <= START < END <= {circuit.qubits}")
        ids = list(range(int(left), int(right)))
    else:
        if spec not in circuit.register_ids:
            raise ValueError(f"unknown qubit register {spec!r}; KMB registers: "
                             f"{', '.join(circuit.register_ids) or 'none'}")
        contents = circuit.register_ids[spec]
        if any(not is_qubit for is_qubit, _ in contents):
            raise ValueError(f"register {spec!r} contains classical bits")
        ids = [index for _, index in contents]
    if not ids:
        raise ValueError(f"empty register {spec!r}")
    return ids


def _field_mul(a: int, b: int, width: int, modulus: int) -> int:
    out = 0
    for _ in range(width):
        if b & 1:
            out ^= a
        b >>= 1
        a <<= 1
        if a >> width & 1:
            a ^= modulus
    return out


def analyze(circuit, a_spec: str, b_spec: str, target_spec: str, modulus: int, *,
            expected_basis_products: list[int] | None = None) -> dict:
    from workbench import digest

    a_ids, b_ids, t_ids = (_register(circuit, spec) for spec in
                           (a_spec, b_spec, target_spec))
    n = len(a_ids)
    if len(b_ids) != n or len(t_ids) != n:
        raise ValueError("a, b and target widths must match")
    if set(a_ids) & set(b_ids) or set(a_ids) & set(t_ids) or set(b_ids) & set(t_ids):
        raise ValueError("a, b and target qubits overlap")
    if sorted(a_ids + b_ids + t_ids) != list(range(circuit.qubits)):
        raise ValueError("certificate requires exactly the three declared registers; no ancilla")
    if modulus.bit_length() != n + 1 or not modulus & 1:
        raise ValueError("modulus must be a monic degree-n polynomial with nonzero constant")
    if expected_basis_products is not None:
        if len(expected_basis_products) != n * n or any(
            not isinstance(value, int) or not 0 <= value < (1 << n)
            for value in expected_basis_products
        ):
            raise ValueError("expected basis products must contain n*n width-bit integers")
    if circuit.bits:
        raise ValueError("classical bits are outside this certificate model")

    # Affine bit n*3 is the constant. Marker bits track CCX products through
    # all later linear gates, independently of INFO_TAG structure.
    constant = 1 << (3 * n)
    linear = [0] * circuit.qubits
    for i, wire in enumerate(a_ids + b_ids + t_ids):
        linear[wire] = 1 << i
    products = [0] * circuit.qubits
    terms = []
    counts = Counter()
    s = {name: circuit.stream(name) for name in
         ("qqq0", "qqq1", "qqq2", "qq0", "qq1", "q0")}
    q3 = q2 = q1 = 0
    a_mask, b_mask = (1 << n) - 1, ((1 << n) - 1) << n

    for op, typ0 in enumerate(circuit.ops):
        typ = int(typ0)
        if typ == 6:
            continue
        if typ == 17:  # X
            wire = int(s["q0"][q1]); q1 += 1
            linear[wire] ^= constant
            counts["x"] += 1
        elif typ in (33, 34):  # CX, SWAP
            left, right = int(s["qq0"][q2]), int(s["qq1"][q2]); q2 += 1
            if typ == 33:
                linear[right] ^= linear[left]
                products[right] ^= products[left]
                counts["cx"] += 1
            else:
                linear[left], linear[right] = linear[right], linear[left]
                products[left], products[right] = products[right], products[left]
                counts["swap"] += 1
        elif typ == 49:  # CCX
            left, right, dst = (int(s[name][q3]) for name in ("qqq0", "qqq1", "qqq2"))
            q3 += 1
            if len({left, right, dst}) != 3:
                raise ValueError(f"op {op}: CCX operands overlap")
            if products[left] or products[right]:
                raise ValueError(f"op {op}: nonlinear CCX control; certificate unavailable")
            u, v = linear[left], linear[right]
            if u & constant or v & constant:
                raise ValueError(f"op {op}: affine CCX control; certificate unavailable")
            if u & a_mask and not u & ~a_mask and v & b_mask and not v & ~b_mask:
                pass
            elif v & a_mask and not v & ~a_mask and u & b_mask and not u & ~b_mask:
                u, v = v, u
            else:
                raise ValueError(f"op {op}: CCX controls are not separate a/b linear forms")
            products[dst] ^= 1 << len(terms)
            terms.append({"op": op, "a_mask": u, "b_mask": v >> n})
            counts["ccx"] += 1
        else:
            raise ValueError(f"op {op}: unsupported operation code {typ}")

    if (q3, q2, q1) != (len(s["qqq0"]), len(s["qq0"]), len(s["q0"])):
        raise ValueError("KMB operand streams have unused entries")
    for i, wire in enumerate(a_ids + b_ids + t_ids):
        if linear[wire] != 1 << i:
            raise ValueError(f"wire {wire}: final affine map is not identity")
    for wire in a_ids + b_ids:
        if products[wire]:
            raise ValueError(f"wire {wire}: product leaks into preserved operand")
    for index, term in enumerate(terms):
        weight = sum(((products[wire] >> index) & 1) << j for j, wire in enumerate(t_ids))
        term["output_mask"] = weight
        if not weight:
            raise ValueError(f"product at op {term['op']} has no target effect")
    mismatches = []
    for i in range(n):
        for j in range(n):
            actual = 0
            for term in terms:
                if term["a_mask"] >> i & term["b_mask"] >> j & 1:
                    actual ^= term["output_mask"]
            expected = (expected_basis_products[i * n + j] if expected_basis_products is not None
                        else _field_mul(1 << i, 1 << j, n, modulus))
            if actual != expected:
                mismatches.append({"a_bit": i, "b_bit": j, "actual": actual, "expected": expected})
    if mismatches:
        raise ValueError(f"multiplication tensor mismatch: {len(mismatches)} basis products; first {mismatches[0]}")
    merged = {}
    for term in terms:
        key = (term["a_mask"], term["b_mask"])
        merged[key] = merged.get(key, 0) ^ term["output_mask"]
    normalized = [{"a_mask": u, "b_mask": v, "output_mask": weight}
                  for (u, v), weight in sorted(merged.items()) if weight]
    return {"schema": "kickmix-bilinear-certificate-v3", "tier": "static model / projection",
            "circuit_sha256": digest(circuit.path), "modulus": modulus, "width": n,
            "target_tensor_kind": "explicit" if expected_basis_products is not None else "field",
            "registers": {"a": a_ids, "b": b_ids, "target": t_ids},
            "decomposition_length": len(terms), "bilinear_rank_upper_bound": len(normalized),
            "lower_bound_proved": False, "physical_gates": sum(counts.values()),
            "gate_counts": dict(counts), "annotation_tags": len(circuit.tags),
            "affine_identity": True, "tensor_identity": True, "ancilla": 0,
            "phase_operations": 0, "terms": terms, "normalized_terms": normalized}
