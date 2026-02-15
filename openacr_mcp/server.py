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
import os
import re
import shutil
import subprocess
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

1. **Query** existing schemas: `list_namespaces`, `list_ctypes`, `list_fields`, `list_fconsts`, `list_ssimfiles`, `list_finputs`, `query`, `search`
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

## Building Executables

- `create_finput` — load ssimdb data at runtime (mutable tables)
- `create_gstatic` — compile reference data into the binary (immutable, no disk I/O)
- Use `indexed=True` with finput to get a Thash hash index for O(1) key lookup

## Composite Keys & Bitfields

- **Composite keys**: Use `create_substr_field` with `.LL` / `.LR` pathcomp expressions
- **Bitfields**: Use `create_bitfield` to pack small values into integers
- **Indexed access**: Use `create_field` with `xref=True`, `hashfld`, `sortfld`, `via`

## Schema Exploration

- `list_fconsts` — list enum constants for a namespace or ctype
- `list_ssimfiles` — list data tables in a namespace
- `list_finputs` — list runtime tables an exe loads
- `get_upstream` / `get_downstream` — traverse FK dependency graph (acr -nup / -ndown)
- `find_unused` — find unreferenced records for cleanup
- `get_record_meta` — get schema metadata (ctype/field definitions) for records
- `select_fields` — query with column projection (return specific fields only)
- `get_input_tables` — list all ssimfiles a target reads (full dependency graph via acr_in)
- `visualize_ctype` — ASCII art diagram of type structure (via amc_vis)

## Structured Deletion

- `delete_ctype` — delete a ctype with full cascade (fields, ssimfile, cfmt, etc.)
- `delete_field` — delete a field with cascade (fconsts, xrefs, etc.)
- `delete_target` — delete an entire namespace with cascade

## Record Updates

- `update_record` — upsert a record (update if exists, insert if not) via acr -merge

## Scaffolding

- `create_srcfile` — create a source file registered with a build target
- `create_unittest` — scaffold a unit test function
- `create_citest` — scaffold a CI integration test
- `create_foutput` — declare output table for an exe target
- `create_cppfunc` — create a computed field (C++ expression, not stored)

## Validation

Always run `validate_schema()` after schema changes to check referential integrity.

## Standalone Projects

Use standalone project directories to keep schema work isolated from the
upstream OpenACR installation.  All reads/writes stay local to the project.

- `init_project("/path/to/project")` — bootstrap a new project directory
  (copies data/, symlinks bin/, creates scaffold dirs, initializes git)
- `set_project("/path/to/project")` — switch the working context to a project
- `set_project("")` — switch back to the upstream openacr directory

## Call `get_workflow_guide` for detailed step-by-step examples.
""",
)

# ===== Group 0: Project Management ========================================

@server.tool()
def init_project(path: str) -> str:
    """Bootstrap a standalone project directory.

    Copies the openacr data/ tree (metadata only, ~2 MB) into the project
    directory and symlinks bin/ so all OpenACR commands work locally.
    After init, call ``set_project`` to switch context.

    Args:
        path: Absolute or relative path to the new project directory.

    Returns:
        JSON with the resolved project_dir on success, or an error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client

    project = Path(path).resolve()
    data_dst = project / "data"

    if data_dst.exists():
        return _error(f"data/ already exists at {project} — refusing to overwrite")

    project.mkdir(parents=True, exist_ok=True)
    shutil.copytree(client.openacr_dir / "data", data_dst)
    os.symlink(client.openacr_dir / "bin", project / "bin")
    for sub in ("lock", "include/gen", "cpp/gen"):
        (project / sub).mkdir(parents=True, exist_ok=True)

    # acr_ed requires a git repository
    subprocess.run(["git", "init"], cwd=str(project), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(project), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init project", "--allow-empty"],
        cwd=str(project), capture_output=True,
    )

    return _json({"ok": True, "project_dir": str(project)})


@server.tool()
def set_project(path: str = "") -> str:
    """Switch the working directory to a standalone project (or back to openacr).

    Args:
        path: Project directory path.  Empty string resets to upstream openacr.

    Returns:
        JSON with the active work_dir on success, or an error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client

    if not path:
        client.work_dir = None
        return _json({"ok": True, "work_dir": str(client.work_dir)})

    resolved = Path(path).resolve()
    if not (resolved / "data" / "dmmeta").is_dir():
        return _error(f"Invalid project directory — missing data/dmmeta at {resolved}")
    if not (resolved / "bin").exists():
        return _error(f"Invalid project directory — missing bin/ at {resolved}")

    client.work_dir = resolved
    return _json({"ok": True, "work_dir": str(client.work_dir)})


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
def get_namespace_tree(namespace: str) -> str:
    """Get a complete tree view of a namespace — all ctypes, fields, fconsts,
    ssimfiles, cfmt records, and reverse references in one call.

    This is the single best way to understand a namespace. Uses ``acr -t``
    to print the full cross-reference tree.

    Args:
        namespace: The namespace to inspect (e.g., "moviedb", "reservedb")

    Returns:
        JSON with the tree output as indented ssim text.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr(f"dmmeta.ns:{namespace}", tree=True)
    if result.ok:
        return _json({"ok": True, "namespace": namespace, "tree": result.stdout})
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


