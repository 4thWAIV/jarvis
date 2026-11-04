// Accepted fixture for tests/ui_coverage_rules.mts: a module the route
// state's declaration names as its co-owner (`@routeStateAlso`) may write
// it. Never imported by the page.
import type { Store } from "../../../../frontend/src/store";
import { panelOpen } from "./RegisteredView";

export function shut(): void {
  (panelOpen as Store<boolean>).set(false);
}
