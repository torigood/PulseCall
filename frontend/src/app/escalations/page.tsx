"use client";

import { useEffect, useState } from "react";
import { listEscalations, acknowledgeEscalation } from "@/lib/api";
import type { Escalation } from "@/lib/api";
import { AlertTriangle, CheckCircle } from "lucide-react";
import { SentimentBadge } from "@/components/SentimentBadge";

export default function EscalationsPage() {
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listEscalations()
      .then(setEscalations)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  async function handleAcknowledge(id: string) {
    try {
      const updated = await acknowledgeEscalation(id);
      setEscalations((prev) => prev.map((e) => (e.id === id ? updated : e)));
    } catch {}
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-700 border-t-emerald-400" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <h1 className="text-2xl font-bold text-white">Escalation Queue</h1>
      <p className="mt-1 mb-8 text-sm text-zinc-400">
        Flagged calls requiring human attention, sorted by priority.
      </p>

      {escalations.length === 0 ? (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-12 text-center">
          <CheckCircle className="mx-auto h-10 w-10 text-emerald-500" />
          <p className="mt-3 text-sm text-zinc-400">No escalations. All clear.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {escalations.map((esc) => (
            <div
              key={esc.id}
              className={`rounded-lg border p-5 ${
                esc.status === "acknowledged"
                  ? "border-zinc-800 bg-zinc-900/30 opacity-60"
                  : "border-zinc-700 bg-zinc-900/50"
              }`}
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <PriorityBadge priority={esc.priority} />
                  <div>
                    <p className="text-sm font-medium text-white">{esc.reason}</p>
                    <p className="mt-0.5 text-xs text-zinc-500">
                      {new Date(esc.created_at).toLocaleString()}
                    </p>
                  </div>
                </div>
                {esc.status === "open" ? (
                  <button
                    onClick={() => handleAcknowledge(esc.id)}
                    className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-800"
                  >
                    Acknowledge
                  </button>
                ) : (
                  <span className="flex items-center gap-1 text-xs text-emerald-400">
                    <CheckCircle className="h-3 w-3" /> Acknowledged
                  </span>
                )}
              </div>
              <div className="mt-3 flex flex-wrap gap-1">
                {esc.detected_flags.map((f) => (
                  <span key={f} className="rounded bg-red-900/40 px-2 py-0.5 text-xs text-red-300">
                    {f}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PriorityBadge({ priority }: { priority: string }) {
  const styles: Record<string, string> = {
    high: "bg-red-900/50 text-red-300",
    medium: "bg-yellow-900/50 text-yellow-300",
    low: "bg-zinc-800 text-zinc-400",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-bold uppercase ${styles[priority] || styles.low}`}>
      {priority}
    </span>
  );
}
