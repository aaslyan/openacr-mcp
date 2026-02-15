# OpenACR MCP Server

An MCP (Model Context Protocol) server that wraps the [OpenACR](https://github.com/alexeilebedev/openacr) CLI tools, letting AI agents query schemas, author new types/fields/enums, generate C++ code, and discover the generated API.

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

### Schema Query (read-only)

| Tool | Description |
|------|-------------|
| `list_namespaces` | List all namespaces |
| `list_ctypes` | List all ctypes (structs) in a namespace |
| `get_ctype` | Get full detail for a ctype with cross-references |
| `list_fields` | List all fields for a ctype |
| `query` | Run a raw acr query pattern |
| `search` | Search ctypes, fields, and comments by text |

### Schema Authoring

| Tool | Description |
|------|-------------|
| `create_target` | Create a new namespace (ssimdb, exe, lib, protocol) |
| `create_ctype` | Create a new ctype (auto-creates ssimfile + cfmt for ssimdb) |
| `create_field` | Add a field to a ctype |
| `create_fconst` | Add an enum constant to a field |
| `delete_record` | Delete ssim records by pattern |
| `rename_record` | Rename a record, propagating references |

### Code Generation & Discovery

| Tool | Description |
|------|-------------|
| `run_amc` | Generate C++ code from the schema |
| `run_abt` | Build/compile a target |
| `list_generated_headers` | List generated .h files for a namespace |
| `get_generated_code` | Read a generated header file |
| `get_functions` | Extract structs, enums, and function signatures from generated headers |

### Help

| Tool | Description |
|------|-------------|
| `get_workflow_guide` | Detailed step-by-step examples for common workflows |
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
│   ├── server.py          # MCP server — 20 tools + embedded workflow knowledge
│   ├── acr_client.py      # Subprocess wrapper for acr/acr_ed/amc/abt
│   └── header_parser.py   # C++ header parser for generated code discovery
├── tests/
│   ├── test_server.py     # 41 tests (unit + integration)
│   ├── test_acr_client.py # 20 tests
│   └── test_header_parser.py # 17 tests
├── .mcp.json.example      # Template MCP config (copy to .mcp.json)
└── pyproject.toml
```
