/**
 * One file a teammate sent, in the centre area, opened by its Open in the
 * Queue or the brain's `show_bundle_member` (bundles.ts). Read with the same
 * routes the brain reads it with:
 *
 *   - a PDF: its pages as rendered images, one at a time with Previous and
 *     Next, beside its text;
 *   - text, Markdown and JSON: the text, a page at a time with More;
 *   - CSV, TSV and sheets: a table of the rows read so far, with More;
 *   - an image: the image;
 *   - HTML: a static preview in a frame with no permissions at all
 *     (`sandbox=""`: no script, no origin; the server sends the same
 *     sandbox), or its text.
 *
 * Every word in it is the sender's and is shown as text. Each part says
 * where it is in the file (page, lines, sheet or slide), so notes can cite
 * it. What is shown is never more than was read: a page that is not the end
 * says so with More.
 *
 * Registered for pointing at as `bundleMember` (its head, Preview and Text,
 * Close, the page turner, the text and More); Close runs
 * `bundle.member.close`, as the brain does.
 */

import { useEffect, useSyncExternalStore } from "react";
import type { Location, MemberInfo, Preview, TextChunk, TextPage } from "./bundleContract";
import {
  bundleWords as W, cells, openBundleMember, PAGED_FORMATS, pageUrl, readPreview, readText,
  TABLE_FORMATS, type MemberKey,
} from "./bundles";
import { CENTRE_SOCKET } from "./ConversationView";
import { t as tNow, useT } from "./locale";
import { sizeText } from "./queueStore";
import { createStore } from "./store";
import type { CommandArgs } from "./uiContract";
import { bind, bindingProps } from "./uiNavigation";
import { registerCommand, useUiTarget } from "./uiRegistry";

function where(location: Location): string {
  switch (location.kind) {
    case "page": return tNow("main.bundle.where.page", { page: location.page });
    case "lines": return location.start === location.end ? tNow("main.bundle.where.line", { line: location.start })
      : tNow("main.bundle.where.lines", { start: location.start, end: location.end });
    case "sheet": return tNow("main.bundle.where.sheet", { sheet: location.sheet, start: location.start_row,
                                                           end: location.end_row });
    case "slide": return tNow("main.bundle.where.slide", { slide: location.slide });
  }
}

/** The open member as shown: its text pages read so far, its preview,
 *  whether its text or its preview is shown, which rendered page, and what
 *  went wrong. Kept here, for the one member the centre shows; changed by
 *  `bundle.member.view` and `bundle.member.more`, a click's and the
 *  brain's alike. */
interface Shown {
  key: string;
  pages: TextPage[];
  preview: Preview | null;
  problem: string | null;
  reading: boolean;
  showText: boolean;
  page: number;
}

const lastOf = (pages: TextPage[]): TextPage | undefined => pages[pages.length - 1];
const keyOf = (m: MemberKey) => `${m.messageId}/${m.attachmentId}/${m.memberId}`;
const shown = createStore<Shown | null>(null);
const put = (key: string, change: Partial<Shown>) => {
  const now = shown.get();
  if (now?.key === key) shown.set({ ...now, ...change });
};

/** Read a member afresh: its first text page and its preview. */
function readFirst(m: MemberKey): void {
  const key = keyOf(m);
  shown.set({ key, pages: [], preview: null, problem: null, reading: true, showText: false, page: 1 });
  void Promise.allSettled([readText(m), readPreview(m)]).then(([text, seen]) => {
    put(key, {
      pages: text.status === "fulfilled" ? [text.value] : [],
      preview: seen.status === "fulfilled" ? seen.value : null,
      problem: text.status === "rejected" && seen.status === "rejected" ? (text.reason as Error).message : null,
      reading: false,
    });
  });
}

const showing = () => {
  const now = shown.get();
  const open = openBundleMember.get();
  return now && open && now.key === keyOf(open) ? now : null;
};

registerCommand("bundle.member.view", {
  availability: () => showing() ? { available: true } : { available: false, reason: "no-entity" },
  execute({ show, page }) {
    const now = showing()!;
    put(now.key, { ...(show ? { showText: show === "text" } : {}), ...(page ? { page } : {}) });
    return { status: "completed", effects: [] };
  },
});

registerCommand("bundle.member.more", {
  availability: () => lastOf(showing()?.pages ?? [])?.continuation && !showing()!.reading
    ? { available: true } : { available: false, reason: "no-entity" },
  async execute() {
    const now = showing()!;
    const member = openBundleMember.get()!;
    put(now.key, { reading: true });
    try {
      const next = await readText(member, lastOf(now.pages)!.continuation);
      put(now.key, { pages: [...(shown.get()?.pages ?? []), next] });
    } catch (e) {
      put(now.key, { problem: (e as Error).message });
    } finally {
      put(now.key, { reading: false });
    }
    return { status: "completed", effects: ["list.paged"] };
  },
});

