"""Extreme-value calibration.

Scoring a submission against the distribution of ALL pairs is wrong once the
corpus is large: the question is not "is this pair unusual?" but "is the best
match out of N repos unusual?". With enough candidates, some repo always
overlaps a few percent with anyone by chance, so a pairwise z-score produces a
growing pile of false MEDIUMs as the corpus grows.

This computes the null distribution of the TOP match — for each repo, its best
containment against every other repo — which is what a submission is actually
compared against.
"""
import statistics, itertools, os, json, random
from collections import defaultdict
from . import index as index_mod


def top_match_null(spec, progress=None):
    con = index_mod.open_index(spec)
    boiler = {h for (h,) in con.execute("SELECT hash FROM boiler")}
    reps = [r for (r,) in con.execute(
        "SELECT id FROM repo WHERE representative=1")]
    sets = {}
    for rid in reps:
        s = {h for (h,) in con.execute(
            "SELECT DISTINCT hash FROM print WHERE repo=?", (rid,))} - boiler
        if s:
            sets[rid] = s
    ids = sorted(sets)
    best = {r: 0.0 for r in ids}
    # Same inversion as index.candidate_pairs: two repos sharing no fingerprint
    # cannot be each other's best match, so that pair never needs scoring.
    pairs = list(index_mod.candidate_pairs(sets))
    for n, (a, b) in enumerate(pairs, 1):
        sa, sb = sets[a], sets[b]
        c = len(sa & sb) / min(len(sa), len(sb))
        if c > best[a]:
            best[a] = c
        if c > best[b]:
            best[b] = c
        if progress and n % 20000 == 0:
            progress(n, len(pairs))
    if progress:
        progress(len(pairs), len(pairs))
    vals = sorted(best.values())
    stats = {
        "top_null_mean": statistics.mean(vals),
        "top_null_stdev": statistics.pstdev(vals),
        "top_null_p50": vals[len(vals) // 2],
        "top_null_p95": vals[int(0.95 * len(vals))],
        "top_null_p99": vals[int(0.99 * len(vals))],
        "top_null_max": vals[-1],
        "top_null_n": len(vals),
    }
    con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)",
                    [(k, str(v)) for k, v in stats.items()])
    con.commit()
    con.close()
    return stats, vals


def longest_run_null(spec, sample_repos=90, sample_pairs=400, seed=42,
                     progress=None):
    """Null distribution of the longest identical token run between honest repos.

    Long identical runs are far more common than intuition suggests: 42 subjects
    force near-identical shapes for the poll loop, the client lookup and the
    early-return guards. Measuring this stops a striking-looking 90-token match
    from being read as evidence when ~10% of honest pairs produce one.

    Sampled rather than exhaustive: all-pairs is O(n^2) longest-run searches.
    """
    from collections import defaultdict
    from . import normalize, index as index_mod

    # Sample cluster REPRESENTATIVES only. ft_irc is a group project, so
    # teammates push near-identical repos under separate accounts; including
    # those makes "honest" pairs look like 10,000-token matches and pushes the
    # threshold so high it can never fire.
    con = index_mod.open_index(spec)
    rep_dirs = [d for (d,) in con.execute(
        "SELECT dir FROM repo WHERE representative=1")]
    con.close()
    man = json.load(open(os.path.join(spec.corpus_dir, "_manifest.json")))
    by_dir = {v["dir"]: v for v in man["accepted"].values()}
    repos = sorted((by_dir[d] for d in rep_dirs if d in by_dir),
                   key=lambda v: v["dir"])
    rng = random.Random(seed)
    picked = rng.sample(repos, min(sample_repos, len(repos)))

    streams = {}
    for r in picked:
        st = normalize.token_stream(os.path.join(spec.corpus_dir, r["dir"]), spec)
        if len(st) > 500:
            streams[r["dir"]] = [t[0] for t in st]
    names = sorted(streams)

    def longest(a, b, K=12):
        idx = defaultdict(list)
        for i in range(len(b) - K + 1):
            idx[hash(tuple(b[i:i + K]))].append(i)
        best = 0
        for i in range(len(a) - K + 1):
            for j in idx.get(hash(tuple(a[i:i + K])), ())[:12]:
                L = K
                while i + L < len(a) and j + L < len(b) and a[i + L] == b[j + L]:
                    L += 1
                best = max(best, L)
        return best

    pairs = [(names[i], names[j]) for i in range(len(names))
             for j in range(i + 1, len(names))]
    rng.shuffle(pairs)
    pairs = pairs[:sample_pairs]
    runs = []
    for n, (x, y) in enumerate(pairs, 1):
        runs.append(longest(streams[x], streams[y]))
        if progress:
            progress(n, len(pairs))
    runs.sort()

    # A submission is compared against the whole corpus, so what matters is the
    # largest run out of N draws, not a single pair. Approximate that quantile.
    n_corpus = len(repos)
    q = max(0.0, 1.0 - 1.0 / max(n_corpus, 2))
    expected_top = runs[min(len(runs) - 1, int(q * len(runs)))]

    stats = {
        "run_null_median": runs[len(runs) // 2],
        "run_null_p95": runs[int(0.95 * len(runs))],
        "run_null_p99": runs[int(0.99 * len(runs))],
        "run_null_max": runs[-1],
        "run_null_expected_top": expected_top,
        # Guard against a contaminated sample: a threshold far above the p99 of
        # honest pairs means the sample still contains copies.
        "run_null_threshold": min(max(int(expected_top * 1.2), 250),
                                  max(int(runs[int(0.99 * len(runs))] * 2), 400)),
        "run_null_pairs": len(runs),
    }
    con = index_mod.open_index(spec)
    con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)",
                    [(k, str(v)) for k, v in stats.items()])
    con.commit()
    con.close()
    return stats, runs
