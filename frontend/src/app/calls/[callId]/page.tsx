"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { getCall } from "@/lib/api";
import type { CallRecord } from "@/lib/api";
import { SentimentBadge } from "@/components/SentimentBadge";
import { ArrowLeft } from "lucide-react";

export default function CallDetailPage() {
  const { callId } = useParams<{ callId: string }>();
  const [call, setCall] = useState<CallRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!callId) return;
    getCall(callId)
      .then(setCall)
      .catch(() => setError("Call not found"))
      .finally(() => setLoading(false));
  }, [callId]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-700 border-t-emerald-400" />
      </div>
    );
  }

  if (error || !call) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-red-400">{error || "Call not found"}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <Link
        href="/"
        className="mb-6 inline-flex items-center gap-1 text-sm text-zinc-400 hover:text-white"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Dashboard
      </Link>

      <h1 className="text-2xl font-bold text-white">Call Detail</h1>
      <p className="mt-1 mb-8 font-mono text-xs text-zinc-500">
        {call.call_id}
      </p>

      {/* Summary Card */}
      <div className="mb-8 rounded-lg border border-zinc-800 bg-zinc-900/50 p-6">
        <div className="grid gap-6 sm:grid-cols-2">
          <div>
            <span className="text-xs font-medium uppercase text-zinc-500">
              Summary
            </span>
            <p className="mt-1 text-sm text-zinc-300">{call.summary}</p>
          </div>
          <div>
            <span className="text-xs font-medium uppercase text-zinc-500">
              Recommended Action
            </span>
            <p className="mt-1 text-sm text-zinc-300">
              {call.recommended_action}
            </p>
          </div>
          <div>
            <span className="text-xs font-medium uppercase text-zinc-500">
              Sentiment
            </span>
            <div className="mt-1">
              <SentimentBadge score={call.sentiment_score} />
            </div>
          </div>
          <div>
            <span className="text-xs font-medium uppercase text-zinc-500">
              Detected Flags
            </span>
            <div className="mt-1 flex flex-wrap gap-1">
              {call.detected_flags.length > 0 ? (
                call.detected_flags.map((f) => (
                  <span
                    key={f}
                    className="rounded bg-red-900/40 px-2 py-0.5 text-xs text-red-300"
                  >
                    {f}
                  </span>
                ))
              ) : (
                <span className="text-xs text-zinc-600">None</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Transcript */}
      <h2 className="mb-4 text-lg font-semibold text-white">Transcript</h2>
      <div className="space-y-3">
        {call.transcript.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm ${
                msg.role === "user"
                  ? "bg-emerald-600 text-white"
                  : "bg-zinc-800 text-zinc-200"
              }`}
            >
              <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider opacity-60">
                {msg.role === "user" ? "Recipient" : "Agent"}
              </span>
              {msg.content}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
