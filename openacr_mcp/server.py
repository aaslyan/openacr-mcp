"""MCP server wrapping OpenACR CLI tools for AI-assisted schema queries,
authoring, and code generation.

Usage:
    python -m openacr_mcp --openacr-dir ~/openacr

Claude Code config (.mcp.json):
    {
      "mcpServers": {
        "openacr": {
          "command": "python",
          "args": ["-m", "openacr_mcp", "--openacr-dir", "/path/to/openacr"]
        }
      }
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from mcp.server import FastMCP

from .acr_client import AcrClient
from .header_parser import parse_header_file

# ---------------------------------------------------------------------------
# Global state — initialized once at startup
# ---------------------------------------------------------------------------

_client: AcrClient | None = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2)


def _error(msg: str, **extra: Any) -> str:
    return _json({"error": msg, **extra})


def _client_or_error() -> AcrClient | str:
    if _client is None:
        return _error("OpenACR client not initialized")
    return _client


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case (e.g. ReadingStatus -> reading_status)."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = FastMCP(
    name="openacr",
    instructions="""\
OpenACR MCP Server — relational schema to C++ code generator.

## What is OpenACR?

OpenACR defines data schemas as relational ssimfiles (simple structured input method).
The code generator `amc` reads these schemas and produces C++ structs, enums, accessors,
hash tables, linked lists, and other data structures — all from the relational model.

## Typical Workflow

1. **Query** existing schemas: `list_namespaces`, `list_ctypes`, `list_fields`, `query`, `search`
2. **Author** new schemas: `create_target` → `create_ctype` → `create_field` → `create_fconst`
3. **Generate** C++ code: `run_amc`
4. **Build**: `run_abt`
5. **Discover** generated API: `get_functions`, `list_generated_headers`, `get_generated_code`

## Creating a New Project

Start with `create_target` to create a namespace:
- nstype `ssimdb` — a data-only namespace (tables/types, no executable)
- nstype `exe` — an executable program
- nstype `lib` — a shared library
- nstype `protocol` — a protocol definition namespace

## Schema Authoring Conventions

**Type names**: CamelCase (e.g., `MyStruct`, `OrderStatus`)
**Field names**: lowercase with underscores (e.g., `order_id`, `created_at`)
**Primary key**: The first field of a ctype is usually its pkey, named the same as the ctype in lowercase.

**Common arg types** (used in create_field `arg` parameter):
- `algo.cstring` — variable-length string
- `algo.Smallstr50` — fixed-capacity string (50 chars). Also: Smallstr10, Smallstr20, Smallstr100, Smallstr150, Smallstr200
- `u32`, `u64`, `i32`, `i64` — unsigned/signed integers
- `bool` — boolean
- `algo.Comment` — a comment/description string

**Reftype meanings** (used in create_field `reftype` parameter):
- `Val` — inline value (the field stores the value directly)
- `Pkey` — foreign key reference to another ctype's primary key
- `Base` — inheritance (this ctype extends the referenced ctype)
- `Thash` — hash table of records
- `Lary` — level array (growable array)

## Enum Pattern

To create an enum:
1. `create_ctype` with a -subset pkey field whose arg is algo.Smallstr20 (or similar)
2. `create_fconst` for each enum value on the pkey field (field = "ns.Type.type", value = "MyValue")

## Call `get_workflow_guide` for detailed step-by-step examples.
""",
)

# ===== Group 1: Schema Query (read-only) =================================

@server.tool()
def list_namespaces() -> str:
    """List all OpenACR namespaces.

    Returns:
        JSON list of namespace records with ns, nstype, license, comment.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.list_namespaces()
    return _json(result.to_dict())


