# OpenACR MCP Server

An MCP (Model Context Protocol) server that wraps the [OpenACR](https://github.com/alexeilebedev/openacr) CLI tools, letting AI agents query schemas, author new types/fields/enums, generate C++ code, and discover the generated API. 44 tools, 148 tests.

## Prerequisites

- Python 3.11+
- [OpenACR](https://github.com/alexeilebedev/openacr) built and installed (with `bin/acr`, `bin/acr_ed`, `bin/amc`, `bin/abt`)

## Setup

```bash
git clone https://github.com/aaslyan/openacr-mcp.git
cd openacr-mcp
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

## Configure for Claude Code

Copy the example config and edit the paths:

```bash
cp .mcp.json.example .mcp.json
```

Edit `.mcp.json` to point to your local paths:

```json
{
  "mcpServers": {
    "openacr": {
      "command": "/path/to/openacr-mcp/venv/bin/python",
      "args": ["-m", "openacr_mcp", "--openacr-dir", "/path/to/openacr"],
      "cwd": "/path/to/openacr-mcp"
    }
  }
}
```

The `--openacr-dir` flag defaults to `~/openacr` if omitted.

## Run Standalone

```bash
python -m openacr_mcp --openacr-dir /path/to/openacr
```

## Run Tests

```bash
PYTHONPATH=. pytest -v
```

The test suite includes unit tests (always run) and integration tests (require OpenACR installed at `~/openacr`, skipped otherwise).

## Tools

### Schema Query & Exploration (17 tools)

| Tool | Description |
|------|-------------|
| `list_namespaces` | List all namespaces |
| `get_namespace_tree` | Full tree view of a namespace (ctypes, fields, fconsts, ssimfiles) |
| `list_ctypes` | List all ctypes (structs) in a namespace |
| `get_ctype` | Get full detail for a ctype with cross-references |
| `list_fields` | List all fields for a ctype |
| `list_fconsts` | List enum constants for a namespace or ctype |
| `list_ssimfiles` | List data tables in a namespace |
| `list_finputs` | List runtime table inputs for an exe target |
| `query` | Run a raw acr query pattern |
| `search` | Search ctypes, fields, and comments by text |
| `get_downstream` | Traverse FK dependencies downward (acr -ndown) |
| `get_upstream` | Traverse FK references upward (acr -nup) |
| `find_unused` | Find unreferenced records for cleanup (acr -unused) |
| `get_record_meta` | Get schema metadata for records (acr -meta) |
| `select_fields` | Query with column projection (acr -field) |
| `get_input_tables` | List all ssimfiles a target reads (acr_in) |
| `visualize_ctype` | ASCII art type structure diagram (amc_vis) |

### Schema Authoring (20 tools)

| Tool | Description |
|------|-------------|
| `create_target` | Create a new namespace (ssimdb, exe, lib, protocol) |
| `create_ctype` | Create a new ctype (auto-creates ssimfile + cfmt for ssimdb) |
| `create_field` | Add a field to a ctype (supports xref, hashfld, sortfld, cascdel, before) |
| `create_fconst` | Add an enum constant to a field |
| `create_finput` | Add runtime table loading for an exe target |
| `create_gstatic` | Add compile-time static table (baked into binary) |
| `create_foutput` | Declare output table for an exe target |
| `create_substr_field` | Create substring field for composite key extraction |
| `create_bitfield` | Create bitfield packed into an integer |
| `create_cppfunc` | Create computed field (C++ expression, not stored) |
| `create_srcfile` | Create source file registered with build target |
| `create_unittest` | Scaffold a unit test function |
| `create_citest` | Scaffold a CI integration test |
| `update_record` | Upsert a record (update if exists, insert if not) |
| `delete_record` | Delete ssim records by pattern |
| `delete_ctype` | Delete ctype with full cascade (fields, ssimfile, cfmt) |
| `delete_field` | Delete field with cascade (fconsts, xrefs) |
| `delete_target` | Delete entire namespace with cascade |
| `rename_record` | Rename a record, propagating references |
| `validate_schema` | Run referential integrity checks (acr -check) |

### Code Generation & Discovery (5 tools)

| Tool | Description |
|------|-------------|
| `run_amc` | Generate C++ code from the schema |
| `run_abt` | Build/compile a target |
| `list_generated_headers` | List generated .h files for a namespace |
| `get_generated_code` | Read a generated header file |
| `get_functions` | Extract structs, enums, and function signatures from generated headers |

### Help & Examples (2 tools)

| Tool | Description |
|------|-------------|
| `get_workflow_guide` | Step-by-step examples for 12 common workflows |
| `get_usage_examples` | Generate C++ usage examples for a namespace's types |

## What `create_ctype` Auto-Creates

For ssimdb namespaces, `create_ctype` automatically inserts:

- **`dmmeta.ssimfile`** — backing ssimfile for data storage
- **`dmmeta.cfmt`** with `read:Y print:Y` — generates `ReadStrptrMaybe`/`Print` functions needed by finput to load data into exe targets

No manual wiring needed.

## Typical Workflow

```
1. create_target("mydb", "ssimdb")        -- create namespace
2. create_ctype("mydb", "Status")         -- create enum type
3. create_fconst("mydb.Status.status", "active")  -- add enum values
4. create_ctype("mydb", "Record")         -- create struct
5. create_field("mydb.Record", "status", "mydb.Status", "Pkey")  -- add FK field
6. run_amc()                              -- generate C++
7. get_functions("mydb")                  -- discover generated API
8. get_usage_examples("mydb")            -- C++ code examples for each type
```

## Architecture

```
openacr-mcp/
├── openacr_mcp/
│   ├── __init__.py
│   ├── __main__.py
│   ├── server.py          # MCP server — 44 tools + embedded workflow knowledge
│   ├── acr_client.py      # Subprocess wrapper for acr/acr_ed/amc/abt/acr_in/amc_vis
│   └── header_parser.py   # C++ header parser for generated code discovery
├── tests/
│   ├── test_server.py     # 111 tests (unit + integration)
│   ├── test_acr_client.py # 20 tests
│   └── test_header_parser.py # 17 tests
├── .mcp.json.example      # Template MCP config (copy to .mcp.json)
└── pyproject.toml
```
