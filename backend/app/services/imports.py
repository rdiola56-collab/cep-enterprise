"""Drawing and BIM quantity import: stage → map → validate → commit through the engine.

    file ──adapter──▶ staged rows (what was measured, with units, and where)
         ──rules────▶ module / item type / formula inputs / floor     (project configuration)
         ──engine───▶ preview: quantity, unit and amount per item     (same path as manual entry)
         ──commit───▶ estimate items, with takeoffs pointing back at the drawing markup or BIM element

Rules:
- nothing is imported until every row is either mapped and valid, or excluded by a person with a reason;
- every quantity is priced by ``EstimatingEngine.calculate_item`` exactly like a manual entry,
  so module validation, formula versioning, units and project prices all apply;
- item keys are stable per source (drawing set or model), so re-importing a revised file
  recalculates the same items instead of adding duplicates; items that disappeared from the
  source are listed and removed only when the person asks for it.
"""
from __future__ import annotations

import fnmatch
import hashlib
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..engine.formula.library import MEASURED_FORMULA_FOR_KIND
from ..engine.service import EstimatingEngine
from ..engine.units import convert, get_unit
from ..integrations import ADAPTERS, SourceError
from ..modules.registry import get_module
from . import estimates as est
from .audit import audit
from .ratebook import SqlAssemblyResolver, SqlFormulaResolver, SqlRateBook

PREVIEW_ERRORS = (LookupError, ValueError)


class ImportBlocked(RuntimeError):
    """The batch cannot be committed yet; the message says which rows need attention."""


# ---------------------------------------------------------------- mapping rules


RULE_FIELDS = ("code", "adapter", "match_group", "match_label", "match_properties", "module", "item_type",
               "assembly_code", "priced_item_code", "quantity_basis", "input_map", "condition", "aggregate", "priority")


def validate_rule(s: Session, project_id: uuid.UUID, data: dict) -> list[str]:
    errors = []
    if data.get("adapter") not in ADAPTERS:
        errors.append(f"adapter must be one of {', '.join(ADAPTERS)}")
    if not data.get("code") or not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", data["code"]):
        errors.append("code: 1-60 letters, digits, '_', '.', '-'")
    if not data.get("match_group"):
        errors.append("match_group is required (a name or a pattern such as 'IfcWall*' or 'A-WALL-*')")
    try:
        mod = get_module(data.get("module", ""))
        it = mod.item_type(data.get("item_type", ""))
    except KeyError as exc:
        return errors + [exc.args[0]]
    if mod.kind != "estimating":
        return errors + [f"module '{mod.name}' does not take estimate items"]
    basis = data.get("quantity_basis", "measured")
    if basis not in ("formula", "measured"):
        errors.append("quantity_basis must be 'formula' or 'measured'")
        return errors
    formulas = SqlFormulaResolver(s, project_id)
    try:
        base = formulas(it.formula_code, None)
        formula = base if basis == "formula" else formulas(MEASURED_FORMULA_FOR_KIND[base.output_kind], None)
    except KeyError as exc:
        return errors + [f"{it.label} cannot be imported as a measured quantity ({exc})"]
    names = {i.name for i in formula.inputs}
    input_map = data.get("input_map") or {}
    if not isinstance(input_map, dict):
        return errors + ["input_map must map formula inputs to measure names"]
    unknown = set(input_map) - names
    if unknown:
        errors.append(f"{formula.code} has no input {', '.join(sorted(unknown))} (inputs: {', '.join(sorted(names))})")
    for spec in formula.inputs:
        if spec.required and spec.default is None and spec.name not in input_map:
            errors.append(f"input '{spec.name}' of {formula.code} must be mapped")
    for k, v in input_map.items():
        if isinstance(v, dict):
            if "value" not in v or "unit" not in v:
                errors.append(f"input '{k}': a constant needs value and unit")
        elif not isinstance(v, str) or not v:
            errors.append(f"input '{k}': give a measure name or a constant {{value, unit}}")
    if data.get("aggregate") and basis != "measured":
        errors.append("only measured quantities can be aggregated (summing dimensions would be wrong)")
    if it.priced_directly != bool(data.get("priced_item_code")):
        errors.append(f"{it.label} " + ("needs a price-book item (priced_item_code)" if it.priced_directly
                                        else "is priced through an assembly, not a price-book item"))
    if s.scalar(select(m.ImportMappingRule.id).where(m.ImportMappingRule.project_id == project_id,
                                                     m.ImportMappingRule.code == data.get("code"))):
        errors.append(f"a rule with code '{data.get('code')}' already exists")
    return errors


