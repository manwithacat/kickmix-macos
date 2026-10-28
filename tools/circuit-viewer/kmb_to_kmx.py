#!/usr/bin/env python
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
# AI-assisted (Claude, Anthropic), under the repository owner's direction.
"""Convert a kickmix .kmb circuit to .kmx text so the circuit viewer can load it.

Usage: python kmb_to_kmx.py circuit.kmb [circuit.kmx]
"""
import sys
from pathlib import Path

import kickmix as km


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) == 3 else src.with_suffix(".kmx")
    circuit = km.Circuit.from_file(src, format="auto")
    circuit.to_file(dst, format="kmx")
    print(f"{src} -> {dst}: {len(circuit):,} ops, {circuit.num_qubits} qubits")


if __name__ == "__main__":
    main()
