/**
 * The interface's words in Latin American Spanish (es-419), one part per
 * region as in en.ts. Terms are kept the same throughout: "agente", "flujo
 * de trabajo" ("flujo" where space is short), "cola", "carpeta", "red",
 * "diagnóstico", "configuración", "servidor del equipo", "modelo de voz".
 * Usted is not used: the interface speaks impersonally or in the
 * infinitive, as Latin American software usually does.
 */

import type { Catalog } from "../locale";
import { leftEs } from "./left.es";
import { mainEs } from "./main.es";
import { rightEs } from "./right.es";
import { shellEs } from "./shell.es";

export const PARTS = { shell: shellEs, right: rightEs, main: mainEs, left: leftEs } as const;

export const es: Catalog = { ...shellEs, ...rightEs, ...mainEs, ...leftEs };
