"use client";

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

const BUTTON_VARIANTS = {
  primary: "bg-brand-500 hover:bg-brand-600 text-white disabled:bg-ink-700",
  secondary: "bg-ink-800 hover:bg-ink-700 text-ink-100 disabled:text-ink-500",
  ghost: "bg-transparent hover:bg-ink-800 text-ink-300",
  danger: "bg-red-600/90 hover:bg-red-600 text-white",
} as const;

export function Button({
  variant = "primary",
  loading = false,
  className,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof BUTTON_VARIANTS;
  loading?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-60",
        BUTTON_VARIANTS[variant],
        className,
      )}
    >
      {loading && <Spinner className="h-4 w-4" />}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cx("animate-spin", className ?? "h-5 w-5")}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cx(
        "w-full rounded-md border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-ink-100",
        "placeholder:text-ink-500 focus:border-brand-500 focus:outline-none",
        className,
      )}
    />
  );
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cx(
        "w-full rounded-md border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-ink-100",
        "placeholder:text-ink-500 focus:border-brand-500 focus:outline-none",
        className,
      )}
    />
  );
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cx(
        "w-full rounded-md border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-ink-100",
        "focus:border-brand-500 focus:outline-none",
        className,
      )}
    />
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium uppercase tracking-wide text-ink-400">{label}</span>
      {children}
      {hint && <span className="block text-xs text-ink-500">{hint}</span>}
    </label>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cx("rounded-lg border border-ink-800 bg-ink-900/60 p-4", className)}>
      {children}
    </div>
  );
}

const BADGE_TONES = {
  neutral: "bg-ink-800 text-ink-300",
  info: "bg-brand-500/15 text-brand-400",
  success: "bg-emerald-500/15 text-emerald-400",
  warning: "bg-amber-500/15 text-amber-400",
  danger: "bg-red-500/15 text-red-400",
} as const;

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: keyof typeof BADGE_TONES;
  children: ReactNode;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium",
        BADGE_TONES[tone],
      )}
    >
      {children}
    </span>
  );
}

export function statusTone(status: string): keyof typeof BADGE_TONES {
  switch (status) {
    case "succeeded":
      return "success";
    case "running":
    case "queued":
      return "info";
    case "budget_exceeded":
    case "timed_out":
      return "warning";
    case "failed":
      return "danger";
    default:
      return "neutral";
  }
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300"
    >
      {message}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-ink-800 px-6 py-12 text-center">
      <p className="text-sm font-medium text-ink-200">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-ink-500">{description}</p>
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold text-ink-50">{title}</h1>
        {description && <p className="mt-1 text-sm text-ink-500">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-ink-800 bg-ink-900 p-5"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-ink-50">{title}</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-ink-400 hover:bg-ink-800 hover:text-ink-100"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
