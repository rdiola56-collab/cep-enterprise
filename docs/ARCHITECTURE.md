# CEP: ENTERPRISE architecture

CEP is a **modular monolith**: one deployable backend (FastAPI), one PostgreSQL
database, one frontend (Next.js). Layers talk through Python interfaces, not
network calls. There are no microservices at this stage.

## Layers and where they live

| # | Layer | Code | Status |
|---|-------|------|--------|
| 1 | Presentation | `frontend/`, `backend/app/api/` (HTTP boundary, permissions) | Sign-in, projects, estimate views (items, BOQ, consolidation, approve), formula library, module pages |
| 2 | Project management | `services/projects.py`, `services/users.py`, `auth/` | Projects, members and roles, WBS; WBS has API only, no editing screen |
| 3 | Estimating modules | `app/modules/registry.py` | All 19 configured: 16 estimating (39 item types), 2 library, 1 reporting |
| 4 | Universal quantity calculation | `app/engine/units.py`, `app/engine/formula/` | Built and tested |
| 5 | Construction assembly | `app/engine/assembly.py`, `services/library.py` | Built and tested |
| 6 | Cost calculation | `app/engine/costing.py`, `app/engine/rates.py` | Built and tested |
| 7 | Database and data access | `app/db/`, `services/ratebook.py`, `migrations/` | Built and tested |
| 8 | Drawing and BIM integration | `drawing_references`, `bim_elements` tables only | **Not built** (phase 5) |
| 9 | Reporting and export | `services/estimates.py` (BOQ, cost and resource summaries) | Data built; Excel/PDF export **not built** |

## The one calculation path

Every module, API call and future drawing/BIM import goes through
`EstimatingEngine.calculate_item` (`app/engine/service.py`):

```
measured inputs (any unit)
  → module validation rules            (modules/registry.py: configuration only)
  → shared, versioned formula          (engine/formula: controlled parser + dimensional check)
  → work quantity in the assembly unit
  → assembly explosion                 (engine/assembly.py)
  → materials: consumption × qty × (1 + wastage), priced per price-unit
  → labor: qty ÷ productivity(condition) = crew-days → hours per labor class × rate
  → equipment: hours (per unit, or tied to a labor task) × rental + fuel + operating
  → rate analysis: direct cost + configurable markups (overhead, contingency, profit, tax…)
  → CostBreakdown with a source reference for every rate used
```

Modules contain **no arithmetic**. A module declares item types, which shared
formula measures each one, which assembly prices it, and tighter validation
rules. Adding a module is configuration.

## Formula engine

- Grammar: numbers, variables, `+ − × ÷ ^`, parentheses and a whitelist of
  functions (`ceil floor round abs min max sqrt`). Typographic `× ÷ −` accepted.
- Never uses `eval`, `exec` or Python's `ast`. Length, node-count and depth limits.
- Every input declares a dimension (length, area, volume, mass, linear mass…).
  The formula is dimension-checked when saved: `length + area` is rejected, and
  an expression that yields area cannot be declared as a volume.
- Publishing a change creates a new immutable version with a content hash. Each
  estimate item records the formula version used, the inputs as entered, their
  SI values, and the methodology with values substituted, e.g.
  `(([6 m] × [300 mm]) × [600 mm]) × [10 pcs]`.

## Units and precision

- Exact conversion factors (1 in = 0.0254 m, 1 lb = 0.45359237 kg, 1 US gal =
  3.785411784 L). Decimal arithmetic at 34 significant digits; no floats.
- Rounding happens only at reporting (project `money_places`, ROUND_HALF_UP).
  Snapshots keep full precision so totals reconcile exactly.

## Rates and project isolation

- Library tables carry `project_id`: NULL = master library, a value = that
  project's own record. For each code, if a project has its own records only
  those are used; otherwise the master applies. Another project's records are
  never visible (`services/ratebook.py`).
- Prices, labor rates, equipment rates, productivity and cost parameters are
  append-only. A change is a new row/revision; only `valid_to` may be set on an
  existing rate row. Enforced by database triggers, not just application code.
- Productivity resolves per condition (normal, high_rise, commercial,
  basement, difficult_access): an explicit record for that condition, or the
  normal rate × a configured factor. If neither exists the engine raises an
  error instead of silently using the normal rate.
- Estimate items, cost components, takeoffs and BOQ lines carry `project_id`
  with composite foreign keys to their parent, so a row cannot be attached to
  another project's estimate.

## Approved estimates are immutable

- `approve` materialises the BOQ, then freezes the version. Triggers reject any
  insert, update or delete on items, cost components, takeoffs or BOQ lines of a
  non-draft version, and any change to the version itself (except approved →
  superseded when a newer version is approved).
- Changes go into a new revision (`new_revision`), which recalculates every item
  through the engine at the chosen pricing date. The approved version keeps the
  prices it was approved with, because every resolved rate is stored with it.
- `compare_versions` lists added, removed and changed items with quantity and
  amount deltas.

## Item identity and consolidation

Each estimate item has a stable `item_key`, unique per version. Recalculating
replaces the item, so the BOQ and summaries can never double-count it.
Consolidation is available by module, trade, assembly, building, floor, zone and room.

## Access control

- Passwords are hashed with scrypt; access tokens are HS256-signed with
  `CEP_SECRET_KEY` and expire (default 8 h). "Sign out everywhere" bumps a
  per-user token version.
- Global roles: admin (everything), cost_engineer (master library, create
  projects), estimator (create projects), viewer. Project roles: manager,
  estimator, approver, viewer. Matrix in `app/auth/permissions.py`.
- A user who is not a member of a project gets 404 for it, so project ids
  cannot be probed. The project creator becomes its manager.
- Project managers maintain their project's own prices, rates, assemblies and
  formulas; only admins and cost engineers change the master library.

## Modules

Modules 1–16 each declare item types → shared formula → default assembly (or
direct pricing for provisional sums). 39 item types share 13 formulas, e.g.
COUNT serves doors, plate counts, plumbing rough-ins, boxes, sprinkler heads,
FDAS devices and wheel stoppers. Modules 17–19 are workspaces over the
library and estimate services and take no items themselves.

Default assemblies are referenced by code but not seeded: the master library or
the project must define them. `GET /api/modules?project_id=` reports which are
missing for a project, and pricing an item with a missing assembly fails with a
clear error instead of a made-up figure.

## Not built yet (and not presented as working)

- Bluebeam, PDF takeoff, DWG/DXF, IFC and Revit: no adapters or import pipeline
  yet. Only the reference tables exist.
- Excel/PDF export, Celery jobs (Redis is in docker-compose for that later).
- Editing screens: Excel-like item grid, WBS tree, assembly/material/labor
  library editors. These exist as API endpoints only.
- Single sign-on, password reset, account lockout after failed attempts.
