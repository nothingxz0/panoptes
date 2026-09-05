"""Loading of per-project pattern files (projects/*.yaml)."""
import os, re, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_DIR = os.path.join(ROOT, "projects")
CORPUS_DIR = os.path.join(ROOT, "corpus")


class Spec:
    def __init__(self, data, path):
        self.path = path
        self.raw = data
        self.name = data["name"]
        self.label = data.get("label", self.name)
        self.discovery = data.get("discovery", {})
        self.verification = data.get("verification", {})
        self.source = data.get("source", {})
        self.compare = data.get("compare", {})
        # Present when the project is a directory inside a larger repository
        # (every CPP module) rather than a whole repository (ft_irc).
        self.subproject = data.get("subproject")
        self.name_res = [re.compile(p) for p in self.discovery.get("name_patterns", [])]

    @property
    def is_subproject(self):
        return bool(self.subproject)

    @property
    def corpus_dir(self):
        return os.path.join(CORPUS_DIR, self.name)

    @property
    def extensions(self):
        return tuple(self.source.get("extensions", [".cpp", ".hpp", ".h"]))

    @property
    def exclude_dirs(self):
        return set(self.source.get("exclude_dirs", [".git"]))

    def name_matches(self, repo_name):
        return any(r.search(repo_name) for r in self.name_res)

    def __repr__(self):
        return f"<Spec {self.name}>"


def load(name):
    path = name if os.path.isfile(name) else os.path.join(PROJECTS_DIR, f"{name}.yaml")
    if not os.path.isfile(path):
        raise SystemExit(f"no pattern file for project {name!r} (looked at {path})")
    with open(path) as fh:
        return Spec(yaml.safe_load(fh), path)


def available():
    if not os.path.isdir(PROJECTS_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(PROJECTS_DIR) if f.endswith(".yaml"))