@server.tool()
def list_fconsts(namespace: str, ctype: str = "") -> str:
    """List enum constants (fconsts) in a namespace or for a specific ctype.

    Args:
        namespace: Namespace to search (e.g., "dev", "mydb")
        ctype: Optional ctype to filter by (e.g., "dev.Mdmark"). If omitted, lists all fconsts in the namespace.

    Returns:
        JSON list of fconst records with fconst, value, and comment.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    if ctype:
        pattern = f"dmmeta.fconst:{ctype}.%"
    else:
        pattern = f"dmmeta.fconst:{namespace}.%"
    result = client.acr(pattern)
    return _json(result.to_dict())


@server.tool()
def list_ssimfiles(namespace: str) -> str:
    """List all ssimfiles (data tables) in a namespace.

    Each ssimfile is a flat file in data/<ns>/<table>.ssim that stores
    records for one ctype. This tells you what persistent tables exist.

    Args:
        namespace: Namespace to query (e.g., "dev", "dmmeta")

    Returns:
        JSON list of ssimfile records with ssimfile and ctype.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr(f"dmmeta.ssimfile:{namespace}.%")
    return _json(result.to_dict())


@server.tool()
def list_finputs(target: str) -> str:
    """List all runtime table inputs (finputs) for an exe target.

    Shows which ssimfiles an executable loads into memory at startup.

    Args:
        target: Target namespace (e.g., "acr", "amc", "myapp")

    Returns:
        JSON list of finput records showing which ssimfiles the target reads.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr(f"dmmeta.finput:{target}.%")
    return _json(result.to_dict())


@server.tool()
def get_downstream(pattern: str, levels: int = 1) -> str:
    """Get downstream dependencies — records that depend on the matched records.

    Uses ``acr -ndown`` to traverse foreign key references downward.
    For example, querying a ctype with ndown=1 shows its fields.

    Args:
        pattern: ACR query pattern (e.g., "dmmeta.ctype:dev.Builddir")
        levels: Number of levels to traverse down (1-100, default 1)

    Returns:
        JSON list of matched records plus their downstream dependents.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    levels = max(1, min(100, levels))
    result = client.acr_ndown(pattern, levels)
    return _json(result.to_dict())


@server.tool()
def get_upstream(pattern: str, levels: int = 1) -> str:
    """Get upstream references — records that the matched records depend on.

    Uses ``acr -nup`` to traverse foreign key references upward.
    For example, querying a field with nup=1 shows its parent ctype and arg type.

    Args:
        pattern: ACR query pattern (e.g., "dmmeta.field:dev.Builddir.builddir")
        levels: Number of levels to traverse up (1-100, default 1)

    Returns:
        JSON list of matched records plus their upstream dependencies.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    levels = max(1, min(100, levels))
    result = client.acr_nup(pattern, levels)
    return _json(result.to_dict())


@server.tool()
def find_unused(pattern: str) -> str:
    """Find records matching the pattern that are not referenced by any other record.

    Useful for cleanup — identifying orphaned types, fields, or other records
    that can be safely deleted.

    Args:
        pattern: ACR query pattern (e.g., "dmmeta.ctype:myns.%", "dmmeta.field:myns.%")

    Returns:
        JSON list of unreferenced records.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_unused(pattern)
    return _json(result.to_dict())


@server.tool()
def get_record_meta(pattern: str) -> str:
    """Get schema metadata for records matching the pattern.

    Returns the ctype and field definitions that describe the structure of
    the matched records. Useful for understanding what columns a table has.

    Args:
        pattern: ACR query pattern (e.g., "dmmeta.ctype:algo.Bool")

    Returns:
        JSON list of metadata records describing the schema.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_meta(pattern)
    return _json(result.to_dict())


@server.tool()
def select_fields(pattern: str, fields: list[str]) -> str:
    """Query records with field projection — only return specified columns.

    Instead of returning all columns, select specific fields to reduce output.

    Args:
        pattern: ACR query pattern (e.g., "dmmeta.field:algo.%")
        fields: List of field names to project (e.g., ["field", "arg", "reftype"])

    Returns:
        JSON with raw projected output text.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    if not fields:
        return _error("Must specify at least one field to project")
    result = client.acr_select_fields(pattern, fields)
    if result.ok:
        return _json({"ok": True, "output": result.stdout, "pattern": pattern, "fields": fields})
    return _json(result.to_dict())


