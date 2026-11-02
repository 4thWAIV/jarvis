/**
 * What a received, checked file holds, on its card in the Queue (mounted by
 * QueueFiles.tsx). Open reads what it is (bundles.ts): a single document
 * opens in the centre area (BundleMemberView.tsx); an archive shows its
 * contents here, one folder at a time, each member with its name, type and
 * size, its state only when it is not readable, and Open. A zip that is
 * also a work package says so; opening or running a package stays with the
 * card's existing Open page and the brain's open_package.
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
      {archive && expanded && <Folder file={file} />}
    </div>
  );
}

/** One folder of the archive: its members, a page at a time. */
function Folder({ file }: { file: FileKey }) {
  const [folder, setFolder] = useState("");
  const [pages, setPages] = useState<MemberPage[]>([]);
  const [problem, setProblem] = useState<string | null>(null);

  const { messageId, attachmentId } = file;
  const read = useCallback(async (where: string, continuation: string | null) => {
    setProblem(null);
    try {
      const page = await listMembers({ messageId, attachmentId }, where, continuation);
      setPages((now) => (continuation ? [...now, page] : [page]));
    } catch (e) {
      setProblem((e as Error).message);
    }
  }, [messageId, attachmentId]);
  useEffect(() => { setPages([]); void read(folder, null); }, [folder, read]);

  const members = pages.flatMap((p) => p.members);
  const more = pages[pages.length - 1]?.continuation ?? null;
  const parent = folder.includes("/") ? folder.slice(0, folder.lastIndexOf("/")) : "";
  return (
    <div className="bundle-folder">
      {folder && (
        <nav className="bundle-path" aria-label="Folder">
          <button type="button" className="link-button" onClick={() => setFolder("")}>{W.top}</button>
          {folder.split("/").map((part, i, all) => (
            <span key={i}>
              {" / "}
              <button type="button" className="link-button"
                      onClick={() => setFolder(all.slice(0, i + 1).join("/"))}>{part}</button>
            </span>
          ))}
        </nav>
      )}
      <ul className="bundle-members">
        {folder && (
          <li className="bundle-member is-dir">
            <button type="button" className="link-button" onClick={() => setFolder(parent)}
                    aria-label="Up">..</button>
          </li>
        )}
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
