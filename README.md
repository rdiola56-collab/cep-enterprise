# CEP: ENTERPRISE

Construction Estimating Program for high-rise and commercial buildings: one
application, one PostgreSQL database, **one Universal Estimating Engine**
shared by all 19 estimating modules.

This first phase delivers the engine and its foundations. See
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
- REST API and a Next.js shell (module list, formula library, unit converter).

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
| 8 | Drawing quantity import through the engine | **Not built** (phase 5) | |
| 9 | BIM quantity mapping to assemblies | **Not built** (phase 5) | |
| 10 | Accurate cost reporting | Passing | `test_costing.py::test_rate_analysis_hand_calculation`, `test_at10_*` |

The reference case for test 10 (100 m² of floor tiling) is checked by hand:
materials 33,811.50 + labor 10,666.67 + equipment 225.00 = direct 44,703.17;
overhead 10 %, contingency 5 % and profit 10 % on direct, VAT 12 % on the
running total; total 62,584.43, unit rate 625.84 per m².

## Run it

### Docker (any OS)

```bash
docker compose up --build
# API: http://localhost:8000/docs   Web: http://localhost:3000
```

The backend container runs migrations and seeds the reference formulas. No
prices or rates are seeded: enter them through the API (`/api/library/*`).

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
uvicorn app.main:app --reload

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