@server.tool()
def get_input_tables(target: str) -> str:
    """List all ssimfiles that a target reads as input at runtime.

    Uses ``acr_in`` to show the complete data dependency graph — which
    ssimfiles an exe needs loaded to function. More comprehensive than
    ``list_finputs`` because it includes transitive dependencies.

    Args:
        target: Target name (e.g., "acr", "amc", "myapp")

    Returns:
        JSON list of input ssimfile records.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_in(target)
    return _json(result.to_dict())


@server.tool()
def visualize_ctype(ctype: str) -> str:
    """Generate an ASCII art diagram showing a ctype's field structure and relationships.

    Uses ``amc_vis`` to produce a visual representation of the type layout,
    showing Val fields, Pkey references, Base inheritance, etc.

    Args:
        ctype: Ctype to visualize (e.g., "dmmeta.Ctype", "algo.Bool")

    Returns:
        JSON with the ASCII art diagram as text.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.amc_vis(ctype)
    if result.ok:
        return _json({"ok": True, "ctype": ctype, "diagram": result.stdout})
    return _json(result.to_dict())


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
def create_ctype(
    namespace: str,
    name: str,
    comment: str = "",
    subset: str = "",
    separator: str = "",
) -> str:
    """Create a new ctype (struct) in a namespace.

    For ssimdb namespaces, automatically creates the required ssimfile and
    cfmt records so the type can be read/printed in Tuple format.

    Args:
        namespace: Target namespace (e.g., "myns")
        name: Type name in CamelCase (e.g., "MyStruct")
        comment: Description of the type
        subset: Primary key subset type (e.g., "algo.Smallstr50"). Sets the pkey
                field's arg type. Important for enum types where you want a string pkey.
        separator: Key separator for composite keys (default: "."). Use "/" for
                   junction tables with composite pkeys like "movie/actor".

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    ctype_name = f"{namespace}.{name}"
    args = ["-ctype", ctype_name]
    if subset:
        args.extend(["-subset", subset])
    if separator:
        args.extend(["-separator", separator])
    if comment:
        args.extend(["-comment", comment])
    result = client.acr_ed_create(args)

    # For ssimdb namespaces, auto-insert the required ssimfile and cfmt records
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
        # Auto-insert cfmt so the type has ReadStrptrMaybe / Print (needed by finput)
        cfmt_line = (
            f'dmmeta.cfmt  cfmt:{ctype_name}.String  printfmt:Tuple'
            f'  read:Y  print:Y  sep:""  genop:Y  comment:""'
        )
        cfmt_result = client.acr_insert(cfmt_line)
        if not cfmt_result.ok:
            return _error(
                f"ctype created but cfmt insert failed: {cfmt_result.stderr.strip()}",
                ctype=ctype_name,
            )
        # Re-run amc now that ssimfile + cfmt exist
        client.amc()

    # Re-read the result since we may have fixed the amc error
    if nstype == "ssimdb":
        return _json({"ok": True, "ctype": ctype_name, "ssimfile_auto_created": True,
                       "cfmt_auto_created": True})
    return _json(result.to_dict())


@server.tool()
def create_field(
    ctype: str,
    name: str,
    arg: str,
    reftype: str = "Val",
    dflt: str = "",
    comment: str = "",
    xref: bool = False,
    via: str = "",
    hashfld: str = "",
    sortfld: str = "",
    inscond: str = "",
    before: str = "",
    cascdel: bool = False,
) -> str:
    """Add a field to an existing ctype.

    Args:
        ctype: Parent ctype (e.g., "myns.MyStruct")
        name: Field name (e.g., "count")
        arg: Field type (e.g., "u32", "algo.cstring", "myns.MyType")
        reftype: Reference type (Val, Pkey, Thash, Lary, Bheap, Atree, Llist, Ptrary, etc.)
        dflt: Default value
        comment: Field description
        xref: If true, auto-create cross-reference record (for Thash/Bheap/Atree/Llist indexes)
        via: Cross-reference path (e.g., "myns.Order/order"). Required with -xref for indexes
        hashfld: Hash field for Thash reftypes (e.g., "myns.Order.order")
        sortfld: Sort field for Bheap/Atree reftypes
        inscond: Insert condition for xref (default: "true")
        before: Place field before this field in the struct
        cascdel: If true, deleting this record cascades to referenced records

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
    if xref:
        args.append("-xref")
    if via:
        args.extend(["-via", via])
    if hashfld:
        args.extend(["-hashfld", hashfld])
    if sortfld:
        args.extend(["-sortfld", sortfld])
    if inscond:
        args.extend(["-inscond", inscond])
    if before:
        args.extend(["-before", before])
    if cascdel:
        args.append("-cascdel")
    result = client.acr_ed_create(args)
    return _json(result.to_dict())


