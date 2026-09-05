"""Clone discovered candidates, verify them, discard the ones that aren't
the project we're after. Maintains corpus/<project>/_manifest.json.
"""
import os, json, base64, shutil, subprocess, concurrent.futures as cf
from . import verify as verify_mod, locate as locate_mod

MANIFEST = "_manifest.json"


def _safe_dir(full_name):
    return full_name.replace("/", "~")


def load_manifest(spec):
    p = os.path.join(spec.corpus_dir, MANIFEST)
    if os.path.isfile(p):
        with open(p) as fh:
            return json.load(fh)
    return {"accepted": {}, "rejected": {}}


def save_manifest(spec, man):
    os.makedirs(spec.corpus_dir, exist_ok=True)
    with open(os.path.join(spec.corpus_dir, MANIFEST), "w") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)


def _git_auth_args():
    """Authenticate clones. Anonymous git clone is rate-limited hard enough that
    parallel fetching fails with credential prompts; an authenticated clone is
    not. Passed per-invocation with -c so the token never lands in .git/config.
    """
    from . import discover
    tok = discover.read_token()
    if not tok:
        return []
    basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
    return ["-c", f"http.extraheader=Authorization: Basic {basic}"]


def _run(cmd, cwd=None, timeout=180):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/true")
    return subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd, env=env)


def clone_subproject(url, dest, spec, timeout=240):
    """Fetch only the sub-project directory from a repository.

    A blobless clone downloads commit and tree objects but no file contents, so
    the full path listing is available for a fraction of the transfer. The
    module directory is identified from that listing, and only its blobs are
    then materialised via sparse checkout. For a repository holding ten CPP
    modules this transfers roughly a tenth of the data.
    """
    if os.path.isdir(dest):
        return True, "already present", None
    auth = _git_auth_args()
    r = _run(["git"] + auth + ["clone", "--quiet", "--depth", "1", "--no-tags",
                               "--single-branch", "--filter=blob:none",
                               "--no-checkout", url, dest], timeout=timeout)
    if r.returncode != 0:
        return False, r.stderr.decode(errors="ignore").strip()[:120], None

    r = _run(["git", "-C", dest, "ls-tree", "-r", "HEAD", "--name-only"],
             timeout=90)
    if r.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        return False, "ls-tree failed", None
    paths = r.stdout.decode(errors="ignore").split("\n")

    found = locate_mod.locate(paths, spec)
    if not found:
        shutil.rmtree(dest, ignore_errors=True)
        return False, "sub-project not present in repo", None

    _run(["git", "-C", dest, "sparse-checkout", "init", "--no-cone"], timeout=60)
    pattern = "/" + found.path.strip("/") + "/**\n" if found.path else "/*\n"
    with open(os.path.join(dest, ".git", "info", "sparse-checkout"), "w") as fh:
        fh.write(pattern)
    r = _run(["git"] + auth + ["-C", dest, "checkout"], timeout=timeout)
    if r.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        return False, f"sparse checkout failed: {r.stderr.decode(errors='ignore')[:80]}", None
    return True, "cloned", found.path


def _clone(url, dest, depth=1, timeout=180):
    if os.path.isdir(dest):
        return True, "already present"
    cmd = (["git"] + _git_auth_args() +
           ["clone", "--quiet", "--no-tags", "--single-branch", url, dest])
    if depth:
        cmd.insert(cmd.index("clone") + 1, "--depth")
        cmd.insert(cmd.index("--depth") + 1, str(depth))
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/true")
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        if r.returncode != 0:
            return False, r.stderr.decode(errors="ignore").strip()[:120]
        return True, "cloned"
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        return False, "clone timeout"
    except Exception as e:                                  # noqa: BLE001
        return False, str(e)[:120]


def fetch(spec, candidates, jobs=8, keep_git=False, progress=None):
    """Clone + verify each candidate. Returns (accepted, rejected) counts."""
    os.makedirs(spec.corpus_dir, exist_ok=True)
    man = load_manifest(spec)
    todo = [c for name, c in candidates.items()
            if name not in man["accepted"] and name not in man["rejected"]]

    def work(cand):
        dest = os.path.join(spec.corpus_dir, _safe_dir(cand["full_name"]))
        subpath = None
        if spec.is_subproject:
            ok, msg, subpath = clone_subproject(cand["clone_url"], dest, spec)
        else:
            ok, msg = _clone(cand["clone_url"], dest)
        if not ok:
            return cand, None, msg
        # For a sub-project the comparison unit is the module directory, not
        # the repository around it.
        target = os.path.join(dest, subpath) if subpath else dest
        v = verify_mod.verify(target, spec)
        v.subpath = subpath
        if not v.ok:
            shutil.rmtree(dest, ignore_errors=True)
            return cand, None, v.reason
        if not keep_git:
            shutil.rmtree(os.path.join(dest, ".git"), ignore_errors=True)
        return cand, v, "ok"

    done = 0
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        for cand, v, msg in ex.map(work, todo):
            done += 1
            name = cand["full_name"]
            if v is None:
                man["rejected"][name] = {"reason": msg}
            else:
                sp0 = getattr(v, "subpath", None)
                entry = {
                    "dir": os.path.join(_safe_dir(name), sp0) if sp0
                           else _safe_dir(name),
                    "lines": v.lines, "files": v.files,
                    "score": v.score, "stars": cand.get("stars", 0),
                    "owner": cand.get("owner", ""), "pushed_at": cand.get("pushed_at", ""),
                    "url": f"https://github.com/{name}",
                }
                if sp0:
                    entry["subpath"] = sp0
                    entry["repo_dir"] = _safe_dir(name)
                man["accepted"][name] = entry
            if progress:
                progress(done, len(todo), name, msg)
            if done % 25 == 0:
                save_manifest(spec, man)

    save_manifest(spec, man)
    return len(man["accepted"]), len(man["rejected"])


def retry_failed(spec, patterns=("Username", "could not read", "timeout",
                                 "Connection", "rate limit", "429")):
    """Re-queue candidates whose clone failed for transport reasons rather than
    because they are not the project. Verification rejections are left alone."""
    man = load_manifest(spec)
    retry = {k: v for k, v in man["rejected"].items()
             if any(p.lower() in v["reason"].lower() for p in patterns)}
    for k in retry:
        del man["rejected"][k]
    save_manifest(spec, man)
    return set(retry)


def adopt_existing(spec):
    """Register already-cloned directories (e.g. cloned by hand) in the manifest."""
    man = load_manifest(spec)
    if not os.path.isdir(spec.corpus_dir):
        return 0, 0
    added = dropped = 0
    for d in sorted(os.listdir(spec.corpus_dir)):
        full = os.path.join(spec.corpus_dir, d)
        if not os.path.isdir(full) or d.startswith("_"):
            continue
        name = d.replace("~", "/", 1)
        if name in man["accepted"] or name in man["rejected"]:
            continue
        v = verify_mod.verify(full, spec)
        if v.ok:
            man["accepted"][name] = {
                "dir": d, "lines": v.lines, "files": v.files, "score": v.score,
                "stars": 0, "owner": name.split("/")[0], "pushed_at": "",
                "url": f"https://github.com/{name}",
            }
            added += 1
        else:
            man["rejected"][name] = {"reason": v.reason}
            dropped += 1
    save_manifest(spec, man)
    return added, dropped