def create_rule(s: Session, project_id: uuid.UUID, data: dict, actor: str) -> m.ImportMappingRule:
    data = {k: data.get(k) for k in RULE_FIELDS if data.get(k) is not None}
    data.setdefault("quantity_basis", "measured")
    errors = validate_rule(s, project_id, data)
    if errors:
        raise ValueError("; ".join(errors))
    rule = m.ImportMappingRule(project_id=project_id, match_properties=data.pop("match_properties", {}) or {}, **data)
    s.add(rule)
    s.flush()
    audit(s, project_id, "import_mapping_rules", rule.id, "create", actor, {"code": rule.code})
    return rule


def deactivate_rule(s: Session, project_id: uuid.UUID, rule_id: uuid.UUID, actor: str) -> None:
    rule = s.get(m.ImportMappingRule, rule_id)
    if rule is None or rule.project_id != project_id:
        raise LookupError("rule not found")
    rule.is_active = False  # kept: committed rows reference it, and their snapshot records what it said
    audit(s, project_id, "import_mapping_rules", rule.id, "deactivate", actor, {"code": rule.code})


def rules(s: Session, project_id: uuid.UUID, adapter: str | None = None, active_only: bool = True) -> list[m.ImportMappingRule]:
    q = select(m.ImportMappingRule).where(m.ImportMappingRule.project_id == project_id)
    if adapter:
        q = q.where(m.ImportMappingRule.adapter == adapter)
    if active_only:
        q = q.where(m.ImportMappingRule.is_active.is_(True))
    return list(s.scalars(q.order_by(m.ImportMappingRule.priority, m.ImportMappingRule.code)))


def rule_dict(r: m.ImportMappingRule) -> dict:
    return {"id": str(r.id), **{k: getattr(r, k) for k in RULE_FIELDS}, "is_active": r.is_active}


def _matches(rule: m.ImportMappingRule, row: m.ImportRow) -> bool:
    if not fnmatch.fnmatchcase(row.group.lower(), rule.match_group.lower()):
        return False
    if rule.match_label and not fnmatch.fnmatchcase((row.label or "").lower(), rule.match_label.lower()):
        return False
    for k, v in (rule.match_properties or {}).items():
        if str(row.properties.get(k, "")).lower() != str(v).lower():
            return False
    return True


# ---------------------------------------------------------------- staging


def stage(s: Session, version_id: uuid.UUID, *, adapter: str, file_name: str, content: bytes, source_name: str,
          source_revision: str, options: dict | None = None, building_id: uuid.UUID | None = None,
          actor: str, project_id: uuid.UUID | None = None) -> m.ImportBatch:
    v = est._version(s, version_id, project_id)
    if v.status != "draft":
        raise est.EstimateLocked(f"estimate version {v.version_no} is {v.status}; import into a draft revision")
    if adapter not in ADAPTERS:
        raise ValueError(f"no importer for '{adapter}'")
    if not source_name.strip() or not source_revision.strip():
        raise ValueError("name the drawing set or model and its revision: they identify the items on re-import")
    options = dict(options or {})
    parsed = ADAPTERS[adapter](content, options)  # SourceError propagates: the file is refused as a whole

    batch = m.ImportBatch(project_id=v.project_id, estimate_version_id=v.id, adapter=adapter, source_name=source_name.strip(),
                          source_revision=source_revision.strip(), file_name=file_name,
                          file_sha256=hashlib.sha256(content).hexdigest(), status="staged",
                          options={**options, "building_id": str(building_id) if building_id else None},
                          summary={"info": parsed.info, "warnings": parsed.warnings}, created_by=actor)
    s.add(batch)
    s.flush()
    for n, r in enumerate(parsed.rows, start=1):
        row = m.ImportRow(project_id=v.project_id, batch_id=batch.id, row_no=n, source_ref=r.source_ref[:200],
                          group=r.group[:200], label=(r.label or "")[:300], level=(r.level or None) and r.level[:120],
                          measures={k: x.to_dict() for k, x in r.measures.items()}, properties=r.properties,
                          status="invalid" if r.messages else "unmapped", read_errors=list(r.messages),
                          messages=list(r.messages))
        if adapter == "ifc":
            row.bim_element_id = _bim_element(s, batch, r).id
        else:
            ref = m.DrawingReference(project_id=v.project_id, drawing_name=batch.source_name, revision=batch.source_revision,
                                     file_uri=file_name, source_markup_id=r.source_ref[:200],
                                     metadata_json={"adapter": adapter, "group": r.group, "label": r.label, "level": r.level,
                                                    "batch_id": str(batch.id)})
            s.add(ref)
            s.flush()
            row.drawing_reference_id = ref.id
        s.add(row)
    s.flush()
    apply_rules(s, batch.id)
    audit(s, v.project_id, "import_batches", batch.id, "stage", actor,
          {"adapter": adapter, "file": file_name, "sha256": batch.file_sha256, "rows": len(parsed.rows)})
    return batch