export function BundleMemberView({ member }: { member: MemberKey }) {
  useT();
  const { messageId, attachmentId, memberId } = member;
  useEffect(() => { readFirst({ messageId, attachmentId, memberId }); }, [messageId, attachmentId, memberId]);
  const now = useSyncExternalStore(shown.subscribe, shown.get);
  const current = now?.key === keyOf(member) ? now : null;
  const pages = current?.pages ?? [];
  const preview = current?.preview ?? null;
  const problem = current?.problem ?? null;
  const reading = current?.reading ?? true;
  const showText = current?.showText ?? false;
  const page = current?.page ?? 1;
  const view = (args: CommandArgs["bundle.member.view"]) => bindingProps(bind("bundle.member.view", args));

  const info: MemberInfo | undefined = pages[0]?.member ?? preview?.member;
  const chunks = pages.flatMap((p) => p.chunks);
  const last = pages[pages.length - 1];
  const notes = [...new Set(pages.flatMap((p) => p.notes))];
  const html = preview?.refs.find((r) => r.kind === "html");
  const image = preview?.refs.find((r) => r.kind === "image");
  const paged = info ? PAGED_FORMATS.has(info.format) : false;
  const pageCount = last?.extent.pages ?? preview?.extent.pages ?? null;
  const blocked = preview?.blocked_resources.length ?? 0;

  const ui = useUiTarget({ ref: { region: "main", view: "main.file", kind: "bundleMember", key: memberId },
                           label: info?.name ?? W.reading, socket: CENTRE_SOCKET });
  const text = chunks.length > 0 && (
    <TextChunks chunks={chunks} table={info ? TABLE_FORMATS.has(info.format) : false}
                format={info?.format ?? "text"} />
  );
  return (
    <div id="bundle-member-view" data-member-id={memberId} ref={ui.whole}>
      <section className="package-head" ref={ui.part("header")}>
        <div className="package-head-words">
          <h2>{info?.name ?? W.reading}</h2>
          {info && <p>{info.path} · {info.format} · {sizeText(info.size_bytes)}</p>}
        </div>
        {html && chunks.length > 0 && (
          <div className="item-actions" role="group" aria-label={W.show} ref={ui.part("show")}>
            <button type="button" aria-pressed={!showText} {...view({ show: "preview" })}>{W.preview}</button>
            <button type="button" aria-pressed={showText} {...view({ show: "text" })}>{W.text}</button>
          </div>
        )}
        <button type="button" className="workflow-close" title={W.close} aria-label={W.close} ref={ui.part("close")}
                {...bindingProps(bind("bundle.member.close", {}))}>×</button>
      </section>
      {notes.length > 0 && <p className="bundle-notes">{notes.map((n) => W.reason(n)).join(" ")}</p>}
      {problem && <p className="file-state bundle-problem" role="status">{problem}</p>}
      <div className={`bundle-body${paged ? " with-pages" : ""}`}>
        {paged && (
          <figure className="bundle-page">
            <img src={pageUrl(member, page)} alt={`${info?.name ?? ""} ${W.pageOf(page, pageCount)}`} />
            <figcaption className="item-actions" ref={ui.part("pages")}>
              <button type="button" disabled={page <= 1} {...view({ page: page - 1 })}>{W.previous}</button>
              <span className="bundle-page-number">{W.pageOf(page, pageCount)}</span>
              <button type="button" disabled={pageCount !== null && page >= pageCount}
                      {...view({ page: page + 1 })}>{W.next}</button>
            </figcaption>
          </figure>
        )}
        {image && !paged && <img className="bundle-image" src={image.url} alt={info?.name ?? ""} />}
        {html && !showText && (
          <div className="bundle-html">
            <iframe className="package-frame" title={info?.name ?? W.preview} src={html.url}
                    sandbox="" referrerPolicy="no-referrer" />
            {blocked > 0 && <p className="file-state" title={preview!.blocked_resources.join("\n")}>{W.blocked(blocked)}</p>}
          </div>
        )}
        {(!html || showText) && (
          <div className="bundle-text" ref={ui.part("body")}>
            {text}
            {!reading && chunks.length === 0 && !image && !paged && !problem && <p className="file-state">{W.nothing}</p>}
            {reading && <p className="file-state">{W.reading}</p>}
            {last?.continuation && !reading && (
              <button type="button" className="file-action" ref={ui.part("more")}
                      {...bindingProps(bind("bundle.member.more", {}))}>{W.more}</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function TextChunks({ chunks, table, format }: {
  chunks: TextChunk[]; table: boolean; format: MemberInfo["format"];
}) {
  return (
    <>
      {chunks.map((c, i) => (
        <section key={i} className="bundle-chunk">
          <h3 className="bundle-where">{where(c.location)}</h3>
          {table
            ? (
              <div className="bundle-table-wrap">
                <table className="bundle-table">
                  <tbody>
                    {c.text.split("\n").filter((row) => row !== "").map((row, r) => (
                      <tr key={r}>{cells(row, format).map((cell, k) => <td key={k}>{cell}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
            : <div className="message-text">{c.text}</div>}
        </section>
      ))}
    </>
  );
}
