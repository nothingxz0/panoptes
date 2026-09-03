"""HTML evidence report. Output is evidence for a human, never a verdict."""
import os, html, datetime

CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e2e2e2;--card:#fafafa;
--hi:#c0392b;--med:#d68910;--low:#7d8c1e;--ok:#2e7d5b;--self:#5b6dcd;--add:#eef7ff}
*{box-sizing:border-box}
body{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
background:var(--bg);color:var(--fg)}
.wrap{max-width:1180px;margin:0 auto;padding:32px 24px 80px}
h1{font-size:24px;margin:0 0 4px} h2{font-size:17px;margin:36px 0 12px}
.sub{color:var(--mut);font-size:13px;margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0 8px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.stat b{display:block;font-size:20px;font-variant-numeric:tabular-nums}
.stat span{color:var(--mut);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.04em;
color:var(--mut);border-bottom:1px solid var(--line);padding:8px 10px;font-weight:600}
td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;
font-weight:700;letter-spacing:.03em;color:#fff}
.HIGH{background:var(--hi)}.MEDIUM{background:var(--med)}.LOW{background:var(--low)}
.NORMAL{background:#aaa}.SELF{background:var(--self)}
a{color:#0645ad;text-decoration:none}a:hover{text-decoration:underline}
.ev{border:1px solid var(--line);border-radius:8px;margin:14px 0;overflow:hidden}
.ev>summary{cursor:pointer;padding:11px 14px;background:var(--card);font-size:14px;
font-weight:600;list-style:none}
.ev>summary::-webkit-details-marker{display:none}
.ev>summary:before{content:"\\25B8 ";color:var(--mut)}
.ev[open]>summary:before{content:"\\25BE "}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:0;border-top:1px solid var(--line)}
.pane{overflow-x:auto;padding:0 0 8px}
.pane h4{margin:0;padding:8px 12px;font-size:12px;font-weight:600;color:var(--mut);
background:var(--card);border-bottom:1px solid var(--line);position:sticky;left:0}
.pane:first-child{border-right:1px solid var(--line)}
pre{margin:0;padding:8px 12px;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
white-space:pre}
.ln{color:#bbb;user-select:none;display:inline-block;width:44px;text-align:right;
padding-right:12px}
.mark{background:var(--add)}
.note{background:#fffbe6;border:1px solid #f0e0a0;border-radius:8px;padding:12px 14px;
font-size:13px;margin:24px 0}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);
color:var(--mut);font-size:12px}
@media(max-width:820px){.pair{grid-template-columns:1fr}.pane:first-child{border-right:0}}
"""


def _snippet(path, start, end, pad=3):
    try:
        with open(path, errors="ignore") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return "<pre>(source unavailable)</pre>"
    lo, hi = max(0, start - 1 - pad), min(len(lines), end + pad)
    out = []
    for i in range(lo, hi):
        cls = ' class="mark"' if start - 1 <= i <= end - 1 else ""
        out.append(f'<span class="ln">{i+1}</span><span{cls}>'
                   f'{html.escape(lines[i])}</span>')
    return "<pre>" + "\n".join(out) + "</pre>"


def render(summary, matches, spec, out_path):
    m = summary["meta"]
    sub = summary["submission"]
    hi = [x for x in matches if x.verdict == "HIGH" and not x.self_match]
    med = [x for x in matches if x.verdict == "MEDIUM"]
    p = []
    p.append(f"<!doctype html><meta charset=utf-8><title>code_cmp — "
             f"{html.escape(os.path.basename(sub))}</title><style>{CSS}</style>")
    p.append('<div class="wrap">')
    p.append(f"<h1>{html.escape(os.path.basename(sub))}</h1>")
    p.append(f'<div class="sub">{html.escape(spec.label)} &middot; compared against '
             f'{m["repos"]} verified public repositories &middot; '
             f'{datetime.datetime.now():%Y-%m-%d %H:%M}</div>')

    top = matches[0].containment * 100 if matches else 0
    topz = matches[0].z if matches else 0
    p.append('<div class="grid">')
    for label, val in (("top match", f"{top:.1f}%"),
                       ("deviation", f"{topz:+.1f}σ"),
                       ("corpus baseline", f"{m['baseline_mean']*100:.2f}%"),
                       ("honest ceiling", f"{m['baseline_max']*100:.1f}%"),
                       ("source files", summary["files"]),
                       ("lines", f"{summary['lines']:,}")):
        p.append(f'<div class="stat"><b>{val}</b><span>{label}</span></div>')
    p.append("</div>")

    if hi:
        p.append(f'<div class="note"><b>{len(hi)} high-similarity match'
                 f'{"es" if len(hi)>1 else ""}.</b> The corpus baseline for honest, '
                 f'independent work on this project is {m["baseline_mean"]*100:.2f}% '
                 f'(never above {m["baseline_max"]*100:.1f}% across '
                 f'{m["representatives"]} independent repos). Review the evidence '
                 f'below before drawing any conclusion.</div>')

    p.append("<h2>Ranked matches</h2><table><tr><th>Verdict</th><th>Repository</th>"
             "<th class=num>Token overlap</th><th class=num>vs baseline</th>"
             "<th class=num>Same shape</th><th class=num>Same types</th>"
             "<th class=num>Shared</th></tr>")
    for x in matches:
        p.append(f'<tr><td><span class="tag {x.verdict}">{x.verdict}</span></td>'
                 f'<td><a href="{html.escape(x.url)}" target=_blank rel=noopener>'
                 f'{html.escape(x.repo)}</a>'
                 f'{" <em>(student’s own account)</em>" if x.self_match else ""}</td>'
                 f'<td class=num>{x.containment*100:.2f}%</td>'
                 f'<td class=num>{x.z:+.1f}σ</td>'
                 f'<td class=num>{format(x.ast_frac*100, ".0f") + "%" if x.ast_shared else "—"}</td>'
                 f'<td class=num>{format(x.sig_frac*100, ".0f") + "%" if x.sig_frac >= 0.05 else "—"}</td>'
                 f'<td class=num>{x.shared}</td></tr>')
    p.append("</table>")

    shown = [x for x in matches if x.regions and not x.self_match][:8]
    if shown:
        p.append("<h2>Evidence — longest identical regions</h2>")
        p.append('<div class="sub">Identifiers are normalised before matching, so '
                 'renamed variables still match. Line numbers refer to the original '
                 'files.</div>')
    for x in shown:
        d = os.path.join(spec.corpus_dir,
                         x.repo.replace("/", "~"))
        p.append(f'<details class="ev"><summary>{html.escape(x.repo)} — '
                 f'{x.containment*100:.2f}% ({len(x.regions)} region'
                 f'{"s" if len(x.regions)!=1 else ""})</summary>')
        if x.ast_rows:
            p.append('<div style="padding:10px 14px;font-size:13px;'
                     'border-top:1px solid var(--line)"><b>'
                     f'{x.ast_shared} function{"s" if x.ast_shared!=1 else ""} '
                     'with an identical control-flow skeleton</b> '
                     '<span style="color:var(--mut)">(same shape after removing all '
                     'names and literals)</span><br>')
            for r in x.ast_rows[:6]:
                p.append(f'<code style="font-size:12px">{html.escape(r["sub"]["name"])}'
                         f'</code> &rarr; <code style="font-size:12px">'
                         f'{html.escape(r["corpus"]["name"])}</code> '
                         f'<span style="color:var(--mut)">'
                         f'({html.escape(r["corpus"]["file"])}:{r["corpus"]["start"]}, '
                         f'{r["size"]} nodes)</span><br>')
            p.append("</div>")
        for r in x.regions:
            p.append('<div class="pair">')
            p.append(f'<div class="pane"><h4>submission &middot; '
                     f'{html.escape(r["sub_file"])}:{r["sub_start"]}-{r["sub_end"]}</h4>'
                     f'{_snippet(os.path.join(sub, r["sub_file"]), r["sub_start"], r["sub_end"])}</div>')
            p.append(f'<div class="pane"><h4>{html.escape(x.repo)} &middot; '
                     f'{html.escape(r["corpus_file"])}:{r["corpus_start"]}-{r["corpus_end"]}</h4>'
                     f'{_snippet(os.path.join(d, r["corpus_file"]), r["corpus_start"], r["corpus_end"])}</div>')
            p.append("</div>")
        p.append("</details>")

    p.append(f'<footer>code_cmp &middot; corpus: {m["repos"]} repos, '
             f'{m["representatives"]} independent representatives, '
             f'{m["clusters"]} clusters &middot; k={m["k"]}, w={m["w"]} &middot; '
             f'{m["boilerplate_prints"]:,} fingerprints suppressed as subject '
             f'boilerplate.<br>This report is evidence for human review. It does not '
             f'establish plagiarism on its own.</footer></div>')

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write("\n".join(p))
    return out_path
