"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createCampaign } from "@/lib/api";

export default function SetupPage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [name, setName] = useState("");
  const [agentPersona, setAgentPersona] = useState("");
  const [conversationGoal, setConversationGoal] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [escalationKeywords, setEscalationKeywords] = useState("");
  const [recipientName, setRecipientName] = useState("");
  const [recipientPhone, setRecipientPhone] = useState("");
  const [recipientEmail, setRecipientEmail] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      const campaign = await createCampaign({
        name,
        agent_persona: agentPersona,
        conversation_goal: conversationGoal,
        system_prompt: systemPrompt,
        escalation_keywords: escalationKeywords
          .split(",")
          .map((k) => k.trim())
          .filter(Boolean),
        recipients: [
          {
            name: recipientName,
            phone: recipientPhone || undefined,
            email: recipientEmail || undefined,
          },
        ],
      });
      router.push(`/simulate/${campaign.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create campaign");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <h1 className="text-2xl font-bold text-white">Create Campaign</h1>
      <p className="mt-1 mb-8 text-sm text-zinc-400">
        Configure your AI agent, define the conversation goal, and set
        escalation triggers.
      </p>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Campaign Name */}
        <Field label="Campaign Name" required>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Post-Discharge Follow-Up"
            required
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
          />
        </Field>

        {/* Agent Persona */}
        <Field label="Agent Persona" required>
          <input
            type="text"
            value={agentPersona}
            onChange={(e) => setAgentPersona(e.target.value)}
            placeholder="e.g. Claire, a warm post-discharge nurse"
            required
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
          />
        </Field>

        {/* Conversation Goal */}
        <Field label="Conversation Goal" required>
          <textarea
            value={conversationGoal}
            onChange={(e) => setConversationGoal(e.target.value)}
            placeholder="e.g. Check pain levels, medication adherence, and flag any concerning symptoms"
            required
            rows={2}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
          />
        </Field>

        {/* System Prompt */}
        <Field label="System Prompt" required>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="You are Claire, a warm post-discharge nurse. Your tone is empathetic and concise. Keep responses under 3 sentences..."
            required
            rows={5}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
          />
        </Field>

        {/* Escalation Keywords */}
        <Field label="Escalation Keywords" hint="Comma-separated">
          <input
            type="text"
            value={escalationKeywords}
            onChange={(e) => setEscalationKeywords(e.target.value)}
            placeholder="e.g. chest pain, can't breathe, fell, bleeding"
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
          />
        </Field>

        {/* Recipient */}
        <fieldset className="rounded-lg border border-zinc-800 p-4">
          <legend className="px-2 text-sm font-medium text-zinc-300">
            Recipient
          </legend>
          <div className="space-y-4">
            <Field label="Name" required>
              <input
                type="text"
                value={recipientName}
                onChange={(e) => setRecipientName(e.target.value)}
                placeholder="e.g. Alex Johnson"
                required
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
              />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Phone">
                <input
                  type="text"
                  value={recipientPhone}
                  onChange={(e) => setRecipientPhone(e.target.value)}
                  placeholder="+1-555-0100"
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                />
              </Field>
              <Field label="Email">
                <input
                  type="email"
                  value={recipientEmail}
                  onChange={(e) => setRecipientEmail(e.target.value)}
                  placeholder="alex@example.com"
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                />
              </Field>
            </div>
          </div>
        </fieldset>

        {error && (
          <p className="rounded-lg bg-red-900/30 px-4 py-2 text-sm text-red-300">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-lg bg-emerald-600 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
        >
          {submitting ? "Creating..." : "Create Campaign & Start Simulation"}
        </button>
      </form>
    </div>
  );
}

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-baseline gap-2 text-sm font-medium text-zinc-300">
        {label}
        {required && <span className="text-red-400">*</span>}
        {hint && <span className="text-xs text-zinc-500">({hint})</span>}
      </span>
      {children}
    </label>
  );
}
