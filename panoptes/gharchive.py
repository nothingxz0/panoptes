"""Discovery via the GH Archive event log (ClickHouse public endpoint).

GitHub's search API only indexes repositories that exist *right now*. GH Archive
logs every public event since 2011, so it also surfaces repos that were renamed,
made private, or deleted — which is exactly where a copied-then-hidden source
repo ends up. No credentials required.

Queried through ClickHouse's public playground rather than BigQuery, which needs
a billed Google Cloud project.
"""
import requests

ENDPOINT = "https://play.clickhouse.com/?user=play"
TIMEOUT = 280


def query(sql, timeout=TIMEOUT):
    r = requests.post(ENDPOINT, data=sql.encode(), timeout=timeout)
    r.raise_for_status()
    if r.text.startswith("Code:"):
        raise RuntimeError(r.text[:300])
    return [line.split("\t") for line in r.text.strip().split("\n") if line]


def _conditions(spec):
    """Build a fast substring filter from the project's discovery patterns."""
    subs = spec.discovery.get("archive_substrings")
    if not subs:
        # Fall back to literal fragments of the name patterns.
        subs = [spec.name]
    return " OR ".join(
        f"positionCaseInsensitive(repo_name, '{s}') > 0" for s in subs)


def find_repos(spec, min_events=1, verbose=True):
    """Return {full_name: metadata} for every repo GH Archive has ever seen."""
    sql = f"""
    SELECT repo_name,
           count() AS events,
           min(created_at) AS first_seen,
           max(created_at) AS last_seen
    FROM github_events
    WHERE {_conditions(spec)}
    GROUP BY repo_name
    HAVING events >= {min_events}
    ORDER BY events DESC
    FORMAT TSV
    """
    rows = query(sql)
    out = {}
    for name, events, first, last in rows:
        if "/" not in name:
            continue
        out[name] = {
            "full_name": name,
            "clone_url": f"https://github.com/{name}.git",
            "size": 0,               # unknown until cloned
            "stars": 0,
            "pushed_at": last[:10],
            "owner": name.split("/")[0],
            "fork": False,
            "found_by": "gharchive",
            "archive_events": int(events),
            "first_seen": first[:10],
        }
    if verbose:
        print(f"  GH Archive: {len(out)} distinct repos ever seen")
    return out
