/**
 * The voice page: the left column (the orb, status, mic, the team server
 * and brain usage), the right panel (agents, diagnostics, workflows,
 * network), and the centre area between them with its views. The page's one
 * human-activity collector (humanActivity.ts) runs for as long as it does.
 */

import { useEffect } from "react";
import { CentreArea } from "./CentreArea";
import { Footer } from "./Footer";
import { MenuControl } from "./MenuControl";
import { BrainUsage } from "./BrainUsage";
import { MicControl } from "./MicControl";
import { RayLane } from "./RayLane";
import { RightPanel } from "./RightPanel";
import { Settings } from "./Settings";
import { OrbControl } from "./OrbControl";
import { StatusControl } from "./StatusControl";
import { TeamServerTile } from "./TeamServerTile";
import { startHumanActivity } from "./humanActivity";
import { startJarvis } from "./jarvis";

export function App() {
  useEffect(startHumanActivity, []);
  return (
    <>
      {/* The upper left: the orb, what he is doing, the microphone, the team
          server's health and the brains' weekly usage. The agents are a
          tab of the right-hand display. */}
      <div id="left-column">
        <OrbControl onReady={startJarvis} />
        <StatusControl />
        <MicControl />
        <TeamServerTile />
        <BrainUsage />
      </div>

      {/* The right-hand display, mirroring the left column. */}
      <RightPanel />

      {/* The ⋮ menu, top right, and the Settings view it opens. */}
      <MenuControl />
      <Settings />

      <RayLane />
      {/* Between the two columns: the view tabs and the selected view. */}
      <CentreArea />
      <Footer />
    </>
  );
}
