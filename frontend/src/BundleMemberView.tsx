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
 */

import { useEffect, useState } from "react";
import type { Location, MemberInfo, Preview, TextChunk, TextPage } from "./bundleContract";
import {
  bundleWords as W, cells, openBundleMember, PAGED_FORMATS, pageUrl, readPreview, readText,
  TABLE_FORMATS, type MemberKey,
} from "./bundles";
import { sizeText } from "./queueStore";

function where(location: Location): string {
  switch (location.kind) {
    case "page": return `Page ${location.page}`;
    case "lines": return location.start === location.end ? `Line ${location.start}`
      : `Lines ${location.start}–${location.end}`;
    case "sheet": return `${location.sheet}, rows ${location.start_row}–${location.end_row}`;
    case "slide": return `Slide ${location.slide}`;
  }
}

export function BundleMemberView({ member }: { member: MemberKey }) {
  const [pages, setPages] = useState<TextPage[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [reading, setReading] = useState(true);
  const [showText, setShowText] = useState(false);
  const [page, setPage] = useState(1);
  const { messageId, attachmentId, memberId } = member;

  useEffect(() => {
    let live = true;
    const key = { messageId, attachmentId, memberId };
    Promise.allSettled([readText(key), readPreview(key)]).then(([text, shown]) => {
      if (!live) return;
      if (text.status === "fulfilled") setPages([text.value]);
      if (shown.status === "fulfilled") setPreview(shown.value);
      if (text.status === "rejected" && shown.status === "rejected") {
        setProblem((text.reason as Error).message);
      }
      setReading(false);
    });
    return () => { live = false; };
  }, [messageId, attachmentId, memberId]);

  const more = async () => {
    const last = pages[pages.length - 1];
    if (!last?.continuation) return;
    setReading(true);
    try {
      const next = await readText(member, last.continuation);
      setPages((now) => [...now, next]);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setReading(false);
    }
  };

  const info: MemberInfo | undefined = pages[0]?.member ?? preview?.member;
  const chunks = pages.flatMap((p) => p.chunks);
  const last = pages[pages.length - 1];
  const notes = [...new Set(pages.flatMap((p) => p.notes))];
  const html = preview?.refs.find((r) => r.kind === "html");
  const image = preview?.refs.find((r) => r.kind === "image");
  const paged = info ? PAGED_FORMATS.has(info.format) : false;
  const pageCount = last?.extent.pages ?? preview?.extent.pages ?? null;
  const blocked = preview?.blocked_resources.length ?? 0;

  const text = chunks.length > 0 && (
    <TextChunks chunks={chunks} table={info ? TABLE_FORMATS.has(info.format) : false}
                format={info?.format ?? "text"} />
  );
  return (
    <div id="bundle-member-view" data-member-id={memberId}>
      <section className="package-head">
        <div className="package-head-words">
          <h2>{info?.name ?? W.reading}</h2>
          {info && <p>{info.path} · {info.format} · {sizeText(info.size_bytes)}</p>}
        </div>
        {html && chunks.length > 0 && (
          <div className="item-actions" role="group" aria-label="Show">
            <button type="button" aria-pressed={!showText} onClick={() => setShowText(false)}>{W.preview}</button>
            <button type="button" aria-pressed={showText} onClick={() => setShowText(true)}>{W.text}</button>
          </div>
        )}
        <button type="button" className="workflow-close" title={W.close} aria-label={W.close}
                onClick={() => openBundleMember.set(null)}>×</button>
      </section>
      {notes.length > 0 && <p className="bundle-notes">{notes.map((n) => W.reason(n)).join(" ")}</p>}
      {problem && <p className="file-state bundle-problem" role="status">{problem}</p>}
      <div className={`bundle-body${paged ? " with-pages" : ""}`}>
        {paged && (
          <figure className="bundle-page">
            <img src={pageUrl(member, page)} alt={`${info?.name ?? ""} ${W.pageOf(page, pageCount)}`} />
            <figcaption className="item-actions">
              <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>{W.previous}</button>
              <span className="bundle-page-number">{W.pageOf(page, pageCount)}</span>
              <button type="button" disabled={pageCount !== null && page >= pageCount}
                      onClick={() => setPage(page + 1)}>{W.next}</button>
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
          <div className="bundle-text">
            {text}
            {!reading && chunks.length === 0 && !image && !paged && !problem && <p className="file-state">{W.nothing}</p>}
            {reading && <p className="file-state">{W.reading}</p>}
            {last?.continuation && !reading && (
              <button type="button" className="file-action" onClick={() => void more()}>{W.more}</button>
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
