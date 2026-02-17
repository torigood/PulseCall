"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createPatient } from "@/lib/api";

export default function SetupPage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [age, setAge] = useState("");
  const [gender, setGender] = useState("");
  const [primaryDiagnosis, setPrimaryDiagnosis] = useState("");
  const [agentPersona, setAgentPersona] = useState("");
  const [conversationGoal, setConversationGoal] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [escalationKeywords, setEscalationKeywords] = useState("");
  const [voiceId, setVoiceId] = useState("rachel");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      const patient = await createPatient({
        name,
        phone,
        email: email || undefined,
        age: age ? parseInt(age, 10) : undefined,
        gender: gender || undefined,
        primary_diagnosis: primaryDiagnosis || undefined,
        agent_persona: agentPersona || undefined,
        conversation_goal: conversationGoal || undefined,
        system_prompt: systemPrompt || undefined,
        escalation_keywords: escalationKeywords
          .split(",")
          .map((k) => k.trim())
          .filter(Boolean),
        voice_id: voiceId,
      });
      router.push(`/simulate/${patient.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to register patient");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <h1 className="text-2xl font-bold text-white">Register Patient</h1>
      <p className="mt-1 mb-8 text-sm text-zinc-400">
        Add a new patient for post-discharge AI follow-up calls.
      </p>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Patient Info */}
        <fieldset className="rounded-lg border border-zinc-800 p-4">
          <legend className="px-2 text-sm font-medium text-zinc-300">
            Patient Information
          </legend>
          <div className="space-y-4">
            <Field label="Full Name" required>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Michael Thompson"
                required
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
              />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Phone" required>
                <input
                  type="text"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+1-555-0100"
                  required
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                />
              </Field>
              <Field label="Email">
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="patient@example.com"
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                />
              </Field>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <Field label="Age">
                <input
                  type="number"
                  value={age}
                  onChange={(e) => setAge(e.target.value)}
                  placeholder="58"
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                />
              </Field>
              <Field label="Gender">
                <select
                  value={gender}
                  onChange={(e) => setGender(e.target.value)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                >
                  <option value="">Select</option>
                  <option value="Male">Male</option>
                  <option value="Female">Female</option>
                  <option value="Other">Other</option>
                </select>
              </Field>
              <Field label="Primary Diagnosis">
                <input
                  type="text"
                  value={primaryDiagnosis}
                  onChange={(e) => setPrimaryDiagnosis(e.target.value)}
                  placeholder="e.g. Knee OA"
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                />
              </Field>
            </div>
          </div>
        </fieldset>

        {/* AI Agent Configuration */}
        <fieldset className="rounded-lg border border-zinc-800 p-4">
          <legend className="px-2 text-sm font-medium text-zinc-300">
            AI Agent Configuration
          </legend>
          <div className="space-y-4">
            <Field label="Agent Persona">
              <input
                type="text"
                value={agentPersona}
                onChange={(e) => setAgentPersona(e.target.value)}
                placeholder="e.g. PulseCall Medical Assistant"
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
              />
            </Field>
            <Field label="Conversation Goal">
              <textarea
                value={conversationGoal}
                onChange={(e) => setConversationGoal(e.target.value)}
                placeholder="e.g. Check pain levels, medication adherence, and flag any concerning symptoms"
                rows={2}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
              />
            </Field>
            <Field label="System Prompt">
              <textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder="Be concise, empathetic, and clear. Ask one question at a time."
                rows={3}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
              />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="TTS Voice">
                <select
                  value={voiceId}
                  onChange={(e) => setVoiceId(e.target.value)}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                >
                  <option value="rachel">Rachel</option>
                  <option value="emily">Emily</option>
                  <option value="matt">Matt</option>
                </select>
              </Field>
              <Field label="Escalation Keywords" hint="Comma-separated">
                <input
                  type="text"
                  value={escalationKeywords}
                  onChange={(e) => setEscalationKeywords(e.target.value)}
                  placeholder="e.g. chest pain, bleeding"
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
          {submitting ? "Registering..." : "Register Patient & Start Simulation"}
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
