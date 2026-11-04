/**
 * What a received, checked file holds, on its card in the Queue (mounted by
 * QueueFiles.tsx). Open reads what it is (bundles.ts): a single document
 * opens in the centre area (BundleMemberView.tsx); an archive shows its
 * contents here, one folder at a time, each member with its name, type and
 * size, its state only when it is not readable, and Open. A zip that is
 * also a work package says so; opening or running a package stays with the
 * card's Open package and Open page (QueueFiles.tsx) and the brain's
 * open_package.
 *
 * Showing the contents, a folder in them and the next page of one are the
 * registered `bundle.contents`, `bundle.folder` and `bundle.folder.more`
 * commands, a click's and the brain's alike; what each archive shows is
 * kept here by its file. Each member is a `bundleEntry` target (its name,
 * and Open); the path above the list, Contents and More are parts of the
 * file's `queueAttachment`.
 */

import { useSyncExternalStore } from "react";
import type { Classification, MemberInfo, MemberPage } from "./bundleContract";
import {
  bundleWords as W, classify, listMembers, openMember, type FileKey,
} from "./bundles";
import { CENTRE_SOCKET } from "./ConversationView";
import { useT } from "./locale";
import { sizeText } from "./queueStore";
import { createStore } from "./store";
import { bind, bindingProps, runBinding } from "./uiNavigation";
import { registerCommand, useUiTarget, type CommandOutcome } from "./uiRegistry";

/** A part's callback ref, from the file's registered target. */
type PartRef = (part: string) => (el: Element | null) => void;

/** One archive's contents as shown: what it is, whether they are open,
 *  the folder shown (the one top folder that only wraps the files is
 *  `root`), its pages read so far, and what went wrong. */
interface Shown {
  what: Classification | null;
  expanded: boolean;
  root: string | null;
  folder: string;
  pages: MemberPage[];
  busy: boolean;
  problem: string | null;
}

const EMPTY: Shown = { what: null, expanded: false, root: null, folder: "", pages: [], busy: false, problem: null };
const keyOf = (f: FileKey) => `${f.messageId}/${f.attachmentId}`;

/** Every archive's contents as shown, by its file. */
const shown = createStore<Record<string, Shown>>({});
const shownOf = (f: FileKey): Shown => shown.get()[keyOf(f)] ?? EMPTY;
const put = (f: FileKey, change: Partial<Shown>) =>
  shown.set({ ...shown.get(), [keyOf(f)]: { ...shownOf(f), ...change } });

const done = (): CommandOutcome => ({ status: "completed", effects: [] });
const paged = (): CommandOutcome => ({ status: "completed", effects: ["list.paged"] });

/** Read one page of `folder` (the next one with `continuation`). The
 *  archive's first read finds the one top folder that only wraps the files,
 *  and opens inside it. */
async function read(f: FileKey, folder: string, continuation: string | null): Promise<void> {
  put(f, { problem: null, folder, ...(continuation ? {} : { pages: [] }) });
  try {
    const page = await listMembers(f, folder, continuation);
    if (folder === "" && !continuation && shownOf(f).root === null) {
      const only = page.members.length === 1 && page.members[0].is_dir && !page.continuation
        ? page.members[0].path : "";
      put(f, { root: only });
      if (only) { await read(f, only, null); return; }
    }
    put(f, { pages: continuation ? [...shownOf(f).pages, page] : [page] });
  } catch (e) {
    put(f, { problem: (e as Error).message });
  }
}

const lastPage = (f: FileKey): MemberPage | undefined => shownOf(f).pages[shownOf(f).pages.length - 1];
const isArchive = (f: FileKey) => shownOf(f).what?.container === "zip";
const archiveOnly = (f: FileKey) => isArchive(f) ? { available: true } : { available: false, reason: "no-entity" as const };

registerCommand("bundle.contents", {
  availability: archiveOnly,
  async execute({ messageId, attachmentId, shown: open }) {
    const f = { messageId, attachmentId };
    put(f, { expanded: open });
    if (!open) return done();
    if (shownOf(f).pages.length === 0) await read(f, shownOf(f).folder, null);
    return paged();
  },
});

registerCommand("bundle.folder", {
  availability: (f) => isArchive(f) && shownOf(f).expanded ? { available: true } : { available: false, reason: "no-entity" },
  async execute({ messageId, attachmentId, folder }) {
    await read({ messageId, attachmentId }, folder, null);
    return paged();
  },
});

registerCommand("bundle.folder.more", {
  availability: (f) => shownOf(f).expanded && lastPage(f)?.continuation
    ? { available: true } : { available: false, reason: "no-entity" },
  async execute({ messageId, attachmentId }) {
    const f = { messageId, attachmentId };
    await read(f, shownOf(f).folder, lastPage(f)!.continuation);
    return paged();
  },
});

/** Open on a file not read yet: what it is decides. A single document
 *  opens in the centre, an archive's contents show here. */
async function first(f: FileKey): Promise<void> {
  put(f, { busy: true, problem: null });
  try {
    const got = await classify(f);
    put(f, { what: got });
    if (got.container === "zip") runBinding(bind("bundle.contents", { ...f, shown: true }));
    else if (got.container === "none") put(f, { problem: await openMember({ ...f, memberId: got.root_member_id }) });
    else put(f, { problem: W.reason(got.reason) });
  } catch (e) {
    put(f, { problem: (e as Error).message });
  } finally {
    put(f, { busy: false });
  }
}

