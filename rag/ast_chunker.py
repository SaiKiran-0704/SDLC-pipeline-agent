"""
AST-aware code chunking, using tree-sitter to split on real function/class
boundaries instead of arbitrary character counts.

Strategy:
  - Walk the file's top-level nodes.
  - If a node is a "chunkable" type (function, method, class, etc. — this
    varies by language, see CHUNK_NODE_TYPES) and fits within max_chunk_chars,
    emit it whole. This keeps a class with all its methods as ONE coherent
    chunk when it's not too large, rather than fragmenting it unnecessarily.
  - If a chunkable node is too large, recurse into its children looking for
    smaller chunkable units (e.g. individual methods inside a big class).
  - Anything NOT inside a chunkable node (imports, module-level constants,
    top-level script logic) still gets captured via a character-window
    fallback, so nothing is silently dropped.
  - Files in languages with no available grammar, or that fail to parse,
    fall back entirely to the character-window method.
"""

from tree_sitter_language_pack import get_parser

CHAR_FALLBACK_SIZE = 1200
CHAR_FALLBACK_OVERLAP = 150
MAX_CHUNK_CHARS = 2000  # nodes larger than this get recursed into for smaller units

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".php": "php",
}

# Node types considered a meaningful, self-contained chunk unit per language.
# Tree-sitter grammars name these differently per language — this is the
# real reason AST chunking can't be one generic rule; it needs a mapping.
CHUNK_NODE_TYPES = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "class_declaration", "method_definition"},
    "typescript": {"function_declaration", "class_declaration", "method_definition", "interface_declaration"},
    "tsx": {"function_declaration", "class_declaration", "method_definition", "interface_declaration"},
    "go": {"function_declaration", "method_declaration"},
    "java": {"method_declaration", "class_declaration", "interface_declaration"},
    "ruby": {"method", "class"},
    "rust": {"function_item", "impl_item", "struct_item"},
    "c": {"function_definition"},
    "cpp": {"function_definition", "class_specifier"},
    "php": {"function_definition", "method_declaration", "class_declaration"},
}


def _detect_language(path: str) -> str | None:
    for ext, lang in EXTENSION_TO_LANGUAGE.items():
        if path.endswith(ext):
            return lang
    return None


def _char_chunks(text: str, base_line: int = 0) -> list[dict]:
    """Plain sliding-window fallback — used for unsupported languages,
    parse failures, and content outside any AST node."""
    if not text.strip():
        return []
    if len(text) <= CHAR_FALLBACK_SIZE:
        return [{"content": text, "node_type": "text", "name": None,
                  "start_line": base_line, "end_line": base_line + text.count("\n")}]
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHAR_FALLBACK_SIZE
        piece = text[start:end]
        if piece.strip():
            chunks.append({"content": piece, "node_type": "text", "name": None,
                            "start_line": base_line, "end_line": base_line})
        start += CHAR_FALLBACK_SIZE - CHAR_FALLBACK_OVERLAP
    return chunks


def _node_name(node, source: bytes) -> str | None:
    """Best-effort extraction of a function/class's name from its AST node,
    for readable metadata — not required for chunking to work, just for
    citations later ('this came from function reset_password')."""
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier"):
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
    return None


def _collect_chunks(node, source: bytes, chunk_types: set[str]) -> list[dict]:
    chunks = []
    if node.type in chunk_types:
        text = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
        if len(text) <= MAX_CHUNK_CHARS:
            chunks.append({
                "content": text,
                "node_type": node.type,
                "name": _node_name(node, source),
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
            })
            return chunks
        # Too large — recurse into children for smaller chunkable units
        for child in node.children:
            chunks.extend(_collect_chunks(child, source, chunk_types))
        if not chunks:
            # No smaller units found inside it either — fall back to char-split
            chunks.extend(_char_chunks(text, node.start_point[0] + 1))
        return chunks

    for child in node.children:
        chunks.extend(_collect_chunks(child, source, chunk_types))
    return chunks


def ast_chunk_file(path: str, content: str) -> list[dict] | None:
    """Returns a list of chunk dicts, or None if this file's language isn't
    supported (caller should fall back to plain character chunking)."""
    language = _detect_language(path)
    if not language:
        return None

    try:
        parser = get_parser(language)
        source = content.encode("utf-8")
        tree = parser.parse(source)
    except Exception:
        return None  # unsupported grammar, or content tree-sitter couldn't parse

    chunk_types = CHUNK_NODE_TYPES.get(language, set())
    root = tree.root_node

    chunks = []
    covered_ranges = []

    for child in root.children:
        if child.type in chunk_types:
            found = _collect_chunks(child, source, chunk_types)
            chunks.extend(found)
            covered_ranges.append((child.start_byte, child.end_byte))
        else:
            text = source[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
            if text.strip():
                # Module-level content not inside a function/class — imports,
                # top-level constants, script logic. Still captured, just via
                # the simpler fallback method since it has no meaningful
                # sub-structure to chunk along.
                chunks.extend(_char_chunks(text, child.start_point[0] + 1))

    return chunks if chunks else None
