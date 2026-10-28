#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 James Barlow
#
# AI-assisted: this file was written with an AI coding model (Claude, Anthropic)
# under the repository owner's direction, who is responsible for it. See NOTICE.
"""kickmix_tools: run the unchanged official verifiers against the shipped kickmix or the fork.

Single-file helper for the Kaggle competition "Google Quantum AI - Verifiable Quantum
Circuit Compilation". Not affiliated with Google, Qualtran or Kaggle. The organisers'
`kickmix.so` stays the reference build; the fork (github.com/manwithacat/kickmix-macos)
is an openly AI-assisted variant that aims at identical results with less memory/time.

    import kickmix_tools as kt
    fork = kt.install()                                  # fork into an isolated directory
    r = kt.verify("invsqrt", "/kaggle/input/.../sample_submission/invsqrt.kmb", build="fork")
    c = kt.compare("dqi", ".../sample_submission/dqi.kmb")     # both builds, equality table
    s = kt.angle_strict_check(".../sample_submission/dfthcsos") # sampled strict ANGLE_k check

Every verification runs in a fresh subprocess so the two builds never share an interpreter
and the process tree's memory can be measured. The verifier files are the organisers' own
and are not modified. Local overrides (all optional): the environment variables
KICKMIX_TOOLS_COMPETITION_DIR, KICKMIX_TOOLS_OFFICIAL_BUILD, KICKMIX_TOOLS_FORK_BUILD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

try:  # not available on Windows
    import resource
except ImportError:  # pragma: no cover
    resource = None

__version__ = "0.2.0"

REPO = "manwithacat/kickmix-macos"
DEFAULT_TAG = "latest"  # the newest GitHub release; pass tag="vX.Y.Z" to pin
KAGGLE_COMPETITION_DIR = "/kaggle/input/google-verifiable-quantum-circuit-compilation"
TASKS = ("dfthcsos", "invsqrt", "dqi")
_MARK = "KICKMIX_TOOLS_RESULT "

# Paths (read at call time so notebooks can set the environment variables late).


def competition_dir() -> Path:
    """The competition data root: the environment override, the usual Kaggle mount, or (since mount
    paths vary) the directory two levels above the one `kickmix/kickmix.so` found under /kaggle/input."""
    env = os.environ.get("KICKMIX_TOOLS_COMPETITION_DIR")
    if env:
        return Path(env)
    default = Path(KAGGLE_COMPETITION_DIR)
    if (default / "kickmix").is_dir():
        return default
    root = Path("/kaggle/input")
    if root.is_dir():
        hits = sorted(p for p in root.rglob("kickmix.so")
                      if p.parent.name == "kickmix" and (p.parent.parent / "verifiers").is_dir())
        if hits:
            return hits[0].parent.parent
    return default


def official_build() -> Path:
    """Directory that holds the organisers' kickmix extension module (Kaggle: <comp>/kickmix)."""
    return Path(os.environ.get("KICKMIX_TOOLS_OFFICIAL_BUILD", str(competition_dir() / "kickmix")))


def fork_build() -> Path | None:
    env = os.environ.get("KICKMIX_TOOLS_FORK_BUILD")
    return Path(env) if env else _STATE.get("fork_build")


_STATE: dict = {}


# --------------------------------------------------------------------------- install


def _has_module(path: Path) -> bool:
    path = Path(path)
    return path.is_dir() and bool(list(path.glob("kickmix*.so")) or list(path.glob("kickmix*.pyd")))


def _http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "kickmix_tools"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _pick_wheel(assets: list[str]) -> str | None:
    """Choose the release asset matching this interpreter and platform."""
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    machine = platform.machine().lower()
    system = platform.system()
    if system == "Linux":
        want = ("manylinux", "aarch64" if machine in ("aarch64", "arm64") else "x86_64")
    elif system == "Darwin":
        want = ("macosx", "arm64" if machine == "arm64" else "x86_64")
    else:
        want = ("win", "amd64")
    for name in assets:
        if name.endswith(".whl") and f"-{tag}-{tag}-" in name and all(w in name.lower() for w in want):
            return name
    return None


