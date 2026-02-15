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

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = FastMCP(
    name="openacr",
    instructions=(
        "OpenACR MCP Server. Wraps the OpenACR CLI tools (acr, acr_ed, amc, abt) "
        "to let AI agents query schemas, author new types/fields/enums, generate "
        "C++ code, and discover available functions in generated headers."
    ),
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
def create_ctype(namespace: str, name: str, comment: str = "") -> str:
    """Create a new ctype (struct) in a namespace.

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
    args = ["-ctype", f"{namespace}.{name}"]
    if comment:
        args.extend(["-comment", comment])
    result = client.acr_ed_create(args)
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
    # fconst records are inserted directly via acr
    fconst_key = f"{field}/{value}"
    line = f'dmmeta.fconst  fconst:{fconst_key}  value:"{value}"  comment:"{comment}"'
    proc_args = [str(client.bin_dir / "bash"), "-c",
                 f'echo \'{line}\' | {client.bin_dir}/acr -insert -write']
    # Simpler: use acr -insert
    import subprocess
    import os
    env = os.environ.copy()
    env["PATH"] = f"{client.bin_dir}:{env.get('PATH', '')}"
    try:
        proc = subprocess.run(
            [str(client.bin_dir / "acr"), "-insert", "-write"],
            input=line + "\n",
            cwd=str(client.openacr_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return _json({"ok": True, "fconst": fconst_key})
        return _json({"ok": False, "error": proc.stderr.strip()})
    except Exception as e:
        return _error(str(e))


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
