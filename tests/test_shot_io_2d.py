# SPDX-License-Identifier: Apache-2.0. AI-assisted code; see NOTICE.
"""2-D bool write_across_shots / read_across_shots agree with the 1-D per-register path.

The 2-D path packs whole 64-bit words (patches/bulk-shot-io.patch) and assumes the simulator
word's bit order; the 1-D path is upstream's untouched per-bit code. Running both in the same
build checks that assumption on every platform's word type. Run with a venv that has kickmix:
    python tests/test_shot_io_2d.py
"""
import numpy as np

import kickmix as km

rng = np.random.default_rng(2026)
trials = 0
for batch in (1, 63, 64, 65, 255, 256, 257, 700, 1024):
    for rows in (1, 7, 40):
        for layout in ("C", "F", "sliced"):
            regs = [km.q(i) if i % 3 else km.b(i) for i in range(rows)]
            data = rng.random((rows, batch)) < 0.5
            if layout == "F":
                data = np.asfortranarray(data)
            elif layout == "sliced":
                data = (rng.random((rows, 2 * batch)) < 0.5)[:, ::2]

            packed = km.Simulator(batch_size=batch, debug_print=False, seed=1)
            perbit = km.Simulator(batch_size=batch, debug_print=False, seed=1)
            # Same random pre-fill in both, so partial-word writes must preserve neighbours.
            for r in regs:
                fill = rng.random(batch) < 0.5
                packed.write_across_shots(r, fill)
                perbit.write_across_shots(r, fill)

            packed.write_across_shots(km.array(regs), data)
            for r, row in zip(regs, data):
                perbit.write_across_shots(r, np.ascontiguousarray(row))

            got = packed.read_across_shots(km.array(regs))
            want = np.stack([perbit.read_across_shots(r) for r in regs])
            assert got.shape == want.shape and np.array_equal(got, want), (batch, rows, layout)
            assert np.array_equal(got, data), (batch, rows, layout)
            assert np.array_equal(np.stack([packed.read_across_shots(r) for r in regs]), want)
            trials += 1
print(f"OK: {trials} cases, 2-D packed path == 1-D per-bit path")
