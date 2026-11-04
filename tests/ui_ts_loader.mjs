// Lets node (--experimental-strip-types) load the page's TypeScript modules
// as Vite does: a relative import without an extension is the `.ts` file of
// that name. Registered by tests/ui_navigation_cases.mts's command line
// (`--import ./tests/ui_ts_loader.mjs`); nothing else changes.
import { register } from "node:module";

if (!process.env.UI_TS_LOADER_HOOKS) {
  process.env.UI_TS_LOADER_HOOKS = "1";
  register(import.meta.url);
}

export async function resolve(specifier, context, next) {
  const relative = specifier.startsWith("./") || specifier.startsWith("../");
  if (relative && !/\.[cm]?[jt]sx?$/.test(specifier)) {
    try {
      return await next(`${specifier}.ts`, context);
    } catch {
      // not a .ts module: resolved as written below
    }
  }
  return next(specifier, context);
}
