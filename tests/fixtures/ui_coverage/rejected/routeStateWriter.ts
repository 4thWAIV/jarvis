// Negative fixture for tests/ui_coverage_rules.mts: route state written
// from a module that does not own it. The exports are typed read-only, so
// each write needs a cast, and the rule sees through it. Never imported by
// the page.
import { settingsOpen } from "../../../../frontend/src/Settings";
import { menuOpen } from "../../../../frontend/src/MenuControl";
import type { Store } from "../../../../frontend/src/store";

export function sneakOpen(): void {
  (settingsOpen as Store<boolean>).set(true);
  const menu = menuOpen as unknown as Store<boolean>;
  menu.set(true);
}