def _pip_install(target: Path, spec: str) -> None:
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--upgrade",
           "--target", str(target), spec]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"pip install failed ({spec}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")


def _parse_sha256sums(text: str) -> dict[str, str]:
    """Map file name -> hex digest from `sha256sum`-style lines ("<hex>  <name>" or "<hex> *<name>")."""
    listed = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            listed[parts[1].strip().lstrip("*").rsplit("/", 1)[-1]] = parts[0].lower()
    return listed


def _check_local_wheel(wheel: Path) -> str:
    """Check a local wheel against a SHA256SUMS file next to it, if there is one.

    Raises RuntimeError on a mismatch or when the file does not list the wheel; returns a short
    description of what was (or was not) checked, recorded in _STATE["fork_how"].
    """
    sums = wheel.with_name("SHA256SUMS")
    if not sums.is_file():
        return "no SHA256SUMS next to the wheel; hash not checked"
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    listed = _parse_sha256sums(sums.read_text()).get(wheel.name)
    if listed is None:
        raise RuntimeError(f"{sums} does not list {wheel.name}; refusing to install an unlisted wheel "
                           f"(sha256 {digest})")
    if listed != digest:
        raise RuntimeError(f"SHA-256 mismatch for {wheel.name}: got {digest}, {sums.name} lists {listed}")
    return "SHA-256 matches SHA256SUMS"


def _release(tag: str, repo: str) -> dict:
    """GitHub release metadata for `tag` ("latest" = the newest published release)."""
    path = "releases/latest" if tag == "latest" else f"releases/tags/{tag}"
    return json.loads(_http_get(f"https://api.github.com/repos/{repo}/{path}"))


def _wheel_in_dir(directory: Path) -> Path | None:
    """The wheel in `directory` matching this interpreter and platform (e.g. the Kaggle wheels dataset)."""
    name = _pick_wheel(sorted(p.name for p in directory.glob("*.whl")))
    return directory / name if name else None


def _install_wheel(target: Path, tag: str, repo: str) -> str:
    api = _release(tag, repo)
    tag = api.get("tag_name", tag)
    urls = {a["name"]: a["browser_download_url"] for a in api["assets"]}
    name = _pick_wheel(list(urls))
    if name is None:
        raise RuntimeError(f"no wheel for cp{sys.version_info.major}{sys.version_info.minor} on "
                           f"{platform.system()} {platform.machine()} in release {tag}")
    data = _http_get(urls[name], timeout=300)
    digest = hashlib.sha256(data).hexdigest()
    if "SHA256SUMS" in urls:
        sums = _http_get(urls["SHA256SUMS"]).decode()
        listed = {parts[1].lstrip("*").rsplit("/", 1)[-1]: parts[0] for parts in (l.split() for l in sums.splitlines()) if len(parts) == 2}
        if listed.get(name) != digest:
            raise RuntimeError(f"SHA-256 mismatch for {name}: got {digest}, SHA256SUMS lists {listed.get(name)}")
        checked = "SHA-256 matches SHA256SUMS"
    else:
        checked = "no SHA256SUMS in the release; hash not checked"
    with tempfile.TemporaryDirectory() as tmp:
        wheel = Path(tmp) / name
        wheel.write_bytes(data)
        _pip_install(target, str(wheel))
    return f"prebuilt wheel {name} from release {tag} ({checked}; sha256 {digest[:16]}...)"


def _install_source(target: Path, tag: str, repo: str) -> str:
    _pip_install(target, f"git+https://github.com/{repo}.git@{tag}#subdirectory=kickmix")
    return f"source build from git tag {tag} (compiled here)"


