# CEP: ENTERPRISE architecture

CEP is a **modular monolith**: one deployable backend (FastAPI), one PostgreSQL
database, one frontend (Next.js). Layers talk through Python interfaces, not
network calls. There are no microservices at this stage.

## Layers and where they live

| # | Layer | Code | Status |
|---|-------|------|--------|
| 1 | Presentation | `frontend/`, `backend/app/api/` (HTTP boundary, permissions) | Sign-in, projects, estimate editor (items, bulk entry, BOQ, consolidation, approve, export), library editors, formula library, module pages |
| 2 | Project management | `services/projects.py`, `services/users.py`, `auth/` | Projects, members and roles, WBS with editing screen, accounts, SSO |
| 3 | Estimating modules | `app/modules/registry.py` | All 19 configured: 16 estimating (39 item types), 2 library, 1 reporting |
| 4 | Universal quantity calculation | `app/engine/units.py`, `app/engine/formula/` | Built and tested |
| 5 | Construction assembly | `app/engine/assembly.py`, `services/library.py` | Built and tested |
| 6 | Cost calculation | `app/engine/costing.py`, `app/engine/rates.py` | Built and tested |
| 7 | Database and data access | `app/db/`, `services/ratebook.py`, `migrations/` | Built and tested |
| 8 | Drawing and BIM integration | `app/integrations/`, `services/imports.py` | Takeoff CSV, DXF, DWG, IFC, Revit (via APS) and on-screen PDF takeoff built and tested |
| 9 | Reporting and export | `services/estimates.py`, `services/reports.py` | BOQ, summaries, Excel (openpyxl) and PDF (ReportLab) export built and tested |

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

## Drawing and BIM import

```
file ──adapter──▶ staged rows: what was measured, with units, where (page / layer / storey)
     ──rules────▶ module, item type, formula inputs, floor          (project configuration, no prices)
     ──engine───▶ preview: quantity, unit, amount per item          (EstimatingEngine.calculate_item)
     ──commit───▶ estimate items; takeoffs point at the drawing markup or BIM element
```

- **Adapters** (`app/integrations/`) only read files. Takeoff CSV reads a Bluebeam
  Markups Summary export: every measured cell must carry its unit, and feet-inches
  such as `12'-6"` are accepted. DXF reads lengths, closed areas (bulge arcs are
  measured exactly) and block counts by layer. A drawing without `$INSUNITS` is
  refused unless the user states its unit. IFC reads each element's quantity sets
  and properties, plus its storey. Model units are converted to SI exactly for SI
  and the standard imperial units. Quantities are never derived from geometry: an
  element without quantity sets has only a count.
- **DWG** (`integrations/dwg.py`) is read directly: a separate reader program,
  GNU LibreDWG compiled to WebAssembly (`backend/tools/dwg/dwg2dxf.mjs`, run with
  Node.js), converts it to DXF and the DXF adapter measures that. So a DWG and a DXF
  of the same drawing give identical rows, entity handles and units, and share the
  DXF mapping rules and item keys. Tests write DWG files (R2000 to R2018) with an
  independent library (acad-ts, a port of ACadSharp) and check they read back
  exactly as the DXF. Drawings from before R13 are refused. LibreDWG is GPL-3.0 and
  runs as its own process; `CEP_DWG_CONVERTER` can name another converter (for
  example ODA File Converter behind a wrapper) that reads DWG on stdin and writes DXF.
  If no reader is installed, the importer says so and refuses DWG files.
- **Revit** (`integrations/rvt.py`): the .rvt format can only be read by Autodesk
  software, so CEP uploads the model to the company's own Autodesk Platform
  Services app (a transient bucket, deleted by APS after 24 hours), has the Model
  Derivative service export it to IFC with Revit's own exporter, downloads the IFC
  and measures it with the IFC adapter. Revit imports share the IFC rules and item
  keys and always run as background jobs. It is off until `CEP_APS_CLIENT_ID` and
  `CEP_APS_CLIENT_SECRET` are set (optional: `CEP_APS_REGION`, `CEP_APS_BUCKET`,
  `CEP_APS_IFC_SETTING`, `CEP_APS_TIMEOUT`). It is tested against a simulated APS,
  not yet against a real APS account.
- **PDF drawings** (`services/takeoff.py`, `integrations/pdf_takeoff.py`) are
  measured on screen. The PDF is kept as uploaded; pages are rendered by PDFium and
  the page's vector line work (read with pdfplumber) is offered for snapping. Each
  page needs a scale before lengths or areas count: a stated drawing scale
  (1/4" = 1'-0", 1:100…) or a known dimension picked on the page. Points are stored
  in PDF points and every quantity is recomputed in decimal from them and the
  scale, so changing a scale updates every mark on that page. Marks (length, area
  with perimeter, count) have a subject that mapping rules match and a stable
  reference (M1, M2…), and are sent to a draft estimate through the same review and
  commit as an imported file. The page label is matched to a WBS floor.
