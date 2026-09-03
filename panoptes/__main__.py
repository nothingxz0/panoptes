#!/usr/bin/env python3
"""Panoptes command line."""
import argparse, os, sys, json, time

from . import spec as spec_mod
from . import discover, gharchive, fetch, index as index_mod
from . import calibrate, compare as cmp_mod, report as report_mod
from . import swh, pipeline


# ----------------------------------------------------------------- helpers
def _bar(i, n, msg="", extra=""):
    pct = i / max(n, 1)
    fill = int(30 * pct)
    sys.stderr.write(f"\r      [{'#'*fill}{'.'*(30-fill)}] {i}/{n} {str(msg)[:38]:38s}")
    sys.stderr.flush()
    if i >= n:
        sys.stderr.write("\n")


def _fail(msg):
    raise SystemExit(f"panoptes: {msg}")


# ---------------------------------------------------------------- commands
def cmd_setup(a):
    """Everything from an empty corpus to a calibrated index."""
    s = spec_mod.load(a.project)
    t0 = time.time()
    print(f"Building corpus for {s.label}\n")
    pipeline.run(s, force=a.force, jobs=a.jobs, recover=a.recover,
                 max_repos=a.max, progress=lambda i, n, *r: _bar(i, n, r[0] if r else ""))
    print(f"\nReady in {(time.time()-t0)/60:.1f} min. Now run:")
    print(f"  ./pan check <submission_dir> -p {s.name} --student <github-handle>")


def cmd_check(a):
    s = spec_mod.load(a.project)
    if not os.path.isdir(a.submission):
        _fail(f"no such directory: {a.submission}")
    t0 = time.time()
    summary, matches = cmp_mod.check(
        a.submission, s, student_handles=a.student or (),
        top_n=a.top, evidence=not a.no_evidence,
        progress=lambda i, n, r: _bar(i, n, r))

    v = summary["verify"]
    name = os.path.basename(os.path.abspath(a.submission))
    print(f"\n{name}: {summary['files']} files, {summary['lines']:,} lines")
    if v.ok:
        print(f"project check: OK — {'; '.join(v.hits)}")
    else:
        print(f"project check: WARNING — {v.reason}")
        print(f"  this may not be a {s.name} submission; results will be unreliable")

    m = summary["meta"]
    if "top_null_mean" in m:
        print(f"corpus: {m['repos']:,} repos | an honest submission's best match "
              f"averages {m['top_null_mean']*100:.1f}% (p99 {m['top_null_p99']*100:.1f}%)\n")
    else:
        print(f"corpus: {m['repos']:,} repos | NOT CALIBRATED — run ./pan setup {s.name}\n")

    if not a.student:
        print("  note: no --student given. If the student published their own work,")
        print("        it will appear here as a near-100% match.\n")

    shown = 0
    for x in matches[:a.top]:
        if x.verdict == "NORMAL" and not a.all:
            continue
        shown += 1
        flag = "  [own account]" if x.self_match else ""
        run = f"  run={x.longest}t" if x.longest >= 60 else ""
        ast = f"  shape={x.ast_frac*100:.0f}%" if x.ast_shared else ""
        sig = f"  types={x.sig_frac*100:.0f}%" if x.sig_frac >= 0.05 else ""
        print(f"  {x.verdict:7s} {x.containment*100:6.2f}%  {x.z:+6.1f}σ"
              f"{run}{ast}{sig}  {x.repo}{flag}")
    if not shown:
        print("  nothing above the normal range — no match worth reviewing.")

    out = a.out or os.path.join("reports", name + ".html")
    report_mod.render(summary, matches, s, out)
    print(f"\nreport: {out}  ({time.time()-t0:.1f}s)")


def cmd_projects(a):
    names = spec_mod.available()
    if not names:
        _fail("no project patterns in projects/")
    for n in names:
        s = spec_mod.load(n)
        man = fetch.load_manifest(s)
        idx = os.path.join(s.corpus_dir, index_mod.INDEX)
        ready = "calibrated" if os.path.isfile(idx) else "no corpus — run ./pan setup"
        print(f"  {n:12s} {s.label}")
        print(f"  {'':12s} {len(man['accepted']):,} verified repos · {ready}")