def install(source: str = "auto", *, target: str | os.PathLike | None = None, tag: str = DEFAULT_TAG,
            repo: str = REPO) -> Path:
    """Install the fork into an isolated directory and return that directory.

    The organisers' kickmix.so is never touched: the fork goes to `target`
    (default /kaggle/working/kickmix_fork, or a temp dir elsewhere) and is selected by
    putting that directory first on sys.path in the verifier subprocess.

    source: "auto" (wheel from the GitHub release `tag`, default the newest release, then a
    source build if that fails), "wheel", "source", a path to a .whl file, a directory of wheels
    (such as the attached Kaggle dataset kickmix-fork-wheels: the wheel for this interpreter is
    chosen, so notebooks need not name a version), or a directory that already holds a built
    kickmix module (used as is). Prints which route was used; `provenance()` records it.
    """
    override = os.environ.get("KICKMIX_TOOLS_FORK_BUILD")
    if override:
        _STATE["fork_build"] = Path(override)
        print(f"fork: using KICKMIX_TOOLS_FORK_BUILD override {override}")
        return Path(override)
    src_path = Path(source)
    if source not in ("auto", "wheel", "source") and src_path.is_dir() and _has_module(src_path):
        _STATE["fork_build"] = src_path
        print(f"fork: using existing build directory {src_path}")
        return src_path
    if target is None:
        base = Path("/kaggle/working") if Path("/kaggle/working").is_dir() else Path(tempfile.gettempdir())
        target = base / "kickmix_fork"
    target = Path(target)
    if source not in ("auto", "wheel", "source") and src_path.is_dir():
        found = _wheel_in_dir(src_path)
        if found is None:
            raise FileNotFoundError(f"no kickmix module or wheel for cp{sys.version_info.major}"
                                    f"{sys.version_info.minor} on {platform.system()} in {src_path}")
        src_path, source = found, str(found)
    if target.exists():
        for old in target.glob("kickmix*"):
            if old.is_dir():
                import shutil
                shutil.rmtree(old)
            else:
                old.unlink()
    target.mkdir(parents=True, exist_ok=True)
    if source.endswith(".whl") and src_path.is_file():
        checked = _check_local_wheel(src_path)
        _pip_install(target, str(src_path))
        how = f"local wheel {src_path.name} ({checked})"
    elif source == "wheel":
        how = _install_wheel(target, tag, repo)
    elif source == "source":
        how = _install_source(target, tag, repo)
    elif source == "auto":
        try:
            how = _install_wheel(target, tag, repo)
        except Exception as exc:  # noqa: BLE001 - any failure falls back to the source build
            print(f"fork: wheel route unavailable ({type(exc).__name__}: {str(exc)[:200]}); building from source (a few minutes)")
            how = _install_source(target, tag, repo)
    else:
        raise ValueError(f"unknown source {source!r}")
    if not _has_module(target):
        raise RuntimeError(f"install finished but no kickmix module found in {target}")
    _STATE["fork_build"] = target
    _STATE["fork_how"] = how
    print(f"fork: installed via {how}\nfork: directory {target}")
    p = provenance()
    print(f"fork: kickmix {p['fork_version']}, kickmix_tools {__version__}")
    return target


def _installed_version(build: Path | None) -> str | None:
    """Package version recorded in the build directory's kickmix-*.dist-info, if any."""
    if build is None:
        return None
    for info in sorted(Path(build).glob("kickmix-*.dist-info")):
        return info.name[len("kickmix-"):-len(".dist-info")]
    return None


def provenance() -> dict:
    """Which fork build this session uses: record it with any result (version, install route)."""
    build = _STATE.get("fork_build")
    return {"kickmix_tools_version": __version__, "fork_version": _installed_version(build),
            "fork_install": _STATE.get("fork_how"), "fork_build_dir": str(build) if build else None}


