/**
 * Where a received file is on this Mac: jarvis/attachment_locations.py's
 * `AttachmentLocation`, read from its two routes. The fields are that
 * model's (tests/test_attachment_locations.py checks they match). Copying
 * a path or revealing it never opens the file.
 */

import { messagingApi } from "./messagingApi";

export type LocationState =
  | "remote_only" | "downloading" | "verified" | "missing" | "changed" | "moved" | "failed";
export type LocationAction = "make_local" | "reveal" | "copy_path";

export interface AttachmentExtraction {
  root: string;
  source_sha256: string;
  members: string[];
  root_exists: boolean;
}

export interface AttachmentLocation {
  message_id: string;
  attachment_id: string;
  filename: string;
  media_type: string;
  node_id: string;
  state: LocationState;
  local_path: string | null;
  intended_path: string | null;
  previous_path: string | null;
  size_bytes: number;
  sha256: string;
  bytes_done: number;
  verified_at: string | null;
  reason: string | null;
  actions: LocationAction[];
  extraction: AttachmentExtraction | null;
  shown: string;
}

/** The location as it is now (read only). */
export function locate(messageId: string, attachmentId: string): Promise<AttachmentLocation> {
  return messagingApi().location(messageId, attachmentId, false);
}

/** Download and check it if it is not here, then its location. */
export function makeLocal(messageId: string, attachmentId: string): Promise<AttachmentLocation> {
  return messagingApi().location(messageId, attachmentId, true);
}

/** The exact path onto the clipboard; false when the browser refused. */
export async function copyPath(path: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(path);
    return true;
  } catch {
    return false;
  }
}
