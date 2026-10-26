/** A small coloured dot: green, red, yellow (pulsing: being checked), or off
 *  (no reading). Used by the Settings view's rows and the brains' checks. */

export type Dot = "green" | "red" | "yellow" | "off";

export function StatusDot({ status }: { status: Dot }) {
  return <span className={`status-dot${status === "off" ? "" : ` status-${status}`}`} />;
}
