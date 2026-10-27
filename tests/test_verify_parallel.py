# SPDX-License-Identifier: Apache-2.0. AI-assisted code; see NOTICE.
"""tools/verify_parallel.py gives the same result as a serial run of the same verifier.

Uses tests/fake_verifier/, which has the official verifiers' scheduling contract. Run with the
interpreter of a venv that has kickmix:  python tests/test_verify_parallel.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import verify_parallel  # noqa: E402

LOAD = (os.path.join(HERE, "fake_verifier"), "fake_verifier", None, None)


def main():
    module, circuit, spec = verify_parallel.load(*LOAD)
    module._run_parallel = lambda fn, tasks, max_workers: [fn(t) for t in tasks]
    references = {}
    for salt in ("", "abc"):
        serial = module.verify(circuit, spec, salt=salt, max_workers=1)
        assert serial["order"] == list(range(spec["batches"])), serial
        for workers in (1, 3, 5, 20):  # 20 > number of tasks: some workers get none
            spawned = verify_parallel.verify_spawned(LOAD, salt, workers)
            assert spawned == serial, (salt, workers, spawned, serial)
            print(f"salt={salt!r} workers={workers}: identical to serial ({serial['toffolis']} Toffolis)")
        references[salt] = serial
    assert references[""] != references["abc"], "the salt should change the seeded results"
    print("OK")


if __name__ == "__main__":
    main()
