/**
 * The providers whose usage the page reports, by the `service` each report
 * carries (/api/usage/week: `provider: {service, subscription}`). The card
 * picks the logo from the report's own service; nothing here assumes which.
 * A provider with no logo file yet keeps its slot, empty. Adding one (Codex
 * is next) is an entry here and its logo file in frontend/public/providers/.
 */

export interface Provider {
  name: string;
  /** Where its logo is served from, when there is one. */
  logo?: string;
}

export interface ReportProvider {
  service: string;
  subscription?: string;
}

export const PROVIDERS: Record<string, Provider> = {
  anthropic: { name: "Anthropic", logo: "/providers/anthropic.svg" },
  // Ready for when a Codex brain reports usage; nothing sends "openai" yet.
  openai: { name: "OpenAI", logo: "/providers/openai.svg" },
};
