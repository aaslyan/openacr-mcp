"""Tests for header_parser — parsing AMC-generated .h files."""

import pytest
from pathlib import Path

from openacr_mcp.header_parser import (
    parse_header,
    parse_header_file,
    ParsedHeader,
    ParsedEnum,
    ParsedStruct,
    ParsedFunction,
)


# ---------------------------------------------------------------------------
# Unit tests (synthetic header fragments)
# ---------------------------------------------------------------------------

class TestParseEnum:
    def test_simple_enum(self):
        text = """\
// --- algo_BoolEnum

enum algo_BoolEnum {        // algo.Bool.value
     algo_Bool_N       = 0
    ,algo_Bool_Y       = 1
    ,algo_Bool_true    = 1
    ,algo_Bool_false   = 0
};

enum { algo_BoolEnum_N = 4 };
"""
        result = parse_header(text)
        assert len(result.enums) == 1
        e = result.enums[0]
        assert e.name == "algo_BoolEnum"
        assert e.ctype == "algo.Bool.value"
        assert len(e.values) == 4
        assert e.values[0] == ("algo_Bool_N", "0")
        assert e.values[1] == ("algo_Bool_Y", "1")

    def test_hex_values(self):
        text = """\
enum algo_FileFlagsEnum {                // algo.FileFlags.value
     algo_FileFlags_read       = 0x1     // algo.FileFlags.read
    ,algo_FileFlags_write      = 0x2     // algo.FileFlags.write
};
"""
        result = parse_header(text)
        assert len(result.enums) == 1
        e = result.enums[0]
        assert e.values[0] == ("algo_FileFlags_read", "0x1")
        assert e.values[1] == ("algo_FileFlags_write", "0x2")

    def test_multiple_enums(self):
        text = """\
enum Foo {        // ns.Foo.value
     Foo_A   = 0
    ,Foo_B   = 1
};

enum Bar {        // ns.Bar.value
     Bar_X   = 0
};
"""
        result = parse_header(text)
        assert len(result.enums) == 2
        assert result.enums[0].name == "Foo"
        assert result.enums[1].name == "Bar"


class TestParseStruct:
    def test_simple_struct(self):
        text = """\
// --- acr.Err
struct Err { // acr.Err: Error record
    u32             id;      //   0  ID
    algo::cstring   text;    // Error text
    // func:acr.Err..Ctor
    inline               Err() __attribute__((nothrow));
};
"""
        result = parse_header(text)
        assert len(result.structs) == 1
        s = result.structs[0]
        assert s.name == "Err"
        assert s.ctype == "acr.Err"
        assert s.comment == "Error record"
        assert len(s.fields) == 2
        assert s.fields[0].type == "u32"
        assert s.fields[0].name == "id"
        assert s.fields[1].type == "algo::cstring"
        assert s.fields[1].name == "text"
        assert len(s.member_functions) == 1
        assert s.member_functions[0].func_tag == "acr.Err..Ctor"

    def test_struct_with_pointers(self):
        text = """\
struct FQuery { // acr.FQuery
    acr::FCtype*    ctype;   // optional pointer
    // func:acr.FQuery..Ctor
    inline               FQuery() __attribute__((nothrow));
};
"""
        result = parse_header(text)
        assert len(result.structs) == 1
        s = result.structs[0]
        assert len(s.fields) == 1
        assert "FCtype*" in s.fields[0].type


