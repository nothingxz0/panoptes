<div align="center">

<img src="watchman.jpg" alt="Argus Panoptes, the hundred-eyed watchman" width="720">

# Panoptes

**Finds copied C++ submissions in 42 projects.**

<em>Argus Panoptes kept a hundred eyes and slept with only a few closed at a time,<br>
so nothing passed him unseen — until someone patient enough lulled every eye shut.</em>

</div>

---

You point it at one student's folder. It compares that folder against every
public repository of the same project it can find — thousands of them — and
produces an HTML report ranking the closest matches with side-by-side code.

```
$ ./pan check ./student_submission -p ft_irc --student their_handle

student_submission: 21 files, 2,267 lines
project check: OK — 9/9 required tokens; Makefile NAME=ircserv
corpus: 3,002 repos | an honest submission's best match averages 4.5% (p99 13.4%)

  HIGH    47.20%   +15.6σ  run=1830t  shape=61%  types=68%  someuser/ft_irc
  LOW      9.81%    +1.9σ  run=310t                         other/ircserv

report: reports/student_submission.html
```

---

## Install

Needs Python 3.9+ and `git`.

```bash
pip install -r requirements.txt
```

`tree-sitter` and `tree-sitter-cpp` power two of the three detection channels.
Without them Panoptes still runs on token matching alone, with reduced recall.

---

## 1. Add a GitHub token

Building a corpus means thousands of API calls and clones. Without a token
GitHub allows 10 searches/minute and throttles anonymous clones hard enough that
they start failing. With one you get 30/min and 5,000 clones/hour.

**Create the token:**

1. Go to <https://github.com/settings/tokens/new>
2. **Note:** `panoptes`
3. **Expiration:** 90 days
4. **Scopes: leave every box unchecked**
5. **Generate token** and copy the `ghp_...` string

Zero scopes is correct and deliberate. The rate limits come from being
authenticated, not from permissions — Panoptes only ever reads public data and
runs `git clone`. A token with no scopes can do nothing else if it leaks.

**Store it:**

```bash
./pan token
```

Input is hidden and never echoed. It is written to
`~/.config/panoptes/token` with mode 600 and verified immediately.

Panoptes also accepts `$GITHUB_TOKEN`, or a `.token` file in the project root.

---

## 2. Build the corpus

```bash
./pan setup ft_irc
```

One command, five stages, fully resumable — press Ctrl-C and rerun to continue:

| stage | what it does |
|---|---|
| **discover** | the GH Archive event log first — one query returns every repository that ever existed, including renamed and deleted ones — then GitHub search for whatever its name patterns miss. Add `--deep` to also month-slice search; the event log already covers most of what that adds |
| **fetch** | clones each candidate, verifies it really is the project, deletes the ones that aren't |
| **recover** | optional (`--recover`): tries Software Heritage for repos GitHub no longer serves |
| **index** | fingerprints everything, collapses duplicate clusters |
| **calibrate** | measures what "normal" looks like for this project |

Expect **30–60 minutes** and **~4 GB** for ft_irc, most of it cloning. It only
has to be done once; rerun it monthly to pick up new repos.

Discovery itself is fast — around three minutes for 14,000 candidates, because
the event log answers in seconds and month-slicing search is off by default.

Useful flags:

```bash
./pan setup ft_irc --max 200      # quick trial run on 200 candidates
./pan setup ft_irc --recover      # include Software Heritage (slow, ~10% yield)
./pan setup ft_irc --force        # redo completed stages
./pan status ft_irc               # where things stand
```

---

## 3. Check a submission

```bash
./pan check ./path/to/submission -p ft_irc --student their_github_handle
```

Takes a few seconds. Writes `reports/<name>.html`.

**Always pass `--student`.** Many 42 students publish their own work. Without
it, their own repository is found and reported as a ~100% match — the single
most dangerous false positive this tool can produce. Pass the flag more than
once for group projects:

```bash
./pan check ./submission -p ft_irc --student alice --student bob
```

Other flags: `--all` (show normal matches too), `--top N`, `--out PATH`,
`--no-evidence` (scores only, faster).

---

## Reading the output

Similarity is reported **relative to the baseline for that project**, never as a
raw percentage. This matters: 42 subjects mandate class names, method signatures
and Orthodox Canonical Form, so some overlap between honest students is
unavoidable. For ft_irc, an honest submission's *single best match* out of 3,000
repos averages **4.5%** and reaches 13.4% at the 99th percentile. A raw "12%
similar" is therefore completely normal, and a tool reporting it as alarming is
wrong.

The `σ` column is the number that carries meaning.

| verdict | meaning |
|---|---|
| `HIGH` | far outside anything honest work produces — review immediately |
| `MEDIUM` | outside the normal range — review |
| `LOW` | slightly elevated; usually nothing |
| `NORMAL` | indistinguishable from independent work |
| `SELF` | matched a repo on the student's own account |

Extra columns appear when a channel has something to say: `run=1830t` is the
longest identical run of tokens, `shape=61%` the share of functions with an
identical control-flow skeleton, `types=68%` the share with an identical type
signature. Agreement across channels is what makes a case, not any single number.

