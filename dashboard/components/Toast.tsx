"use client";

/**
 * Toast — minimal in-house notification surface.
 *
 * Why not Sonner / shadcn-toast: those are great libraries but pulling
 * in a 30-50 KB dep for ONE call site (the PROMOTE button) is overkill
 * given Track D's "no fake data, keep the bundle lean" rule. The
 * implementation here is ~80 lines and exposes the same imperative
 * `toast(...)` API the workshop page calls so a future swap to a real
 * library would be a single-file refactor.
 *
 * The toast lives in a portal-less fixed-position container so it
 * stacks above the workshop modal without any z-index tetris. Two
 * variants: success (green) + error (red). Auto-dismiss after 3 s.
 * Click to dismiss early.
 *
 * Playwright contract:
 *   - root `data-testid="toast-container"`
 *   - each toast `data-testid="toast"` + `data-variant="success|error"`
 *
 * The acceptance criterion for T-D-015 is:
 *   "PROMOTE button shows success toast 'Promoted to live agent's next
 *    start' after configure roundtrip"
 *
 * The test asserts the toast text contains the locked string AND that
 * `data-variant="success"` so a regression that silently switches to
 * the error styling still fails.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
  type ReactNode,
} from "react";

export type ToastVariant = "success" | "error";

export interface ToastMessage {
  readonly id: number;
  readonly text: string;
  readonly variant: ToastVariant;
}

interface ToastContextValue {
  toast: (text: string, variant?: ToastVariant) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export interface ToastProviderProps {
  readonly children: ReactNode;
  /** Override for tests so a 3 s timer doesn't slow Playwright down. */
  readonly autoDismissMs?: number;
}

const DEFAULT_AUTO_DISMISS_MS = 3_000;

export function ToastProvider(props: ToastProviderProps): JSX.Element {
  const [messages, setMessages] = useState<ToastMessage[]>([]);
  const seqRef = useRef<number>(0);
  const autoDismissMs = props.autoDismissMs ?? DEFAULT_AUTO_DISMISS_MS;

  const dismiss = useCallback((id: number) => {
    setMessages((prev) => prev.filter((m) => m.id !== id));
  }, []);

  const push = useCallback(
    (text: string, variant: ToastVariant = "success") => {
      seqRef.current += 1;
      const id = seqRef.current;
      setMessages((prev) => [...prev, { id, text, variant }]);
    },
    [],
  );

  // Auto-dismiss each toast after `autoDismissMs`.
  useEffect(() => {
    if (messages.length === 0) return;
    const timers = messages.map((m) =>
      setTimeout(() => dismiss(m.id), autoDismissMs),
    );
    return () => {
      timers.forEach(clearTimeout);
    };
  }, [messages, autoDismissMs, dismiss]);

  const ctx = useMemo<ToastContextValue>(() => ({ toast: push }), [push]);

  return (
    <ToastContext.Provider value={ctx}>
      {props.children}
      <div
        data-testid="toast-container"
        aria-live="polite"
        aria-atomic="true"
        className="fixed bottom-6 right-6 z-[60] flex flex-col items-end gap-2"
      >
        {messages.map((m) => (
          <button
            key={m.id}
            type="button"
            data-testid="toast"
            data-variant={m.variant}
            onClick={() => dismiss(m.id)}
            className={[
              "pointer-events-auto rounded-md border px-4 py-2 font-mono text-[11px]",
              "uppercase tracking-[0.22em] shadow-[0_18px_40px_-18px_rgba(0,0,0,0.45)]",
              "transition-opacity",
              m.variant === "success"
                ? "border-genesis-win/60 bg-genesis-win/[0.08] text-genesis-win"
                : "border-genesis-loss/60 bg-genesis-loss/[0.08] text-genesis-loss",
            ].join(" ")}
          >
            {m.text}
          </button>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/**
 * Imperative toast API. Throws (in dev) if invoked outside a
 * `<ToastProvider>` — that catches the "I forgot to wrap" case before
 * it ships. Returns a no-op in production so SSR / hydration mismatches
 * don't crash the page.
 */
export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (ctx == null) {
    if (process.env.NODE_ENV !== "production") {
      throw new Error("useToast must be used inside <ToastProvider>");
    }
    return { toast: () => {} };
  }
  return ctx;
}
