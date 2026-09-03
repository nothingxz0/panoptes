"""Recover repositories from Software Heritage.

GH Archive tells us a repository existed; GitHub returns 404 for it now. Those
are exactly the interesting ones — a source that was copied and then deleted or
made private. Software Heritage archives public GitHub repos independently, so
a good fraction survive there.

Uses the Vault API (request one flat tarball per repo) rather than walking the
directory tree file by file, which would need ~30 requests per repo instead of
three.
"""
import os, io, time, tarfile, shutil, requests

API = "https://archive.softwareheritage.org/api/1"
UA = "code_cmp/0.1 (42 academic integrity tooling)"

# Branch names to try, best first.
BRANCHES = ["refs/heads/main", "refs/heads/master", "refs/heads/develop", "HEAD"]


class Unavailable(Exception):
    """Repository is not in the archive, or has no usable snapshot."""


class Session:
    def __init__(self, token=None, verbose=False):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        tok = token or os.environ.get("SWH_TOKEN")
        if tok:
            self.s.headers["Authorization"] = f"Bearer {tok}"
        self.verbose = verbose

    def get(self, url, tries=5, **kw):
        for attempt in range(tries):
            r = self.s.get(url, timeout=60, **kw)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 0) or 0)
                if not wait:
                    reset = r.headers.get("X-RateLimit-Reset")
                    wait = max(5, int(reset) - int(time.time())) if reset else 30
                time.sleep(min(wait + 1, 300))
                continue
            if r.status_code in (502, 503, 504):
                time.sleep(3 * (attempt + 1))
                continue
            return r
        return r

    def post(self, url, tries=4):
        for attempt in range(tries):
            r = self.s.post(url, timeout=60)
            if r.status_code == 429:
                time.sleep(30)
                continue
            if r.status_code in (502, 503, 504):
                time.sleep(3 * (attempt + 1))
                continue
            return r
        return r


def directory_swhid(sess, full_name):
    """Resolve github.com/<full_name> to the SWHID of its newest root tree."""
    origin = f"https://github.com/{full_name}"
    r = sess.get(f"{API}/origin/{origin}/visits/", params={"per_page": 20})
    if r.status_code != 200:
        raise Unavailable("origin not archived")
    visits = [v for v in r.json()
              if v.get("status") == "full" and v.get("snapshot")]
    if not visits:
        raise Unavailable("no successful visit")
    visits.sort(key=lambda v: v.get("date", ""), reverse=True)

    for visit in visits[:3]:
        r = sess.get(f"{API}/snapshot/{visit['snapshot']}/")
        if r.status_code != 200:
            continue
        branches = r.json().get("branches", {})
        if not branches:
            continue
        names = [b for b in BRANCHES if b in branches] or list(branches)
        for name in names:
            target = branches[name]
            # Aliases point at another branch; follow one hop.
            if target.get("target_type") == "alias":
                target = branches.get(target["target"], target)
            if target.get("target_type") != "revision":
                continue
            rev = sess.get(f"{API}/revision/{target['target']}/")
            if rev.status_code != 200:
                continue
            d = rev.json().get("directory")
            if d:
                return f"swh:1:dir:{d}"
    raise Unavailable("no revision resolved")


def fetch_tree(sess, swhid, dest, poll_timeout=600, poll_every=8):
    """Cook a flat tarball of one directory and unpack it into dest."""
    url = f"{API}/vault/flat/{swhid}/"
    r = sess.post(url)
    if r.status_code not in (200, 201, 202):
        raise Unavailable(f"vault refused ({r.status_code})")

    deadline = time.time() + poll_timeout
    status = r.json().get("status")
    while status not in ("done", "failed"):
        if time.time() > deadline:
            raise Unavailable("vault timed out")
        time.sleep(poll_every)
        p = sess.get(url)
        if p.status_code != 200:
            raise Unavailable(f"vault poll {p.status_code}")
        status = p.json().get("status")
    if status == "failed":
        raise Unavailable("vault cooking failed")

    raw = sess.get(f"{url}raw/", stream=True)
    if raw.status_code != 200:
        raise Unavailable(f"vault download {raw.status_code}")
    blob = io.BytesIO(raw.content)

    tmp = dest + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        with tarfile.open(fileobj=blob, mode="r:*") as tf:
            members = [m for m in tf.getmembers()
                       if m.isreg() and not m.name.startswith(("/", ".."))
                       and ".." not in m.name.split("/")]
            tf.extractall(tmp, members=members, filter="data")
    except Exception as e:                                       # noqa: BLE001
        shutil.rmtree(tmp, ignore_errors=True)
        raise Unavailable(f"bad tarball: {str(e)[:60]}")

    # The vault wraps everything in a single swh:1:dir:... directory.
    entries = [os.path.join(tmp, e) for e in os.listdir(tmp)]
    root = entries[0] if len(entries) == 1 and os.path.isdir(entries[0]) else tmp
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(root, dest)
    shutil.rmtree(tmp, ignore_errors=True)
    return dest


def recover(full_name, dest, sess=None):
    """Fetch one deleted repo. Raises Unavailable if the archive lacks it."""
    sess = sess or Session()
    swhid = directory_swhid(sess, full_name)
    return fetch_tree(sess, swhid, dest)


def recover_missing(spec, names, jobs=4, progress=None):
    """Try to recover a list of repos into the corpus. Verified ones are kept."""
    import concurrent.futures as cf
    from . import verify as verify_mod, fetch as fetch_mod

    man = fetch_mod.load_manifest(spec)
    sess = Session()
    stats = {"archived": 0, "verified": 0, "absent": 0, "failed": 0}

    def work(name):
        dest = os.path.join(spec.corpus_dir, name.replace("/", "~"))
        if os.path.isdir(dest):
            return name, "already present", None
        try:
            swhid = directory_swhid(sess, name)
        except Unavailable as e:
            return name, f"absent: {e}", None
        try:
            fetch_tree(sess, swhid, dest)
        except Unavailable as e:
            return name, f"failed: {e}", None
        v = verify_mod.verify(dest, spec)
        if not v.ok:
            shutil.rmtree(dest, ignore_errors=True)
            return name, f"archived but {v.reason}", None
        return name, "ok", v

    done = 0
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        for name, msg, v in ex.map(work, names):
            done += 1
            if v is not None:
                stats["verified"] += 1
                stats["archived"] += 1
                man["accepted"][name] = {
                    "dir": name.replace("/", "~"), "lines": v.lines,
                    "files": v.files, "score": v.score, "stars": 0,
                    "owner": name.split("/")[0], "pushed_at": "",
                    "url": f"https://github.com/{name}", "source": "software-heritage",
                }
                man["rejected"].pop(name, None)
            elif msg.startswith("absent"):
                stats["absent"] += 1
            elif msg.startswith("archived"):
                stats["archived"] += 1
            else:
                stats["failed"] += 1
            if done % 20 == 0:
                fetch_mod.save_manifest(spec, man)
            if progress:
                progress(done, len(names), name, msg)
    fetch_mod.save_manifest(spec, man)
    return stats
