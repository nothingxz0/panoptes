"""Fingerprint index over a verified corpus.

Uses winnowing (Schleimer/Wilkerson/Aiken): hash every k-gram of tokens, then
keep the minimum hash in each sliding window of w. This preserves the guarantee
that any shared run of >= k+w-1 tokens shares at least one fingerprint, while
storing only ~2/(w+1) of the hashes.
"""
import os, sqlite3, hashlib, statistics, itertools
from collections import Counter, defaultdict
from . import normalize, fetch

INDEX = "_index.sqlite"


def gram_hash(tokens):
    return int.from_bytes(
        hashlib.blake2b(" ".join(tokens).encode(), digest_size=8).digest(), "big",
        signed=True)


def fingerprints(stream, k, w):
    """Winnowed (hash, file, line) from a (token, file, line) stream."""
    toks = [t[0] for t in stream]
    n = len(toks) - k + 1
    if n <= 0:
        return []
    hashes = [gram_hash(toks[i:i + k]) for i in range(n)]
    out, last = [], -1
    for i in range(max(1, len(hashes) - w + 1)):
        window = hashes[i:i + w]
        if not window:
            break
        mn = min(window)
        j = i + window.index(mn)          # rightmost-min would also be valid
        if j != last:
            out.append((mn, stream[j][1], stream[j][2]))
            last = j
    return out


def repo_prints(root, spec, k, w, channel="abstract"):
    stream = normalize.token_stream(root, spec, channel)
    return fingerprints(stream, k, w), len(stream)


def candidate_pairs(sets, max_repos_per_print=200):
    """Yield every pair of repos sharing at least one fingerprint.

    Inverts {repo: {hashes}} into {hash: [repos]} and emits pairs from each
    bucket. Fingerprints held by a very large number of repos are skipped: they
    are residual boilerplate, contribute nothing discriminative, and would
    generate quadratically many pairs on their own.
    """
    buckets = defaultdict(list)
    for rid, hs in sets.items():
        for h in hs:
            buckets[h].append(rid)
    seen = set()
    for repos in buckets.values():
        if len(repos) < 2 or len(repos) > max_repos_per_print:
            continue
        repos.sort()
        for i in range(len(repos)):
            for j in range(i + 1, len(repos)):
                pair = (repos[i], repos[j])
                if pair not in seen:
                    seen.add(pair)
                    yield pair


def _connect(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    return con


def build(spec, progress=None):
    """(Re)build the index for a project's accepted corpus."""
    man = fetch.load_manifest(spec)
    accepted = man["accepted"]
    if not accepted:
        raise SystemExit(f"corpus for {spec.name} is empty — run discover/fetch first")

    k = spec.compare.get("k_high", 25)
    w = spec.compare.get("winnow_window", 8)
    path = os.path.join(spec.corpus_dir, INDEX)
    if os.path.exists(path):
        os.remove(path)
    con = _connect(path)
    con.executescript("""
        CREATE TABLE repo (id INTEGER PRIMARY KEY, name TEXT UNIQUE, dir TEXT,
                           url TEXT, owner TEXT, lines INT, tokens INT, nprints INT,
                           representative INT DEFAULT 1, cluster INT);
        CREATE TABLE print (hash INTEGER, repo INTEGER, file TEXT, line INT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)

    sets = {}
    for i, (name, info) in enumerate(sorted(accepted.items()), 1):
        root = os.path.join(spec.corpus_dir, info["dir"])
        if not os.path.isdir(root):
            continue
        prints, ntok = repo_prints(root, spec, k, w)
        con.execute("INSERT INTO repo (name,dir,url,owner,lines,tokens,nprints) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (name, info["dir"], info.get("url", ""), info.get("owner", ""),
                     info.get("lines", 0), ntok, len(prints)))
        rid = con.execute("SELECT id FROM repo WHERE name=?", (name,)).fetchone()[0]
        con.executemany("INSERT INTO print VALUES (?,?,?,?)",
                        ((h, rid, f, l) for h, f, l in prints))
        sets[rid] = {h for h, _, _ in prints}
        if progress:
            progress(i, len(accepted), name, len(prints))
    con.commit()

    # --- boilerplate: fingerprints present in many independent repos ---
    df = Counter()
    for s in sets.values():
        df.update(s)
    n = len(sets)
    thr = spec.compare.get("boilerplate_df", 0.25)
    boiler = {h for h, c in df.items() if c >= thr * n}

    # --- cluster-collapse: the public corpus contains copied repos, and if
    # they are left in they inflate the "normal" band and mask real cheating ---
    clean = {r: s - boiler for r, s in sets.items() if s - boiler}
    ids = sorted(clean)
    edges = defaultdict(set)
    # All-pairs is O(n^2) and does not survive a corpus of a few thousand. Almost
    # every pair shares zero fingerprints, so invert the index instead: group
    # repos by fingerprint and only score pairs that actually co-occur.
    for a, b in candidate_pairs(clean):
        sa, sb = clean[a], clean[b]
        c = len(sa & sb) / min(len(sa), len(sb))
        if c >= 0.15:                      # far above the honest ceiling
            edges[a].add(b)
            edges[b].add(a)

    seen, cluster_id = set(), 0
    for r in ids:
        if r in seen:
            continue
        stack, group = [r], []
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            group.append(x)
            stack.extend(edges[x] - seen)
        cluster_id += 1
        keep = max(group, key=lambda i: len(clean[i]))
        for x in group:
            con.execute("UPDATE repo SET cluster=?, representative=? WHERE id=?",
                        (cluster_id, 1 if x == keep else 0, x))

    reps = {r for r in ids if con.execute(
        "SELECT representative FROM repo WHERE id=?", (r,)).fetchone()[0]}
    base = [len(clean[a] & clean[b]) / min(len(clean[a]), len(clean[b]))
            for a, b in candidate_pairs({r: clean[r] for r in reps})]

    stats = {
        "k": k, "w": w, "repos": n, "representatives": len(reps),
        "clusters": cluster_id, "boilerplate_prints": len(boiler),
        "distinct_prints": len(df),
        "baseline_mean": statistics.mean(base) if base else 0.0,
        "baseline_stdev": statistics.pstdev(base) if len(base) > 1 else 0.0,
        "baseline_max": max(base) if base else 0.0,
        "baseline_p95": sorted(base)[int(0.95 * len(base))] if base else 0.0,
    }
    con.executemany("INSERT INTO meta VALUES (?,?)",
                    [(k_, str(v)) for k_, v in stats.items()])
    con.execute("CREATE TABLE boiler (hash INTEGER PRIMARY KEY)")
    con.executemany("INSERT OR IGNORE INTO boiler VALUES (?)", ((h,) for h in boiler))
    con.execute("CREATE INDEX idx_print_hash ON print(hash)")
    con.execute("CREATE INDEX idx_print_repo ON print(repo)")
    con.commit()
    con.close()
    return stats


def open_index(spec):
    path = os.path.join(spec.corpus_dir, INDEX)
    if not os.path.isfile(path):
        raise SystemExit(f"no index for {spec.name} — run: ./cmp.py index {spec.name}")
    return _connect(path)


def meta(con):
    out = {}
    for k, v in con.execute("SELECT key,value FROM meta"):
        try:
            out[k] = float(v) if "." in v else int(v)
        except ValueError:
            out[k] = v
    return out