def _bim_element(s: Session, batch: m.ImportBatch, r) -> m.BimElement:
    el = s.scalars(select(m.BimElement).where(m.BimElement.project_id == batch.project_id,
                                              m.BimElement.model_name == batch.source_name,
                                              m.BimElement.model_revision == batch.source_revision,
                                              m.BimElement.element_guid == r.source_ref)).first()
    props = {"label": r.label, "level": r.level, "properties": r.properties,
             "quantities": {k: x.to_dict() for k, x in r.measures.items()}}
    if el is None:
        el = m.BimElement(project_id=batch.project_id, model_name=batch.source_name, model_revision=batch.source_revision,
                          element_guid=r.source_ref, category=r.group, properties=props)
        s.add(el)
        s.flush()
    else:
        el.category, el.properties = r.group, props
    return el


def _slug(text: str, n: int) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")[:n] or "source"


def _floors(s: Session, project_id: uuid.UUID, building_id: str | None) -> dict[str, list[m.Floor]]:
    q = select(m.Floor).where(m.Floor.project_id == project_id)
    if building_id:
        q = q.where(m.Floor.building_id == uuid.UUID(building_id))
    out: dict[str, list[m.Floor]] = defaultdict(list)
    for f in s.scalars(q):
        for key in {f.name.strip().lower(), f.code.strip().lower()}:
            out[key].append(f)
    return out


def apply_rules(s: Session, batch_id: uuid.UUID) -> m.ImportBatch:
    """(Re)map every row that is not excluded and price the planned items through the engine, without saving them."""
    batch = s.get(m.ImportBatch, batch_id)
    if batch.status != "staged":
        raise ImportBlocked(f"batch is {batch.status}")
    v = s.get(m.EstimateVersion, batch.estimate_version_id)
    active = rules(s, batch.project_id, batch.adapter)
    floors = _floors(s, batch.project_id, batch.options.get("building_id"))
    formulas = SqlFormulaResolver(s, batch.project_id)
    engine = EstimatingEngine(formulas=formulas, assemblies=SqlAssemblyResolver(s, batch.project_id),
                              rates=SqlRateBook(s, batch.project_id))
    prefix = f"{batch.adapter}:{_slug(batch.source_name, 40)}"
    rows = list(s.scalars(select(m.ImportRow).where(m.ImportRow.batch_id == batch.id).order_by(m.ImportRow.row_no)))

    groups: dict[str, list[tuple[m.ImportRow, m.ImportMappingRule, dict]]] = defaultdict(list)
    for row in rows:
        if row.status == "excluded":
            continue
        row.rule_id = row.rule_snapshot = row.item_key = row.floor_id = row.preview = None
        if row.read_errors:
            row.status, row.messages = "invalid", list(row.read_errors)
            continue
        row.messages = []
        rule = next((r for r in active if _matches(r, row)), None)
        if rule is None:
            row.status = "unmapped"
            continue
        row.rule_id, row.rule_snapshot = rule.id, rule_dict(rule)
        notes = []
        floor = None
        if row.level:
            cands = floors.get(row.level.strip().lower(), [])
            if len(cands) == 1:
                floor = cands[0]
            elif len(cands) > 1:
                notes.append(f"note: level '{row.level}' matches floors in several buildings; choose a building for the import")
            else:
                notes.append(f"note: level '{row.level}' is not a floor in the project WBS; the item has no floor")
        row.floor_id = floor.id if floor else None
        inputs, errors = _inputs(formulas, rule, row)
        if errors:
            row.status, row.messages = "invalid", errors + notes
            continue
        row.messages = notes
        key = (f"{prefix}:{rule.code}@{floor.code if floor else '-'}" if rule.aggregate else f"{prefix}:{_slug(row.source_ref, 70)}")
        if len(key) > 120:
            row.status, row.messages = "invalid", [f"item key too long ({len(key)} characters)"] + notes
            continue
        row.item_key = key
        row.status = "mapped"
        groups[key].append((row, rule, inputs))

    planned = {}
    for key, members in groups.items():
        row, rule, _ = members[0]
        if len({r.rule_id for r, _, _ in members}) > 1:
            for r, _, _ in members:
                r.status, r.messages = "invalid", [f"source id '{r.source_ref}' appears twice with different rules"]
            continue
        inputs = _sum_inputs([i for _, _, i in members]) if rule.aggregate else members[0][2]
        if not rule.aggregate and len(members) > 1:
            for r, _, _ in members:
                r.status, r.messages = "invalid", [f"source id '{r.source_ref}' appears {len(members)} times in the file"]
            continue
        try:
            item = engine.calculate_item(
                item_id=key, module=rule.module, item_type=rule.item_type, inputs=est._raw_inputs(inputs), as_of=v.pricing_date,
                condition=rule.condition, assembly_code=rule.assembly_code, priced_item_code=rule.priced_item_code,
                quantity_basis=rule.quantity_basis)
        except PREVIEW_ERRORS as exc:
            for r, _, _ in members:
                msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
                r.status, r.messages = "invalid", [msg] + r.messages
            continue
        b = item.breakdown
        preview = {"quantity": format(b.quantity.normalize(), "f"), "unit": b.unit,
                   "amount": format(b.total_amount.normalize(), "f"), "unit_rate": format(b.unit_rate.normalize(), "f"),
                   "assembly": item.assembly_code, "methodology": item.quantity_source.methodology,
                   "inputs": inputs, "rows": len(members)}
        for r, _, _ in members:
            r.preview = preview
        planned[key] = preview

    existing = {it.item_key for it in est.items(s, v.id) if it.item_key.startswith(prefix + ":")}
    counts = defaultdict(int)
    for row in rows:
        counts[row.status] += 1
    total = sum((Decimal(p["amount"]) for p in planned.values()), Decimal(0))
    batch.summary = {**{k: x for k, x in batch.summary.items() if k in ("info", "warnings")},
                     "counts": dict(counts), "items": len(planned), "new_items": len(set(planned) - existing),
                     "updated_items": len(set(planned) & existing), "stale_items": sorted(existing - set(planned)),
                     "total_amount": format(total.normalize(), "f"),
                     "ready": counts["unmapped"] == 0 and counts["invalid"] == 0 and bool(planned)}
    s.flush()
    return batch


