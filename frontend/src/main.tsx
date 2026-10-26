/**
 * JARVIS — Main entry point: renders the voice page with React.
 */

import { Profiler } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { countRender } from "./voiceHealth";
import "./style.css";

// Renders are counted for the voice record: whether a busy page is part of
// why speech falls behind (voiceHealth.ts).
createRoot(document.getElementById("root")!).render(
  <Profiler id="page" onRender={countRender}><App /></Profiler>);
