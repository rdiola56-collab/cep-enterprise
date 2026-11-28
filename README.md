# CEP: ENTERPRISE

Construction Estimating Program for high-rise and commercial buildings: one
application, one PostgreSQL database, **one Universal Estimating Engine**
shared by all 19 estimating modules.

Phases 1–2 deliver the engine and its foundations; phase 3 connects all 19
modules and adds sign-in with role permissions; phase 4 adds Excel/PDF export
and the editing screens; phase 5 adds drawing and BIM quantity import; phase 6
adds WBS editing, account security, single sign-on and background jobs; phase 7
reads DWG and Revit files directly and adds on-screen PDF takeoff; phase 8 adds
outgoing email and SAML single sign-on. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and for what is
not built yet.

## What works now

- Unit-aware quantity engine with exact metric ↔ imperial conversion.
- Safe formula language (no code execution), dimension-checked and versioned.
- Construction assemblies with material, labor and equipment components,
  customisable per project as new revisions.
- Material, labor (crew + productivity by working condition), equipment
  (rental, fuel, operating) and rate analysis (configurable markups).
- PostgreSQL schema for the specification's entities, with project isolation,
  append-only rates and immutable approved estimates enforced by the database.
- Estimates: calculate items, BOQ consolidation, cost/material/labor/equipment
  summaries, approval, revisions and revision comparison.
- All 19 modules: 16 estimating modules (39 item types on 13 shared formulas),
  plus the price book, labor/productivity library and detailed-estimate workspaces.
- Provisional sums priced from dated price-book records, still through rate analysis.
- Sign-in (scrypt passwords, signed tokens) and roles: global (admin, cost
  engineer, estimator, viewer) and per project (manager, estimator, approver, viewer).
  Users outside a project get "not found" for everything in it.
- Project WBS (buildings, floors incl. basements, zones, rooms); master-estimate
  consolidation by module, trade, building, floor, zone, room and basement; cost per m².
- REST API and a Next.js app: sign-in, projects, estimates (items, BOQ, consolidation,
  approval), formula library, unit converter and module pages.
- Estimate editor: add or recalculate items through the engine, bulk entry
  (one item per line), sortable/filterable item grid with keyboard navigation.
- Library screens for prices, materials, labor rates, crews, productivity,
  equipment rates and assemblies, at master or project scope.
- Excel export (Summary, BOQ, Items, Materials, Labor, Equipment, By trade,
  By floor, with live SUM formulas) and PDF export (executive summary, trade
  analysis, BOQ), both built from the same report data and logged per export.
  Draft estimates are labelled DRAFT.
- Drawing and BIM quantity import: takeoff CSV (Bluebeam Markups Summary), DXF and
  DWG drawings (DWG read by LibreDWG), IFC models and Revit models (exported to IFC by
  your Autodesk Platform Services app; off until configured). Project mapping rules turn markups, layers or model
  elements into item types; every quantity is then priced through the engine like a
  manual entry. Rows are reviewed before import, re-importing a revised file updates
  the same items, and each item traces back to its markup or model element.
  Sample files are in `samples/`.
- On-screen PDF takeoff: upload a PDF drawing set, set each page's scale (stated, or
  from a known dimension), and measure lengths, areas and counts with snapping to the
  drawing's lines. Marks go to an estimate through the same rules and review.
- Building / floor / zone / room editor on the project page. Renaming never changes
  an amount, and a node still used by estimate items or child nodes cannot be deleted.
- Accounts: change password, one-time reset links (emailed when email is set up, or
  handed over by an administrator), "Forgot your password?" by email, lockout after 5
  failed sign-ins, deactivation, and a user admin page.
- Outgoing email over SMTP, off until configured: reset links and notices for password
  changes, lockouts, project membership and estimate approvals, through an outbox with
  retries that administrators can see. Tested against a local SMTP server, not yet
  against your mail provider.
- Single sign-on with any OpenID Connect provider (Entra ID, Google, Okta, Keycloak…)
  or SAML 2.0 identity provider (ADFS, Okta, Entra ID, Shibboleth…), each off until
  configured. Both have been tested against simulated providers, not yet against your own.
- Background jobs (Celery + Redis) for exports and large imports. Without Redis, jobs run
  inside the request instead.

## Acceptance tests (spec §12)

| # | Test | Status | Where |
|---|------|--------|-------|
| 1 | One formula across modules | Passing | `test_acceptance_engine.py::test_at1_*`, `test_acceptance_db.py::test_formula_versioning_*` |
| 2 | One assembly, several projects, different prices | Passing | `test_at2_*` (engine and database) |
| 3 | Labor costing with different crew productivity | Passing | `test_costing.py::test_labor_*` |
| 4 | Automatic equipment costing | Passing | `test_costing.py::test_equipment_costing` |
| 5 | Automatic BOQ consolidation | Passing | `test_at5_*` (engine and database) |
| 6 | Project data isolation | Passing | `test_acceptance_db.py::test_at6_*` |
| 7 | Estimate revision comparison | Passing | `test_at7_*` (engine and database) |
| 8 | Drawing quantity import through the engine | Passing | `test_acceptance_import.py::test_at8_*`, `test_integrations.py` |
| 9 | BIM quantity mapping to assemblies | Passing | `test_acceptance_import.py::test_at9_*` |
| 10 | Accurate cost reporting | Passing | `test_costing.py::test_rate_analysis_hand_calculation`, `test_at10_*` |

The reference case for test 10 (100 m² of floor tiling) is checked by hand:
materials 33,811.50 + labor 10,666.67 + equipment 225.00 = direct 44,703.17;
overhead 10 %, contingency 5 % and profit 10 % on direct, VAT 12 % on the
running total; total 62,584.43, unit rate 625.84 per m².

## Run it

To run CEP on your own server with HTTPS, follow [docs/DEPLOY.md](docs/DEPLOY.md)
(`docker-compose.prod.yml`, settings in `.env`). The steps below are for trying it locally.

### Docker (any OS)

```bash
docker compose up --build
# API: http://localhost:8000/docs   Web: http://localhost:3000   (the worker service runs background jobs)
```

The backend container runs migrations and seeds the reference formulas. No
prices, rates or assemblies are seeded: enter them through the API (`/api/library/*`).
Set `CEP_SECRET_KEY` to a long random value before real use.

Create the first administrator:

```bash
docker compose exec backend python -m app.services.users create-admin you@example.com "Your Name"
```

### Local development (Windows, macOS, Linux)

Requires Python 3.12+, Node 22+ and PostgreSQL 16.

```bash
# backend
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate elsewhere)
pip install -e ".[dev]"
set CEP_DATABASE_URL=postgresql+psycopg://cep:cep@localhost:5432/cep   # export on macOS/Linux
alembic upgrade head
python -m app.services.seed
python -m app.services.users create-admin you@example.com "Your Name"
set CEP_SECRET_KEY=<long random string>   # export on macOS/Linux
uvicorn app.main:app --reload
# optional, for background jobs (needs Redis): set CEP_REDIS_URL=redis://localhost:6379/0 for both, then
celery -A app.jobs worker --pool=solo     # --pool=solo on Windows

# frontend (second terminal)
cd frontend
npm install
npm run dev
```

### Tests

```bash
cd backend
pytest -q        # needs a PostgreSQL server; set CEP_TEST_ADMIN_URL if not postgres@localhost
```

The database tests create and drop their own `cep_pytest` database.