export function BundleInventory({ file, filename, part }: { file: FileKey; filename: string; part: PartRef }) {
  useT();
  const all = useSyncExternalStore(shown.subscribe, shown.get);
  const now = all[keyOf(file)] ?? EMPTY;
  const archive = now.what?.container === "zip";
  const toggle = bind("bundle.contents", { ...file, shown: !now.expanded });
  return (
    <div className="bundle-inventory">
      <div className="bundle-line">
        {archive
          ? <button type="button" className="file-action" disabled={now.busy} ref={part("contents")}
                    aria-expanded={now.expanded} aria-label={`${W.contents} ${filename}`} {...bindingProps(toggle)}>
              {W.contents}
            </button>
          : <button type="button" className="file-action" disabled={now.busy} ref={part("contents")}
                    aria-label={`${W.open} ${filename}`} data-ui-command="bundle.member.open"
                    onClick={() => void first(file)}>
              {W.open}
            </button>}
        {archive && now.what?.inventory && <span className="file-meta">{W.files(now.what.inventory.files)}</span>}
        {now.what?.package?.recognised && (
          <span className="bundle-badge" title={now.what.package.runs ?? undefined}>{W.package}</span>
        )}
        {now.problem && !now.expanded && <span className="file-state bundle-problem" role="status">{now.problem}</span>}
      </div>
      {archive && now.expanded && <Folder file={file} filename={filename} shown={now} part={part} />}
    </div>
  );
}

/** One folder of the archive: its members, a page at a time. The path
 *  above the list names where you are, starting from the zip's own name;
 *  every part but the last goes back there. */
function Folder({ file, filename, shown: now, part }: { file: FileKey; filename: string; shown: Shown; part: PartRef }) {
  const members = now.pages.flatMap((p) => p.members);
  const more = now.pages[now.pages.length - 1]?.continuation ?? null;
  const base = now.root ?? "";
  const inside = now.folder === base ? [] : now.folder.slice(base ? base.length + 1 : 0).split("/");
  const crumbs = [{ name: filename, path: base }, ...inside.map((name, i) => ({
    name, path: [base, ...inside.slice(0, i + 1)].filter(Boolean).join("/"),
  }))];
  return (
    <div className="bundle-folder">
      {crumbs.length > 1 && (
        <nav className="bundle-path" aria-label={W.folder} ref={part("path")}>
          {crumbs.map((c, i) => (
            <span key={c.path}>
              {i > 0 && " / "}
              {i < crumbs.length - 1
                ? <button type="button" className="link-button"
                          {...bindingProps(bind("bundle.folder", { ...file, folder: c.path }))}>{c.name}</button>
                : <span aria-current="location">{c.name}</span>}
            </span>
          ))}
        </nav>
      )}
      <ul className="bundle-members">
        {members.map((m) => <Member key={m.member_id} m={m} file={file} />)}
      </ul>
      {now.pages.length === 0 && !now.problem && <p className="file-state">{W.reading}</p>}
      {now.problem && <p className="file-state bundle-problem" role="status">{now.problem}</p>}
      {more && (
        <button type="button" className="file-action" ref={part("more")}
                {...bindingProps(bind("bundle.folder.more", { ...file }))}>{W.more}</button>
      )}
    </div>
  );
}

function Member({ m, file }: { m: MemberInfo; file: FileKey }) {
  const readable = m.support === "readable" || m.support === "partial";
  const ui = useUiTarget({ ref: { region: "main", view: "main.queue", kind: "bundleEntry", key: m.member_id },
                           label: m.name, socket: CENTRE_SOCKET });
  return (
    <li className={`bundle-member support-${m.support}${m.is_dir ? " is-dir" : ""}`}
        data-member-id={m.member_id} ref={ui.whole}>
      {m.is_dir
        ? <button type="button" className="link-button bundle-name" ref={ui.part("name")}
                  {...bindingProps(bind("bundle.folder", { ...file, folder: m.path }))}>{m.name}/</button>
        : <span className="bundle-name" ref={ui.part("name")}>{m.name}</span>}
      {!m.is_dir && <span className="file-meta">{m.format} · {sizeText(m.size_bytes)}</span>}
      {m.support !== "readable" && !m.is_dir && (
        <span className="file-state bundle-support" title={W.reason(m.reason)}>{W.reason(m.reason) || m.support}</span>
      )}
      {!m.is_dir && readable && <OpenMember file={file} memberId={m.member_id} name={m.name} part={ui.part} />}
    </li>
  );
}

/** Open on one member: the server's one way to show it, then here. */
function OpenMember({ file, memberId, name, part }: { file: FileKey; memberId: string; name: string; part: PartRef }) {
  const problems = useSyncExternalStore(memberProblems.subscribe, memberProblems.get);
  return (
    <>
      <button type="button" className="file-action" aria-label={`${W.open} ${name}`} ref={part("open")}
              data-ui-command="bundle.member.open" data-ui-args={JSON.stringify({ ...file, memberId })}
              onClick={async () => memberProblems.set({ ...memberProblems.get(),
                                                         [memberId]: await openMember({ ...file, memberId }) })}>
        {W.open}
      </button>
      {problems[memberId] && <span className="file-state bundle-problem" role="status">{problems[memberId]}</span>}
    </>
  );
}

/** Why a member's Open did not show it, by member. */
const memberProblems = createStore<Record<string, string | null>>({});