@server.tool()
def create_fconst(field: str, value: str, comment: str = "") -> str:
    """Add an enum constant to a field.

    The ``field`` parameter can be either:
    - A full field path: ``"myns.MyEnum.my_enum"`` (3 dot-separated parts)
    - A ctype shorthand: ``"myns.MyEnum"`` (2 parts) — the pkey field name
      is auto-derived from the CamelCase type name (MyEnum → my_enum)

    Args:
        field: Parent field or ctype (e.g., "myns.MyEnum" or "myns.MyEnum.my_enum")
        value: Constant name (e.g., "Active")
        comment: Description of the constant

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    # Auto-derive pkey field name if only ctype was given (ns.Type -> ns.Type.type)
    parts = field.split(".")
    if len(parts) == 2:
        ns, type_name = parts
        pkey_name = _camel_to_snake(type_name)
        field = f"{ns}.{type_name}.{pkey_name}"
    fconst_key = f"{field}/{value}"
    line = f'dmmeta.fconst  fconst:{fconst_key}  value:"{value}"  comment:"{comment}"'
    result = client.acr_insert(line)
    if result.ok:
        return _json({"ok": True, "fconst": fconst_key})
    return _json({"ok": False, "error": result.stderr.strip()})


@server.tool()
def create_enum(
    namespace: str,
    name: str,
    values: list[str],
    comment: str = "",
    subset: str = "algo.Smallstr50",
) -> str:
    """Create an enum type with all its constants in one call.

    This is a high-level convenience tool that combines ``create_ctype`` (with
    ``-subset``) and multiple ``create_fconst`` calls.  It creates the ctype,
    then adds one fconst for each value.

    Args:
        namespace: Target namespace (e.g., "mydb")
        name: CamelCase enum type name (e.g., "Status", "Priority")
        values: List of enum constant names (e.g., ["pending", "active", "done"])
        comment: Description of the enum type
        subset: Underlying string type for the pkey (default: "algo.Smallstr50")

    Returns:
        JSON with the created ctype, fconst list, and any errors.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client

    ctype_name = f"{namespace}.{name}"
    pkey_name = _camel_to_snake(name)
    field_name = f"{ctype_name}.{pkey_name}"

    # Step 1: create the ctype with -subset
    args = ["-ctype", ctype_name, "-subset", subset]
    if comment:
        args.extend(["-comment", comment])
    result = client.acr_ed_create(args)
    if not result.ok:
        return _json({"ok": False, "error": result.stderr.strip(), "step": "create_ctype"})

    # Step 2: add fconst for each value
    created: list[str] = []
    errors: list[dict] = []
    for val in values:
        fconst_key = f"{field_name}/{val}"
        line = f'dmmeta.fconst  fconst:{fconst_key}  value:"{val}"  comment:""'
        r = client.acr_insert(line)
        if r.ok:
            created.append(fconst_key)
        else:
            errors.append({"value": val, "error": r.stderr.strip()})

    return _json({
        "ok": len(errors) == 0,
        "ctype": ctype_name,
        "pkey_field": field_name,
        "fconsts_created": created,
        "errors": errors,
    })


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


@server.tool()
def create_finput(target: str, ssimfile: str, indexed: bool = False) -> str:
    """Add an in-memory table to an exe target, loaded from an ssimfile at startup.

    This is how exe targets load ssimdb data at runtime. When the program starts,
    the generated Init code reads the ssimfile and populates an in-memory table.
    Use ``indexed=True`` to auto-add a Thash index for O(1) key lookup.

    Args:
        target: Exe target namespace (e.g., "myapp")
        ssimfile: Ssimfile to load (e.g., "mydb.my_table")
        indexed: If true, also create a hash index for the primary key

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    args = ["-finput", "-target", target, "-ssimfile", ssimfile]
    if indexed:
        args.append("-indexed")
    result = client.acr_ed_create(args)
    return _json(result.to_dict())


@server.tool()
def create_gstatic(target: str, ssimfile: str) -> str:
    """Add a compile-time static table to a target, baked into the binary.

    Like finput but the data is compiled directly into the C++ binary.
    No disk I/O at startup — the table is read-only and immutable.
    Ideal for reference data: country codes, currency tables, error messages,
    exchange instrument definitions, etc.

    Args:
        target: Target namespace (e.g., "myapp")
        ssimfile: Ssimfile whose data will be compiled in (e.g., "mydb.country")

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    args = ["-gstatic", "-target", target, "-ssimfile", ssimfile]
    result = client.acr_ed_create(args)
    return _json(result.to_dict())


