"""Compare one student submission against the whole indexed corpus."""
import os, statistics
from collections import defaultdict
from . import normalize, index as index_mod, verify as verify_mod, aststruct, typesig
from . import locate as locate_mod


class Match:
    def __init__(self, repo, url, owner, shared, containment, z, regions):
        self.repo, self.url, self.owner = repo, url, owner
        self.shared, self.containment, self.z = shared, containment, z
        self.regions = regions
        self.self_match = False
        self.ast_shared = 0       # functions with an identical control-flow skeleton
        self.ast_frac = 0.0
        self.ast_rows = []
        self.p99 = 1.0            # 99th percentile of the top-match null
        self.run_threshold = 250  # corpus-derived; see cmp/calibrate.py
        self.longest = 0          # longest identical token run
        self.sig_shared = 0       # functions with an identical type signature
        self.sig_frac = 0.0
        self.dir = ""             # corpus path; for a sub-project this is the
                                  # module directory, not the repository root

    @property
    def verdict(self):
        """Thresholds are relative to the top-match null, so they hold as the
        corpus grows. `p99` is the 99th percentile of what an honest submission
        scores as its single best match."""
        if self.self_match:
            return "SELF"
        # Thresholds per channel come from measured honest baselines:
        # AST skeletons  honest mean 0.18%, p95 2.0%   -> 0.20 is ~10x p95
        # type signatures honest mean 1.84%, p95 7.3%  -> 0.30 is ~4x p95
        # Signatures are noisier, so they need a higher bar for the same weight.
        if (self.containment >= max(0.30, 2.5 * self.p99)
                or self.ast_frac >= 0.40 or self.sig_frac >= 0.55):
            return "HIGH"
        if (self.containment >= max(0.18, 1.5 * self.p99)
                or self.ast_frac >= 0.20 or self.sig_frac >= 0.30):
            return "MEDIUM"
        if (self.containment >= self.p99 or self.longest >= self.run_threshold
                or self.ast_frac >= 0.08 or self.sig_frac >= 0.15):
            return "LOW"
        return "NORMAL"


def _longest_runs(sub_stream, corpus_root, spec, limit=5, min_run=20):
    """Re-derive the longest identical abstracted-token runs, for evidence."""
    corp = normalize.token_stream(corpus_root, spec)
    a = [t[0] for t in sub_stream]
    b = [t[0] for t in corp]
    K = 12
    idx = defaultdict(list)
    for i in range(len(b) - K + 1):
        idx[tuple(b[i:i + K])].append(i)
    runs, used = [], set()
    for i in range(len(a) - K + 1):
        if i in used:
            continue
        for j in idx.get(tuple(a[i:i + K]), ())[:30]:
            L = K
            while i + L < len(a) and j + L < len(b) and a[i + L] == b[j + L]:
                L += 1
            if L >= min_run:
                runs.append((L, i, j))
                used.update(range(i, i + L))
                break
    runs.sort(reverse=True)
    out = []
    for L, i, j in runs[:limit]:
        out.append({
            "tokens": L,
            "sub_file": sub_stream[i][1], "sub_start": sub_stream[i][2],
            "sub_end": sub_stream[i + L - 1][2],
            "corpus_file": corp[j][1], "corpus_start": corp[j][2],
            "corpus_end": corp[j + L - 1][2],
        })
    return out


