"""Find candidate repos on GitHub using a project's discovery pattern.

GitHub caps any single search at 1000 results, so queries are sliced by
creation date to reach everything. Unauthenticated search allows 10 req/min;
set GITHUB_TOKEN to get 30 req/min and much higher core limits.
"""
import os, sys, time, json, datetime as dt
import requests

API = "https://api.github.com"
PER_PAGE = 100
RESULT_CAP = 1000          # GitHub's hard per-query limit


TOKEN_FILES = [
    os.path.expanduser("~/.config/panoptes/token"),
    os.path.expanduser("~/.config/code_cmp/token"),   # previous location
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".token"),
]


def read_token(explicit=None):
    """Token from argument, env, or a file. A file keeps it out of shell history."""
    if explicit:
        return explicit.strip()
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    for path in TOKEN_FILES:
        if os.path.isfile(path):
            with open(path) as fh:
                tok = fh.read().strip()
            if tok:
                return tok
    return None


def token_source(explicit=None):
    if explicit:
        return "argument"
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return f"${var}"
    for path in TOKEN_FILES:
        if os.path.isfile(path) and open(path).read().strip():
            return path
    return None


class GitHub:
    def __init__(self, token=None, verbose=True):
        self.token = read_token(token)
        self.verbose = verbose
        self.s = requests.Session()
        self.s.headers.update({
            "Accept": "application/vnd.github+json",
            "User-Agent": "code_cmp/0.1 (42 academic integrity tooling)",
        })
        if self.token:
            self.s.headers["Authorization"] = f"Bearer {self.token}"

    def log(self, *a):
        if self.verbose:
            print(*a, file=sys.stderr, flush=True)

    def search_repos(self, q, page=1):
        """One page of repo search, respecting rate limits and retrying."""
        for attempt in range(6):
            r = self.s.get(f"{API}/search/repositories",
                           params={"q": q, "per_page": PER_PAGE, "page": page,
                                   "sort": "updated", "order": "desc"},
                           timeout=30)
            if r.status_code == 200:
                remaining = int(r.headers.get("X-RateLimit-Remaining", 1))
                if remaining <= 1:
                    reset = int(r.headers.get("X-RateLimit-Reset", 0))
                    wait = max(0, reset - int(time.time())) + 2
                    self.log(f"    rate limit reached, sleeping {wait}s")
                    time.sleep(wait)
                return r.json()
            if r.status_code in (403, 429):
                reset = int(r.headers.get("X-RateLimit-Reset", 0))
                wait = max(5, min(90, reset - int(time.time()) + 2))
                self.log(f"    throttled ({r.status_code}), sleeping {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 422:      # page beyond the 1000 cap
                return {"items": [], "total_count": 0}
            self.log(f"    HTTP {r.status_code}, retrying")
            time.sleep(3)
        return {"items": [], "total_count": 0}


def _month_slices(start="2015-01", end=None):
    """Yield created:YYYY-MM-DD..YYYY-MM-DD ranges to break the 1000 cap."""
    y, m = (int(x) for x in start.split("-"))
    today = dt.date.today()
    end_y, end_m = (today.year, today.month) if not end else (int(end[:4]), int(end[5:7]))
    while (y, m) <= (end_y, end_m):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        last = dt.date(ny, nm, 1) - dt.timedelta(days=1)
        yield f"{y:04d}-{m:02d}-01..{last.isoformat()}"
        y, m = ny, nm


def _keep(item, spec):
    d = spec.discovery
    if d.get("skip_forks", True) and item.get("fork"):
        return False
    band = d.get("size_kb", {})
    size = item.get("size", 0)
    if band and not (band.get("min", 0) <= size <= band.get("max", 10**9)):
        return False
    if item.get("archived") and item.get("size", 0) == 0:
        return False
    return True


def discover(spec, max_repos=None, slice_by_month=True, token=None, verbose=True):
    """Return a dict of full_name -> candidate metadata."""
    gh = GitHub(token, verbose)
    queries = list(spec.discovery.get("queries", []))
    for topic in spec.discovery.get("topics", []):
        queries.append(f"topic:{topic} language:C++")

    found = {}
    for q in queries:
        if max_repos and len(found) >= max_repos:
            break
        gh.log(f"  query: {q}")
        head = gh.search_repos(q, page=1)
        total = head.get("total_count", 0)
        gh.log(f"    {total} total results")

        # Under the cap: page straight through. Over it: slice by month.
        if total <= RESULT_CAP or not slice_by_month:
            pages = [(q, p) for p in range(1, min(total // PER_PAGE + 2, 11))]
        else:
            pages = [(f"{q} created:{sl}", 1) for sl in _month_slices()]
            gh.log(f"    over 1000 cap, slicing into {len(pages)} monthly windows")

        for sub_q, page in pages:
            if max_repos and len(found) >= max_repos:
                break
            data = head if (sub_q == q and page == 1) else gh.search_repos(sub_q, page)
            items = data.get("items", [])
            # A sliced window may itself have several pages.
            if sub_q != q and data.get("total_count", 0) > PER_PAGE:
                for extra in range(2, min(data["total_count"] // PER_PAGE + 2, 11)):
                    items += gh.search_repos(sub_q, extra).get("items", [])
            for it in items:
                if it["full_name"] in found or not _keep(it, spec):
                    continue
                found[it["full_name"]] = {
                    "full_name": it["full_name"],
                    "clone_url": it["clone_url"],
                    "size": it["size"],
                    "stars": it["stargazers_count"],
                    "pushed_at": it.get("pushed_at", "")[:10],
                    "owner": it["owner"]["login"],
                    "fork": it.get("fork", False),
                    "found_by": sub_q,
                }
            gh.log(f"    running total: {len(found)} candidates")
    return found


def save(found, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(found, fh, indent=1, sort_keys=True)
    return path


def expand_by_users(spec, seed_names, max_users=None, verbose=True, token=None):
    """Find more repos through the collaborator graph.

    ft_irc is a group project, so every verified repo names 2-3 students. Those
    students' own accounts, and the accounts of everyone they worked with, tend
    to hold the same project — often under a name that keyword search misses
    ('IRC_Server', 'projet_irc', or buried in a 42cursus mega-repo).

    This reaches repos that no search query would surface.
    """
    gh = GitHub(token, verbose)
    users, found = set(), {}

    for i, name in enumerate(seed_names, 1):
        if max_users and len(users) >= max_users:
            break
        r = gh.s.get(f"{API}/repos/{name}/contributors",
                     params={"per_page": 20}, timeout=30)
        if r.status_code == 200:
            for c in r.json():
                if c.get("type") == "User":
                    users.add(c["login"])
        users.add(name.split("/")[0])
        if verbose and i % 50 == 0:
            gh.log(f"    {i}/{len(seed_names)} repos scanned, {len(users)} users")
        if int(r.headers.get("X-RateLimit-Remaining", 100)) < 20:
            wait = max(0, int(r.headers.get("X-RateLimit-Reset", 0)) - int(time.time())) + 2
            gh.log(f"    core limit low, sleeping {wait}s")
            time.sleep(wait)

    users = sorted(users)[:max_users] if max_users else sorted(users)
    gh.log(f"  enumerating repos of {len(users)} users")

    for i, u in enumerate(users, 1):
        page = 1
        while True:
            r = gh.s.get(f"{API}/users/{u}/repos",
                         params={"per_page": 100, "page": page, "type": "owner"},
                         timeout=30)
            if r.status_code != 200:
                break
            items = r.json()
            for it in items:
                short = it["name"]
                if not spec.name_matches(short):
                    continue
                if it["full_name"] in found or not _keep(it, spec):
                    continue
                found[it["full_name"]] = {
                    "full_name": it["full_name"], "clone_url": it["clone_url"],
                    "size": it["size"], "stars": it["stargazers_count"],
                    "pushed_at": it.get("pushed_at", "")[:10],
                    "owner": it["owner"]["login"], "fork": it.get("fork", False),
                    "found_by": f"user-graph:{u}",
                }
            if len(items) < 100:
                break
            page += 1
        if verbose and i % 100 == 0:
            gh.log(f"    {i}/{len(users)} users, {len(found)} new candidates")
        if int(r.headers.get("X-RateLimit-Remaining", 100)) < 20:
            wait = max(0, int(r.headers.get("X-RateLimit-Reset", 0)) - int(time.time())) + 2
            gh.log(f"    core limit low, sleeping {wait}s")
            time.sleep(wait)
    return found
