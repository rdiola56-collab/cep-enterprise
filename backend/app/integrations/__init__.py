"""Drawing and BIM quantity sources. Adapters read files; they never price anything."""
from __future__ import annotations

from typing import Callable

from . import dxf, ifc, takeoff_csv
from .base import Measure, ParsedRow, ParsedSource, SourceError

ADAPTERS: dict[str, Callable[..., ParsedSource]] = {
    "takeoff_csv": takeoff_csv.parse,
    "dxf": dxf.parse,
    "ifc": ifc.parse,
}

ADAPTER_INFO = {
    "takeoff_csv": {"label": "Takeoff CSV (Bluebeam Markups Summary)", "extensions": [".csv"], "status": "operational"},
    "dxf": {"label": "DXF drawing (AutoCAD; save DWG as DXF)", "extensions": [".dxf"], "status": "operational"},
    "ifc": {"label": "IFC model (BIM; Revit via IFC export)", "extensions": [".ifc"], "status": "operational"},
    # Listed so the UI can say so plainly; there is no adapter behind these.
    "dwg": {"label": "DWG (native AutoCAD)", "extensions": [".dwg"], "status": "not built: save as DXF"},
    "rvt": {"label": "Revit (.rvt)", "extensions": [".rvt"], "status": "not built: export IFC from Revit"},
    "pdf": {"label": "PDF drawing takeoff", "extensions": [".pdf"], "status": "not built: measure in Bluebeam and export the Markups Summary CSV"},
}

__all__ = ["ADAPTERS", "ADAPTER_INFO", "Measure", "ParsedRow", "ParsedSource", "SourceError"]
