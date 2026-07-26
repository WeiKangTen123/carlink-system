const CHANNEL_COLORS: Record<string, string> = {
  telegram: "var(--series-1)",
  whatsapp: "var(--series-3)",
  manual: "var(--series-4)",
};
const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  whatsapp: "WhatsApp",
  manual: "Manual entry",
};

export function ChannelBadge({ channel }: { channel: string }) {
  return (
    <span className="channel-badge">
      <span className="channel-dot" style={{ background: CHANNEL_COLORS[channel] ?? "var(--series-8)" }} />
      {CHANNEL_LABELS[channel] ?? channel}
    </span>
  );
}
