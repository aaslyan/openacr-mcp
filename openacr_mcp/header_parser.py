"""Parse AMC-generated .h files to extract structs, enums, and function signatures.

AMC generates headers with consistent patterns:
- Enums: ``enum name_Enum { ... };``
- Structs: ``struct Name { // ns.Name: comment`` followed by fields and member funcs
- Functions: ``// func:ns.Name.field.Op`` comment followed by signature line
- Section markers: ``// gen:ns_enums``, ``// --- ns.Name``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedEnum:
    """An enum extracted from a generated header."""
    name: str
    comment: str = ""
    ctype: str = ""  # e.g., "algo.Bool.value"
    values: list[tuple[str, str]] = field(default_factory=list)  # (name, value)


@dataclass
class ParsedField:
    """A struct field."""
    type: str
    name: str
    default: str = ""
    comment: str = ""


@dataclass
class ParsedFunction:
    """A function signature extracted from a generated header."""
    func_tag: str  # e.g., "algo.cstring.ch.Alloc"
    return_type: str
    name: str
    params: str
    comment: str = ""
    is_member: bool = False  # inside struct body


@dataclass
class ParsedStruct:
    """A struct extracted from a generated header."""
    name: str
    ctype: str = ""  # e.g., "acr.AttrRegx"
    comment: str = ""
    fields: list[ParsedField] = field(default_factory=list)
    member_functions: list[ParsedFunction] = field(default_factory=list)


@dataclass
class ParsedHeader:
    """All extracted information from a generated header."""
    path: str = ""
    namespace: str = ""
    enums: list[ParsedEnum] = field(default_factory=list)
    structs: list[ParsedStruct] = field(default_factory=list)
    functions: list[ParsedFunction] = field(default_factory=list)

    def to_dict(self) -> dict:
        result: dict = {
            "path": self.path,
            "namespace": self.namespace,
            "enum_count": len(self.enums),
            "struct_count": len(self.structs),
            "function_count": len(self.functions),
        }
        if self.enums:
            result["enums"] = [
                {
                    "name": e.name,
                    "ctype": e.ctype,
                    "comment": e.comment,
                    "value_count": len(e.values),
                    "values": [{"name": n, "value": v} for n, v in e.values],
                }
                for e in self.enums
            ]
        if self.structs:
            result["structs"] = [
                {
                    "name": s.name,
                    "ctype": s.ctype,
                    "comment": s.comment,
                    "fields": [
                        {"type": f.type, "name": f.name, "default": f.default, "comment": f.comment}
                        for f in s.fields
                    ],
                    "member_function_count": len(s.member_functions),
                }
                for s in self.structs
            ]
        if self.functions:
            result["functions"] = [
                {
                    "func_tag": f.func_tag,
                    "return_type": f.return_type,
                    "name": f.name,
                    "params": f.params,
                    "comment": f.comment,
                }
                for f in self.functions
            ]
        return result


# ---------------------------------------------------------------------------
# Regex patterns for AMC output
# ---------------------------------------------------------------------------

# enum algo_BoolEnum {        // algo.Bool.value
_RE_ENUM_START = re.compile(
    r"^enum\s+(\w+)\s*\{(?:\s*//\s*(.*))?$"
)
# ,algo_Bool_Y       = 1
_RE_ENUM_VALUE = re.compile(
    r"^\s*,?\s*(\w+)\s*=\s*([^/\s]+)\s*(?://\s*(.*))?$"
)

# struct Name { // ns.Name: comment
_RE_STRUCT_START = re.compile(
    r"^struct\s+(\w+)\s*\{\s*//\s*(\S+?)(?::\s*(.*))?$"
)
#     algo_lib::Regx   name;    // Acr Regx
_RE_FIELD = re.compile(
    r"^\s+([\w:*&<>\s]+?)\s{2,}(\w+);\s*(?://\s*(.*))?$"
)

# // func:algo.cstring.ch.Alloc
_RE_FUNC_TAG = re.compile(
    r"^\s*//\s*func:(\S+)\s*$"
)

# Function signature line (free or member):
# return_type    func_name(params) attributes;
_RE_FUNC_SIG = re.compile(
    r"^\s*(?:(?:inline|static|explicit|virtual)\s+)*"
    r"([\w:*&<>\s]+?)\s+"
    r"([\w:~]+(?:\s*operator\s*[^\(]+)?)\s*"
    r"\(([^)]*)\)\s*"
    r"(?:const\s*)?"
    r"(?:__attribute__\(\([^)]*\)\)\s*)?;?\s*$"
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_header(text: str, *, path: str = "") -> ParsedHeader:
    """Parse an AMC-generated header and extract enums, structs, functions."""
    result = ParsedHeader(path=path)

    # Detect namespace from path: "algo_gen.h" -> "algo"
    if path:
        fname = Path(path).stem
        if fname.endswith("_gen"):
            result.namespace = fname[:-4]
        elif fname.endswith("_gen.inl"):
            result.namespace = fname[:-8]

    lines = text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]

        # -- Enum block --
        m = _RE_ENUM_START.match(line)
        if m and not line.strip().startswith("enum {"):
            enum = _parse_enum(lines, i, m)
            if enum:
                result.enums.append(enum)
                # Skip past enum
                while i < len(lines) and "};" not in lines[i]:
                    i += 1
                i += 1
                continue

        # -- Struct block --
        m = _RE_STRUCT_START.match(line)
        if m:
            struct, end_i = _parse_struct(lines, i, m)
            if struct:
                result.structs.append(struct)
                i = end_i + 1
                continue

        # -- Free function (func tag followed by signature) --
        m = _RE_FUNC_TAG.match(line)
        if m:
            func_tag = m.group(1)
            # Collect comment lines above
            comment = _collect_comment_above(lines, i)
            # Look ahead for signature
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines):
                sig_m = _RE_FUNC_SIG.match(lines[j])
                if sig_m:
                    func = ParsedFunction(
                        func_tag=func_tag,
                        return_type=sig_m.group(1).strip(),
                        name=sig_m.group(2).strip(),
                        params=sig_m.group(3).strip(),
                        comment=comment,
                    )
                    result.functions.append(func)
                    i = j + 1
                    continue

        i += 1

    return result


def _parse_enum(lines: list[str], start: int, m: re.Match) -> ParsedEnum | None:
    """Parse an enum block starting at line `start`."""
    name = m.group(1)
    comment_raw = m.group(2) or ""

    enum = ParsedEnum(name=name, comment=comment_raw.strip())
    if comment_raw:
        enum.ctype = comment_raw.strip()

    i = start + 1
    while i < len(lines):
        line = lines[i]
        if "};" in line:
            break
        vm = _RE_ENUM_VALUE.match(line)
        if vm:
            enum.values.append((vm.group(1), vm.group(2)))
        i += 1

    return enum if enum.values else None


def _parse_struct(lines: list[str], start: int, m: re.Match) -> tuple[ParsedStruct | None, int]:
    """Parse a struct block. Returns (struct, end_line_index)."""
    struct = ParsedStruct(
        name=m.group(1),
        ctype=m.group(2),
        comment=(m.group(3) or "").strip(),
    )

    i = start + 1
    brace_depth = 1

    while i < len(lines) and brace_depth > 0:
        line = lines[i]

        if "{" in line:
            brace_depth += line.count("{")
        if "}" in line:
            brace_depth -= line.count("}")
            if brace_depth <= 0:
                break

        # Member func tag
        fm = _RE_FUNC_TAG.match(line)
        if fm:
            func_tag = fm.group(1)
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and "}" not in lines[j]:
                sig_m = _RE_FUNC_SIG.match(lines[j])
                if sig_m:
                    struct.member_functions.append(ParsedFunction(
                        func_tag=func_tag,
                        return_type=sig_m.group(1).strip(),
                        name=sig_m.group(2).strip(),
                        params=sig_m.group(3).strip(),
                        is_member=True,
                    ))
                    i = j + 1
                    continue

        # Field
        fm = _RE_FIELD.match(line)
        if fm and "func:" not in line:
            struct.fields.append(ParsedField(
                type=fm.group(1).strip(),
                name=fm.group(2),
                comment=(fm.group(3) or "").strip(),
            ))

        i += 1

    return struct, i


def _collect_comment_above(lines: list[str], func_tag_line: int) -> str:
    """Collect comment lines immediately above a func: tag line."""
    comments: list[str] = []
    j = func_tag_line - 1
    while j >= 0:
        stripped = lines[j].strip()
        if stripped.startswith("//") and "func:" not in stripped:
            text = stripped.lstrip("/").strip()
            if text:
                comments.append(text)
        else:
            break
        j -= 1
    comments.reverse()
    return " ".join(comments)


def parse_header_file(path: Path) -> ParsedHeader:
    """Parse a generated header file from disk."""
    text = path.read_text(encoding="utf-8")
    return parse_header(text, path=str(path))
