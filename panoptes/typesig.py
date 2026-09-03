"""Type-signature fingerprints.

A third channel, independent of tokens and control flow: reduce each function to
the types it touches — return type, parameter types in order, and the multiset of
locally declared variable types — with every identifier discarded. Two functions
sharing a signature move the same shapes of data around, however they are worded.

Whether that is discriminative depends entirely on how large the type vocabulary
is. Measured on ft_irc it is small, so this collides often; see the false-positive
figures in the README.
"""
import hashlib, os, re
from . import normalize

try:
    import tree_sitter_cpp
    from tree_sitter import Language, Parser
    _LANG = Language(tree_sitter_cpp.language())
    AVAILABLE = True
except Exception:                                            # noqa: BLE001
    AVAILABLE = False

_parser = None
_WS = re.compile(r"\s+")


def _get_parser():
    global _parser
    if _parser is None:
        _parser = Parser(_LANG)
    return _parser


def _text(node, src):
    return _WS.sub(" ", src[node.start_byte:node.end_byte].decode(
        errors="ignore")).strip()


def _norm_type(t):
    """Canonicalise a type so trivial rewrites do not change it."""
    t = _WS.sub(" ", t).strip()
    for junk in ("const ", "static ", "inline ", "virtual ", "unsigned ",
                 "struct ", "class ", "typename "):
        t = t.replace(junk, "")
    t = t.replace("& ", "&").replace("* ", "*").replace(" &", "&").replace(" *", "*")
    # Collapse aliases that mean the same thing in practice.
    for a, b in (("std::size_t", "size_t"), ("long long", "long"),
                 ("uint32_t", "int"), ("int32_t", "int"), ("short", "int")):
        t = t.replace(a, b)
    return t.strip()


def _collect(node, src, types):
    """Recursively gather declared variable types inside a function body."""
    if node.type in ("declaration", "field_declaration", "parameter_declaration"):
        ty = node.child_by_field_name("type")
        if ty is not None:
            types.append(_norm_type(_text(ty, src)))
    for c in node.children:
        if c.type != "function_definition":
            _collect(c, src, types)


def signatures(path):
    """Yield one signature record per function in a file."""
    if not AVAILABLE:
        return []
    try:
        src = open(path, "rb").read()
    except OSError:
        return []
    tree = _get_parser().parse(src)
    out, stack = [], [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "function_definition":
            ret = n.child_by_field_name("type")
            ret_t = _norm_type(_text(ret, src)) if ret is not None else "?"
            params = []
            decl = n.child_by_field_name("declarator")
            if decl is not None:
                for d in decl.children:
                    if d.type == "parameter_list":
                        for pd in d.children:
                            if pd.type == "parameter_declaration":
                                ty = pd.child_by_field_name("type")
                                params.append(_norm_type(_text(ty, src))
                                              if ty is not None else "?")
            locals_ = []
            body = n.child_by_field_name("body")
            if body is not None:
                _collect(body, src, locals_)
            sig = f"{ret_t}({','.join(params)}){{{','.join(sorted(locals_))}}}"
            out.append({
                "sig": sig,
                "hash": hashlib.blake2b(sig.encode(), digest_size=8).hexdigest(),
                "start": n.start_point[0] + 1,
                "weight": 1 + len(params) + len(locals_),
            })
            continue
        stack.extend(n.children)
    return out


def repo_signatures(root, spec, min_weight=3):
    """{sig_hash: [records]} for a directory. Trivial getters are dropped."""
    out = {}
    for path in normalize.source_files(root, spec):
        rel = os.path.relpath(path, root)
        for f in signatures(path):
            if f["weight"] < min_weight:
                continue
            f["file"] = rel
            out.setdefault(f["hash"], []).append(f)
    return out


def compare(a, b):
    shared = set(a) & set(b)
    denom = min(len(a), len(b)) or 1
    return len(shared), len(shared) / denom