def check_latest(repo: str = REPO, *, quiet: bool = False) -> dict:
    """Compare the installed fork with the newest GitHub release (needs internet).

    Returns {"installed", "latest", "up_to_date"}; up_to_date is None when the release could not
    be read (internet off) or nothing is installed. Prints a warning when the build is behind.
    """
    installed = provenance()["fork_version"]
    try:
        latest = _release("latest", repo).get("tag_name", "").lstrip("v") or None
    except Exception:  # noqa: BLE001 - offline notebooks cannot reach GitHub
        latest = None
    ok = None if (installed is None or latest is None) else installed == latest
    if not quiet:
        if ok is False:
            print(f"WARNING: kickmix fork {installed} is behind the latest release {latest} "
                  f"(https://github.com/{repo}/releases/latest)")
        elif ok is None:
            print(f"fork: installed {installed}; latest release {'unknown (offline?)' if latest is None else latest}")
        else:
            print(f"fork: {installed} is the latest release")
    return {"installed": installed, "latest": latest, "up_to_date": ok}


# --------------------------------------------------------------------------- memory sampling


def _tree_pids_proc(root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                with open(f"/proc/{entry}/stat") as f:
                    stat = f.read()
                ppid = int(stat[stat.rindex(")") + 2:].split()[1])
            except (OSError, ValueError):
                continue
            children.setdefault(ppid, []).append(int(entry))
    out, stack = [], [root]
    while stack:
        pid = stack.pop()
        out.append(pid)
        stack.extend(children.get(pid, []))
    return out


def _sample_linux(root: int) -> tuple[float, float | None]:
    rss = pss = 0.0
    have_pss = True
    for pid in _tree_pids_proc(root):
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss += int(line.split()[1]) / 1024
                        break
        except OSError:
            continue
        try:
            with open(f"/proc/{pid}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Pss:"):
                        pss += int(line.split()[1]) / 1024
                        break
        except OSError:
            have_pss = False
    return rss, (pss if have_pss else None)


_PROC_RUSAGE_V2 = 2          # RUSAGE_INFO_V2 flavour of proc_pid_rusage (macOS)
_PHYS_FOOTPRINT_OFFSET = 72  # rusage_info_v2: ri_uuid[16] + seven uint64 fields precede ri_phys_footprint
_libproc = None


def _macos_footprint_fn():
    """Return f(pid) -> phys_footprint in MB (None if unreadable), or None if libproc is unusable.

    phys_footprint is the figure Activity Monitor calls "Memory": it counts compressed pages and
    excludes clean file-backed pages, so it is much closer to what limits a Mac than RSS. It is read
    in-process through proc_pid_rusage (libproc, via ctypes), which is cheap (no subprocess per
    sample). The `footprint` CLI was rejected: it needs a subprocess per pid per sample and is slow
    on large process trees. The probe below must succeed on this process before it is trusted.
    """
    global _libproc
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util
        if _libproc is None:
            _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
            _libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            _libproc.proc_pid_rusage.restype = ctypes.c_int
        lib = _libproc

        def footprint_mb(pid: int):
            buf = ctypes.create_string_buffer(512)
            if lib.proc_pid_rusage(pid, _PROC_RUSAGE_V2, buf) != 0:
                return None
            return int.from_bytes(buf.raw[_PHYS_FOOTPRINT_OFFSET:_PHYS_FOOTPRINT_OFFSET + 8], "little") / (1024 * 1024)

        mine = footprint_mb(os.getpid())
        return footprint_mb if mine is not None and mine > 1 else None
    except Exception:  # noqa: BLE001 - fall back to RSS
        return None


def _sample_ps(root: int) -> tuple[float, float | None]:
    """Tree memory on macOS and other non-/proc systems: (RSS sum in MB, footprint sum in MB or None).

    The second value is phys_footprint where proc_pid_rusage works (macOS), reported in the
    `peak_pss_sum_mb` slot because, like PSS, it is the per-process cost that avoids double
    counting shared pages. `memory_kind` labels it "footprint".
    """
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True).stdout
    rows = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            rows[int(parts[0])] = (int(parts[1]), int(parts[2]))
    tree, grew = {root}, True
    while grew:
        grew = False
        for pid, (ppid, _) in rows.items():
            if ppid in tree and pid not in tree:
                tree.add(pid)
                grew = True
    rss = sum(rows[p][1] for p in tree if p in rows) / 1024
    fp_fn = _macos_footprint_fn()
    if fp_fn is None:
        return rss, None
    vals = [fp_fn(p) for p in tree]
    return rss, sum(v for v in vals if v is not None)