@server.tool()
def list_ctypes(namespace: str) -> str:
    """List all ctypes (structs) in a namespace.

    Args:
        namespace: The namespace to query (e.g., "algo", "acr", "dmmeta")

    Returns:
        JSON list of ctype records.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.list_ctypes(namespace)
    return _json(result.to_dict())


@server.tool()
def get_ctype(ctype: str) -> str:
    """Get full detail for a ctype including cross-references (tree view).

    Args:
        ctype: The ctype name (e.g., "algo.Bool", "dmmeta.Ctype")

    Returns:
        JSON with tree output showing the ctype and its related records.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.get_ctype(ctype)
    if result.ok:
        return _json({"ok": True, "tree": result.stdout})
    return _json(result.to_dict())


@server.tool()
def list_fields(ctype: str) -> str:
    """List all fields for a ctype.

    Args:
        ctype: The ctype name (e.g., "algo.Bool", "dmmeta.Ctype")

    Returns:
        JSON list of field records with field, arg, reftype, dflt, comment.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.list_fields(ctype)
    return _json(result.to_dict())


@server.tool()
def query(pattern: str) -> str:
    """Run a raw acr query against the ssimfile database.

    Args:
        pattern: acr query pattern (e.g., "dmmeta.ctype:algo.%", "dmmeta.field:acr.FDb.%")

    Returns:
        JSON list of matching records.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr(pattern)
    return _json(result.to_dict())


@server.tool()
def search(text: str) -> str:
    """Search for ctypes, fields, and comments matching a text string.

    Queries multiple acr patterns and deduplicates results.

    Args:
        text: Search text to match against ctype names, field names, and comments.

    Returns:
        JSON with matching ctypes and fields.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client

    results: dict[str, Any] = {"query": text, "ctypes": [], "fields": []}

    # Search ctype names
    ctype_result = client.acr(f"dmmeta.ctype:%{text}%")
    if ctype_result.ok:
        results["ctypes"] = ctype_result.records

    # Search field names
    field_result = client.acr(f"dmmeta.field:%.{text}%")
    if field_result.ok:
        results["fields"].extend(field_result.records)

    # Search field arg types
    field_arg_result = client.acr(f"dmmeta.field:%")
    if field_arg_result.ok:
        text_lower = text.lower()
        for rec in field_arg_result.records:
            comment = rec.get("comment", "").lower()
            arg = rec.get("arg", "").lower()
            if text_lower in comment or text_lower in arg:
                if rec not in results["fields"]:
                    results["fields"].append(rec)

    results["ctype_count"] = len(results["ctypes"])
    results["field_count"] = len(results["fields"])

    return _json(results)


# ===== Group 2: Schema Authoring (wrap acr_ed) ============================

@server.tool()
def create_target(name: str, nstype: str, comment: str = "") -> str:
    """Create a new namespace/target. This is the entry point for any new project.

    Args:
        name: Namespace name (e.g., "mydb", "myapp")
        nstype: Namespace type — one of: ssimdb, exe, lib, protocol
        comment: Description of the namespace

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    if nstype not in ("ssimdb", "exe", "lib", "protocol"):
        return _error(f"Invalid nstype '{nstype}'. Must be one of: ssimdb, exe, lib, protocol")
    result = client.acr_ed_create_target(name, nstype, comment)
    return _json(result.to_dict())