def _inputs(formulas, rule: m.ImportMappingRule, row: m.ImportRow) -> tuple[dict, list[str]]:
    errors, inputs = [], {}
    for name, src in rule.input_map.items():
        if isinstance(src, dict):
            inputs[name] = {"value": str(src["value"]), "unit": src["unit"]}
            continue
        measure = row.measures.get(src)
        if measure is None:
            have = ", ".join(sorted(row.measures)) or "none"
            errors.append(f"no '{src}' measured for this {row.group} (available: {have})")
            continue
        inputs[name] = dict(measure)
    return inputs, errors


def _sum_inputs(all_inputs: list[dict]) -> dict:
    """Sum measured quantities of an aggregated item, in the first row's unit (exact conversion)."""
    out: dict[str, dict] = {}
    for inputs in all_inputs:
        for name, q in inputs.items():
            if name not in out:
                out[name] = {"value": Decimal(q["value"]), "unit": q["unit"]}
            elif name == "measured":
                out[name]["value"] += convert(Decimal(q["value"]), q["unit"], out[name]["unit"])
    return {k: {"value": format(Decimal(x["value"]).normalize(), "f"), "unit": get_unit(x["unit"]).symbol} for k, x in out.items()}


def set_excluded(s: Session, batch_id: uuid.UUID, row_ids: list[uuid.UUID], excluded: bool, reason: str, actor: str) -> m.ImportBatch:
    batch = s.get(m.ImportBatch, batch_id)
    if batch.status != "staged":
        raise ImportBlocked(f"batch is {batch.status}")
    if excluded and not reason.strip():
        raise ValueError("give a reason for excluding rows: the quantities will not be in the estimate")
    for row in s.scalars(select(m.ImportRow).where(m.ImportRow.batch_id == batch_id, m.ImportRow.id.in_(row_ids))):
        if excluded:
            row.status, row.messages = "excluded", [f"excluded by {actor}: {reason.strip()}"]
            row.item_key = row.preview = None
        elif row.status == "excluded":
            row.status, row.messages = "unmapped", []
    s.flush()
    audit(s, batch.project_id, "import_batches", batch.id, "exclude" if excluded else "include", actor,
          {"rows": [str(r) for r in row_ids], "reason": reason})
    return apply_rules(s, batch_id)


# ---------------------------------------------------------------- commit


