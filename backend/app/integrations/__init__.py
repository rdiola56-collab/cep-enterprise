"""Drawing and BIM quantity sources. Adapters read files; they never price anything."""
from __future__ import annotations

from typing import Callable

from . import dwg, dxf, ifc, pdf_takeoff, rvt, takeoff_csv
from .base import Measure, ParsedRow, ParsedSource, SourceError

ADAPTERS: dict[str, Callable[..., ParsedSource]] = {
    "takeoff_csv": takeoff_csv.parse,
    "dxf": dxf.parse,
    "dwg": dwg.parse,
    "ifc": ifc.parse,
    "rvt": rvt.parse,
    "pdf_takeoff": pdf_takeoff.parse,
}

# A DWG is measured as the DXF it converts to, and a Revit model as its IFC export, so they share
# those mapping rules and item keys: re-importing the same drawing in the other format updates the same items.
RULE_ADAPTER = {"dwg": "dxf", "rvt": "ifc"}

# Formats whose reading can take minutes: always run as a background job.
BACKGROUND_ONLY = {"rvt"}


def rule_adapter(adapter: str) -> str:
    return RULE_ADAPTER.get(adapter, adapter)


def adapter_info() -> dict:
    """What each format is and whether it works on this server right now (never claimed when it does not)."""
    return {
        "takeoff_csv": {"label": "Takeoff CSV (Bluebeam Markups Summary)", "extensions": [".csv"], "status": "operational",
                        "upload": True},
        "dxf": {"label": "DXF drawing (AutoCAD)", "extensions": [".dxf"], "status": "operational", "upload": True},
        "dwg": {"label": "DWG drawing (AutoCAD, read directly)", "extensions": [".dwg"], "status": dwg.status(),
                "upload": True, "rules": "dxf"},
        "ifc": {"label": "IFC model (BIM)", "extensions": [".ifc"], "status": "operational", "upload": True},
        "rvt": {"label": "Revit model (.rvt, via Autodesk Platform Services)", "extensions": [".rvt"], "status": rvt.status(),
                "upload": True, "rules": "ifc", "background": True},
        "pdf_takeoff": {"label": "PDF drawings (measured on screen in CEP)", "extensions": [".pdf"], "status": "operational",
                        "upload": False, "note": "upload PDFs under Drawings, set the scale and measure there"},
    }


__all__ = ["ADAPTERS", "BACKGROUND_ONLY", "Measure", "ParsedRow", "ParsedSource", "SourceError",
           "adapter_info", "rule_adapter"]
