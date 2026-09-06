"""Per-language tokenisation.

The C++ projects are normalised by erasing identifiers: renaming a variable
must not hide a copy, and the structure is what carries the signal.

Infrastructure projects invert that. An inception submission is Dockerfiles,
shell scripts and a compose file whose *structure* is dictated by the subject —
every student declares the same three services, the same networks, the same
volumes. What actually varies is content: which base image, which packages, in
which order, with which flags. So here the content words are kept and only the
things a student may freely rename (shell variables, numeric literals) are
abstracted. Erasing identifiers in a Dockerfile would erase the evidence.
"""
import re

# ------------------------------------------------------------------ detection
_EXT = {
    ".cpp": "cpp", ".hpp": "cpp", ".h": "cpp", ".cc": "cpp", ".hh": "cpp",
    ".cxx": "cpp", ".tpp": "cpp", ".ipp": "cpp",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".yml": "yaml", ".yaml": "yaml",
    ".conf": "conf", ".cnf": "conf", ".ini": "conf", ".cfg": "conf",
    ".template": "conf", ".env": "conf", ".properties": "conf",
    ".sql": "conf", ".toml": "conf",
}
_NAME = {
    "dockerfile": "dockerfile", "containerfile": "dockerfile",
    "makefile": "make", "gnumakefile": "make",
    "docker-compose.yml": "yaml", "docker-compose.yaml": "yaml",
    ".env": "conf", "nginx.conf": "conf",
}


def language_of(path):
    """Language for a path, or None if it carries no comparable content."""
    import os
    base = os.path.basename(path).lower()
    if base in _NAME:
        return _NAME[base]
    # Dockerfile.nginx, nginx.Dockerfile, Dockerfile-wp
    if base.startswith("dockerfile") or base.endswith(".dockerfile"):
        return "dockerfile"
    if base.startswith("makefile"):
        return "make"
    for ext, lang in _EXT.items():
        if base.endswith(ext):
            return lang
    return None


# ------------------------------------------------------------------- keywords
CPP_KEYWORDS = set("""
alignas alignof and and_eq asm auto bitand bitor bool break case catch char char16_t
char32_t class compl const constexpr const_cast continue decltype default delete do
double dynamic_cast else enum explicit export extern false float for friend goto if
inline int long mutable namespace new noexcept not not_eq nullptr operator or or_eq
private protected public register reinterpret_cast return short signed sizeof static
static_assert static_cast struct switch template this throw true try typedef typeid
typename union unsigned using virtual void volatile wchar_t while xor xor_eq
""".split())

CPP_LIBRARY = set("""
std string vector map set list deque queue stack pair size_t ostream istream
sstream stringstream fstream ifstream ofstream iterator const_iterator exception
socket bind listen accept connect send recv poll epoll epoll_wait epoll_ctl kqueue
select fcntl setsockopt getaddrinfo inet_ntoa htons ntohs close read write signal
sockaddr_in pollfd memset strerror errno atoi strtol sprintf snprintf
""".split())

DOCKER_INSTR = set("""
from run cmd label maintainer expose env add copy entrypoint volume user
workdir arg onbuild stopsignal healthcheck shell as
""".split())

SHELL_KEYWORDS = set("""
if then else elif fi for while until do done case esac function return exit
local export readonly declare set unset shift source eval exec trap break continue
in select time coproc
""".split())

# ------------------------------------------------------------------- patterns
CPP_TOKEN = re.compile(r"""
      [A-Za-z_]\w*
    | \d+\.?\d*(?:[eE][+-]?\d+)?[uUlLfF]*
    | <<=|>>=|\.\.\.|->\*|<=>
    | <<|>>|->|\+\+|--|==|!=|<=|>=|&&|\|\||::|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\.\*
    | [^\s\w]
""", re.X)

# Content languages: words may contain -, ., /, : because package names, paths
# and image tags are the signal.
TEXT_TOKEN = re.compile(r"""
      \$\{[^}]*\}
    | \$[A-Za-z_]\w*
    | [A-Za-z_][\w.\-+/:@]*
    | \d[\w.\-]*
    | &&|\|\||>>|<<|==|!=|<=|>=|=~
    | [^\s\w]
""", re.X)

_HASH_COMMENT = re.compile(r"(?m)(?<!\\)#[^\n]*")
_CPP_LINE = re.compile(r"//[^\n]*")
_CPP_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_DQ = re.compile(r'"(?:\\.|[^"\\])*"')
_SQ = re.compile(r"'(?:\\.|[^'\\])*'")
_SHELL_VAR = re.compile(r"\$\{?([A-Za-z_]\w*)\}?")


def strip_comments(src, lang):
    if lang == "cpp":
        src = _CPP_BLOCK.sub(lambda m: "\n" * m.group(0).count("\n"), src)
        src = _CPP_LINE.sub("", src)
        src = _DQ.sub('"S"', src)
        src = _SQ.sub("'C'", src)
        return src
    # every other language here comments with #
    return _HASH_COMMENT.sub("", src)


def tokenize(src, lang, channel="abstract"):
    """(token, line) pairs. `channel` only affects C++."""
    stripped = strip_comments(src, lang)
    pat = CPP_TOKEN if lang == "cpp" else TEXT_TOKEN
    out, line, pos = [], 1, 0
    for m in pat.finditer(stripped):
        line += stripped.count("\n", pos, m.start())
        pos = m.start()
        t = m.group()
        out.append((_map_token(t, lang, channel), line))
    return out


def _map_token(t, lang, channel):
    c = t[0]
    if lang == "cpp":
        if c.isalpha() or c == "_":
            if t in CPP_KEYWORDS:
                return t
            if channel == "library" and t in CPP_LIBRARY:
                return t
            return "ID"
        if c.isdigit():
            return "LIT"
        return t
    # ---- content languages ----
    if c == "$":
        # A shell variable name is the one thing a student renames freely.
        return "$VAR"
    if c.isdigit():
        return "NUM"
    low = t.lower()
    if lang == "dockerfile" and low in DOCKER_INSTR:
        return low.upper()
    if lang == "shell" and low in SHELL_KEYWORDS:
        return low
    if c.isalpha() or c == "_":
        # Content is the signal: keep it, case-folded so casing is not noise.
        return low
    return t
