"""Function-level structural fingerprints via tree-sitter.

The token channel catches copied text with renamed identifiers. This channel
catches something different: a function whose *shape* is identical — same
control flow, same nesting, same statement kinds — even when statements have
been reordered or the body reworded.

Each function is reduced to its control-flow skeleton (node types only, no
identifiers, no literals) and hashed. A submission sharing many exact function
skeletons with one corpus repo is a strong signal independent of token overlap.
"""
import hashlib, os
from . import normalize

try:
    import tree_sitter_cpp
    from tree_sitter import Language, Parser
    _LANG = Language(tree_sitter_cpp.language())
    AVAILABLE = True
except Exception:                                            # noqa: BLE001
    AVAILABLE = False
    _LANG = None

# Node types that carry structure. Everything else is noise for our purpose.
STRUCTURAL = {
    "function_definition", "compound_statement", "if_statement", "else_clause",
    "for_statement", "for_range_loop", "while_statement", "do_statement",
    "switch_statement", "case_statement", "break_statement", "continue_statement",
    "return_statement", "try_statement", "catch_clause", "throw_statement",
    "declaration", "expression_statement", "assignment_expression",
    "binary_expression", "unary_expression", "call_expression", "field_expression",
    "subscript_expression", "conditional_expression", "lambda_expression",
    "new_expression", "delete_expression", "parameter_list", "init_declarator",
}

_parser = None


def _get_parser():
    global _parser
    if _parser is None:
        _parser = Parser(_LANG)
    return _parser


def _skeleton(node, depth=0, out=None, max_depth=40):
    """Pre-order walk emitting only structural node types."""
    if out is None:
        out = []
    if depth > max_depth:
        return out
    if node.type in STRUCTURAL:
        out.append(node.type)
    for child in node.children:
        _skeleton(child, depth + 1, out, max_depth)
    return out


def _name_of(node, src):
    """Best-effort function name, for reporting only — never for matching."""
    for child in node.children:
        if child.type in ("function_declarator", "reference_declarator",
                          "pointer_declarator"):
            for g in child.children:
                if g.type in ("identifier", "qualified_identifier",
                              "field_identifier", "destructor_name",
                              "operator_name"):
                    return src[g.start_byte:g.end_byte].decode(errors="ignore")
            return _name_of(child, src)
    return "?"


def functions(path, min_nodes=15):
    """Yield (struct_hash, name, start_line, end_line, size) per function."""
    if not AVAILABLE:
        return []
    try:
        src = open(path, "rb").read()
    except OSError:
        return []
    tree = _get_parser().parse(src)
    out = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "function_definition":
            skel = _skeleton(n)
            if len(skel) >= min_nodes:
                h = hashlib.blake2b(" ".join(skel).encode(),
                                    digest_size=8).hexdigest()
                out.append({
                    "hash": h, "name": _name_of(n, src),
                    "start": n.start_point[0] + 1, "end": n.end_point[0] + 1,
                    "size": len(skel),
                })
            continue                      # do not descend into nested functions
        stack.extend(n.children)
    return out


def repo_functions(root, spec, min_nodes=15):
    """All function skeletons in a directory, keyed by hash."""
    out = {}
    for path in normalize.source_files(root, spec):
        rel = os.path.relpath(path, root)
        for f in functions(path, min_nodes):
            f["file"] = rel
            out.setdefault(f["hash"], []).append(f)
    return out


def compare(sub_funcs, corpus_funcs):
    """Shared function skeletons, largest first."""
    shared = set(sub_funcs) & set(corpus_funcs)
    rows = []
    for h in shared:
        a, b = sub_funcs[h][0], corpus_funcs[h][0]
        rows.append({"size": a["size"], "sub": a, "corpus": b})
    rows.sort(key=lambda r: r["size"], reverse=True)
    total = len(sub_funcs) or 1
    return rows, len(shared) / total
