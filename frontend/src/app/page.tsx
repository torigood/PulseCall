"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listPatients, listCalls, listEscalations } from "@/lib/api";
import type { Patient, CallRecord, Escalation } from "@/lib/api";
import { Phone, AlertTriangle, TrendingUp, Plus, Users } from "lucide-react";
import { SentimentBadge } from "@/components/SentimentBadge";

export default function Dashboard() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([listPatients(), listCalls(), listEscalations()])
      .then(([p, cl, e]) => {
        setPatients(p);
        setCalls(cl);
        setEscalations(e);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const openEscalations = escalations.filter((e) => e.status === "open");
  const avgSentiment =
    calls.length > 0
      ? (calls.reduce((s, c) => s + c.sentiment_score, 0) / calls.length).toFixed(1)
      : "—";

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-700 border-t-emerald-400" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Overview of patients, calls, and escalations
          </p>
        </div>
        <Link
          href="/setup"
          className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
        >
          <Plus className="h-4 w-4" />
          New Patient
        </Link>
      </div>

      {/* Stats */}
      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-4">
        <StatCard
          label="Patients"
          value={patients.length}
          icon={<Users className="h-5 w-5 text-emerald-400" />}
        />
        <StatCard
          label="Total Calls"
          value={calls.length}
          icon={<Phone className="h-5 w-5 text-blue-400" />}
        />
        <StatCard
          label="Avg Sentiment"
          value={avgSentiment}
          icon={<TrendingUp className="h-5 w-5 text-yellow-400" />}
        />
        <StatCard
          label="Open Escalations"
          value={openEscalations.length}
          icon={<AlertTriangle className="h-5 w-5 text-red-400" />}
        />
      </div>

      {/* Patients */}
      <section className="mb-8">
        <h2 className="mb-4 text-lg font-semibold text-white">Patients</h2>
        {patients.length === 0 ? (
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-8 text-center text-sm text-zinc-500">
            No patients yet.{" "}
            <Link href="/setup" className="text-emerald-400 hover:underline">
              Register one
            </Link>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {patients.map((p) => {
              const patientCalls = calls.filter(
                (cl) => cl.patient_id === p.id
              );
              return (
                <div
                  key={p.id}
                  className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-5"
                >
                  <div className="flex items-center justify-between">
                    <h3 className="font-semibold text-white">{p.name}</h3>
                    <StatusBadge status={p.status} />
                  </div>
                  <p className="mt-1 text-xs text-zinc-500 line-clamp-2">
                    {p.conversation_goal || p.primary_diagnosis || "No details"}
                  </p>
                  <div className="mt-3 flex items-center justify-between">
                    <span className="text-xs text-zinc-400">
                      {patientCalls.length} call
                      {patientCalls.length !== 1 ? "s" : ""}
                    </span>
                    <Link
                      href={`/simulate/${p.id}`}
                      className="text-xs font-medium text-emerald-400 hover:underline"
                    >
                      Simulate Call →
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Recent Calls */}
      <section>
        <h2 className="mb-4 text-lg font-semibold text-white">Recent Calls</h2>
        {calls.length === 0 ? (
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-8 text-center text-sm text-zinc-500">
            No calls yet. Simulate a call from a patient.
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-zinc-800">
            <table className="w-full text-sm">
              <thead className="border-b border-zinc-800 bg-zinc-900/80">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-zinc-400">
                    Call ID
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-zinc-400">
                    Summary
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-zinc-400">
                    Sentiment
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-zinc-400">
                    Flags
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-zinc-400">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody>
                {calls.slice(0, 10).map((call) => (
                  <tr
                    key={call.call_id}
                    className="border-b border-zinc-800/50 hover:bg-zinc-900/40"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-zinc-500">
                      {call.call_id.slice(0, 16)}
                    </td>
                    <td className="max-w-xs truncate px-4 py-3 text-zinc-300">
                      {call.summary}
                    </td>
                    <td className="px-4 py-3">
                      <SentimentBadge score={call.sentiment_score} />
                    </td>
                    <td className="px-4 py-3">
                      {call.detected_flags.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {call.detected_flags.map((f) => (
                            <span
                              key={f}
                              className="rounded bg-red-900/40 px-2 py-0.5 text-xs text-red-300"
                            >
                              {f}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-xs text-zinc-600">None</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <Link
                        href={`/calls/${call.call_id}`}
                        className="text-xs font-medium text-emerald-400 hover:underline"
                      >
                        View
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function StatCard({
  label,
  value,
  icon,
}: {
  label: string;
  value: string | number;
  icon: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-5">
      <div className="flex items-center justify-between">
        <span className="text-sm text-zinc-400">{label}</span>
        {icon}
      </div>
      <p className="mt-2 text-2xl font-bold text-white">{value}</p>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    PENDING_REVIEW: "bg-yellow-900/50 text-yellow-300",
    CONFIRMED: "bg-blue-900/50 text-blue-300",
    ACTIVE: "bg-emerald-900/50 text-emerald-300",
    COMPLETED: "bg-zinc-800 text-zinc-400",
  };
  const labels: Record<string, string> = {
    PENDING_REVIEW: "Pending",
    CONFIRMED: "Confirmed",
    ACTIVE: "Active",
    COMPLETED: "Completed",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-[10px] font-bold uppercase ${styles[status] || styles.PENDING_REVIEW}`}>
      {labels[status] || status}
    </span>
  );
}
