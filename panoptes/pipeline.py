"""End-to-end orchestration: from an empty corpus to a calibrated index.

Each stage is idempotent and records its result, so an interrupted run can be
resumed by invoking the same command again. Stages that already hold their
output are skipped unless --force is given.
"""
import json, os, time
from . import discover, gharchive, fetch, index, calibrate, swh, spec as spec_mod

STATE = "_pipeline.json"


def _state_path(spec):
    return os.path.join(spec.corpus_dir, STATE)


def load_state(spec):
    p = _state_path(spec)
    if os.path.isfile(p):
        with open(p) as fh:
            return json.load(fh)
    return {}


def save_state(spec, state):
    os.makedirs(spec.corpus_dir, exist_ok=True)
    with open(_state_path(spec), "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)


def _done(state, stage, spec):
    return stage in state.get("completed", {})


def _mark(state, stage, spec, detail):
    state.setdefault("completed", {})[stage] = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"), **detail}
    save_state(spec, state)


def run(spec, stages=None, force=False, jobs=10, recover=False,
        max_repos=None, deep=False, log=print, progress=None):
    """Run the full corpus pipeline. Returns the final state dict."""
    all_stages = ["discover", "fetch", "recover", "index", "calibrate"]
    stages = stages or [s for s in all_stages if s != "recover" or recover]
    state = load_state(spec)
    cand_path = os.path.join(spec.corpus_dir, "_candidates.json")

    # Resumability is based on artifacts on disk, not just the state file, so a
    # corpus built by an earlier version (or by the individual stage commands)
    # is still recognised as complete.
    have_candidates = (os.path.isfile(cand_path)
                       and len(json.load(open(cand_path))) > 0)

    # ---------------------------------------------------------- discover
    if "discover" in stages and (force or not (have_candidates
                                               or _done(state, "discover", spec))):
        log("[1/5] discovering repositories")
        found = json.load(open(cand_path)) if os.path.isfile(cand_path) else {}
        before = len(found)
        # The event log first: one query returns every repository that ever
        # existed, in seconds, and covers more than paged search does. GitHub
        # search then adds only what the log's name patterns miss.
        log("      GH Archive event log")
        try:
            arch = gharchive.find_repos(spec, verbose=False)
            found.update({k: v for k, v in arch.items() if k not in found})
            log(f"      {len(arch):,} repos from the event log")
        except Exception as e:                                   # noqa: BLE001
            log(f"      GH Archive unavailable: {str(e)[:100]}")
        src = discover.token_source()
        log(f"      GitHub search — {'authenticated' if src else 'anonymous (slow)'}"
            f"{', month-sliced' if deep else ''}")
        found.update({k: v for k, v in
                      discover.discover(spec, max_repos=max_repos,
                                        slice_by_month=deep).items()
                      if k not in found})
        discover.save(found, cand_path)
        log(f"      {len(found)} candidates ({len(found)-before} new)")
        _mark(state, "discover", spec, {"candidates": len(found)})
    elif "discover" in stages:
        n = len(json.load(open(cand_path))) if have_candidates else 0
        log(f"[1/5] discover — SKIPPED, using {n:,} candidates from a previous run.")
        log(f"      This does not mean there are no new repositories.")
        log(f"      Run './pan setup {spec.name} --refresh' to search the sources again.")
        state.setdefault("completed", {}).setdefault(
            "discover", {"at": "pre-existing", "candidates": n})
        save_state(spec, state)

    # ------------------------------------------------------------- fetch
    if "fetch" in stages:
        cands = json.load(open(cand_path)) if os.path.isfile(cand_path) else {}
        man = fetch.load_manifest(spec)
        todo = len(cands) - len(man["accepted"]) - len(man["rejected"])
        if todo > 0 or force:
            log(f"[2/5] cloning and verifying {todo} repositories")
            fetch.retry_failed(spec)
            acc, rej = fetch.fetch(spec, cands, jobs=jobs, progress=progress)
            log(f"      {acc} verified, {rej} rejected")
            _mark(state, "fetch", spec, {"verified": acc, "rejected": rej})
        else:
            log(f"[2/5] fetch — nothing new ({len(man['accepted']):,} verified)")
            state.setdefault("completed", {}).setdefault(
                "fetch", {"at": "pre-existing", "verified": len(man["accepted"])})
            save_state(spec, state)

    # ----------------------------------------------------------- recover
    if "recover" in stages:
        man = fetch.load_manifest(spec)
        gone = sorted(k for k, v in man["rejected"].items()
                      if "not found" in v["reason"].lower()
                      or "could not read" in v["reason"].lower())
        if gone:
            log(f"[3/5] Software Heritage recovery for {len(gone)} deleted repos")
            st = swh.recover_missing(spec, gone, jobs=4, progress=progress)
            log(f"      {st['verified']} recovered and verified "
                f"({st['absent']} not archived)")
            _mark(state, "recover", spec, st)
        else:
            log("[3/5] recover — nothing to recover")
    else:
        log("[3/5] recover — skipped (pass --recover to enable)")

    # ------------------------------------------------------------- index
    man = fetch.load_manifest(spec)
    idx_path = os.path.join(spec.corpus_dir, index.INDEX)
    # Staleness is judged from the index itself — how many repos it actually
    # holds versus how many the manifest says are verified. Relying on a state
    # key would force a needless full rebuild for any corpus built elsewhere.
    indexed = 0
    if os.path.isfile(idx_path):
        try:
            _c = index.open_index(spec)
            indexed = _c.execute("SELECT count(*) FROM repo").fetchone()[0]
            _c.close()
        except Exception:                                        # noqa: BLE001
            indexed = 0
    stale = indexed < len(man["accepted"])
    if "index" in stages and (force or stale):
        log(f"[4/5] indexing {len(man['accepted'])} repositories")
        st = index.build(spec, progress=progress)
        log(f"      {st['repos']} repos -> {st['representatives']} independent "
            f"({st['repos']-st['representatives']} duplicates collapsed)")
        log(f"      {st['distinct_prints']:,} fingerprints")
        state["indexed_repos"] = len(man["accepted"])
        _mark(state, "index", spec, {"repos": st["repos"],
                                     "representatives": st["representatives"]})
    elif "index" in stages:
        log(f"[4/5] index — up to date ({len(man['accepted']):,} repos)")
        state.setdefault("completed", {}).setdefault(
            "index", {"at": "pre-existing", "repos": len(man["accepted"])})
        save_state(spec, state)

    # --------------------------------------------------------- calibrate
    con_meta = {}
    if os.path.isfile(idx_path):
        _c = index.open_index(spec)
        con_meta = index.meta(_c)
        _c.close()
    calibrated = "top_null_mean" in con_meta and "run_null_threshold" in con_meta
    if "calibrate" in stages and (force or stale or not calibrated):
        log("[5/5] calibrating against honest baselines")
        top, _ = calibrate.top_match_null(spec, progress=progress)
        log(f"      honest best-match: {top['top_null_mean']*100:.2f}% "
            f"(p99 {top['top_null_p99']*100:.2f}%)")
        runs, _ = calibrate.longest_run_null(spec, progress=progress)
        log(f"      honest longest run: {runs['run_null_median']} tokens median, "
            f"{runs['run_null_p99']} p99")
        log(f"      evidence threshold: {runs['run_null_threshold']} tokens")
        ch = calibrate.channel_nulls(spec, progress=progress)
        if ch:
            log(f"      honest shape overlap: {ch['ast_null_mean']*100:.1f}% "
                f"(p99 {ch['ast_null_p99']*100:.1f}%)")
            log(f"      honest type overlap : {ch['sig_null_mean']*100:.1f}% "
                f"(p99 {ch['sig_null_p99']*100:.1f}%)")
        _mark(state, "calibrate", spec, {**top, **runs, **ch})
    elif "calibrate" in stages:
        log(f"[5/5] calibrate — up to date "
            f"(honest best-match {con_meta['top_null_mean']*100:.2f}%)")
        state.setdefault("completed", {}).setdefault(
            "calibrate", {"at": "pre-existing"})
        save_state(spec, state)

    return state


def status(spec):
    """Human-readable pipeline state."""
    state = load_state(spec)
    man = fetch.load_manifest(spec)
    out = {
        "project": spec.name,
        "verified": len(man["accepted"]),
        "rejected": len(man["rejected"]),
        "stages": list(state.get("completed", {})),
    }
    idx = os.path.join(spec.corpus_dir, index.INDEX)
    if os.path.isfile(idx):
        con = index.open_index(spec)
        out["index"] = index.meta(con)
        con.close()
    return out
