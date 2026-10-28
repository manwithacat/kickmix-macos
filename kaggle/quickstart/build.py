#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 James Barlow
#
# AI-assisted: this file was written with an AI coding model (Claude, Anthropic)
# under the repository owner's direction, who is responsible for it. See NOTICE.
"""Build notebooks/kaggle-quickstart.ipynb, the Kaggle entry-point notebook, from the cells below.

The notebook is generated so that it can be reviewed as code. Edit this file, then run

    python kaggle/quickstart/build.py           # writes notebooks/kaggle-quickstart.ipynb
    python kaggle/quickstart/build.py --check   # exit 1 if the committed notebook is out of date

The public Kaggle copy (manwithacat/kickmix-quickstart, metadata in kernel-metadata.json next to this
file) is pushed by .github/workflows/kaggle-publish.yml.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "notebooks" / "kaggle-quickstart.ipynb"

COMPETITION = "google-verifiable-quantum-circuit-compilation"
COMPETITION_TITLE = "Google Quantum AI - Verifiable Quantum Circuit Compilation"
FORK_WHEELS = "manwithacat/kickmix-fork-wheels"
ENGINE_WHEELS = "manwithacat/kickmix-engine-wheels"
CIRCUITS = "manwithacat/vqcc-apache-public-circuits"
TOOLS = "manwithacat/kickmix-tools"
KERNEL = "manwithacat/kickmix-quickstart"
GITHUB = "https://github.com/manwithacat/kickmix-macos"
IMPORT_URL = f"https://kaggle.com/kernels/welcome?src={GITHUB}/blob/main/notebooks/kaggle-quickstart.ipynb"


def md(cell_id: str, source: str) -> dict:
    return {"cell_type": "markdown", "id": cell_id, "metadata": {},
            "source": textwrap.dedent(source).strip().splitlines(keepends=True)}


def code(cell_id: str, source: str) -> dict:
    return {"cell_type": "code", "id": cell_id, "metadata": {}, "execution_count": None, "outputs": [],
            "source": textwrap.dedent(source).strip("\n").splitlines(keepends=True)}


CELLS = [
    md("intro", f"""
        # kickmix quickstart: three simulators, one answer

        This notebook runs the competition's **unchanged official verifiers** for the invsqrt and DQI tasks with
        three different simulators and checks that they agree on every result field:

        | build | what it is |
        | --- | --- |
        | **official** | the organisers' `kickmix` module shipped with the competition data, the reference |
        | **fork** | [kickmix-macos]({GITHUB}), a fork of kickmix that aims at identical results with less memory and time |
        | **engine** | [kickmix-engine](https://github.com/manwithacat/kickmix-engine), an independent Rust simulator that runs the official verifier logic |

        For each run it prints the wall time, peak memory and whether the results are identical. At the end you
        can verify your own circuit with any of the three builds.

        **Getting it.** Either *Copy & Edit* the public Kaggle notebook [`{KERNEL}`](https://www.kaggle.com/code/{KERNEL})
        (inputs already attached), or import it from GitHub with [this link]({IMPORT_URL}) (File -> Import Notebook ->
        GitHub also works). After a GitHub import, attach the inputs listed below; the first code cell tells you
        exactly what is missing.

        **Inputs.**
        - Required: the competition data (`{COMPETITION}`). You must have joined the competition.
        - Recommended (offline install): datasets [`{FORK_WHEELS}`](https://www.kaggle.com/datasets/{FORK_WHEELS}) and
          [`{ENGINE_WHEELS}`](https://www.kaggle.com/datasets/{ENGINE_WHEELS}), and the utility script
          [`{TOOLS}`](https://www.kaggle.com/code/{TOOLS}). With the internet turned on, anything missing from these
          three is downloaded from the GitHub releases instead.
        - Optional: dataset [`{CIRCUITS}`](https://www.kaggle.com/datasets/{CIRCUITS}) to also verify the best
          public DQI circuit.

        **Run time.** The defaults are sized for roughly 15-25 minutes on Kaggle's 4-vCPU CPU notebook: the
        sample DQI circuit with all three builds; the public DQI circuit and the sample invsqrt circuit with the
        fork and the engine. The official build is the slowest, especially on invsqrt; set `RUN_LONG = True`
        to include it everywhere (roughly 25 minutes more). Each run prints its own time.
    """),
    md("inputs-md", """
        ## 1. Settings and inputs

        Finds every input, says how to fix anything missing, and stops before installing anything if a
        required input cannot be found or downloaded.
    """),
    code("inputs", f"""
        WORKERS = 4        # processes (official, fork) or threads (engine); Kaggle CPU notebooks have 4 vCPUs
        RUN_LONG = False   # True: also run the official build on invsqrt and on the public DQI circuit (slow)
        DQI_SHOTS = None   # None = the official 102,400 DQI shots. A smaller multiple of 256 (4,096 or more)
                           # is a quick check only, NOT an official verification

        import csv, glob, hashlib, json, os, platform, sys, time, urllib.request
        from pathlib import Path

        INPUT = Path("/kaggle/input")            # attached inputs
        USR_LIB = Path("/kaggle/usr/lib")        # where Kaggle mounts utility scripts
        WORK = Path("/kaggle/working")           # notebook output
        TMP = Path("/tmp/kickmix-quickstart")    # installs and caches (not saved as output)
        TMP.mkdir(parents=True, exist_ok=True)
        NEED_TOOLS = (0, 3, 0)                   # kickmix_tools with engine support
        TOOLS_RAW = "https://raw.githubusercontent.com/manwithacat/kickmix-macos/{{ref}}/tools/kickmix_tools.py"

        def find(pattern, *roots):
            hits = set()
            for root in roots or (INPUT,):
                hits.update(glob.glob(f"{{root}}/**/{{pattern}}", recursive=True))  # follows symlinked mounts
            return sorted(Path(h) for h in hits)

        def fetch(url, timeout=20):
            req = urllib.request.Request(url, headers={{"User-Agent": "kickmix-quickstart"}})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()

        def tools_version(path):
            for line in Path(path).read_text().splitlines():
                if line.startswith("__version__"):
                    return tuple(int(x) for x in line.split('"')[1].split("."))
            return (0,)

        def add_input(tab, query, what):
            return f'Input panel -> "+ Add Input" -> {{tab}} -> search "{{query}}" ({{what}}).'

        try:
            fetch("https://github.com", timeout=5)
            ONLINE = True
        except Exception:
            ONLINE = False
        print(f"Python {{platform.python_version()}} on {{platform.system()}} {{platform.machine()}}, "
              f"{{os.cpu_count()}} CPUs, internet {{'on' if ONLINE else 'off'}}")
        FIXES, NOTES, FOUND = [], [], {{}}

        # kickmix_tools: the attached utility script, else (internet on) the file from GitHub
        TOOLS_FILE = None
        attached = find("kickmix_tools.py", USR_LIB, INPUT)
        good = [p for p in attached if tools_version(p) >= NEED_TOOLS]
        if good:
            TOOLS_FILE = good[0]
        else:
            if attached:
                NOTES.append(f"the attached kickmix_tools is {{'.'.join(map(str, tools_version(attached[0])))}}; this "
                             f"notebook needs {{'.'.join(map(str, NEED_TOOLS))}} or later (update the input to its latest version)")
            if ONLINE:
                refs = []
                try:
                    refs.append(json.loads(fetch("https://api.github.com/repos/manwithacat/kickmix-macos/releases/latest"))["tag_name"])
                except Exception:
                    pass
                for ref in refs + ["main"]:
                    try:
                        dest = TMP / "tools" / "kickmix_tools.py"
                        dest.parent.mkdir(exist_ok=True)
                        dest.write_bytes(fetch(TOOLS_RAW.format(ref=ref)))
                    except Exception as exc:
                        NOTES.append(f"could not download kickmix_tools at {{ref}}: {{exc}}")
                        continue
                    if tools_version(dest) >= NEED_TOOLS:
                        TOOLS_FILE = dest
                        NOTES.append(f"kickmix_tools downloaded from GitHub ({{ref}}), sha256 "
                                     f"{{hashlib.sha256(dest.read_bytes()).hexdigest()[:16]}}...")
                        break
                    NOTES.append(f"kickmix_tools on GitHub ({{ref}}) is {{'.'.join(map(str, tools_version(dest)))}}, "
                                 "older than this notebook needs")
            if TOOLS_FILE is None:
                FIXES.append(add_input("Notebooks", "kickmix-tools", "utility script {TOOLS}, version 0.3.0 or later")
                             + ("" if ONLINE else " Or turn on Internet in the notebook settings so it can be "
                                "fetched from GitHub."))
        kt = None
        if TOOLS_FILE:
            sys.path.insert(0, str(TOOLS_FILE.parent))
            sys.modules.pop("kickmix_tools", None)
            import kickmix_tools as kt
            FOUND["kickmix_tools"] = f"{{kt.__version__}} from {{TOOLS_FILE}}"

        # The competition data: verifiers, sample submission and the organisers' kickmix build
        COMP = None
        hits = find("verifiers/dqi/verify_submission_dqi.py")
        if hits:
            COMP = hits[0].parents[2]
            os.environ["KICKMIX_TOOLS_COMPETITION_DIR"] = str(COMP)
            FOUND["competition data"] = str(COMP)
            if not (COMP / "sample_submission" / "dqi.kmb").is_file():
                FIXES.append(f"the competition data at {{COMP}} has no sample_submission/: re-attach the full competition data.")
            if not list((COMP / "kickmix").glob("kickmix*.so")):
                NOTES.append(f"no organisers' kickmix module under {{COMP}}/kickmix: the official build is skipped")
        else:
            FIXES.append(add_input("Competitions", "{COMPETITION_TITLE}", "the competition data")
                         + " You need to have joined the competition (accepted its rules). The competition data "
                         "cannot be downloaded from inside a notebook; it has to be attached.")

        # Wheels: the attached datasets, else (internet on) the GitHub releases
        def wheel_source(kind, dataset, pattern, online_route):
            found = kt.find_wheel_dir(kind, INPUT) if kt else (find(pattern)[0].parent if find(pattern) else None)
            if found:
                FOUND[f"{{kind}} wheels"] = str(found)
                return found
            if find(pattern):
                NOTES.append(f"{{dataset}} is attached but has no {{kind}} wheel for Python {{platform.python_version()}} "
                             f"on {{platform.machine()}}")
            if ONLINE:
                NOTES.append(f"{{dataset}} not attached: the {{kind}} wheel will be downloaded from the latest GitHub release")
                FOUND[f"{{kind}} wheels"] = "GitHub release (internet)"
                return online_route
            FIXES.append(add_input("Datasets", dataset.split("/")[1], f"dataset {{dataset}}")
                         + " Or turn on Internet in the notebook settings so the wheel is downloaded from GitHub.")
            return None

        FORK_SRC = wheel_source("fork", "{FORK_WHEELS}", "kickmix-*.whl", "wheel")
        ENGINE_SRC = wheel_source("engine", "{ENGINE_WHEELS}", "kickmix_engine-*.whl", "latest")

        # Optional: public circuits (Apache-2.0) with a manifest that credits each circuit
        PUBLIC_DQI = None
        manifests = [m for m in find("manifest.csv") if (m.parent / "circuits" / "dqi").is_dir()]
        if manifests:
            rows = [r for r in csv.DictReader(open(manifests[0], newline=""))
                    if r["task"] == "dqi" and r.get("local_task_score")]
            if rows:
                PUBLIC_DQI = min(rows, key=lambda r: float(r["local_task_score"]))
                PUBLIC_DQI["path"] = manifests[0].parent / "circuits" / "dqi" / f"{{PUBLIC_DQI['sha12']}}.kmb"
                FOUND["public circuits"] = str(manifests[0].parent)
        else:
            NOTES.append("optional: " + add_input("Datasets", "vqcc-apache-public-circuits",
                                                   "dataset {CIRCUITS}") + " Adds the best public DQI circuit.")

        for name, where in FOUND.items():
            print(f"  found  {{name:17}} {{where}}")
        for note in NOTES:
            print(f"  note   {{note}}")
        if FIXES:
            print("\\nMISSING INPUTS. Fix these, then run all cells again:")
            for i, fix in enumerate(FIXES, 1):
                print(f"  {{i}}. {{fix}}")
            raise RuntimeError(f"{{len(FIXES)}} required input(s) missing; see the list above")
        print("\\nAll required inputs are available.")
    """),
    md("install-md", """
        ## 2. Install the fork and the engine

        Each build goes into its own directory under `/tmp`; the organisers' `kickmix` is never touched. Every
        verification runs in a fresh subprocess with exactly one build on its import path. Wheels are checked
        against the SHA-256 sums published with them. `kickmix-engine doctor` then reports the CPU features and
        the simulator kernel the engine selected (on Kaggle's x86-64 CPUs: `baseline`, `avx2` or `avx512`).
    """),
    code("install", """
        FORK_DIR = kt.install(str(FORK_SRC), target=TMP / "fork")
        ENGINE_DIR = kt.install_engine(str(ENGINE_SRC), target=TMP / "engine")
        OFFICIAL = kt.official_available()
        print("official build:", kt.official_build() if OFFICIAL else "not available (skipped)")
        if ONLINE:
            kt.check_latest()
        print()
        print(kt.engine_doctor())
    """),
    md("verify-md", """
        ## 3. Verify with all three builds

        Full official settings: default shot counts, the seed derived from each circuit's SHA-256, empty salt.
        The builds run one after the other, so their timings do not disturb each other. "Identical" means every
        field of the verifier's result (score, error counts, Toffoli counts, operation counts, circuit hash, ...)
        is exactly equal. The engine's first run on a circuit includes compiling it; later runs reuse a cache.
        Memory is the peak of the whole process tree (PSS on Linux, so pages shared by worker processes count once).
    """),
    code("verify", """
        long_builds = (["official"] if RUN_LONG and OFFICIAL else []) + ["fork", "engine"]
        PLAN = [("dqi", "sample_submission/dqi.kmb", COMP / "sample_submission" / "dqi.kmb",
                 (["official"] if OFFICIAL else []) + ["fork", "engine"])]
        if PUBLIC_DQI:
            PLAN.append(("dqi", f"public DQI {PUBLIC_DQI['sha12']} ({PUBLIC_DQI['author']})", PUBLIC_DQI["path"],
                         long_builds))
        PLAN.append(("invsqrt", "sample_submission/invsqrt.kmb", COMP / "sample_submission" / "invsqrt.kmb", long_builds))
        if DQI_SHOTS:
            print(f"DQI_SHOTS={DQI_SHOTS}: reduced DQI runs, NOT official verifications")

        RESULTS = []
        t_start = time.monotonic()
        for task, label, path, builds in PLAN:
            print(f"\\n=== {label}: {task}, builds {', '.join(builds)}, {WORKERS} workers each ===", flush=True)
            c = kt.compare_builds(task, path, builds=tuple(builds), workers=WORKERS, dqi_shots=DQI_SHOTS,
                                  cache_dir=TMP / "engine-cache")
            RESULTS.append((label, c))
        print(f"\\nall verifications: {(time.monotonic() - t_start) / 60:.1f} min")
    """),
    code("summary", """
        print(f"{'circuit':34} {'build':16} {'wall (s)':>9} {'peak MB':>9} {'status':>7} {'score':>22}  identical")
        for label, c in RESULTS:
            for b, r in c["runs"].items():
                mem = r["peak_pss_sum_mb"] or r["peak_rss_sum_mb"]
                print(f"{label[:34]:34} {kt.build_label(r):16} {r['wall_seconds']:9.1f} {mem:9,.0f} "
                      f"{r['result']['status']:>7} {r['result']['score']:22.10g}  {'yes' if c['identical'] else 'NO'}")
        ALL_IDENTICAL = all(c["identical"] for _, c in RESULTS)
        print("\\nEvery build returned identical results on every circuit." if ALL_IDENTICAL else
              "\\nDIFFERENCES FOUND: see the differing fields printed above.")

        (WORK / "quickstart_results.json").write_text(json.dumps({
            "settings": {"workers": WORKERS, "run_long": RUN_LONG, "dqi_shots": DQI_SHOTS},
            "provenance": kt.provenance(), "all_identical": ALL_IDENTICAL,
            "comparisons": [{"circuit": label, "task": c["task"], "identical": c["identical"],
                             "n_fields": c["n_fields"], "n_different": c["n_different"],
                             "runs": {b: {k: r.get(k) for k in ("wall_seconds", "peak_pss_sum_mb", "peak_rss_sum_mb",
                                                                "workers", "result", "provenance", "kickmix_module")}
                                      for b, r in c["runs"].items()}} for label, c in RESULTS]},
            indent=1, default=str))
        print("wrote", WORK / "quickstart_results.json")
    """),
    md("own-md", """
        ## 4. Verify your own circuit

        Point `MY_CIRCUIT` at a `.kmb` file: attach a dataset holding it, or write it to `/kaggle/working`.
        Pick the build. With the engine, `CROSS_CHECK = True` also runs the official verifier with the
        organisers' kickmix and compares every field (it then takes as long as the official run). The engine
        covers invsqrt and DQI; for chemistry (`dfthcsos`) use `kt.verify("dfthcsos", directory, build="fork")`.
    """),
    code("own", """
        MY_CIRCUIT = None      # e.g. "/kaggle/input/my-circuits/dqi.kmb"
        MY_TASK = "dqi"        # "dqi" or "invsqrt"
        MY_BUILD = "engine"    # "engine", "fork" or "official"
        CROSS_CHECK = False    # engine only: also run the official verifier and compare every field
        MY_SALT = ""           # the official default is the empty salt

        if MY_CIRCUIT is None:
            print("Set MY_CIRCUIT to a .kmb file to verify it here.")
        else:
            try:
                r = kt.verify(MY_TASK, MY_CIRCUIT, build=MY_BUILD, workers=WORKERS, salt=MY_SALT,
                              cross_check=CROSS_CHECK, cache_dir=TMP / "engine-cache")
            except RuntimeError as exc:
                print("verification failed:\\n", exc)
            else:
                print(json.dumps(r["result"], indent=1))
                print(f"{kt.build_label(r)}: {r['wall_seconds']:.1f} s, peak "
                      f"{r['peak_pss_sum_mb'] or r['peak_rss_sum_mb']:,.0f} MB")
                if "cross_check" in r:
                    cc = r["cross_check"]
                    print("cross-check against the official verifier:", "MATCH" if cc["match"] else "DIFFERENT",
                          cc["seconds"], cc["differences"] or "")
    """),
    md("credits", f"""
        ## Credits, provenance and licence

        - **kickmix** and the **official verifiers** are by Google Quantum AI, part of
          [Qualtran](https://github.com/quantumlib/Qualtran) (Apache-2.0); the competition data is used under the
          competition's rules and is not redistributed here.
        - **kickmix-macos** (the fork), **kickmix-engine** and **kickmix_tools** are published by James Barlow
          (manwithacat). Their code, and this notebook, were written by an AI model (Claude, by Anthropic) under
          his direction; he reviews the work and is responsible for it. Each repository's NOTICE file has the
          details. The official verifier running on the organisers' kickmix is the reference: the fork and the
          engine aim to match it exactly, and this notebook checks that they do on the circuits above.
        - The public DQI circuit, when used, is other people's work released under Apache-2.0; the cell above
          prints its author, and the dataset's `manifest.csv` credits its source notebook.
        - The source of this notebook is [`kaggle/quickstart/build.py`]({GITHUB}/blob/main/kaggle/quickstart/build.py)
          in the kickmix-macos repository. Licence: Apache-2.0.

        Not affiliated with Google, Qualtran or Kaggle. Only Kaggle's own scoring of a submission counts in the
        competition. This notebook is maintained for the remaining months of the competition and will be retired
        after it closes.
    """),
    code("provenance", """
        print(json.dumps(kt.provenance(), indent=1))
        if PUBLIC_DQI:
            print(f"public DQI circuit {PUBLIC_DQI['sha12']}: {PUBLIC_DQI['author']}, \\"{PUBLIC_DQI['notebook_title']}\\"\\n"
                  f"  {PUBLIC_DQI['source_notebook_url']}")
    """),
]

# Intended inputs, recorded in the notebook metadata. Whether Kaggle honours this block when a notebook is
# imported from GitHub is not known; kernel-metadata.json is what the API push uses.
KAGGLE_METADATA = {
    "accelerator": "none",
    "isGpuEnabled": False,
    "isInternetEnabled": False,
    "language": "python",
    "sourceType": "notebook",
    "dataSources": [
        {"name": COMPETITION, "type": "competition"},
        {"name": FORK_WHEELS, "type": "dataset"},
        {"name": ENGINE_WHEELS, "type": "dataset"},
        {"name": CIRCUITS, "type": "dataset"},
        {"name": TOOLS, "type": "kernel"},
    ],
}


def build() -> str:
    ids = [c["id"] for c in CELLS]
    assert len(ids) == len(set(ids))
    nb = {"cells": CELLS, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.13"},
        "kaggle": KAGGLE_METADATA}, "nbformat": 4, "nbformat_minor": 5}
    return json.dumps(nb, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str]) -> int:
    text = build()
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python kaggle/quickstart/build.py", file=sys.stderr)
            return 1
        print(f"{OUT.relative_to(ROOT)} is up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(CELLS)} cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
