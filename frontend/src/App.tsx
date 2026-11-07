/**
 * The voice page: the left column (the orb, status, the team server and
 * brain usage), the right panel (agents, diagnostics, workflows,
 * network), and the centre area between them with its views. The page's one
 * human-activity collector (humanActivity.ts) runs for as long as it does.
 */

import { useEffect } from "react";
import { CentreArea } from "./CentreArea";
import { Footer } from "./Footer";
import { MenuControl } from "./MenuControl";
import { BrainUsage } from "./BrainUsage";
import { RayLane } from "./RayLane";
import { UiPipelineLayer } from "./UiPipelineLayer";
import { RightPanel } from "./RightPanel";
import { useFirstRunSetup } from "./Settings";
import { OrbControl } from "./OrbControl";
import { TeamServerTile } from "./TeamServerTile";
import { startHumanActivity } from "./humanActivity";
import { startJarvis } from "./jarvis";

export function App() {
  useEffect(startHumanActivity, []);
  useFirstRunSetup();
  return (
    <>
      {/* The upper left: the orb, what he is doing, the team server's
          health and the brains' weekly usage. The microphone is at the
          Conversation's message input's lower right (Composer.tsx). The agents are a
          tab of the right-hand display. */}
      <div id="left-column">
        <OrbControl onReady={startJarvis} />
        <TeamServerTile />
        <BrainUsage />
      </div>

      {/* The right-hand display, mirroring the left column. */}
      <RightPanel />

      {/* The ⋮ menu, top right; the Settings it opens is a centre view. */}
      <MenuControl />

      {/* The pipeline map and white attention (uiAttention.ts), under the
          rays: the brain's pointing never covers a message's transmission. */}
      <UiPipelineLayer />
      <RayLane />
      {/* Between the two columns: the view tabs and the selected view. */}
      <CentreArea />
      <Footer />
    </>
  );
}