@server.tool()
def create_ctype(namespace: str, name: str, comment: str = "") -> str:
    """Create a new ctype (struct) in a namespace.

    For ssimdb namespaces, automatically creates the required ssimfile record.

    Args:
        namespace: Target namespace (e.g., "myns")
        name: Type name in CamelCase (e.g., "MyStruct")
        comment: Description of the type

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    ctype_name = f"{namespace}.{name}"
    args = ["-ctype", ctype_name]
    if comment:
        args.extend(["-comment", comment])
    result = client.acr_ed_create(args)

    # For ssimdb namespaces, auto-insert the required ssimfile record
    nstype = client.get_ns_type(namespace)
    if nstype == "ssimdb":
        ssimfile_name = f"{namespace}.{_camel_to_snake(name)}"
        ssim_line = f"dmmeta.ssimfile  ssimfile:{ssimfile_name}  ctype:{ctype_name}"
        ssim_result = client.acr_insert(ssim_line)
        if not ssim_result.ok:
            return _error(
                f"ctype created but ssimfile insert failed: {ssim_result.stderr.strip()}",
                ctype=ctype_name,
            )
        # Re-run amc now that ssimfile exists
        client.amc()

    # Re-read the result since we may have fixed the amc error
    if nstype == "ssimdb":
        return _json({"ok": True, "ctype": ctype_name, "ssimfile_auto_created": True})
    return _json(result.to_dict())


@server.tool()
def create_field(
    ctype: str,
    name: str,
    arg: str,
    reftype: str = "Val",
    dflt: str = "",
    comment: str = "",
) -> str:
    """Add a field to an existing ctype.

    Args:
        ctype: Parent ctype (e.g., "myns.MyStruct")
        name: Field name (e.g., "count")
        arg: Field type (e.g., "u32", "algo.cstring")
        reftype: Reference type (Val, Pkey, Thash, Lary, etc.)
        dflt: Default value
        comment: Field description

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    args = ["-field", f"{ctype}.{name}", "-arg", arg, "-reftype", reftype]
    if dflt:
        args.extend(["-dflt", dflt])
    if comment:
        args.extend(["-comment", comment])
    result = client.acr_ed_create(args)
    return _json(result.to_dict())


@server.tool()
def create_fconst(field: str, value: str, comment: str = "") -> str:
    """Add an enum constant to a field.

    Args:
        field: Parent field (e.g., "myns.MyEnum.value")
        value: Constant name (e.g., "Active")
        comment: Description of the constant

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    fconst_key = f"{field}/{value}"
    line = f'dmmeta.fconst  fconst:{fconst_key}  value:"{value}"  comment:"{comment}"'
    result = client.acr_insert(line)
    if result.ok:
        return _json({"ok": True, "fconst": fconst_key})
    return _json({"ok": False, "error": result.stderr.strip()})


@server.tool()
def delete_record(pattern: str) -> str:
    """Delete ssim records matching a pattern.

    Args:
        pattern: Record pattern to delete (e.g., "dmmeta.ctype:myns.MyStruct")

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_ed_delete(pattern)
    return _json(result.to_dict())


@server.tool()
def rename_record(old: str, new: str) -> str:
    """Rename a record, propagating references.

    Args:
        old: Current record key (e.g., "myns.OldName")
        new: New record key (e.g., "myns.NewName")

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_ed_rename(old, new)
    return _json(result.to_dict())


# ===== Group 3: Code Generation & Discovery ===============================

@server.tool()
def run_amc(namespace: str = "") -> str:
    """Run AMC to generate C++ code from the ssim schema.

    Args:
        namespace: Optional namespace to regenerate (empty = all)

    Returns:
        JSON with success status and stderr output.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.amc(namespace)
    return _json({
        "ok": result.ok,
        "stdout": result.stdout[:2000] if result.stdout else "",
        "stderr": result.stderr[:2000] if result.stderr else "",
    })


@server.tool()
def run_abt(target: str) -> str:
    """Build/compile a target using abt.

    Args:
        target: Build target name (e.g., "acr", "amc")

    Returns:
        JSON with success status and build output.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.abt(target)
    return _json({
        "ok": result.ok,
        "stdout": result.stdout[:5000] if result.stdout else "",
        "stderr": result.stderr[:5000] if result.stderr else "",
    })


@server.tool()
def list_generated_headers(namespace: str) -> str:
    """List generated .h files for a namespace.

    Args:
        namespace: The namespace (e.g., "algo", "acr")

    Returns:
        JSON list of header file paths (relative to openacr dir).
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    headers = client.list_generated_headers(namespace)
    relative = [str(h.relative_to(client.openacr_dir)) for h in headers]
    return _json({"namespace": namespace, "headers": relative, "count": len(relative)})