class _PeakSampler(threading.Thread):
    """Samples the summed resident memory of a process tree (peak of the sum, not sum of peaks)."""

    def __init__(self, pid: int, interval: float = 0.2):
        super().__init__(daemon=True)
        self.pid, self.interval = pid, interval
        self.peak_rss = 0.0
        self.peak_pss: float | None = None
        self._halt = threading.Event()
        self._sample = _sample_linux if Path("/proc/self/status").exists() else _sample_ps

    def run(self):
        while not self._halt.is_set():
            try:
                rss, pss = self._sample(self.pid)
            except Exception:  # noqa: BLE001 - sampling must never break a run
                rss, pss = 0.0, None
            self.peak_rss = max(self.peak_rss, rss)
            if pss is not None:
                self.peak_pss = max(self.peak_pss or 0.0, pss)
            self._halt.wait(self.interval)

    def finish(self):
        self._halt.set()
        self.join()


# --------------------------------------------------------------------------- worker (subprocess side)


def _jsonable(x):
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        np = None
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if np is not None and isinstance(x, np.ndarray):
        return _jsonable(x.tolist())
    if np is not None and isinstance(x, np.generic):
        return _jsonable(x.item())
    if isinstance(x, (str, int, bool)) or x is None:
        return x
    if isinstance(x, float):
        return x if x == x and abs(x) != float("inf") else repr(x)
    return str(x)


def _maxrss_mb(children: bool) -> float:
    if resource is None:
        return float("nan")
    v = resource.getrusage(resource.RUSAGE_CHILDREN if children else resource.RUSAGE_SELF).ru_maxrss
    return v / (1024 * 1024) if sys.platform == "darwin" else v / 1024


