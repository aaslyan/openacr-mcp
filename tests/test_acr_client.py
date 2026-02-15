"""Tests for acr_client — ssim parser and subprocess wrapper."""

import pytest
from pathlib import Path

from openacr_mcp.acr_client import (
    parse_ssim_line,
    parse_ssim_output,
    AcrResult,
    AcrClient,
)


# ---------------------------------------------------------------------------
# ssim parser tests (pure, no subprocess)
# ---------------------------------------------------------------------------

class TestParseSsimLine:
    def test_simple_record(self):
        line = 'dmmeta.ns  ns:algo  nstype:protocol  license:GPL  comment:"Basic types"'
        result = parse_ssim_line(line)
        assert result is not None
        tag, attrs = result
        assert tag == "dmmeta.ns"
        assert attrs["ns"] == "algo"
        assert attrs["nstype"] == "protocol"
        assert attrs["comment"] == "Basic types"

    def test_unquoted_values(self):
        line = "dmmeta.ns  ns:acr  nstype:exe  license:GPL  comment:Algo Cross-Reference"
        result = parse_ssim_line(line)
        assert result is not None
        tag, attrs = result
        assert attrs["ns"] == "acr"
        assert attrs["nstype"] == "exe"

    def test_empty_values(self):
        line = 'dmmeta.ns  ns:""  nstype:protocol  license:GPL  comment:""'
        result = parse_ssim_line(line)
        assert result is not None
        _, attrs = result
        assert attrs["ns"] == ""
        assert attrs["comment"] == ""

    def test_blank_line(self):
        assert parse_ssim_line("") is None
        assert parse_ssim_line("   ") is None

    def test_comment_line(self):
        assert parse_ssim_line("# this is a comment") is None

    def test_field_record(self):
        line = 'dmmeta.field  field:algo.Bool.value  arg:u8  reftype:Val  dflt:false  comment:""'
        result = parse_ssim_line(line)
        assert result is not None
        _, attrs = result
        assert attrs["field"] == "algo.Bool.value"
        assert attrs["arg"] == "u8"
        assert attrs["reftype"] == "Val"
        assert attrs["dflt"] == "false"


class TestParseSsimOutput:
    def test_multi_record_output(self):
        text = (
            'dmmeta.ns  ns:algo  nstype:protocol  license:GPL  comment:""\n'
            'dmmeta.ns  ns:acr  nstype:exe  license:GPL  comment:""\n'
            'report.acr  n_select:2  n_insert:0  n_delete:0  n_ignore:0  n_update:0  n_file_mod:0\n'
        )
        records = parse_ssim_output(text)
        assert len(records) == 2
        assert records[0]["_type"] == "dmmeta.ns"
        assert records[0]["ns"] == "algo"
        assert records[1]["ns"] == "acr"

    def test_report_lines_filtered(self):
        text = 'report.acr  n_select:0  n_insert:0\n'
        records = parse_ssim_output(text)
        assert len(records) == 0

    def test_empty_output(self):
        assert parse_ssim_output("") == []
        assert parse_ssim_output("\n\n") == []


class TestAcrResult:
    def test_ok_result(self):
        r = AcrResult(ok=True, records=[{"_type": "dmmeta.ns", "ns": "algo"}])
        d = r.to_dict()
        assert d["ok"] is True
        assert d["count"] == 1
        assert d["records"][0]["ns"] == "algo"

    def test_error_result(self):
        r = AcrResult(ok=False, stderr="not found", returncode=1)
        d = r.to_dict()
        assert d["ok"] is False
        assert "not found" in d["error"]


# ---------------------------------------------------------------------------
# Integration tests (require ~/openacr)
# ---------------------------------------------------------------------------

OPENACR_DIR = Path.home() / "openacr"
skip_no_openacr = pytest.mark.skipif(
    not (OPENACR_DIR / "bin" / "acr").exists(),
    reason="OpenACR not installed at ~/openacr",
)


@skip_no_openacr
class TestAcrClientIntegration:
    @pytest.fixture
    def client(self):
        return AcrClient(OPENACR_DIR)

    def test_list_namespaces(self, client):
        result = client.list_namespaces()
        assert result.ok
        assert len(result.records) > 0
        ns_names = [r["ns"] for r in result.records]
        assert "algo" in ns_names
        assert "dmmeta" in ns_names

    def test_list_ctypes(self, client):
        result = client.list_ctypes("algo")
        assert result.ok
        assert len(result.records) > 0
        ctypes = [r.get("ctype", "") for r in result.records]
        assert "algo.Bool" in ctypes

    def test_list_fields(self, client):
        result = client.list_fields("algo.Bool")
        assert result.ok
        assert len(result.records) >= 1
        field_names = [r.get("field", "") for r in result.records]
        assert any("value" in f for f in field_names)

    def test_get_ctype_tree(self, client):
        result = client.get_ctype("algo.Bool")
        assert result.ok
        assert "algo.Bool" in result.stdout

    def test_raw_query(self, client):
        result = client.acr("dmmeta.ns:algo")
        assert result.ok
        assert len(result.records) == 1
        assert result.records[0]["ns"] == "algo"

    def test_invalid_pattern(self, client):
        result = client.acr("nonexistent.table:xyz")
        # acr returns 0 even for no matches, just empty
        assert len(result.records) == 0

    def test_list_generated_headers(self, client):
        headers = client.list_generated_headers("algo")
        assert len(headers) >= 1
        assert any("algo_gen.h" in str(h) for h in headers)

    def test_get_generated_code(self, client):
        code = client.get_generated_code("include/gen/algo_gen.h")
        assert "Generated by AMC" in code
        assert "algo_BoolEnum" in code

    def test_get_generated_code_not_found(self, client):
        with pytest.raises(FileNotFoundError):
            client.get_generated_code("include/gen/nonexistent_gen.h")
