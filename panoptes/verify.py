"""Decide whether a directory really is the project described by a Spec.

Repo names lie ("IRCServer" may be a different course), Makefiles use variables,
and many repos are abandoned stubs. So scoring is multi-signal and driven
entirely by the pattern file.
"""
import os, re
from . import normalize


class Verdict:
    def __init__(self, ok, score, lines, files, hits, reason=""):
        self.ok, self.score, self.lines, self.files = ok, score, lines, files
        self.hits, self.reason = hits, reason

    def __repr__(self):
        return f"<{'OK' if self.ok else 'REJECT'} score={self.score} lines={self.lines}>"


def _find_makefile(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        if dirpath.count(os.sep) - root.count(os.sep) > 2:
            continue
        for fn in filenames:
            if fn.lower() in ("makefile", "gnumakefile", "makefile.mk"):
                return os.path.join(dirpath, fn)
    return None


_NAME_RE = re.compile(r"^\s*NAME\s*[:?+]?=\s*(.+?)\s*$", re.M | re.I)


def verify(root, spec):
    v = spec.verification
    paths = list(normalize.source_files(root, spec))
    blobs = [normalize.read(p) for p in paths]
    joined = "\n".join(blobs)
    total_lines = sum(b.count("\n") for b in blobs)

    if len(paths) < v.get("min_source_files", 1):
        return Verdict(False, 0, total_lines, len(paths), [], "too few source files")
    if total_lines < v.get("min_total_lines", 0):
        return Verdict(False, 0, total_lines, len(paths), [], "too few lines (stub?)")

    for bad in v.get("reject_tokens", []):
        if bad and bad in joined:
            return Verdict(False, 0, total_lines, len(paths), [],
                           f"reject token {bad!r} (different course)")

    score, hits = 0, []

    req = v.get("required_tokens", {})
    found = [t for t in req.get("tokens", []) if re.search(rf"\b{re.escape(t)}\b", joined)]
    if req and len(found) < req.get("threshold", 0):
        return Verdict(False, 0, total_lines, len(paths), found,
                       f"only {len(found)}/{req.get('threshold')} required tokens")
    if found:
        score += req.get("weight", 1)
        hits.append(f"{len(found)}/{len(req.get('tokens', []))} required tokens")

    opt = v.get("optional_tokens", {})
    ofound = [t for t in opt.get("tokens", []) if re.search(rf"\b{re.escape(t)}\b", joined)]
    if ofound:
        score += opt.get("weight", 1)
        hits.append(f"{len(ofound)} supporting tokens")

    mk = _find_makefile(root)
    if mk:
        m = _NAME_RE.search(normalize.read(mk))
        if m:
            declared = m.group(1).strip()
            if any(declared == want for want in v.get("makefile_name", [])):
                score += v.get("makefile_name_weight", 1)
                hits.append(f"Makefile NAME={declared}")
            elif "$" in declared:
                hits.append(f"Makefile NAME={declared} (indirect)")

    min_score = v.get("min_score", 1)
    ok = score >= min_score
    return Verdict(ok, score, total_lines, len(paths), hits,
                   "" if ok else f"score {score} < {min_score}")
