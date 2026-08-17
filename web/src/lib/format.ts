import { activeLang } from "@/lib/i18n";

export function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export function formatMicrocents(microcents: number): string {
  const cents = microcents / 1_000_000;
  // Sub-cent step costs are common; showing "$0.00" for all of them hides the
  // difference between a cheap step and a free one.
  if (cents > 0 && cents < 1) return `$${(cents / 100).toFixed(4)}`;
  return `$${(cents / 100).toFixed(2)}`;
}

export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return String(count);
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatRelative(iso: string): string {
  const lang = activeLang();
  const then = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);
  // `numeric: "auto"` is what turns -1 day into "вчера" rather than "1 день
  // назад"; the browser owns the grammar, which for Russian plurals matters.
  const relative = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  if (seconds < 60) return relative.format(0, "second");
  if (seconds < 3600) return relative.format(-Math.floor(seconds / 60), "minute");
  if (seconds < 86_400) return relative.format(-Math.floor(seconds / 3600), "hour");
  if (seconds < 604_800) return relative.format(-Math.floor(seconds / 86_400), "day");
  return new Date(iso).toLocaleDateString(lang);
}

export function formatDateTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(activeLang()) : "—";
}