---

## How it works

Every project is described by one pattern file in `projects/`. It says how to
**find** that project's repositories and how to **verify** a downloaded one
really is that project. Nothing about ft_irc is hardcoded anywhere else.

Comparison runs three independent channels:

**Token k-grams.** Source is reduced to a token stream with every identifier
replaced by `ID` and every literal by `LIT`, so `int clientCount = 0;` and
`int nb_users = 0;` become identical. Every 25-token window is hashed, and
winnowing keeps the smallest hash per group of 8 — enough to guarantee any
shared run of 32+ tokens is caught, at a quarter of the storage.

**Function shape.** tree-sitter parses each function and reduces it to its
control-flow skeleton — the `if`/`for`/`return` structure with all names, types
and literals stripped — then hashes it. Catches restructuring that breaks token
matching.

**Type signatures.** Each function is also reduced to the types it touches:
return type, parameter types in order, and the multiset of local variable types.
Catches a function reworded *and* restructured but still moving the same data.

Measured over 600 honest independent pairs and 60 known-copy pairs:

| channel | honest mean | known copies | separation | recall |
|---|---|---|---|---|
| AST skeletons | 0.18% | 80.7% | 459× | 96.7% |
| type signatures | 1.84% | 88.9% | 48× | 100% |

Type signatures are noisier so they carry a higher threshold, but they catch
what the skeleton channel misses. Neither is reliable alone.

**Every channel is calibrated per project.** Fixed thresholds do not transfer:

| project | token best-match p99 | shape p99 | type-signature p99 |
|---|---|---|---|
| ft_irc | 13.4% | 4.1% | 10.8% |
| cpp06 | 14.4% | 16.7% | 50.0% |

Honest CPP module pairs share half their type signatures, because Orthodox
Canonical Form is mandated on every class. A threshold meaningful for ft_irc
fires on completely normal work here, so `pan calibrate` measures all three
channels against the project's own corpus. A channel is also ignored entirely
when fewer than eight functions are being compared — two shared out of four
reads as 50% and is not evidence.

Both tolerate code that does not compile — much of the public corpus doesn't.

---

## Projects

```
ft_irc            IRC server        whole repository
cpp00 .. cpp09    C++ modules       a directory inside a repository
```

### Whole projects vs sub-projects

`ft_irc` is a repository. A CPP module is not — students publish one repository
holding every module, and the directory naming is wildly inconsistent:

```
cpp06/  CPP06/  cpp_06/  CPP_Module_06/  CPP 06/  C06/  06/  day-06/  module6/
```

...and sometimes the repository *is* the module, with `ex00/` at its root.

So a pattern file may declare a `subproject` block. Panoptes then finds
candidate directories by path pattern and **confirms each by content** — the
directory must contain the classes the subject mandates for that module, and
must not contain another module's classes. Measured against 162 real
repositories, this locates the right directory with **97.1% recall and almost no
false positives**.

Fetching exploits the same information. A blobless clone (`--filter=blob:none`)
downloads the commit and tree objects but no file contents, so the full path
listing is available for a fraction of the transfer; the module is identified
from that listing and only its files are then materialised via sparse checkout.
For a repository holding ten modules this transfers roughly a tenth of the data.

### Adding a new project

Copy the closest existing pattern file, change the values, run setup. No code
changes.

```yaml
name: webserv
discovery:
  queries: ["webserv", "webserv 42", "webserv language:C++"]
  topics: [webserv, 42cursus, 42school]
  name_patterns: ['(?i)web[-_]?serv']
  archive_substrings: [webserv, web_serv]
verification:
  min_source_files: 3
  min_total_lines: 800
  makefile_name: [webserv]
  required_tokens:
    threshold: 5
    tokens: ["HTTP/1.1", Content-Length, chunked, CGI, autoindex]
  reject_tokens: [express, django]
```

The verifier is deliberately multi-signal. Repository names lie, Makefiles use
variables (`NAME = $(TARGET)`), and many repos are abandoned stubs.

---

## Limits — read before acting on a report

- **Panoptes produces evidence, not verdicts.** A high score is a reason to read
  the code, not a finding of plagiarism.
- **Detection rate against deliberately obfuscated copies is unmeasured.** It
  handles renaming and restructuring well, and catches near-identical copies
  reliably. Nobody has yet tested it against a student actively trying to evade
  it.
- **Matching does not establish direction.** The public repo may be the
  student's own, a fork, or both may derive from a common third source. Check
  dates and authorship before concluding anything.
- **Group projects legitimately share code.** ft_irc is done in teams, so of
  3,002 public repos only 1,966 are independent — the rest are teammates pushing
  the same work. Panoptes clusters and collapses these, but the same is true of
  submissions you check.
- **The corpus is not exhaustive.** Roughly 2,800 ft_irc repos exist publicly;
  many students never publish, or delete their work after graduating.

## Commands

```
./pan token                     store a GitHub token
./pan setup <project>           build + calibrate the corpus
./pan check <dir> -p <project>  compare a submission
./pan status <project>          corpus and calibration state
./pan projects                  list project patterns

./pan discover|fetch|recover|index <project>    run one stage
```