- **Mapping rules** (`import_mapping_rules`) are project configuration. A rule
  matches on a glob for subject, layer or IFC class, plus an optional label and
  property values. It names the module and item type, and says which measured
  value feeds which formula input. The first match by priority wins.
- **Quantity basis**:
  - `formula`: dimensions go into the item type's own formula, e.g. wall
    Length × Height → WALL_AREA.
  - `measured`: the measured quantity goes through a shared `MEASURED_<KIND>`
    formula of the same dimension, e.g. NetVolume → MEASURED_VOLUME. It is still
    versioned and still recorded on the takeoff, and the item keeps its basis
    across revisions.
- **Aggregation**: only measured quantities can be aggregated (one item per rule
  and floor). Summing dimensions would be wrong, and the database refuses it.
- **Validation is never bypassed**:
  - A batch can be committed only when every row is mapped and valid, or excluded
    by a person with a reason.
  - Commit re-applies the rules and re-prices everything, so changed rules or
    prices are picked up.
  - The commit is all or nothing.
  - Approved versions take no imports.
- **Stable item keys**: keys have the form `adapter:source:markup-id`, or
  `adapter:source:rule@floor` for aggregated items. Re-importing a revised file
  therefore recalculates the same items instead of duplicating them. Items that
  disappeared from the source are listed, and are removed only on request.
- **Traceability**: each row keeps its file's SHA-256, its source id and the rule
  snapshot used. Each takeoff links to a `drawing_references` or `bim_elements`
  row.

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

## Accounts, sign-in and single sign-on

- **Lockout**: 5 wrong passwords in a row lock an account for 15 minutes, even for the
  right password. The limits are set by `CEP_MAX_FAILED_LOGINS` and `CEP_LOCKOUT_MINUTES`.
  Administrators can unlock an account.
- **Password reset**: an administrator issues a one-time reset link, valid for 24 hours
  (`CEP_RESET_LINK_HOURS`). Only a SHA-256 hash of the token is stored. Using the link
  sets the password, unlocks the account and signs out every session. CEP sends no
  email, so the administrator passes the link on.
- **Password change**: changing your own password signs out every other session.
- **Single sign-on** (`app/auth/sso.py`) uses the OpenID Connect authorization code flow
  with PKCE, plus state and nonce stored server-side for 10 minutes.
  - The ID token is verified against the provider's JWKS: signature, issuer, audience,
    expiry and nonce.
  - The first sign-in links an existing account by verified email. Later sign-ins match
    the provider's issuer and subject.
  - Unknown people are refused, unless `CEP_OIDC_AUTO_CREATE` names a role to create
    them with.
  - SSO never grants project access: project roles still come from CEP.
  - Configure it with `CEP_OIDC_ISSUER`, `CEP_OIDC_CLIENT_ID`, `CEP_OIDC_CLIENT_SECRET`
    and `CEP_OIDC_REDIRECT_URI` (`https://<web>/login/sso`).
  - It is tested against a simulated provider, including forged signatures, wrong
    audience, replayed state and wrong nonce. It has not been tried against a real
    tenant yet.

## Background jobs

`app/jobs.py`: exports and large import staging run as jobs.

- The API stores a `jobs` row and queues its id on Redis. A Celery worker
  (`celery -A app.jobs worker`) runs it through the ordinary services, so a
  background import is validated exactly like one made in the request.
- The worker writes status, result or error and the output file back to PostgreSQL.
- A job's work is all or nothing: a failure keeps only the error message.
- Without `CEP_REDIS_URL`, the same jobs run inside the request (`CEP_JOBS_MODE=inline`).
- The web app runs every export as a job, and every import larger than 5 MB.

## Modules

Modules 1–16 each declare item types → shared formula → default assembly (or
direct pricing for provisional sums). 39 item types share 13 formulas (plus 5 MEASURED_* formulas for imported quantities), e.g.
COUNT serves doors, plate counts, plumbing rough-ins, boxes, sprinkler heads,
FDAS devices and wheel stoppers. Modules 17–19 are workspaces over the
library and estimate services and take no items themselves.

Default assemblies are referenced by code but not seeded: the master library or
the project must define them. `GET /api/modules?project_id=` reports which are
missing for a project, and pricing an item with a missing assembly fails with a
clear error instead of a made-up figure.

## Not built yet (and not presented as working)

- Revit import has not been run against a real Autodesk Platform Services
  account yet; it shows "needs setup" until credentials are configured.
- Raster (scanned) PDFs can be measured on screen, but offer no line work to snap to.
- Outgoing email (reset links are handed over by an administrator).
- SAML single sign-on (OpenID Connect only).
