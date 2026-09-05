"""Locate a sub-project inside a repository.

ft_irc is a whole repository. A CPP module is not: students publish one
repository holding every module, and the directory naming is inconsistent —
`cpp06`, `CPP06`, `cpp_06`, `CPP_Module_06`, `Module06`, `c++06`. Matching on
the directory name alone is therefore unreliable in both directions: it misses
real modules and it accepts unrelated directories that happen to contain "06".

So a candidate directory is found by a loose path pattern and then *confirmed by
content* — it must contain the header files the subject mandates for that
module. Same principle as project verification: names lie, content does not.
"""
import os, re
from collections import defaultdict


class Located:
    def __init__(self, path, files, score, why):
        self.path, self.files, self.score, self.why = path, files, score, why

    def __repr__(self):
        return f"<Located {self.path!r} files={len(self.files)} score={self.score}>"


def _norm(p):
    return p.replace("\\", "/").strip("/")


def candidate_dirs(paths, spec):
    """Group repository paths by the directory a path pattern identifies.

    `paths` is any iterable of repo-relative file paths — from `git ls-tree`
    (no blobs downloaded) or from a local walk.
    """
    sub = spec.subproject or {}
    pats = [re.compile(p) for p in sub.get("path_patterns", [])]
    if not pats:
        return {}
    groups = defaultdict(list)
    for raw in paths:
        p = _norm(raw)
        for pat in pats:
            m = pat.search(p)
            if not m:
                continue
            # The matched component ends the module directory; everything after
            # it belongs inside.
            end = m.end()
            # Walk back to the end of the matched directory component.
            cut = p.find("/", end - 1)
            root = p[:cut] if cut != -1 else os.path.dirname(p)
            groups[root].append(p)
            break
    return groups


def _basenames(files):
    return {os.path.basename(f) for f in files}


def _stems(files):
    out = set()
    for f in files:
        b = os.path.basename(f)
        for ext in (".hpp", ".cpp", ".h", ".tpp", ".ipp", ".hh", ".cc"):
            if b.endswith(ext):
                out.add(b[: -len(ext)])
    return out


def score_dir(files, spec):
    """How strongly does this directory look like the target module?"""
    sub = spec.subproject or {}
    stems = _stems(files)
    required = set(sub.get("required_stems", []))
    optional = set(sub.get("optional_stems", []))
    forbidden = set(sub.get("forbidden_stems", []))

    hit = {s for s in required if s in stems}
    opt = {s for s in optional if s in stems}
    bad = {s for s in forbidden if s in stems}

    need = sub.get("required_threshold", max(1, len(required) // 2))
    if len(hit) < need:
        return 0, f"only {len(hit)}/{need} required classes"
    if bad:
        return 0, f"contains {sorted(bad)[:2]} from another module"
    src = [f for f in files if f.endswith(spec.extensions)]
    if len(src) < sub.get("min_files", 2):
        return 0, f"only {len(src)} source files"
    return len(hit) * 3 + len(opt), f"{len(hit)} required, {len(opt)} supporting classes"


def locate(paths, spec):
    """Best matching sub-project directory, or None.

    Falls back to the repository root, because a student may publish a single
    module as its own repository (`someone/CPP06` holding `ex00/`, `ex01/`).
    That fallback is safe only because scoring rejects directories containing
    another module's classes: a repository holding every module fails it.
    """
    best = None
    for root, files in candidate_dirs(paths, spec).items():
        score, why = score_dir(files, spec)
        if score and (best is None or score > best.score):
            best = Located(root, files, score, why)
    if best is None:
        clean = [_norm(p) for p in paths if p and p.strip()]
        score, why = score_dir(clean, spec)
        if score:
            best = Located("", clean, score, why + " (repository root)")
    return best


def locate_all(paths, spec):
    """Every plausible sub-project directory, best first (a repo may hold forks
    or duplicated attempts of the same module)."""
    out = []
    for root, files in candidate_dirs(paths, spec).items():
        score, why = score_dir(files, spec)
        if score:
            out.append(Located(root, files, score, why))
    out.sort(key=lambda l: l.score, reverse=True)
    return out


def walk_local(root, spec):
    """Repo-relative paths for a directory on disk."""
    out = []
    excl = spec.exclude_dirs
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in excl and not d.startswith(".")]
        for f in fs:
            out.append(os.path.relpath(os.path.join(dp, f), root))
    return out
