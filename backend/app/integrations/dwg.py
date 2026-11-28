"""DWG drawings (native AutoCAD), read directly.

The DWG file is converted to DXF by a separate reader program, by default GNU
LibreDWG (tools/dwg/dwg2dxf.mjs, run with Node.js), and the result is measured by
the DXF adapter. So a DWG and a DXF of the same drawing give the same rows,
units and entity handles, and one set of mapping rules (the DXF rules) serves both.

Another converter can be used instead (for example ODA File Converter behind a
wrapper script): set CEP_DWG_CONVERTER to a command that reads DWG on stdin and
writes DXF on stdout. If no reader is installed, DWG files are refused with that
reason; they are never guessed at.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from . import dxf
from .base import ParsedSource, SourceError

TOOL = Path(__file__).resolve().parents[2] / "tools" / "dwg" / "dwg2dxf.mjs"
TIMEOUT_S = int(os.environ.get("CEP_DWG_TIMEOUT", "300"))

# File signature (first 6 bytes) → AutoCAD release. LibreDWG reads R13 onwards.
VERSIONS = {
    "AC1012": "R13", "AC1014": "R14", "AC1015": "2000", "AC1018": "2004", "AC1021": "2007",
    "AC1024": "2010", "AC1027": "2013", "AC1032": "2018",
}
OLDER = {"MC0.0", "AC1.2", "AC1.4", "AC1.50", "AC2.10", "AC1001", "AC1002", "AC1003", "AC1004", "AC1006", "AC1009"}


def command() -> list[str] | None:
    custom = os.environ.get("CEP_DWG_CONVERTER")
    if custom:
        return shlex.split(custom)
    node = shutil.which("node")
    if node and TOOL.exists() and (TOOL.parent / "node_modules" / "@mlightcad" / "libredwg-web").is_dir():
        return [node, str(TOOL)]
    return None


@lru_cache(maxsize=4)
def _reader_name(cmd: tuple[str, ...]) -> str:
    if os.environ.get("CEP_DWG_CONVERTER"):
        return os.environ.get("CEP_DWG_CONVERTER_NAME") or Path(cmd[0]).name
    try:
        out = subprocess.run([*cmd, "--version"], capture_output=True, timeout=60, check=True)
        return out.stdout.decode().strip() or "LibreDWG"
    except (OSError, subprocess.SubprocessError):
        return "LibreDWG"


def status() -> str:
    return "operational" if command() else "not available on this server: the DWG reader (Node.js + tools/dwg) is not installed"


def signature(content: bytes) -> str:
    return content[:6].decode("ascii", "replace")


def parse(content: bytes, options: dict | None = None) -> ParsedSource:
    sig = signature(content)
    if sig not in VERSIONS:
        if any(sig.startswith(o) for o in OLDER):
            raise SourceError(f"this DWG is from before AutoCAD R13 ({sig}); open and save it in a newer release first")
        raise SourceError("not a DWG file (its header is not an AutoCAD DWG signature)")
    cmd = command()
    if cmd is None:
        raise SourceError("DWG reading is not installed on this server (Node.js and tools/dwg are needed); "
                          "save the drawing as DXF or ask an administrator to install the DWG reader")
    try:
        done = subprocess.run(cmd, input=content, capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise SourceError(f"the DWG reader did not finish within {TIMEOUT_S} s") from None
    except OSError as exc:
        raise SourceError(f"the DWG reader could not be started: {exc}") from None
    if done.returncode != 0 or not done.stdout:
        reason = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise SourceError(f"the DWG reader could not read this file ({reason[-1] if reason else 'no output'})")
    parsed = dxf.parse(done.stdout, options)
    parsed.adapter = "dwg"
    parsed.info = {**parsed.info, "dwg_version": sig, "autocad_release": VERSIONS[sig], "read_by": _reader_name(tuple(cmd))}
    return parsed