def check(submission, spec, student_handles=(), top_n=None, evidence=True,
          progress=None):
    """Compare `submission` against the corpus. Returns (summary, [Match])."""
    if not os.path.isdir(submission):
        raise SystemExit(f"not a directory: {submission}")

    con = index_mod.open_index(spec)
    meta = index_mod.meta(con)
    k, w = meta["k"], meta["w"]
    top_n = top_n or spec.compare.get("report_top_n", 25)

    # A sub-project submission may be handed to us as the module directory
    # itself, or as the parent holding every module. Accept both.
    if spec.is_subproject:
        v0 = verify_mod.verify(submission, spec)
        if not v0.ok:
            found = locate_mod.locate(locate_mod.walk_local(submission, spec), spec)
            if found and found.path:
                submission = os.path.join(submission, found.path)

    v = verify_mod.verify(submission, spec)
    stream = normalize.token_stream(submission, spec)
    prints = index_mod.fingerprints(stream, k, w)
    boiler = {h for (h,) in con.execute("SELECT hash FROM boiler")}
    sub_set = {h for h, _, _ in prints} - boiler
    if not sub_set:
        return {"verify": v, "meta": meta, "prints": 0, "files": v.files}, []

    # one pass over the index: which corpus repos share which fingerprints
    shared = defaultdict(set)
    qmarks = ",".join("?" * min(len(sub_set), 900))
    subs = list(sub_set)
    for chunk in (subs[i:i + 900] for i in range(0, len(subs), 900)):
        q = f"SELECT hash, repo FROM print WHERE hash IN ({','.join('?' * len(chunk))})"
        for h, rid in con.execute(q, chunk):
            shared[rid].add(h)

    repos = {rid: (name, url, owner, np, rdir) for rid, name, url, owner, np, rdir in
             con.execute("SELECT id,name,url,owner,nprints,dir FROM repo")}

    # Score against the null distribution of the BEST match out of N, not the
    # all-pairs distribution — see cmp/calibrate.py. Falls back to all-pairs
    # only if the corpus has never been calibrated.
    if "top_null_mean" in meta:
        mean = meta["top_null_mean"]
        sd = meta["top_null_stdev"] or 1e-9
        p99 = meta.get("top_null_p99", 1.0)
    else:
        mean = meta["baseline_mean"]
        sd = meta["baseline_stdev"] or 1e-9
        p99 = meta.get("baseline_max", 1.0)
    handles = {h.lower() for h in student_handles}

    matches = []
    for rid, hs in shared.items():
        name, url, owner, np, rdir = repos[rid]
        clean = {h for (h,) in con.execute(
            "SELECT DISTINCT hash FROM print WHERE repo=? AND hash NOT IN "
            "(SELECT hash FROM boiler)", (rid,))}
        denom = min(len(sub_set), len(clean)) or 1
        cont = len(hs & clean) / denom
        z = (cont - mean) / sd
        m = Match(name, url, owner, len(hs & clean), cont, z, [])
        m.dir = rdir
        m.p99 = p99
        m.run_threshold = meta.get("run_null_threshold", 250)
        if owner.lower() in handles:
            m.self_match = True
        matches.append(m)

    matches.sort(key=lambda m: m.containment, reverse=True)
    matches = matches[:top_n]

    if evidence:
        sub_funcs = (aststruct.repo_functions(submission, spec)
                     if aststruct.AVAILABLE else {})
        sub_sigs = (typesig.repo_signatures(submission, spec)
                    if typesig.AVAILABLE else {})
        for i, m in enumerate(matches):
            if m.verdict == "NORMAL" and i > 5:
                continue
            if m.dir:
                root = os.path.join(spec.corpus_dir, m.dir)
                m.regions = _longest_runs(stream, root, spec)
                m.longest = max((r["tokens"] for r in m.regions), default=0)
                if sub_funcs:
                    cf = aststruct.repo_functions(root, spec)
                    m.ast_rows, m.ast_frac = aststruct.compare(sub_funcs, cf)
                    m.ast_shared = len(m.ast_rows)
                if sub_sigs:
                    cs = typesig.repo_signatures(root, spec)
                    m.sig_shared, m.sig_frac = typesig.compare(sub_sigs, cs)
            if progress:
                progress(i + 1, len(matches), m.repo)

    con.close()
    summary = {
        "verify": v, "meta": meta, "prints": len(sub_set), "files": v.files,
        "lines": v.lines, "submission": os.path.abspath(submission),
        "corpus_size": meta["repos"],
    }
    return summary, matches
