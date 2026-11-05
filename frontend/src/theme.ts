/** Browser-local paint preference. Changing it never remounts the workspace. */
import { createStore } from "./store";

export type Theme = "classic" | "graphite";
export const THEME_KEY = "jarvis.theme";
const validTheme = (value: string | null | undefined): Theme => value === "graphite" ? "graphite" : "classic";
export const theme = createStore<Theme>(validTheme(document.documentElement.dataset.theme));

function apply(value: Theme): void {
  document.documentElement.dataset.theme = value;
  theme.set(value);
}

export function setTheme(value: Theme): void {
  const next = validTheme(value);
  apply(next);
  try { localStorage.setItem(THEME_KEY, next); } catch { /* Paint still works when storage is unavailable. */ }
}

// Other workspace/dashboard tabs follow the preference without new requests.
window.addEventListener("storage", (event) => {
  if (event.key === THEME_KEY || event.key === null) apply(validTheme(event.newValue));
});
