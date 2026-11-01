"""Exact scalar-output bilinear projection of a validated multiplier certificate."""

from __future__ import annotations

from bilinear_cert import _field_mul


def _parity(value: int) -> int:
    return value.bit_count() & 1


def _factor(rows: list[int]) -> list[dict]:
    """Construct an exact rank factorization of an F2 matrix (row bitsets)."""
    basis = []
    right_forms = []
    for row in rows:
        remainder, combination = row, 0
        for pivot, reduced, witness in basis:
            if remainder >> pivot & 1:
                remainder ^= reduced
                combination ^= witness
        if remainder:
            index = len(right_forms)
            right_forms.append(row)
            pivot = (remainder & -remainder).bit_length() - 1
            basis.append((pivot, remainder, combination ^ (1 << index)))
    left_forms = [0] * len(right_forms)
    for i, row in enumerate(rows):
        remainder, combination = row, 0
        for pivot, reduced, witness in basis:
            if remainder >> pivot & 1:
                remainder ^= reduced
                combination ^= witness
        if remainder:
            raise AssertionError("rank basis did not span source matrix")
        for k in range(len(right_forms)):
            if combination >> k & 1:
                left_forms[k] |= 1 << i
    result = [{"a_mask": u, "b_mask": v} for u, v in zip(left_forms, right_forms)]
    reconstructed = [0] * len(rows)
    for term in result:
        for i in range(len(rows)):
            if term["a_mask"] >> i & 1:
                reconstructed[i] ^= term["b_mask"]
    if reconstructed != rows:
        raise AssertionError("rank factorization does not reconstruct matrix")
    return result


def analyze(certificate: dict, output_mask: int) -> dict:
    if certificate.get("schema") != "kickmix-bilinear-certificate-v3":
        raise ValueError("requires a validated v3 bilinear certificate")
    if certificate.get("tensor_identity") is not True or certificate.get("affine_identity") is not True:
        raise ValueError("certificate does not establish the reversible multiplier map")
    n = certificate["width"]
    if not 0 < output_mask < (1 << n):
        raise ValueError("output mask must be nonzero and within target width")
    rows = [0] * n
    selected_ops = []
    for term in certificate["terms"]:
        if _parity(term["output_mask"] & output_mask):
            selected_ops.append(term["op"])
            for i in range(n):
                if term["a_mask"] >> i & 1:
                    rows[i] ^= term["b_mask"]
    modulus = certificate["modulus"]
    for i in range(n):
        for j in range(n):
            expected = _parity(_field_mul(1 << i, 1 << j, n, modulus) & output_mask)
            if (rows[i] >> j & 1) != expected:
                raise ValueError(f"projected tensor mismatch at basis pair ({i},{j})")
    factorization = _factor(rows)
    return {"schema": "kickmix-bilinear-scalar-projection-v1",
            "tier": "static model / projection",
            "source_circuit_sha256": certificate["circuit_sha256"],
            "modulus": modulus, "width": n, "output_mask": output_mask,
            "existing_terms_selected": len(selected_ops), "selected_ccx_ops": selected_ops,
            "coefficient_matrix_rows": rows, "matrix_rank": len(factorization),
            "exact_scalar_bilinear_rank": len(factorization),
            "rank_factorization": factorization,
            "factorization_verified": True,
            "note": "A scalar bilinear optimum; caller semantics, target wiring, phases and Q²T are unproved."}
