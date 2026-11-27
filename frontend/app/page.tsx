const layers = [
  ["Presentation", "Next.js workspaces (this shell)"],
  ["Project management", "Projects, buildings, floors, zones, rooms"],
  ["Estimating modules", "19 module definitions: configuration only, no calculation code"],
  ["Universal quantity calculation", "Unit-aware formula engine with versioned formulas"],
  ["Construction assembly", "Assembly library, project-specific revisions"],
  ["Cost calculation", "Material, labor, equipment and rate analysis"],
  ["Database and data access", "PostgreSQL, append-only rates, frozen approved estimates"],
  ["Drawing and BIM integration", "Not built yet (phase 5); schema references only"],
  ["Reporting and export", "BOQ and cost summaries via API; Excel/PDF export not built yet"],
];

export default function Overview() {
  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Overview</h1>
        <p className="muted text-sm">
          One application, one database, one estimating engine. Every module sends its measurements through the same
          formula, assembly and cost services.
        </p>
      </div>
      <section className="panel">
        <table className="grid">
          <thead>
            <tr><th>Layer</th><th>Status in this build</th></tr>
          </thead>
          <tbody>
            {layers.map(([name, status]) => (
              <tr key={name}><td className="font-medium">{name}</td><td className="muted">{status}</td></tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