def cmd_status(a):
    s = spec_mod.load(a.project)
    st = pipeline.status(s)
    print(f"{st['project']}: {st['verified']:,} verified, {st['rejected']:,} rejected")
    print(f"stages done: {', '.join(st['stages']) or 'none'}")
    if "index" in st:
        m = st["index"]
        print(f"index: {m['distinct_prints']:,} fingerprints, k={m['k']}, "
              f"{m['representatives']:,} independent of {m['repos']:,}")
        if "top_null_mean" in m:
            print(f"calibration: honest best-match {m['top_null_mean']*100:.2f}%, "
                  f"p99 {m['top_null_p99']*100:.2f}%, "
                  f"evidence threshold {int(m.get('run_null_threshold',0))} tokens")
    else:
        print("index: not built — run ./pan setup " + s.name)


def cmd_discover(a):
    s = spec_mod.load(a.project)
    pipeline.run(s, stages=["discover"], force=True, max_repos=a.max)


def cmd_fetch(a):
    s = spec_mod.load(a.project)
    pipeline.run(s, stages=["fetch"], jobs=a.jobs,
                 progress=lambda i, n, *r: _bar(i, n, r[0] if r else ""))


def cmd_recover(a):
    s = spec_mod.load(a.project)
    pipeline.run(s, stages=["recover"],
                 progress=lambda i, n, *r: _bar(i, n, r[0] if r else ""))


def cmd_index(a):
    s = spec_mod.load(a.project)
    pipeline.run(s, stages=["index", "calibrate"], force=True,
                 progress=lambda i, n, *r: _bar(i, n, r[0] if r else ""))


def cmd_token(a):
    path = discover.TOKEN_FILES[0]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    src = discover.token_source()
    if src and not a.force:
        print(f"token already configured: {src}")
        print("pass --force to replace it")
        return
    import getpass
    tok = getpass.getpass("GitHub token (input hidden): ").strip()
    if not tok:
        _fail("no token entered")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok)
    print(f"saved to {path} (mode 600)")
    gh = discover.GitHub(verbose=False)
    r = gh.s.get("https://api.github.com/rate_limit", timeout=20)
    if r.status_code == 200:
        d = r.json()["resources"]
        print(f"verified: {d['core']['limit']}/hr core, {d['search']['limit']}/min search")
    else:
        print(f"warning: token did not authenticate (HTTP {r.status_code})")


# -------------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(
        prog="pan", description="Panoptes — find copied C++ submissions in 42 projects.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  ./pan token                             store a GitHub token
  ./pan setup ft_irc                      build + calibrate the corpus (once)
  ./pan check ./student -p ft_irc --student their_handle
  ./pan status ft_irc                     show corpus state
""")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("token", help="store a GitHub token (prompts, never echoes)")
    t.add_argument("--force", action="store_true")
    t.set_defaults(fn=cmd_token)

    s_ = sub.add_parser("setup", help="build and calibrate a project corpus end to end")
    s_.add_argument("project")
    s_.add_argument("--jobs", type=int, default=10)
    s_.add_argument("--max", type=int, help="cap candidates (for a quick trial run)")
    s_.add_argument("--recover", action="store_true",
                    help="also try Software Heritage for deleted repos (slow)")
    s_.add_argument("--force", action="store_true", help="redo completed stages")
    s_.set_defaults(fn=cmd_setup)

    c = sub.add_parser("check", help="compare one submission against the corpus")
    c.add_argument("submission")
    c.add_argument("-p", "--project", required=True)
    c.add_argument("--student", action="append", metavar="HANDLE",
                   help="student's GitHub handle (repeatable) — suppresses self-matches")
    c.add_argument("--top", type=int, default=25)
    c.add_argument("--out", help="report path (default reports/<name>.html)")
    c.add_argument("--all", action="store_true", help="also list NORMAL matches")
    c.add_argument("--no-evidence", action="store_true", help="scores only, faster")
    c.set_defaults(fn=cmd_check)

    sub.add_parser("projects", help="list available project patterns").set_defaults(fn=cmd_projects)

    st = sub.add_parser("status", help="show corpus and calibration state")
    st.add_argument("project"); st.set_defaults(fn=cmd_status)

    for name, fn, helptext in (
            ("discover", cmd_discover, "stage: find candidate repos"),
            ("fetch", cmd_fetch, "stage: clone and verify candidates"),
            ("recover", cmd_recover, "stage: Software Heritage recovery"),
            ("index", cmd_index, "stage: rebuild index and recalibrate")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("project")
        sp.add_argument("--jobs", type=int, default=10)
        sp.add_argument("--max", type=int)
        sp.set_defaults(fn=fn)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted — progress is saved, rerun to resume\n")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
