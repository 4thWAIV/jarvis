/** The shell's words in Latin American Spanish (messages/es.ts). */

import type { CatalogOf } from "../locale";
import type { shellEn } from "./shell.en";

export const shellEs: CatalogOf<typeof shellEn> = {
  "tabs.right.folders": "Carpetas",
  "tabs.right.agents": "Agentes",
  "tabs.right.queue": "Cola",
  "tabs.right.workflows": "Flujos",
  "tabs.right.network": "Red",
  "tabs.right.diagnostics": "Diagnóstico",

  "tabs.main.conversation": "Conversación",
  "tabs.main.folders": "Carpetas",
  "tabs.main.main": "Principal",
  "tabs.main.thread": "Hilo",
  "tabs.main.diagnostics": "Diagnóstico",
  "tabs.main.subsystem": "Subsistema",
  "tabs.main.network": "Instancia",
  "tabs.main.teamServer": "Servidor del equipo",
  "tabs.main.queue": "Mensaje",
  "tabs.main.workflow": "Flujo de trabajo",
  "tabs.main.subagent": "Subagente",

  "menu.button": "Menú",
  "menu.settings": "Configuración",
  "menu.updates": "Buscar actualizaciones",
  "menu.restart": "Reiniciar el servidor",
  "menu.restarting": "Reiniciando el servidor.",
  "menu.restartFailed": "No se pudo reiniciar: el servidor no respondió.",

  "language.label": "Idioma",
  "language.loading": "Cargando el modelo de voz; tarda unos segundos.",
  "language.spanishModel": "Modelo de voz en español",
  "language.installed": "Instalado",
  "language.downloading": "Descargando, {done} de {total}",
  "language.verifying": "Verificando la descarga",
  "language.installing": "Instalando",
  "language.notInstalled": "No instalado",
  "language.download": "Descargar ({size})",
  "language.unreadable": "No se pudo leer el idioma ({error}); reintentando.",
  "language.reading": "Leyendo el idioma…",

  "agents.working": "{count} trabajando",
  "agents.workingCapped": "{count}+ trabajando",

  "units.megabytes": "{amount} MB",
  "time.justNow": "justo ahora",
  "time.ago": "hace {age}",
  "time.short.seconds": "{n} s",
  "time.short.minutes": "{n} min",
  "time.short.hours": "{n} h",
  "time.short.days": "{n} d",
  "time.short.weeks": "{n} sem",
  "time.short.years": "{n} a",
  "time.seconds": { one: "{count} segundo", other: "{count} segundos" },
  "time.minutes": { one: "{count} minuto", other: "{count} minutos" },
  "time.hours": { one: "{count} hora", other: "{count} horas" },
  "time.days": { one: "{count} día", other: "{count} días" },
};
