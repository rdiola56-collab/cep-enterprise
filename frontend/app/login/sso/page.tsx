"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, setToken } from "@/lib/api";

function Callback() {
  const q = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const handoff = new URLSearchParams(window.location.hash.slice(1)).get("handoff");  // SAML: one-time code from the API
    if (handoff) {
      history.replaceState(null, "", window.location.pathname);
      api<{ access_token: string }>("/auth/saml/redeem", { method: "POST", body: JSON.stringify({ code: handoff }) })
        .then((r) => { setToken(r.access_token); window.location.href = "/"; })
        .catch((e) => setError(e.message));
      return;
    }
    const code = q.get("code"), state = q.get("state");
    if (q.get("error")) { setError(q.get("error_description") ?? q.get("error")); return; }
    if (!code || !state) { setError("The identity provider did not return a sign-in code."); return; }
    api<{ access_token: string }>("/auth/sso/callback", { method: "POST", body: JSON.stringify({ code, state }) })
      .then((r) => { setToken(r.access_token); window.location.href = "/"; })
      .catch((e) => setError(e.message));
  }, [q]);
  return (
    <div className="panel w-96 space-y-3 p-6 text-sm">
      <div className="font-semibold">CEP: ENTERPRISE</div>
      {error ? <><div className="text-red-500">{error}</div><a className="underline" href="/login">Back to sign in</a></> : <div className="muted">Signing you in…</div>}
    </div>
  );
}

export default function SsoCallbackPage() {
  return <div className="flex h-full items-center justify-center"><Suspense><Callback /></Suspense></div>;
}
