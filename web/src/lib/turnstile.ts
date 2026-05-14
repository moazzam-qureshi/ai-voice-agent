/**
 * Cloudflare Turnstile loader + token resolver — locked invariant pattern.
 *
 * This is the same pattern used by DocuAI. The 10 gotchas documented in
 * CLAUDE.md's "Cloudflare Turnstile" section are all addressed here:
 *
 *   1. appearance: "interaction-only" (the size: "invisible" trap)
 *   2. Reserved 300x65 container so visible challenges have room to render
 *   3. Centered modal backdrop, not a corner widget
 *   4. setTimeout(execute, 0) one-tick defer after render()
 *   5. error-callback code logged for debugging
 *   6-9. Build-time env, allowed hostnames — handled by Coolify/CF dashboard
 *   10. Empty TURNSTILE_SECRET on the server is the dev-mode escape hatch
 */

const TURNSTILE_SRC =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__turnstileOnLoad";

let loadPromise: Promise<void> | null = null;
let backdropEl: HTMLDivElement | null = null;
let containerEl: HTMLDivElement | null = null;
let widgetId: string | null = null;

interface TurnstileGlobal {
  render: (
    el: HTMLElement,
    opts: {
      sitekey: string;
      callback: (token: string) => void;
      "error-callback"?: (code?: string) => void;
      "expired-callback"?: () => void;
      "before-interactive-callback"?: () => void;
      "after-interactive-callback"?: () => void;
      size?: "normal" | "compact" | "flexible";
      appearance?: "always" | "execute" | "interaction-only";
      execution?: "render" | "execute";
    },
  ) => string;
  execute: (widgetId: string) => void;
  reset: (widgetId: string) => void;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileGlobal;
    __turnstileOnLoad?: () => void;
  }
}

function loadTurnstile(): Promise<void> {
  if (loadPromise) return loadPromise;
  loadPromise = new Promise<void>((resolve, reject) => {
    if (typeof window === "undefined") {
      reject(new Error("Turnstile can only run in the browser"));
      return;
    }
    if (window.turnstile) {
      resolve();
      return;
    }

    window.__turnstileOnLoad = () => resolve();

    const script = document.createElement("script");
    script.src = TURNSTILE_SRC;
    script.async = true;
    script.defer = true;
    script.onerror = () => reject(new Error("Failed to load Turnstile script"));
    document.head.appendChild(script);
  });
  return loadPromise;
}

function ensureContainer(): HTMLDivElement {
  if (containerEl && backdropEl) return containerEl;

  const backdrop = document.createElement("div");
  backdrop.className = "turnstile-backdrop";
  backdrop.style.display = "none";

  const modal = document.createElement("div");
  modal.className = "turnstile-modal";

  const label = document.createElement("div");
  label.className = "turnstile-modal__label";
  label.textContent = "VERIFYING…";

  const host = document.createElement("div");
  host.className = "turnstile-host";

  modal.appendChild(label);
  modal.appendChild(host);
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);

  backdropEl = backdrop;
  containerEl = host;
  return host;
}

function showBackdrop() {
  if (backdropEl) backdropEl.style.display = "flex";
}
function hideBackdrop() {
  if (backdropEl) backdropEl.style.display = "none";
}

/**
 * Get a fresh Turnstile token. Returns "" if no site key is configured.
 * On most page loads Turnstile resolves invisibly via Cloudflare heuristics.
 * If the user is challenged, a centered modal appears with the widget.
 */
export async function getTurnstileToken(): Promise<string> {
  const sitekey = process.env.NEXT_PUBLIC_TURNSTILE_SITEKEY ?? "";
  if (!sitekey) return "";

  await loadTurnstile();
  if (!window.turnstile) return "";

  const container = ensureContainer();

  return new Promise<string>((resolve, reject) => {
    if (widgetId !== null) {
      try {
        window.turnstile!.reset(widgetId);
      } catch {
        widgetId = null;
      }
    }

    widgetId = window.turnstile!.render(container, {
      sitekey,
      appearance: "interaction-only",
      execution: "execute",
      callback: (token: string) => {
        hideBackdrop();
        resolve(token);
      },
      "error-callback": (code?: string) => {
        hideBackdrop();
        console.error("[turnstile] error-callback", code);
        reject(new Error(`Turnstile challenge failed${code ? ` (${code})` : ""}`));
      },
      "expired-callback": () => {
        hideBackdrop();
        reject(new Error("Turnstile token expired"));
      },
      "before-interactive-callback": () => showBackdrop(),
      "after-interactive-callback": () => hideBackdrop(),
    });

    // One-tick defer per locked gotcha #4 — synchronous execute() after
    // render() occasionally no-ops before the iframe mounts.
    setTimeout(() => {
      try {
        window.turnstile!.execute(widgetId!);
      } catch (e) {
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    }, 0);
  });
}