def _worker(spec: dict) -> None:
    sys.path[:0] = [str(spec["build"]), str(Path(spec["competition"]) / "verifiers")]
    import kickmix as km
    module_file = getattr(km, "__file__", "?")
    t0 = time.monotonic()
    task, salt = spec["task"], spec["salt"]
    if task == "dfthcsos":
        from dfthcsos.verify import verify as verify_chemistry
        result = verify_chemistry(Path(spec["circuit"]), salt=salt)
    elif task == "invsqrt":
        import invsqrt.verify_submission_invsqrt as module
        problem = module.ProblemSpecInvSqrt.for_n_p(10)
        circuit = km.Circuit.from_file(str(spec["circuit"]), format="kmb")
        result = module.verify(circuit, problem, salt=salt, max_workers=spec["workers"])
    elif task == "dqi":
        import dqi.verify_submission_dqi as module
        problem = module.ProblemSpecDQI.benchmark_instance() if spec["dqi_shots"] is None \
            else module.ProblemSpecDQI.benchmark_instance(num_shots=spec["dqi_shots"])
        circuit = km.Circuit.from_file(str(spec["circuit"]), format="kmb")
        result = module.verify(circuit, problem, salt=salt, max_workers=spec["workers"])
    else:
        raise ValueError(task)
    out = {"result": _jsonable(result), "kickmix_module": str(module_file),
           "inner_wall_seconds": time.monotonic() - t0,
           "max_single_process_mb": max(_maxrss_mb(False), _maxrss_mb(True))}
    sys.stdout.write("\n" + _MARK + json.dumps(out) + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------- public API


def default_workers(task: str) -> int:
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    return max(1, cpus)


def verify(task: str, circuit, build: str = "fork", workers: int | None = None, *, salt: str = "",
           dqi_shots: int | None = None,
           build_dir: str | os.PathLike | None = None, timeout: float | None = None) -> dict:
    """Run the unchanged official verifier for `task` on `circuit` with the chosen build.

    task: "dfthcsos" (circuit = the directory with angle_k_*.kmb + submission.npz),
    "invsqrt" or "dqi" (circuit = the .kmb file). build: "fork" or "official" (or pass
    build_dir). workers: processes for invsqrt/dqi (default: all CPUs; dfthcsos is serial).
    dqi_shots: leave None for the official shot count; a smaller multiple of the verifier's batch
    size gives a reduced run whose result is NOT an official verification (the invsqrt shot count
    cannot be reduced: the verifier needs a multiple of its batch size).

    Returns {"task", "build", "result" (the verifier's own dict), "wall_seconds",
    "peak_rss_sum_mb", "peak_pss_sum_mb" (Linux), "max_single_process_mb", "kickmix_module",
    "workers"}. Memory is the peak, over a 0.2 s sampling of the whole process tree, of the
    summed resident set; PSS counts pages shared by forked workers once. On macOS
    "peak_pss_sum_mb" holds the summed phys_footprint instead (Activity Monitor's "Memory"), when
    available; Linux PSS is unchanged.
    """
    if task not in TASKS:
        raise ValueError(f"task must be one of {TASKS}")
    if build_dir is None:
        if build == "official":
            build_dir = official_build()
        elif build == "fork":
            build_dir = fork_build()
            if build_dir is None:
                raise RuntimeError("fork build not installed: call kickmix_tools.install() first")
        else:
            raise ValueError("build must be 'fork' or 'official'")
    if not _has_module(Path(build_dir)):
        raise FileNotFoundError(f"no kickmix extension module in {build_dir}")
    if workers is None and task != "dfthcsos":
        workers = default_workers(task)
    spec = {"task": task, "circuit": str(circuit), "build": str(build_dir), "salt": salt,
            "competition": str(competition_dir()), "workers": workers,
            "dqi_shots": dqi_shots}
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(spec)]
    start = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    sampler = _PeakSampler(proc.pid)
    sampler.start()
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise TimeoutError(f"{task}/{build} exceeded {timeout} s")
    finally:
        sampler.finish()
    wall = time.monotonic() - start
    if proc.returncode:
        raise RuntimeError(f"{task}/{build} verifier process failed (exit {proc.returncode}):\n{stderr[-3000:]}")
    line = next((l for l in reversed(stdout.splitlines()) if l.startswith(_MARK)), None)
    if line is None:
        raise RuntimeError(f"{task}/{build}: no result line in output:\n{stdout[-1500:]}\n{stderr[-1500:]}")
    payload = json.loads(line[len(_MARK):])
    return {"task": task, "build": build, "result": payload["result"], "wall_seconds": wall,
            "peak_rss_sum_mb": sampler.peak_rss, "peak_pss_sum_mb": sampler.peak_pss,
            "max_single_process_mb": payload["max_single_process_mb"],
            "kickmix_module": payload["kickmix_module"], "workers": workers,
            "provenance": provenance() if build == "fork" else {"kickmix_tools_version": __version__}}


