"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  getCampaign,
  createConversation,
  sendTurn,
  endCall,
} from "@/lib/api";
import type { Campaign, CallRecord } from "@/lib/api";
import { Send, PhoneOff, Loader2 } from "lucide-react";
import { SentimentBadge } from "@/components/SentimentBadge";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export default function SimulatePage() {
  const { campaignId } = useParams<{ campaignId: string }>();
  const router = useRouter();

  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [ending, setEnding] = useState(false);
  const [callResult, setCallResult] = useState<CallRecord | null>(null);
  const [error, setError] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!campaignId) return;
    getCampaign(campaignId)
      .then(setCampaign)
      .catch(() => setError("Campaign not found"));
  }, [campaignId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function startConversation() {
    if (!campaignId) return;
    try {
      const conv = await createConversation(campaignId);
      setConversationId(conv.id);
      setMessages([]);
      setCallResult(null);
    } catch {
      setError("Failed to start conversation");
    }
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || !conversationId || !campaignId || sending) return;

    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setSending(true);

    try {
      const agentReply = await sendTurn(campaignId, conversationId, userMsg);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: agentReply },
      ]);
    } catch {
      setError("Failed to get agent response");
    } finally {
      setSending(false);
    }
  }

  async function handleEndCall() {
    if (!conversationId || !campaignId) return;
    setEnding(true);
    try {
      const result = await endCall(campaignId, conversationId);
      setCallResult(result);
      setConversationId(null);
    } catch {
      setError("Failed to end call");
    } finally {
      setEnding(false);
    }
  }

  if (error && !campaign) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-red-400">{error}</p>
      </div>
    );
  }

  if (!campaign) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-700 border-t-emerald-400" />
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <div className="border-b border-zinc-800 bg-zinc-950 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-white">{campaign.name}</h1>
            <p className="text-xs text-zinc-500">{campaign.agent_persona}</p>
          </div>
          <div className="flex items-center gap-3">
            {conversationId && (
              <button
                onClick={handleEndCall}
                disabled={ending}
                className="flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50"
              >
                {ending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <PhoneOff className="h-4 w-4" />
                )}
                End Call
              </button>
            )}
            {!conversationId && !callResult && (
              <button
                onClick={startConversation}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
              >
                Start Simulated Call
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Chat area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        {!conversationId && !callResult && (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <div className="rounded-full bg-zinc-800 p-6">
              <Send className="h-8 w-8 text-zinc-500" />
            </div>
            <div>
              <p className="text-sm text-zinc-400">
                Ready to simulate a call for this campaign.
              </p>
              <p className="mt-1 text-xs text-zinc-600">
                Goal: {campaign.conversation_goal}
              </p>
            </div>
          </div>
        )}

        {(conversationId || callResult) && (
          <div className="mx-auto max-w-2xl space-y-4">
            {messages.map((msg, i) => (
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
                  {msg.role === "assistant" && (
                    <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                      {campaign.agent_persona}
                    </span>
                  )}
                  {msg.content}
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="rounded-2xl bg-zinc-800 px-4 py-3">
                  <Loader2 className="h-4 w-4 animate-spin text-zinc-400" />
                </div>
              </div>
            )}
          </div>
        )}

        {/* Call Result */}
        {callResult && (
          <div className="mx-auto mt-8 max-w-2xl">
            <div className="rounded-lg border border-zinc-700 bg-zinc-900 p-6">
              <h3 className="mb-4 text-lg font-semibold text-white">
                Call Summary
              </h3>
              <div className="space-y-4">
                <div>
                  <span className="text-xs font-medium uppercase text-zinc-500">
                    Summary
                  </span>
                  <p className="mt-1 text-sm text-zinc-300">
                    {callResult.summary}
                  </p>
                </div>
                <div className="flex items-center gap-4">
                  <div>
                    <span className="text-xs font-medium uppercase text-zinc-500">
                      Sentiment
                    </span>
                    <div className="mt-1">
                      <SentimentBadge score={callResult.sentiment_score} />
                    </div>
                  </div>
                </div>
                {callResult.detected_flags.length > 0 && (
                  <div>
                    <span className="text-xs font-medium uppercase text-zinc-500">
                      Detected Flags
                    </span>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {callResult.detected_flags.map((f) => (
                        <span
                          key={f}
                          className="rounded bg-red-900/40 px-2 py-0.5 text-xs text-red-300"
                        >
                          {f}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <div>
                  <span className="text-xs font-medium uppercase text-zinc-500">
                    Recommended Action
                  </span>
                  <p className="mt-1 text-sm text-zinc-300">
                    {callResult.recommended_action}
                  </p>
                </div>
                <div className="flex gap-3 pt-2">
                  <button
                    onClick={() => router.push("/")}
                    className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800"
                  >
                    Back to Dashboard
                  </button>
                  <button
                    onClick={() => {
                      setCallResult(null);
                      setMessages([]);
                    }}
                    className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
                  >
                    New Simulation
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input bar */}
      {conversationId && (
        <form
          onSubmit={handleSend}
          className="border-t border-zinc-800 bg-zinc-950 px-6 py-4"
        >
          <div className="mx-auto flex max-w-2xl gap-3">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type as the recipient..."
              disabled={sending}
              autoFocus
              className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={sending || !input.trim()}
              className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
            >
              <Send className="h-4 w-4" />
            </button>
          </div>
        </form>
      )}

      {error && (
        <div className="border-t border-red-900/50 bg-red-950/30 px-6 py-2 text-center text-sm text-red-400">
          {error}
        </div>
      )}
    </div>
  );
}