@server.tool()
def get_generated_code(header_path: str) -> str:
    """Return the contents of a generated header file.

    Args:
        header_path: Path relative to openacr dir (e.g., "include/gen/algo_gen.h")

    Returns:
        JSON with the file contents (truncated to 50KB).
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    try:
        code = client.get_generated_code(header_path)
        if len(code) > 50000:
            return _json({
                "path": header_path,
                "truncated": True,
                "total_bytes": len(code),
                "content": code[:50000],
            })
        return _json({"path": header_path, "content": code})
    except FileNotFoundError as e:
        return _error(str(e))


@server.tool()
def get_functions(namespace: str) -> str:
    """Parse generated headers for a namespace and extract structs, enums, and function signatures.

    Args:
        namespace: The namespace (e.g., "algo", "acr", "dmmeta")

    Returns:
        JSON with extracted enums, structs, and functions from generated headers.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    headers = client.list_generated_headers(namespace)
    if not headers:
        return _error(f"No generated headers found for namespace '{namespace}'")

    combined: dict[str, Any] = {
        "namespace": namespace,
        "headers_parsed": [],
        "total_enums": 0,
        "total_structs": 0,
        "total_functions": 0,
        "enums": [],
        "structs": [],
        "functions": [],
    }

    for header_path in headers:
        parsed = parse_header_file(header_path)
        rel_path = str(header_path.relative_to(client.openacr_dir))
        combined["headers_parsed"].append(rel_path)
        combined["total_enums"] += len(parsed.enums)
        combined["total_structs"] += len(parsed.structs)
        combined["total_functions"] += len(parsed.functions)

        for e in parsed.enums:
            combined["enums"].append({
                "name": e.name,
                "ctype": e.ctype,
                "value_count": len(e.values),
                "header": rel_path,
            })
        for s in parsed.structs:
            combined["structs"].append({
                "name": s.name,
                "ctype": s.ctype,
                "comment": s.comment,
                "field_count": len(s.fields),
                "member_function_count": len(s.member_functions),
                "header": rel_path,
            })
        for f in parsed.functions:
            combined["functions"].append({
                "func_tag": f.func_tag,
                "return_type": f.return_type,
                "name": f.name,
                "params": f.params,
                "comment": f.comment,
                "header": rel_path,
            })

    return _json(combined)


# ===== Group 4: Workflow Guide =============================================