def _flatten(x, prefix=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, x


def compare(task: str, circuit, workers: int | None = None, *, quiet: bool = False, **kwargs) -> dict:
    """Verify with the official build, then the fork (one after the other, so timings are
    not disturbed), and compare every field of the two verifier results for exact equality.

    Returns {"official": run, "fork": run, "fields": [(name, official, fork, equal)...],
    "identical": bool, "n_fields": int, "n_different": int}.
    """
    runs = {b: verify(task, circuit, build=b, workers=workers, **kwargs) for b in ("official", "fork")}
    rows = []
    a, b = dict(_flatten(runs["official"]["result"])), dict(_flatten(runs["fork"]["result"]))
    for key in list(a) + [k for k in b if k not in a]:
        va, vb = a.get(key, "<missing>"), b.get(key, "<missing>")
        rows.append((key, va, vb, va == vb))
    out = {"task": task, "official": runs["official"], "fork": runs["fork"], "fields": rows,
           "n_fields": len(rows), "n_different": sum(not r[3] for r in rows),
           "identical": all(r[3] for r in rows)}
    if not quiet:
        print(format_compare(out))
    return out


def _mem(run: dict) -> float:
    return run["peak_pss_sum_mb"] if run["peak_pss_sum_mb"] is not None else run["peak_rss_sum_mb"]


def memory_kind(run: dict) -> str:
    if run["peak_pss_sum_mb"] is None:
        return "RSS"
    return "footprint" if sys.platform == "darwin" else "PSS"


def format_compare(c: dict, max_rows: int = 12) -> str:
    o, f = c["official"], c["fork"]
    kind = memory_kind(o)
    lines = [f"task {c['task']}: {c['n_fields']} result fields compared, {c['n_different']} differ "
             f"-> {'IDENTICAL' if c['identical'] else 'DIFFERENT'}",
             f"  {'':10}{'official':>14}{'fork':>14}{'fork/official':>16}",
             f"  {'wall (s)':10}{o['wall_seconds']:14.1f}{f['wall_seconds']:14.1f}{f['wall_seconds'] / o['wall_seconds']:16.2f}",
             f"  {'peak ' + kind + ' (MB)':10}{_mem(o):14.0f}{_mem(f):14.0f}{_mem(f) / max(_mem(o), 1):16.2f}"]
    shown = [r for r in c["fields"] if not r[3]][:max_rows]
    if shown:
        lines.append("  differing fields:")
        lines += [f"    {k}: official={va!r} fork={vb!r}" for k, va, vb, _ in shown]
    return "\n".join(lines)


def angle_strict_check(family_dir, *, script: str | os.PathLike | None = None, build: str = "fork",
                       shots_per_address: int = 64, seed: int = 0, npz: str | None = None,
                       max_examples: int = 5, build_dir: str | os.PathLike | None = None) -> dict:
    """Run the sampled strict-contract checker for a DF-THC-SOS ANGLE_k family.

    `script` is angle_strict_check.py (default: next to this file, then the working
    directory). Needs the official dfthcsos package (taken from the competition data) and the
    chosen kickmix build. Returns the checker's JSON: official_check, family.verdict, per-circuit
    failure counts. "No violation found" is a statement about the sampled inputs only.
    """
    script = Path(script) if script else next(
        (p for p in (Path(__file__).resolve().with_name("angle_strict_check.py"), Path("angle_strict_check.py"))
         if p.is_file()), None)
    if script is None or not Path(script).is_file():
        raise FileNotFoundError("angle_strict_check.py not found; pass script=")
    if build_dir is None:
        build_dir = official_build() if build == "official" else fork_build()
    if build_dir is None:
        raise RuntimeError("fork build not installed: call kickmix_tools.install() first")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "strict.json"
        cmd = [sys.executable, str(script), str(family_dir), "--verifiers", str(competition_dir() / "verifiers"),
               "--shots-per-address", str(shots_per_address), "--seed", str(seed),
               "--max-examples", str(max_examples), "--out", str(out)]
        if npz:
            cmd += ["--npz", str(npz)]
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(build_dir), os.environ.get("PYTHONPATH")])))
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if not out.exists():
            raise RuntimeError(f"strict check failed (exit {proc.returncode}):\n{proc.stdout[-1500:]}\n{proc.stderr[-2500:]}")
        result = json.loads(out.read_text())
    result["exit_code"] = proc.returncode
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args()
    if args.worker:
        _worker(json.loads(args.worker))
    else:
        print(f"kickmix_tools {__version__}")
