"""Turn C++ source into comparable token streams.

Three channels are produced per file:
  abstract  — identifiers collapsed to ID, literals to LIT. Defeats renaming.
  library   — same, but std/POSIX names preserved. Carries approach signal.
  text      — comments and string literals verbatim. Highest-signal evidence.

The tokenizer is regex-based and deliberately swappable: replace `tokenize`
with a tree-sitter pass and the rest of the pipeline is unaffected.
"""
import os, re
from . import languages

KEYWORDS = set("""
alignas alignof and and_eq asm auto bitand bitor bool break case catch char char16_t
char32_t class compl const constexpr const_cast continue decltype default delete do
double dynamic_cast else enum explicit export extern false float for friend goto if
inline int long mutable namespace new noexcept not not_eq nullptr operator or or_eq
private protected public register reinterpret_cast return short signed sizeof static
static_assert static_cast struct switch template this throw true try typedef typeid
typename union unsigned using virtual void volatile wchar_t while xor xor_eq
""".split())

# Preserved in the `library` channel because they describe approach, not naming.
LIBRARY = set("""
std string vector map set list deque queue stack pair size_t ostream istream
sstream stringstream fstream ifstream ofstream iterator const_iterator exception
socket bind listen accept connect send recv poll epoll epoll_wait epoll_ctl kqueue
select fcntl setsockopt getaddrinfo inet_ntoa htons ntohs close read write signal
sockaddr_in pollfd memset strerror errno atoi strtol sprintf snprintf
""".split())

TOKEN_RE = re.compile(r"""
      [A-Za-z_]\w*
    | \d+\.?\d*(?:[eE][+-]?\d+)?[uUlLfF]*
    | <<=|>>=|\.\.\.|->\*|<=>
    | <<|>>|->|\+\+|--|==|!=|<=|>=|&&|\|\||::|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\.\*
    | [^\s\w]
""", re.X)

_COMMENT_LINE = re.compile(r"//([^\n]*)")
_COMMENT_BLOCK = re.compile(r"/\*(.*?)\*/", re.S)
_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_CHAR = re.compile(r"'(?:\\.|[^'\\])*'")


def strip_source(src):
    """Remove comments and literal contents, preserving line structure."""
    src = _COMMENT_BLOCK.sub(lambda m: "\n" * m.group(0).count("\n"), src)
    src = _COMMENT_LINE.sub("", src)
    src = _STRING.sub('"S"', src)
    src = _CHAR.sub("'C'", src)
    return src


def tokenize(src, channel="abstract", lang="cpp"):
    """Yield (token, line) pairs for one source string."""
    if lang != "cpp":
        return languages.tokenize(src, lang, channel)
    stripped = strip_source(src)
    out = []
    line = 1
    pos = 0
    for m in TOKEN_RE.finditer(stripped):
        line += stripped.count("\n", pos, m.start())
        pos = m.start()
        t = m.group()
        c = t[0]
        if c.isalpha() or c == "_":
            if t in KEYWORDS:
                tok = t
            elif channel == "library" and t in LIBRARY:
                tok = t
            else:
                tok = "ID"
        elif c.isdigit():
            tok = "LIT"
        else:
            tok = t
        out.append((tok, line))
    return out


def extract_text(src):
    """Comments and string literals, for the evidence channel."""
    out = []
    for m in _COMMENT_LINE.finditer(src):
        out.append(m.group(1).strip())
    for m in _COMMENT_BLOCK.finditer(src):
        out.append(" ".join(m.group(1).split()))
    for m in _STRING.finditer(src):
        out.append(m.group(0))
    return [x for x in out if len(x) >= 12]


def source_files(root, spec):
    """Walk a directory, yielding files that count as the student's work.

    A project may select by extension (C++) or by language (inception, whose
    comparable content is Dockerfiles, shell scripts, compose and config files
    that share no common extension).
    """
    excl = spec.exclude_dirs
    langs = spec.languages
    exts = spec.extensions
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excl and not d.startswith(".")]
        for fn in sorted(filenames):
            if langs:
                if languages.language_of(fn) in langs:
                    yield os.path.join(dirpath, fn)
            elif fn.endswith(exts):
                yield os.path.join(dirpath, fn)


def read(path):
    try:
        with open(path, errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def token_stream(root, spec, channel="abstract"):
    """Concatenated (token, relpath, line) for every source file under root.

    Files are visited in sorted order so the stream is stable across machines;
    fingerprints depend on it.
    """
    stream = []
    for path in sorted(source_files(root, spec)):
        rel = os.path.relpath(path, root)
        lang = languages.language_of(path) or "cpp"
        for tok, line in tokenize(read(path), channel, lang):
            stream.append((tok, rel, line))
    return stream