@server.tool()
def create_substr_field(
    ctype: str,
    name: str,
    expr: str,
    srcfield: str,
    comment: str = "",
) -> str:
    """Create a substring field that extracts part of a composite primary key.

    Composite keys use separators (typically '/') to combine multiple references
    into a single pkey. Substr fields extract the components. For example, if
    ctype Review has pkey "movie/reviewer", you create substr fields to extract
    the movie and reviewer parts.

    Common expr values:
    - ".LL" — left of separator (first component)
    - ".LR" — right of separator (second component)
    - ".RL" — second-to-last component
    - ".RR" — last component

    Args:
        ctype: Parent ctype (e.g., "myns.Review")
        name: Field name (e.g., "movie")
        expr: Pathcomp expression (e.g., ".LL" for left part, ".LR" for right part)
        srcfield: Source field to extract from (e.g., "myns.Review.review")
        comment: Field description

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    args = [
        "-field", f"{ctype}.{name}",
        "-substr", expr,
        "-srcfield", srcfield,
    ]
    if comment:
        args.extend(["-comment", comment])
    result = client.acr_ed_create(args)
    return _json(result.to_dict())


@server.tool()
def create_bitfield(
    ctype: str,
    name: str,
    arg: str,
    srcfield: str,
    width: int = 1,
    comment: str = "",
) -> str:
    """Create a bitfield packed into an integer field.

    Bitfields allow packing multiple small values into a single integer.
    Used in protocol definitions and compact data structures where memory
    layout matters.

    Args:
        ctype: Parent ctype (e.g., "myns.Header")
        name: Bitfield name (e.g., "version")
        arg: Bitfield type (e.g., "u8", "u16", "u32")
        srcfield: Integer field that holds the bits (e.g., "myns.Header.flags")
        width: Bit width (number of bits, e.g., 4 for a 4-bit field)
        comment: Field description

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client

    field_name = f"{ctype}.{name}"

    # Auto-calculate offset: query existing bitfields on this srcfield and
    # place the new one right after the last occupied bit.
    offset = 0
    existing = client.acr(f"dmmeta.bitfld:{ctype}.%")
    if existing.ok and existing.records:
        for rec in existing.records:
            if rec.get("srcfield") == srcfield:
                try:
                    rec_offset = int(rec.get("offset", "0"))
                    rec_width = int(rec.get("width", "0"))
                    end = rec_offset + rec_width
                    if end > offset:
                        offset = end
                except (ValueError, TypeError):
                    pass

    # Create the field with Bitfld reftype
    args = [
        "-field", field_name,
        "-arg", arg,
        "-reftype", "Bitfld",
        "-srcfield", srcfield,
    ]
    if comment:
        args.extend(["-comment", comment])
    result = client.acr_ed_create(args)
    if not result.ok:
        return _json(result.to_dict())

    # Update the bitfld record with the computed offset and width
    bitfld_line = (
        f"dmmeta.bitfld  field:{field_name}"
        f"  offset:{offset}  width:{width}  srcfield:{srcfield}"
        f'  comment:"{comment}"'
    )
    client.acr_merge(bitfld_line)
    return _json({"ok": True, "field": field_name, "offset": offset, "width": width})


@server.tool()
def validate_schema(pattern: str = "%") -> str:
    """Run cross-reference and referential integrity checks on the schema.

    Validates that all foreign key references are valid, ssimfiles exist for
    ctypes that need them, required records are present, and other constraints
    are satisfied.

    Args:
        pattern: ACR pattern to scope the check (default: "%" for everything).
                 Use "dmmeta.ctype:myns.%" to check only one namespace.

    Returns:
        JSON with check results: ok=true if no errors, or a list of error records.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_check(pattern)
    if result.ok:
        return _json({"ok": True, "message": "Schema validation passed", "pattern": pattern})
    # Parse error output — acr -check writes errors to stderr
    errors = []
    for line in result.stderr.splitlines():
        line = line.strip()
        if line and not line.startswith("report."):
            errors.append(line)
    return _json({
        "ok": False,
        "pattern": pattern,
        "error_count": len(errors),
        "errors": errors[:50],  # Cap at 50 errors
    })


@server.tool()
def delete_ctype(ctype: str) -> str:
    """Delete a ctype and all its associated records (fields, ssimfile, cfmt, etc.).

    Uses ``acr_ed -del -ctype`` which properly cascades the deletion to all
    dependent records — much safer than raw ``delete_record``.

    Args:
        ctype: Ctype to delete (e.g., "myns.MyStruct")

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_ed_delete_ctype(ctype)
    return _json(result.to_dict())


@server.tool()
def delete_field(field: str) -> str:
    """Delete a field and its associated records (fconsts, xrefs, etc.).

    Uses ``acr_ed -del -field`` which properly cascades.

    Args:
        field: Field to delete (e.g., "myns.MyStruct.my_field")

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_ed_delete_field(field)
    return _json(result.to_dict())


@server.tool()
def delete_target(target: str) -> str:
    """Delete a target (namespace) and all its associated records.

    Uses ``acr_ed -del -target`` which cascades the deletion to all ctypes,
    fields, ssimfiles, finputs, source files, and build configuration.
    This is a destructive operation.

    Args:
        target: Target namespace to delete (e.g., "myns")

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_ed_delete_target(target)
    return _json(result.to_dict())


@server.tool()
def create_srcfile(target: str, path: str, comment: str = "") -> str:
    """Create a new source file and register it with a build target.

    The file is created on disk and a ``dev.targsrc`` record is inserted
    so ``abt`` knows to compile it.

    Args:
        target: Build target that owns the file (e.g., "myapp")
        path: Source file path relative to openacr dir (e.g., "cpp/myapp/main.cpp")
        comment: Description of the file

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_ed_create_srcfile(path, target)
    return _json(result.to_dict())


@server.tool()
def create_unittest(target: str, funcname: str, comment: str = "") -> str:
    """Create a unit test function scaffold.

    Creates a test function in the target's test source file. The test name
    is ``<target>.<funcname>`` (e.g., "myapp.TestSomething").

    Args:
        target: Target namespace (e.g., "myapp")
        funcname: Test function name (e.g., "TestSomething")
        comment: Description of the test

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    test_name = f"{target}.{funcname}"
    result = client.acr_ed_create_unittest(test_name, comment)
    return _json(result.to_dict())


