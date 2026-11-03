/**
 * What a received, checked file holds, on its card in the Queue (mounted by
 * QueueFiles.tsx). Open reads what it is (bundles.ts): a single document
 * opens in the centre area (BundleMemberView.tsx); an archive shows its
 * contents here, one folder at a time, each member with its name, type and
 * size, its state only when it is not readable, and Open. A zip that is
 * also a work package says so; opening or running a package stays with the
 * card's Open package and Open page (QueueFiles.tsx) and the brain's
 * open_package.
 */

import { useCallback, useEffect, useState } from "react";
import type { Classification, MemberInfo, MemberPage } from "./bundleContract";
import {
  bundleWords as W, classify, listMembers, openMember, type FileKey,
} from "./bundles";
import { sizeText } from "./queueStore";

export function BundleInventory({ file, filename }: { file: FileKey; filename: string }) {
  const [what, setWhat] = useState<Classification | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const open = async () => {
    if (what?.container === "zip") { setExpanded((now) => !now); return; }
    setBusy(true);
    setProblem(null);
    try {
      const got = what ?? await classify(file);
      setWhat(got);
      if (got.container === "zip") setExpanded(true);
      else if (got.container === "none") setProblem(await openMember({ ...file, memberId: got.root_member_id }));
      else setProblem(W.reason(got.reason));
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const archive = what?.container === "zip";
  return (
    <div className="bundle-inventory">
      <div className="bundle-line">
        <button type="button" className="file-action" disabled={busy}
                aria-expanded={archive ? expanded : undefined}
                aria-label={`${archive ? W.contents : W.open} ${filename}`} onClick={() => void open()}>
          {archive ? W.contents : W.open}
        </button>
        {archive && what?.inventory && <span className="file-meta">{W.files(what.inventory.files)}</span>}
        {what?.package?.recognised && (
          <span className="bundle-badge" title={what.package.runs ?? undefined}>{W.package}</span>
        )}
        {problem && <span className="file-state bundle-problem" role="status">{problem}</span>}
      </div>
      {archive && expanded && <Folder file={file} filename={filename} />}
    </div>
  );
}

/** One folder of the archive: its members, a page at a time. A zip whose
 *  top level is one folder and nothing else opens inside it, since that
 *  folder only wraps the files. The path above the list names where you
 *  are, starting from the zip's own name; every part but the last goes
 *  back there. */
function Folder({ file, filename }: { file: FileKey; filename: string }) {
  const [root, setRoot] = useState<string | null>(null);
  const [folder, setFolder] = useState("");
  const [pages, setPages] = useState<MemberPage[]>([]);
  const [problem, setProblem] = useState<string | null>(null);

  const { messageId, attachmentId } = file;
  const read = useCallback(async (where: string, continuation: string | null) => {
    setProblem(null);
    try {
      const page = await listMembers({ messageId, attachmentId }, where, continuation);
      if (where === "" && !continuation && root === null) {
        const only = page.members.length === 1 && page.members[0].is_dir && !page.continuation
          ? page.members[0].path : "";
        setRoot(only);
        if (only) { setFolder(only); return; }
      }
      setPages((now) => (continuation ? [...now, page] : [page]));
    } catch (e) {
      setProblem((e as Error).message);
    }
  }, [messageId, attachmentId, root]);
  useEffect(() => { setPages([]); void read(folder, null); }, [folder]); // eslint-disable-line react-hooks/exhaustive-deps

  const members = pages.flatMap((p) => p.members);
  const more = pages[pages.length - 1]?.continuation ?? null;
  const base = root ?? "";
  const inside = folder === base ? [] : folder.slice(base ? base.length + 1 : 0).split("/");
  const crumbs = [{ name: filename, path: base }, ...inside.map((part, i) => ({
    name: part, path: [base, ...inside.slice(0, i + 1)].filter(Boolean).join("/"),
  }))];
  return (
    <div className="bundle-folder">
      {crumbs.length > 1 && (
        <nav className="bundle-path" aria-label="Folder">
          {crumbs.map((c, i) => (
            <span key={c.path}>
              {i > 0 && " / "}
              {i < crumbs.length - 1
                ? <button type="button" className="link-button" onClick={() => setFolder(c.path)}>{c.name}</button>
                : <span aria-current="location">{c.name}</span>}
            </span>
          ))}
        </nav>
      )}
      <ul className="bundle-members">
        {members.map((m) => <Member key={m.member_id} m={m} file={file} enter={setFolder} />)}
      </ul>
      {pages.length === 0 && !problem && <p className="file-state">{W.reading}</p>}
      {problem && <p className="file-state bundle-problem" role="status">{problem}</p>}
      {more && (
        <button type="button" className="file-action" onClick={() => void read(folder, more)}>{W.more}</button>
      )}
    </div>
  );
}

function Member({ m, file, enter }: { m: MemberInfo; file: FileKey; enter: (folder: string) => void }) {
  const [problem, setProblem] = useState<string | null>(null);
  const readable = m.support === "readable" || m.support === "partial";
  return (
    <li className={`bundle-member support-${m.support}${m.is_dir ? " is-dir" : ""}`}
        data-member-id={m.member_id}>
      {m.is_dir
        ? <button type="button" className="link-button bundle-name" onClick={() => enter(m.path)}>{m.name}/</button>
        : <span className="bundle-name">{m.name}</span>}
      {!m.is_dir && <span className="file-meta">{m.format} · {sizeText(m.size_bytes)}</span>}
      {m.support !== "readable" && !m.is_dir && (
        <span className="file-state bundle-support" title={W.reason(m.reason)}>{W.reason(m.reason) || m.support}</span>
      )}
      {!m.is_dir && readable && (
        <button type="button" className="file-action" aria-label={`${W.open} ${m.name}`}
                onClick={async () => setProblem(await openMember({ ...file, memberId: m.member_id }))}>
          {W.open}
        </button>
      )}
      {problem && <span className="file-state bundle-problem" role="status">{problem}</span>}
    </li>
  );
}