@server.tool()
def get_workflow_guide() -> str:
    """Get detailed step-by-step examples for common OpenACR workflows.

    Returns:
        JSON with workflow guides for creating ssimdb namespaces, enum types,
        structs with FK relationships, and building executables.
    """
    guide = {
        "workflows": [
            {
                "title": "Create a new ssimdb with types",
                "steps": [
                    "1. create_target(name='mydb', nstype='ssimdb', comment='My database')",
                    "2. create_ctype(namespace='mydb', name='MyRecord', comment='A record type')",
                    "   — This creates the ctype. The pkey field and ssimfile record are auto-created.",
                    "3. create_field(ctype='mydb.MyRecord', name='name', arg='algo.cstring', reftype='Val', comment='Record name')",
                    "4. create_field(ctype='mydb.MyRecord', name='count', arg='u32', reftype='Val', dflt='0', comment='Counter')",
                    "5. run_amc() — generates C++ code",
                    "6. get_functions(namespace='mydb') — discover the generated API",
                ],
            },
            {
                "title": "Add an enum type",
                "steps": [
                    "1. create_ctype(namespace='mydb', name='Status', comment='Record status')",
                    "   — Creates mydb.Status ctype with auto-generated pkey field and ssimfile",
                    "2. create_fconst(field='mydb.Status.status', value='pending', comment='Not started')",
                    "3. create_fconst(field='mydb.Status.status', value='active', comment='In progress')",
                    "4. create_fconst(field='mydb.Status.status', value='done', comment='Completed')",
                    "5. run_amc() — generates C++ enum class Status { pending, active, done }",
                ],
                "notes": "The pkey field name is auto-derived as lowercase of the type name. "
                         "For mydb.Status, the pkey field is 'mydb.Status.status'.",
            },
            {
                "title": "Create a struct with foreign key references",
                "steps": [
                    "1. First create the referenced types (see 'Add an enum type')",
                    "2. create_ctype(namespace='mydb', name='Task', comment='A task')",
                    "3. create_field(ctype='mydb.Task', name='title', arg='algo.cstring', reftype='Val')",
                    "4. create_field(ctype='mydb.Task', name='status', arg='mydb.Status', reftype='Pkey', comment='Task status')",
                    "   — reftype='Pkey' creates a foreign key to mydb.Status",
                    "5. run_amc()",
                ],
            },
            {
                "title": "Create an exe that uses a ssimdb",
                "steps": [
                    "1. create_target(name='myapp', nstype='exe', comment='My application')",
                    "2. The exe needs an FDb (global database) — it's auto-created",
                    "3. To load data from a ssimdb, add finput records:",
                    "   query('dmmeta.finput:myapp.FDb.%') to see existing inputs",
                    "4. run_amc() then run_abt(target='myapp') to build",
                ],
            },
        ],
        "arg_types_reference": {
            "strings": {
                "algo.cstring": "Variable-length string (heap-allocated)",
                "algo.Smallstr10": "Fixed-capacity 10-char string (stack-allocated)",
                "algo.Smallstr20": "Fixed-capacity 20-char string",
                "algo.Smallstr50": "Fixed-capacity 50-char string",
                "algo.Smallstr100": "Fixed-capacity 100-char string",
                "algo.Smallstr150": "Fixed-capacity 150-char string",
                "algo.Smallstr200": "Fixed-capacity 200-char string",
                "algo.Comment": "Comment/description string",
            },
            "integers": {
                "u8": "Unsigned 8-bit integer",
                "u16": "Unsigned 16-bit integer",
                "u32": "Unsigned 32-bit integer",
                "u64": "Unsigned 64-bit integer",
                "i8": "Signed 8-bit integer",
                "i16": "Signed 16-bit integer",
                "i32": "Signed 32-bit integer",
                "i64": "Signed 64-bit integer",
            },
            "other": {
                "bool": "Boolean",
                "float": "32-bit float",
                "double": "64-bit float",
                "algo.UnTime": "Timestamp (Unix time in microseconds)",
                "algo.UnDiff": "Time difference (microseconds)",
            },
        },
        "reftype_reference": {
            "Val": "Inline value — the field stores the data directly in the struct",
            "Pkey": "Foreign key — references another ctype's primary key. "
                    "Generated code includes lookup functions and referential integrity",
            "Base": "Inheritance — this ctype extends the arg ctype. "
                    "Fields from the base type are included in the derived type",
            "Thash": "Hash table — stores a collection of records indexed by pkey. "
                     "Used in FDb (global database) types for in-memory tables",
            "Lary": "Level array — growable array with O(1) random access. "
                    "Used for collections that grow but never shrink",
            "Tary": "Tight array — standard growable array (like std::vector)",
            "Llist": "Linked list — intrusive doubly-linked list",
            "Count": "Count of linked records (no storage, just bookkeeping)",
            "Upptr": "Up-pointer — cached pointer to parent record for fast traversal",
        },
    }
    return _json(guide)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="MCP server wrapping OpenACR CLI tools",
    )
    parser.add_argument(
        "--openacr-dir",
        type=Path,
        default=Path.home() / "openacr",
        help="Path to OpenACR directory (default: ~/openacr)",
    )

    args = parser.parse_args()

    if not args.openacr_dir.exists():
        print(f"Error: OpenACR dir not found: {args.openacr_dir}", file=sys.stderr)
        sys.exit(1)

    global _client
    _client = AcrClient(args.openacr_dir)
    print(f"OpenACR MCP server initialized: {args.openacr_dir}", file=sys.stderr)

    server.run("stdio")


if __name__ == "__main__":
    main()