@server.tool()
def update_record(line: str) -> str:
    """Update or insert a record (upsert) via ``acr -merge -write``.

    If the record exists, updates only the changed attributes.
    If the record does not exist, inserts it as a new record.
    The line must be a valid ssim tuple.

    Args:
        line: Full ssim record line (e.g., 'dmmeta.ns  ns:myns  nstype:ssimdb  comment:"Updated"')

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_merge(line)
    return _json(result.to_dict())


@server.tool()
def create_foutput(target: str, ssimfile: str) -> str:
    """Declare that an exe target writes to an ssimfile (output table).

    The inverse of finput — marks a table as an output destination for the program.

    Args:
        target: Exe target namespace (e.g., "myapp")
        ssimfile: Ssimfile the target writes to (e.g., "mydb.my_table")

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    args = ["-target", target, "-ssimfile", ssimfile]
    result = client.acr_ed_create_foutput(args)
    return _json(result.to_dict())


@server.tool()
def create_citest(target: str, testname: str, comment: str = "") -> str:
    """Create a CI (integration) test scaffold.

    CI tests run as part of the continuous integration pipeline and test
    end-to-end behavior of a target.

    Args:
        target: Target that the test is for (e.g., "myapp")
        testname: Test name (e.g., "myapp.Smoke")
        comment: Description of the test

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    result = client.acr_ed_create_citest(testname, comment)
    return _json(result.to_dict())


@server.tool()
def create_cppfunc(
    ctype: str,
    name: str,
    arg: str,
    expr: str,
    comment: str = "",
) -> str:
    """Create a computed field whose value is a C++ expression.

    The field's value is not stored — it is computed at access time by
    evaluating the given C++ expression. Useful for derived values.

    Args:
        ctype: Parent ctype (e.g., "myns.MyStruct")
        name: Field name (e.g., "total_cost")
        arg: Return type of the expression (e.g., "u32", "double", "algo.cstring")
        expr: C++ expression to compute the value (e.g., "quantity * unit_price")
        comment: Field description

    Returns:
        JSON with success status or error.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client
    args = [
        "-field", f"{ctype}.{name}",
        "-arg", arg,
        "-cppfunc", expr,
    ]
    if comment:
        args.extend(["-comment", comment])
    result = client.acr_ed_create(args)
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
    relative = [str(h.relative_to(client.work_dir)) for h in headers]
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
        rel_path = str(header_path.relative_to(client.work_dir))
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
                    "3. Add finput for each ssimfile the exe needs to load at runtime:",
                    "   create_finput(target='myapp', ssimfile='mydb.my_table', indexed=True)",
                    "   — indexed=True adds a Thash hash index for O(1) key lookup",
                    "4. run_amc() then run_abt(target='myapp') to build",
                ],
            },
            {
                "title": "Load reference data at compile time (gstatic)",
                "steps": [
                    "1. Create your reference data ssimdb: create_target('refdb', 'ssimdb')",
                    "2. Add types and populate data files in data/refdb/*.ssim",
                    "3. In your exe, use gstatic instead of finput:",
                    "   create_gstatic(target='myapp', ssimfile='refdb.country')",
                    "   — Data is compiled INTO the binary. No disk I/O at startup.",
                    "   — The table is read-only and immutable at runtime.",
                    "4. Use finput for mutable data that changes between runs,",
                    "   gstatic for immutable reference data (currencies, countries, etc.)",
                ],
            },
            {
                "title": "Create a composite key (junction table)",
                "steps": [
                    "1. Create the junction ctype with a composite pkey:",
                    "   create_ctype('mydb', 'MovieCast', 'Movie-actor association')",
                    "   — pkey field 'movie_cast' stores 'movie/actor' composite",
                    "2. Add substr fields to extract each component:",
                    "   create_substr_field('mydb.MovieCast', 'movie', '.LL', 'mydb.MovieCast.movie_cast')",
                    "   create_substr_field('mydb.MovieCast', 'actor', '.LR', 'mydb.MovieCast.movie_cast')",
                    "   — .LL = left of '/', .LR = right of '/'",
                    "3. Add data fields: create_field('mydb.MovieCast', 'role_name', 'algo.cstring', 'Val')",
                    "4. Separator defaults to '/' for composite keys",
                ],
            },
            {
                "title": "Create a bitfield-packed struct",
                "steps": [
                    "1. Create the ctype: create_ctype('myproto', 'Header', 'Protocol header')",
                    "2. Add the integer field that holds the bits:",
                    "   create_field('myproto.Header', 'flags', 'u32', 'Val')",
                    "3. Add bitfields packed into it:",
                    "   create_bitfield('myproto.Header', 'version', 'u8', 'myproto.Header.flags', width=4)",
                    "   create_bitfield('myproto.Header', 'type', 'u8', 'myproto.Header.flags', width=4)",
                    "4. run_amc() — generates accessors: version_Get(hdr), version_Set(hdr, val)",
                ],
            },
            {
                "title": "Add indexed access paths to an exe (Thash/Bheap)",
                "steps": [
                    "1. After create_finput, add indexed fields with xref:",
                    "   create_field('myapp.FDb', 'ind_order', 'myapp.Order', 'Thash',",
                    "     xref=True, hashfld='myapp.Order.order', via='myapp.Order/order')",
                    "   — Creates a hash table indexed by order pkey",
                    "2. For sorted access (priority queue):",
                    "   create_field('myapp.FDb', 'bh_order', 'myapp.Order', 'Bheap',",
                    "     xref=True, sortfld='myapp.Order.price')",
                    "3. run_amc() — generates: ind_order_Find(key), bh_order_First()",
                ],
            },
            {
                "title": "Validate schema integrity",
                "steps": [
                    "1. After making schema changes, always validate:",
                    "   validate_schema() — checks ALL referential integrity",
                    "   validate_schema('dmmeta.ctype:myns.%') — check one namespace",
                    "2. Common errors: broken FK refs, missing ssimfiles, dangling records",
                    "3. Fix any errors before running amc",
                ],
            },
            {
                "title": "Explore an existing namespace",
                "steps": [
                    "1. list_ssimfiles('dev') — see all data tables",
                    "2. list_fconsts('dev') — see all enum constants",
                    "3. list_finputs('acr') — see what tables acr loads at runtime",
                    "4. get_downstream('dmmeta.ctype:dev.Builddir', levels=2) — see fields and fconsts",
                    "5. get_upstream('dmmeta.field:dev.Builddir.builddir', levels=1) — see parent ctype",
                ],
            },
            {
                "title": "Delete and rebuild a ctype",
                "steps": [
                    "1. delete_ctype('myns.OldType') — cascades to fields, ssimfile, cfmt",
                    "2. Or delete just a field: delete_field('myns.MyType.old_field')",
                    "3. Or remove an entire namespace: delete_target('myns')",
                    "4. run_amc() — regenerate code after deletion",
                    "Note: Use delete_ctype/field/target instead of raw delete_record",
                    "      — they handle cascade properly via acr_ed.",
                ],
            },
            {
                "title": "Scaffold source files and tests",
                "steps": [
                    "1. create_srcfile(target='myapp', path='cpp/myapp/utils.cpp')",
                    "   — Creates the file and registers it with abt",
                    "2. create_unittest(target='atf_ut', funcname='myapp.TestAdd')",
                    "   — Scaffolds a test function in the target's test source",
                    "3. run_abt(target='myapp') — build to verify",
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


# ===== Group 5: Usage Examples =============================================

@server.tool()
def get_usage_examples(namespace: str) -> str:
    """Generate C++ usage examples for a namespace's generated types.

    Inspects the actual generated structs, enums, and fields, then produces
    concrete C++ code showing how to use them: initialization, field access,
    enum operations, serialization.

    Args:
        namespace: The namespace (e.g., "reservedb", "bookdb")

    Returns:
        JSON with include directive, and per-type usage examples.
    """
    client = _client_or_error()
    if isinstance(client, str):
        return client

    # Gather schema info
    ctypes_result = client.list_ctypes(namespace)
    if not ctypes_result.ok or not ctypes_result.records:
        return _error(f"No ctypes found for namespace '{namespace}'")

    examples: dict[str, Any] = {
        "namespace": namespace,
        "include": f'#include "include/gen/{namespace}_gen.h"',
        "types": [],
    }

    for ctype_rec in ctypes_result.records:
        ctype_name = ctype_rec.get("ctype", "")  # e.g. "reservedb.Guest"
        comment = ctype_rec.get("comment", "")
        if "." not in ctype_name:
            continue
        ns, type_name = ctype_name.split(".", 1)

        # Skip internal helper types
        if type_name in ("FieldId",) or type_name.endswith("Case"):
            continue

        # Get fields
        fields_result = client.list_fields(ctype_name)
        fields = fields_result.records if fields_result.ok else []

        # Check for fconsts (enum type)
        pkey_field = fields[0] if fields else None
        pkey_field_name = pkey_field.get("field", "") if pkey_field else ""
        fconst_result = client.acr(f"dmmeta.fconst:{pkey_field_name}/%")
        fconsts = fconst_result.records if fconst_result.ok else []
        is_enum = len(fconsts) > 0

        # Get non-pkey fields (the actual data fields)
        data_fields = fields[1:] if len(fields) > 1 else []

        type_example: dict[str, Any] = {
            "ctype": ctype_name,
            "type_name": type_name,
            "comment": comment,
            "is_enum": is_enum,
            "code": [],
        }

        if is_enum:
            # --- Enum usage examples ---
            snake = _camel_to_snake(type_name)
            fconst_values = [r.get("value", "") for r in fconsts]
            fconst_comments = [r.get("comment", "") for r in fconsts]

            # Enum constants
            constants_list = ", ".join(
                f"{ns}_{type_name}Case_{v}" for v in fconst_values[:3]
            )
            first_val = fconst_values[0] if fconst_values else "value"

            type_example["enum_values"] = [
                {"value": v, "constant": f"{ns}_{type_name}Case_{v}", "comment": c}
                for v, c in zip(fconst_values, fconst_comments)
            ]

            type_example["code"] = [
                {
                    "description": f"Use {type_name} enum via the Case helper struct",
                    "cpp": (
                        f"// Construct from enum constant\n"
                        f"{ns}::{type_name}Case val({ns}_{type_name}Case_{first_val});\n"
                        f"\n"
                        f"// Get enum value\n"
                        f"{ns}_{type_name}CaseEnum e = {snake}_GetEnum(val);\n"
                        f"\n"
                        f"// Set enum value\n"
                        f"{snake}_SetEnum(val, {ns}_{type_name}Case_{first_val});\n"
                        f"\n"
                        f"// Convert to string\n"
                        f'const char* str = {snake}_ToCstr(val);  // returns "{first_val}"\n'
                        f"\n"
                        f"// Parse from string\n"
                        f"{ns}::{type_name}Case parsed;\n"
                        f'{snake}_SetStrptrMaybe(parsed, "{first_val}");  // returns true on success'
                    ),
                },
                {
                    "description": f"Use {type_name} as a string pkey (for FK fields)",
                    "cpp": (
                        f"{ns}::{type_name} rec;\n"
                        f'rec.{snake} = "{first_val}";  // set pkey directly as string'
                    ),
                },
                {
                    "description": f"Compare / switch on {type_name} enum",
                    "cpp": (
                        f"{ns}::{type_name}Case val({ns}_{type_name}Case_{first_val});\n"
                        f"switch ({snake}_GetEnum(val)) {{\n"
                        + "".join(
                            f"    case {ns}_{type_name}Case_{v}:  // {c}\n"
                            f"        break;\n"
                            for v, c in zip(fconst_values, fconst_comments)
                        )
                        + f"    default: break;\n"
                        f"}}"
                    ),
                },
            ]

        else:
            # --- Struct usage examples ---
            snake = _camel_to_snake(type_name)
            pkey_name = snake  # first field name

            # Build field assignment lines
            assign_lines = []
            for f in data_fields:
                fname = f.get("field", "").rsplit(".", 1)[-1]
                arg = f.get("arg", "")
                reftype = f.get("reftype", "")
                fcomment = f.get("comment", "")
                dflt = f.get("dflt", "")

                if reftype == "Pkey":
                    # FK field — set as string
                    ref_type = arg.rsplit(".", 1)[-1] if "." in arg else arg
                    assign_lines.append(
                        f'rec.{fname} = "some_{_camel_to_snake(ref_type)}";'
                        f"  // FK to {arg}"
                    )
                elif "cstring" in arg or "Smallstr" in arg or "Comment" in arg:
                    assign_lines.append(
                        f'rec.{fname} = "example";'
                        f'  // {fcomment or arg}'
                    )
                elif arg in ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64"):
                    val = dflt if dflt else "0"
                    assign_lines.append(
                        f"rec.{fname} = {val};"
                        f"  // {fcomment or arg}"
                    )
                elif arg == "bool":
                    assign_lines.append(
                        f"rec.{fname} = true;"
                        f"  // {fcomment or arg}"
                    )
                elif arg in ("float", "double"):
                    assign_lines.append(
                        f"rec.{fname} = 0.0;"
                        f"  // {fcomment or arg}"
                    )
                else:
                    assign_lines.append(
                        f"// rec.{fname} = ...;  // {arg} {fcomment}"
                    )

            assign_block = "\n".join(assign_lines)

            type_example["fields"] = [
                {
                    "name": f.get("field", "").rsplit(".", 1)[-1],
                    "arg": f.get("arg", ""),
                    "reftype": f.get("reftype", ""),
                    "comment": f.get("comment", ""),
                }
                for f in fields
            ]

            type_example["code"] = [
                {
                    "description": f"Create and populate a {type_name}",
                    "cpp": (
                        f"{ns}::{type_name} rec;\n"
                        f'{type_name}_Init(rec);  // set defaults\n'
                        f'rec.{pkey_name} = "my_{snake}_id";  // set primary key\n'
                        + (f"{assign_block}\n" if assign_block else "")
                    ),
                },
                {
                    "description": f"Print {type_name} to string (ssim format)",
                    "cpp": (
                        f"algo::cstring out;\n"
                        f"out << rec;  // uses generated operator<<\n"
                        f"// or: {type_name}_Print(rec, out);"
                    ),
                },
            ]

        examples["types"].append(type_example)

    return _json(examples)


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
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="Bootstrap and activate a standalone project directory on startup",
    )

    args = parser.parse_args()

    if not args.openacr_dir.exists():
        print(f"Error: OpenACR dir not found: {args.openacr_dir}", file=sys.stderr)
        sys.exit(1)

    # Set cwd to openacr dir — this is the standard OpenACR working mode.
    # AcrClient.__init__ adds bin/ to PATH so all commands are findable by name.
    os.chdir(args.openacr_dir)

    global _client
    _client = AcrClient(args.openacr_dir)
    print(f"OpenACR MCP server initialized: {args.openacr_dir} (cwd + PATH set)", file=sys.stderr)

    if args.project:
        project = Path(args.project).resolve()
        if not (project / "data").exists():
            result = init_project(str(project))
            print(f"init_project: {result}", file=sys.stderr)
        result = set_project(str(project))
        print(f"set_project: {result}", file=sys.stderr)

    server.run("stdio")


if __name__ == "__main__":
    main()
