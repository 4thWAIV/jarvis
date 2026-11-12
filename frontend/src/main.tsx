/**
 * JARVIS — Main entry point: renders the voice page with React.
 */

import { Profiler } from "react";
import { createRoot } from "react-dom/client";
import "./theme";
import "./motion";
import { App } from "./App";
import { RecordsMoveGate } from "./RecordsMove";
import { countRender } from "./voiceHealth";
import "./style.css";

// Renders are counted for the voice record: whether a busy page is part of
// why speech falls behind (voiceHealth.ts). While the records move into the
// engine (the first start after that update) the page shows the move
// instead, and draws the workspace once it is done (RecordsMove.tsx).
createRoot(document.getElementById("root")!).render(
  <Profiler id="page" onRender={countRender}><RecordsMoveGate><App /></RecordsMoveGate></Profiler>);