class TestParseFunction:
    def test_free_function(self):
        text = """\
// Return true if index is empty
// func:algo.cstring.ch.EmptyQ
inline bool          ch_EmptyQ(algo::cstring& parent) __attribute__((nothrow));
"""
        result = parse_header(text)
        assert len(result.functions) == 1
        f = result.functions[0]
        assert f.func_tag == "algo.cstring.ch.EmptyQ"
        assert f.return_type == "bool"
        assert f.name == "ch_EmptyQ"
        assert "algo::cstring& parent" in f.params
        assert "Return true if index is empty" in f.comment

    def test_function_with_multi_line_comment(self):
        text = """\
// Reserve space (this may move memory). Insert N element at the end.
// Return aryptr to newly inserted block.
// func:algo.cstring.ch.Addary
algo::aryptr<char>   ch_Addary(algo::cstring& parent, algo::aryptr<char> rhs) __attribute__((nothrow));
"""
        result = parse_header(text)
        assert len(result.functions) == 1
        f = result.functions[0]
        assert f.func_tag == "algo.cstring.ch.Addary"
        assert "Reserve space" in f.comment

    def test_function_without_comment(self):
        text = """\
// func:acr.Err..Init
inline void          Err_Init(acr::Err& parent);
"""
        result = parse_header(text)
        assert len(result.functions) == 1
        f = result.functions[0]
        assert f.func_tag == "acr.Err..Init"
        assert f.return_type == "void"
        assert f.name == "Err_Init"


class TestNamespaceDetection:
    def test_from_gen_path(self):
        result = parse_header("", path="include/gen/algo_gen.h")
        assert result.namespace == "algo"

    def test_from_inl_path(self):
        result = parse_header("", path="include/gen/acr_gen.inl.h")
        assert result.namespace == "acr"

    def test_no_path(self):
        result = parse_header("")
        assert result.namespace == ""


class TestToDict:
    def test_empty_header(self):
        h = ParsedHeader()
        d = h.to_dict()
        assert d["enum_count"] == 0
        assert d["struct_count"] == 0
        assert d["function_count"] == 0
        assert "enums" not in d
        assert "structs" not in d
        assert "functions" not in d

    def test_with_data(self):
        h = ParsedHeader(
            enums=[ParsedEnum(name="E", values=[("A", "0")])],
            structs=[ParsedStruct(name="S", ctype="ns.S")],
            functions=[ParsedFunction(
                func_tag="ns.S..Init",
                return_type="void",
                name="S_Init",
                params="ns::S& parent",
            )],
        )
        d = h.to_dict()
        assert d["enum_count"] == 1
        assert d["struct_count"] == 1
        assert d["function_count"] == 1
        assert d["enums"][0]["name"] == "E"
        assert d["structs"][0]["name"] == "S"
        assert d["functions"][0]["name"] == "S_Init"


# ---------------------------------------------------------------------------
# Integration tests (require ~/openacr)
# ---------------------------------------------------------------------------

OPENACR_DIR = Path.home() / "openacr"
skip_no_openacr = pytest.mark.skipif(
    not (OPENACR_DIR / "include" / "gen" / "algo_gen.h").exists(),
    reason="OpenACR not installed at ~/openacr",
)


@skip_no_openacr
class TestParseRealHeaders:
    def test_algo_gen_h(self):
        path = OPENACR_DIR / "include" / "gen" / "algo_gen.h"
        result = parse_header_file(path)
        assert result.namespace == "algo"
        assert len(result.enums) > 0
        # algo.Bool enum should be present
        bool_enums = [e for e in result.enums if "Bool" in e.name]
        assert len(bool_enums) >= 1
        assert len(bool_enums[0].values) > 0

    def test_algo_gen_h_structs(self):
        path = OPENACR_DIR / "include" / "gen" / "algo_gen.h"
        result = parse_header_file(path)
        assert len(result.structs) > 0
        # cstring struct should be there
        cstring_structs = [s for s in result.structs if s.name == "cstring"]
        assert len(cstring_structs) >= 1

    def test_algo_gen_h_functions(self):
        path = OPENACR_DIR / "include" / "gen" / "algo_gen.h"
        result = parse_header_file(path)
        assert len(result.functions) > 0
        # Some known functions
        func_names = [f.name for f in result.functions]
        assert any("EmptyQ" in name for name in func_names)

    def test_acr_gen_h(self):
        path = OPENACR_DIR / "include" / "gen" / "acr_gen.h"
        result = parse_header_file(path)
        assert result.namespace == "acr"
        assert len(result.enums) > 0
        assert len(result.structs) > 0