def commit(s: Session, batch_id: uuid.UUID, actor: str, remove_missing: bool = False) -> m.ImportBatch:
    batch = apply_rules(s, batch_id)  # rules or prices may have changed since staging: re-check everything
    c = batch.summary["counts"]
    if c.get("unmapped") or c.get("invalid"):
        raise ImportBlocked(f"{c.get('unmapped', 0)} rows have no mapping rule and {c.get('invalid', 0)} are invalid; "
                            "add rules, fix the source, or exclude them with a reason")
    if not batch.summary["items"]:
        raise ImportBlocked("nothing to import")
    rows = list(s.scalars(select(m.ImportRow).where(m.ImportRow.batch_id == batch.id, m.ImportRow.status == "mapped")
                          .order_by(m.ImportRow.row_no)))
    by_key: dict[str, list[m.ImportRow]] = defaultdict(list)
    for row in rows:
        by_key[row.item_key].append(row)
    floors = {f.id: f for f in s.scalars(select(m.Floor).where(m.Floor.project_id == batch.project_id))}
    for key, members in by_key.items():
        first = members[0]
        rule = first.rule_snapshot
        floor = floors.get(first.floor_id)
        single = len(members) == 1
        it = get_module(rule["module"]).item_type(rule["item_type"])
        what = first.label or first.group if single else f"{rule['code']} ({len(members)} {batch.adapter} rows)"
        est.calculate_item(
            s, batch.estimate_version_id, item_key=key, module=rule["module"], item_type=rule["item_type"],
            inputs=first.preview["inputs"], description=f"{it.label}: {what}"[:300], condition=rule["condition"],
            assembly_code=rule["assembly_code"], priced_item_code=rule["priced_item_code"],
            quantity_basis=rule["quantity_basis"],
            building_id=floor.building_id if floor else None, floor_id=floor.id if floor else None,
            source=f"import:{batch.adapter}",
            drawing_reference_id=first.drawing_reference_id if single else None,
            bim_element_id=first.bim_element_id if single else None,
            actor=actor, project_id=batch.project_id)
        for r in members:
            r.status = "imported"
    removed = []
    if remove_missing:
        for key in batch.summary["stale_items"]:
            est.remove_item(s, batch.estimate_version_id, key, actor=actor)
            removed.append(key)
    batch.status = "committed"
    batch.committed_at = datetime.now(timezone.utc)
    batch.summary = {**batch.summary, "removed_items": removed, "committed_by": actor}
    s.flush()
    audit(s, batch.project_id, "import_batches", batch.id, "commit", actor,
          {"items": len(by_key), "removed": removed, "total_amount": batch.summary["total_amount"]})
    return batch


def discard(s: Session, batch_id: uuid.UUID, actor: str) -> None:
    batch = s.get(m.ImportBatch, batch_id)
    if batch.status != "staged":
        raise ImportBlocked(f"batch is {batch.status}")
    batch.status = "discarded"
    audit(s, batch.project_id, "import_batches", batch.id, "discard", actor, {})


def batch_dict(b: m.ImportBatch) -> dict:
    return {"id": str(b.id), "estimate_version_id": str(b.estimate_version_id), "adapter": b.adapter,
            "source_name": b.source_name, "source_revision": b.source_revision, "file_name": b.file_name,
            "file_sha256": b.file_sha256, "status": b.status, "summary": b.summary, "created_by": b.created_by,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "committed_at": b.committed_at.isoformat() if b.committed_at else None}


def row_dict(r: m.ImportRow) -> dict:
    return {"id": str(r.id), "row_no": r.row_no, "source_ref": r.source_ref, "group": r.group, "label": r.label,
            "level": r.level, "measures": r.measures, "properties": r.properties, "status": r.status,
            "messages": r.messages, "rule_code": (r.rule_snapshot or {}).get("code"), "item_key": r.item_key,
            "floor_id": str(r.floor_id) if r.floor_id else None, "preview": r.preview}


def batch_rows(s: Session, batch_id: uuid.UUID, status: str | None = None) -> list[m.ImportRow]:
    q = select(m.ImportRow).where(m.ImportRow.batch_id == batch_id)
    if status:
        q = q.where(m.ImportRow.status == status)
    return list(s.scalars(q.order_by(m.ImportRow.row_no)))


def get_batch(s: Session, project_id: uuid.UUID, batch_id: uuid.UUID) -> m.ImportBatch:
    b = s.get(m.ImportBatch, batch_id)
    if b is None or b.project_id != project_id:
        raise LookupError("import not found")
    return b


__all__ = ["ImportBlocked", "SourceError", "apply_rules", "batch_dict", "batch_rows", "commit", "create_rule",
           "deactivate_rule", "discard", "get_batch", "row_dict", "rule_dict", "rules", "set_excluded", "stage"]
