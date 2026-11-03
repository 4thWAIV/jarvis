/** Conversation keeps the existing workspace and captures around a readable,
 * complete timeline. Audio playback is a control, never a second transcript. */
import { useSyncExternalStore } from "react";
import { conversation } from "./captions";
import { CaptureList } from "./CaptureBox";
import { MessageList } from "./conversation/MessageList";
import { Composer } from "./conversation/Composer";
import { PlaybackBar } from "./conversation/PlaybackBar";
import "./conversation/conversation.css";

export { historyRows } from "./conversation/MessageList";
export { RecordItem, AgentLink } from "./conversation/RecordItem";

export function ConversationView() {
  const { turns } = useSyncExternalStore(conversation.subscribe, conversation.get);
  const hearing = [...turns].reverse().find(t => t.speaker === "user" && t.open);
  return <section id="conversation" aria-label="Conversation">
    <MessageList turns={turns.filter(t => !t.open)} />
    <div className="conversation-dock">
      <div className="conversation-captures"><CaptureList /></div>
      <PlaybackBar />
      {hearing && <div className="conversation-interim" role="status">
        <span>Hearing you</span><p>{hearing.sentences.join(" ")}</p>
      </div>}
      <Composer />
    </div>
  </section>;
}
