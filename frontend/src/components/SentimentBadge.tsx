export function SentimentBadge({ score }: { score: number }) {
  const config: Record<number, { bg: string; text: string; label: string }> = {
    1: { bg: "bg-red-900/50", text: "text-red-300", label: "Very Distressed" },
    2: { bg: "bg-orange-900/50", text: "text-orange-300", label: "Distressed" },
    3: { bg: "bg-yellow-900/50", text: "text-yellow-300", label: "Neutral" },
    4: { bg: "bg-emerald-900/50", text: "text-emerald-300", label: "Positive" },
    5: { bg: "bg-green-900/50", text: "text-green-300", label: "Very Positive" },
  };

  const c = config[score] || config[3];

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${c.bg} ${c.text}`}
    >
      <span className="font-bold">{score}</span>
      <span>{c.label}</span>
    </span>
  );
}
