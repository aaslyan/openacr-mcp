"""Subprocess wrapper for OpenACR CLI tools (acr, acr_ed, amc, abt).

All commands run via subprocess.run with cwd set to the openacr directory.
PATH is extended to include {openacr_dir}/bin so sub-commands spawned by
acr_ed can locate each other.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# ssim tuple parser (ported from concept_parser/ssim_importer.py)
# ---------------------------------------------------------------------------

def parse_ssim_line(line: str) -> Optional[tuple[str, dict[str, str]]]:
    """Parse one ssim tuple line into (type_tag, {key: value}).

    Format: ``ns.Table  key:value  key:value ...``
    Quoted values: ``key:"some value"``
    Returns None for blank lines, comments, or report lines.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parts = re.split(r"  +", line)
    if not parts:
        return None

    type_tag = parts[0]
    attrs: dict[str, str] = {}

    for part in parts[1:]:
        colon = part.find(":")
        if colon < 0:
            continue
        key = part[:colon]
        raw_val = part[colon + 1:]
        if raw_val.startswith('"') and raw_val.endswith('"'):
            raw_val = raw_val[1:-1]
        attrs[key] = raw_val

    return type_tag, attrs


def parse_ssim_output(text: str) -> list[dict[str, str]]:
    """Parse acr's ssim output into a list of flat dicts.

    Each dict has a '_type' key with the ssim type tag, plus all key:value attrs.
    Report lines (report.acr) are filtered out.
    """
    results: list[dict[str, str]] = []
    for line in text.splitlines():
        parsed = parse_ssim_line(line)
        if parsed is None:
            continue
        type_tag, attrs = parsed
        if type_tag.startswith("report."):
            continue
        record = {"_type": type_tag}
        record.update(attrs)
        results.append(record)
    return results


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AcrResult:
    """Result of running an acr/acr_ed/amc/abt command."""
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    records: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to a JSON-serializable dict."""
        if self.ok:
            return {
                "ok": True,
                "records": self.records,
                "count": len(self.records),
            }
        return {
            "ok": False,
            "error": self.stderr.strip() or f"Command failed with exit code {self.returncode}",
            "stderr": self.stderr.strip(),
        }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class AcrClient:
    """Subprocess wrapper for OpenACR CLI tools.

    On init, adds {openacr_dir}/bin to os.environ["PATH"] so all
    subprocesses (and their children) can find OpenACR commands by name.
    """

    def __init__(self, openacr_dir: str | Path):
        self.openacr_dir = Path(openacr_dir).resolve()
        self.bin_dir = self.openacr_dir / "bin"
        if not self.bin_dir.exists():
            raise FileNotFoundError(f"OpenACR bin dir not found: {self.bin_dir}")
        # Export bin dir into process PATH so all subprocesses inherit it.
        # This is critical: acr_ed spawns sub-commands (acr_in, amc_vis, acr)
        # that must be findable by name on PATH.
        bin_str = str(self.bin_dir)
        if bin_str not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{bin_str}:{os.environ.get('PATH', '')}"

    def _run(self, args: list[str], *, timeout: int = 30) -> AcrResult:
        """Run a command and return an AcrResult."""
        try:
            proc = subprocess.run(
                args,
                cwd=str(self.openacr_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            return AcrResult(
                ok=False,
                stderr=f"Command not found: {args[0]}",
                returncode=-1,
            )
        except subprocess.TimeoutExpired:
            return AcrResult(
                ok=False,
                stderr=f"Command timed out after {timeout}s",
                returncode=-1,
            )

        result = AcrResult(
            ok=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
        if result.ok:
            result.records = parse_ssim_output(proc.stdout)
        return result

    # -- acr insert --------------------------------------------------------

    def acr_insert(self, line: str) -> AcrResult:
        """Insert a raw ssim record via ``acr -insert -write``."""
        try:
            proc = subprocess.run(
                ["acr", "-insert", "-write"],
                input=line + "\n",
                cwd=str(self.openacr_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            result = AcrResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout,
                stderr=proc.stderr,
                returncode=proc.returncode,
            )
            if result.ok:
                result.records = parse_ssim_output(proc.stdout)
            return result
        except FileNotFoundError:
            return AcrResult(ok=False, stderr="Command not found: acr", returncode=-1)
        except subprocess.TimeoutExpired:
            return AcrResult(ok=False, stderr="Command timed out after 30s", returncode=-1)

    # -- acr queries -------------------------------------------------------

    def acr(self, pattern: str, *, tree: bool = False) -> AcrResult:
        """Run ``acr '<pattern>'`` and parse ssim output."""
        cmd = ["acr", pattern]
        if tree:
            cmd.append("-t")
        return self._run(cmd)

    def acr_raw(self, pattern: str, *, tree: bool = False) -> AcrResult:
        """Run acr and return raw stdout (useful for -t tree output)."""
        cmd = ["acr", pattern]
        if tree:
            cmd.append("-t")
        return self._run(cmd)

    # -- acr_ed operations -------------------------------------------------

    def acr_ed_create(self, args: list[str]) -> AcrResult:
        """Run ``acr_ed -create <args> -write``."""
        cmd = ["acr_ed", "-create"] + args + ["-write"]
        return self._run(cmd, timeout=60)

    def acr_ed_create_target(self, name: str, nstype: str, comment: str = "") -> AcrResult:
        """Run ``acr_ed -create -target <name> -nstype <type> -write``.

        Creates a new namespace/target (e.g. ssimdb, exe, lib).
        """
        cmd = ["acr_ed", "-create", "-target", name, "-nstype", nstype]
        if comment:
            cmd.extend(["-comment", comment])
        cmd.append("-write")
        return self._run(cmd, timeout=60)

    def acr_ed_delete(self, pattern: str) -> AcrResult:
        """Run ``acr -del -write <pattern>``."""
        cmd = ["acr", "-del", "-write", pattern]
        return self._run(cmd, timeout=30)

    def acr_ed_rename(self, old: str, new: str) -> AcrResult:
        """Run ``acr_ed -rename <old> <new> -write``."""
        cmd = ["acr_ed", "-rename", old, new, "-write"]
        return self._run(cmd, timeout=60)

    # -- amc ---------------------------------------------------------------

    def amc(self, namespace: str = "") -> AcrResult:
        """Run ``amc [namespace]`` to generate C++ code."""
        cmd = ["amc"]
        if namespace:
            cmd.append(namespace)
        return self._run(cmd, timeout=120)

    # -- abt ---------------------------------------------------------------

    def abt(self, target: str) -> AcrResult:
        """Run ``abt <target>`` to build."""
        cmd = ["abt", target]
        return self._run(cmd, timeout=300)

    # -- acr graph traversal -----------------------------------------------

    def acr_ndown(self, pattern: str, ndown: int = 1) -> AcrResult:
        """Run ``acr '<pattern>' -ndown <N>`` for downstream dependencies."""
        cmd = ["acr", pattern, "-ndown", str(ndown)]
        return self._run(cmd, timeout=60)

    def acr_nup(self, pattern: str, nup: int = 1) -> AcrResult:
        """Run ``acr '<pattern>' -nup <N>`` for upstream references."""
        cmd = ["acr", pattern, "-nup", str(nup)]
        return self._run(cmd, timeout=60)

    def acr_unused(self, pattern: str) -> AcrResult:
        """Run ``acr '<pattern>' -unused`` to find unreferenced records."""
        cmd = ["acr", pattern, "-unused"]
        return self._run(cmd, timeout=60)

    # -- acr check ---------------------------------------------------------

    def acr_check(self, pattern: str = "%") -> AcrResult:
        """Run ``acr '<pattern>' -check`` for referential integrity validation."""
        cmd = ["acr", pattern, "-check"]
        return self._run(cmd, timeout=60)

    # -- acr_ed structured delete ------------------------------------------

    def acr_ed_delete_ctype(self, ctype: str) -> AcrResult:
        """Run ``acr_ed -del -ctype <ctype> -write`` (cascades fields, ssimfile, etc.)."""
        cmd = ["acr_ed", "-del", "-ctype", ctype, "-write"]
        return self._run(cmd, timeout=60)

    def acr_ed_delete_field(self, field: str) -> AcrResult:
        """Run ``acr_ed -del -field <field> -write``."""
        cmd = ["acr_ed", "-del", "-field", field, "-write"]
        return self._run(cmd, timeout=60)

    def acr_ed_delete_target(self, target: str) -> AcrResult:
        """Run ``acr_ed -del -target <target> -write`` (cascades everything)."""
        cmd = ["acr_ed", "-del", "-target", target, "-write"]
        return self._run(cmd, timeout=60)

    # -- acr_ed scaffolding ------------------------------------------------

    def acr_ed_create_srcfile(self, path: str, target: str) -> AcrResult:
        """Run ``acr_ed -create -srcfile <path> -target <target> -write``."""
        cmd = ["acr_ed", "-create", "-srcfile", path, "-target", target, "-write"]
        return self._run(cmd, timeout=60)

    def acr_ed_create_unittest(self, test_name: str, comment: str = "") -> AcrResult:
        """Run ``acr_ed -create -unittest <ns.func> -write``."""
        cmd = ["acr_ed", "-create", "-unittest", test_name]
        if comment:
            cmd.extend(["-comment", comment])
        cmd.append("-write")
        return self._run(cmd, timeout=60)

    # -- convenience -------------------------------------------------------

    def list_namespaces(self) -> AcrResult:
        """Query all namespaces."""
        return self.acr("dmmeta.ns:%")

    def list_ctypes(self, namespace: str) -> AcrResult:
        """Query all ctypes in a namespace."""
        return self.acr(f"dmmeta.ctype:{namespace}.%")

    def get_ctype(self, ctype: str) -> AcrResult:
        """Get ctype with full xref tree."""
        return self.acr_raw(f"dmmeta.ctype:{ctype}", tree=True)

    def list_fields(self, ctype: str) -> AcrResult:
        """Query all fields for a ctype."""
        return self.acr(f"dmmeta.field:{ctype}.%")

    def get_ns_type(self, namespace: str) -> str | None:
        """Return the nstype for a namespace (e.g. 'ssimdb', 'exe'), or None if not found."""
        result = self.acr(f"dmmeta.ns:{namespace}")
        if result.ok and result.records:
            return result.records[0].get("nstype")
        return None

    def list_generated_headers(self, namespace: str) -> list[Path]:
        """List generated .h files for a namespace."""
        gen_dir = self.openacr_dir / "include" / "gen"
        if not gen_dir.exists():
            return []
        headers = []
        for pattern in [f"{namespace}_gen.h", f"{namespace}_gen.inl.h"]:
            path = gen_dir / pattern
            if path.exists():
                headers.append(path)
        return headers

    def get_generated_code(self, header_path: str) -> str:
        """Read a generated header file. Path is relative to openacr dir."""
        full_path = self.openacr_dir / header_path
        if not full_path.exists():
            raise FileNotFoundError(f"Header not found: {full_path}")
        return full_path.read_text(encoding="utf-8")
