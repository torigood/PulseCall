"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { login, register, saveToken } from "@/lib/api";

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      if (mode === "login") {
        const res = await login(email, password);
        saveToken(res.access_token);
      } else {
        if (!name.trim()) {
          setError("Name is required");
          return;
        }
        // Register then auto-login
        await register({ email, password, name });
        const res = await login(email, password);
        saveToken(res.access_token);
      }
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950">
      <div className="w-full max-w-sm rounded-xl border border-zinc-800 bg-zinc-900 p-8 shadow-xl">
        {/* Logo / title */}
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-white">PulseCall</h1>
          <p className="mt-1 text-sm text-zinc-400">Post-discharge patient monitoring</p>
        </div>

        {/* Tab switcher */}
        <div className="mb-6 flex rounded-lg border border-zinc-700 p-1">
          {(["login", "register"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(null); }}
              className={`flex-1 rounded-md py-1.5 text-sm font-medium transition-colors ${
                mode === m
                  ? "bg-emerald-500 text-white"
                  : "text-zinc-400 hover:text-white"
              }`}
            >
              {m === "login" ? "Sign in" : "Register"}
            </button>
          ))}
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === "register" && (
            <div>
              <label className="mb-1 block text-xs font-medium text-zinc-400">
                Full name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Dr. Jane Smith"
                required
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-emerald-500 focus:outline-none"
              />
            </div>
          )}

          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-400">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="doctor@hospital.com"
              required
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-emerald-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-400">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              minLength={6}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-emerald-500 focus:outline-none"
            />
          </div>

          {error && (
            <p className="rounded-lg border border-red-800 bg-red-900/30 px-3 py-2 text-xs text-red-400">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-emerald-500 py-2 text-sm font-semibold text-white transition-colors hover:bg-emerald-400 disabled:opacity-50"
          >
            {loading ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
      </div>
    </div>
  );
}
