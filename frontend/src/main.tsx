/**
 * JARVIS — Main entry point: renders the voice page with React.
 */

import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./style.css";

createRoot(document.getElementById("root")!).render(<App />);
