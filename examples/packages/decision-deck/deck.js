// The shared decision deck: draws every slide from deck-data.js (window.DECK)
// and records the answers.
//
// The record is results/decisions.json, with results/decisions.md and
// results/decisions.js beside it. scripts/decide.py writes the same three
// files in the same format, so an answer given by voice through JARVIS and
// one given here agree; the page reads results/decisions.js back every few
// seconds, which is how a spoken answer appears on screen. A page package's
// frame has no origin, so it reads its own files only with <script src> and
// saves only through JARVIS's bridge (docs/work-packages.md).
//
// Every word shown is set with textContent. The current slide is kept in
// memory only.
(function () {
  "use strict";

  var DECK = window.DECK || { title: "", slides: [], decisions: [] };
  var W = 880, H = 640;                        // the fixed canvas, scaled to fit
  var MAX_SCALE = 22 / 18;                     // 18 px body text, at most 22 px on screen
  var RECORDED = "results/decisions.js";
  var FOLLOW_MS = 3000;
  var inFrame = window.parent !== window;

  // ── small helpers ─────────────────────────────────────────────────────
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  // **bold** and `code` inside a line of text, built as elements.
  function inline(parent, text) {
    String(text || "").split(/(\*\*[^*]+\*\*|`[^`]+`)/).forEach(function (part) {
      if (!part) return;
      if (part.slice(0, 2) === "**" && part.slice(-2) === "**") parent.appendChild(el("b", "", part.slice(2, -2)));
      else if (part[0] === "`" && part.slice(-1) === "`") parent.appendChild(el("code", "", part.slice(1, -1)));
      else parent.appendChild(document.createTextNode(part));
    });
    return parent;
  }

  function withId(node, id) { if (id) node.id = id; return node; }

  var DECISIONS = DECK.decisions || [];
  var LETTERS = "ABCDEFGHIJ";
  function byId(id) { return DECISIONS.filter(function (d) { return d.id === id; })[0]; }
  function optionOf(d, id) { return (d.options || []).filter(function (o) { return o.id === id; })[0]; }

  // A child question applies only when its parent's answer is an option
  // that opens it (option.opens), and its parent applies.
  var PARENT = {};
  DECISIONS.forEach(function (d) {
    (d.options || []).forEach(function (o) {
      (o.opens || []).forEach(function (child) {
        var p = PARENT[child] || (PARENT[child] = { decision: d.id, options: [] });
        p.options.push(o.id);
      });
    });
  });

  function applies(d, rec) {
    var p = PARENT[d.id];
    if (!p) return true;
    var e = rec[p.decision];
    return !!e && p.options.indexOf(e.answer) >= 0 && applies(byId(p.decision), rec);
  }

  function opensText(d) {
    var p = PARENT[d.id];
    if (!p) return "";
    var parent = byId(p.decision);
    var names = p.options.map(function (id) { return optionOf(parent, id).name; });
    return parent.id + " is " + names.join(" or ");
  }

  // ── the record ────────────────────────────────────────────────────────
  // {"<decision id>": {"answer": "<option id>" | "later" | "none",
  //   "at": "...", "choice": "<option name>" or "", "note": "...", "recommended": true|false}}
  var record = {};

  function clean(raw) {
    var out = {};
    if (!raw || typeof raw !== "object") return out;
    DECISIONS.forEach(function (d) {
      var e = raw[d.id];
      if (!e || typeof e.answer !== "string") return;
      if (e.answer !== "later" && e.answer !== "none" && !optionOf(d, e.answer)) return;
      out[d.id] = { answer: e.answer, at: String(e.at || ""), choice: String(e.choice || ""),
                    note: String(e.note || ""), recommended: !!e.recommended };
    });
    return out;
  }

  function stamp() {
    var d = new Date(), p = function (n) { return String(n).padStart(2, "0"); };
    var off = -d.getTimezoneOffset(), a = Math.abs(off);
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" +
      p(d.getMinutes()) + ":" + p(d.getSeconds()) + (off >= 0 ? "+" : "-") + p(Math.floor(a / 60)) + ":" + p(a % 60);
  }

  function entry(d, answer, note) {
    var o = optionOf(d, answer);
    return { answer: answer, at: stamp(), choice: o ? o.name : "", note: note || "",
             recommended: answer === d.recommended };
  }

  // json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
  // exactly as scripts/decide.py writes it.
  function dumps(rec) {
    var sorted = {};
    Object.keys(rec).sort().forEach(function (id) {
      var e = rec[id];
      sorted[id] = { answer: e.answer, at: e.at, choice: e.choice, note: e.note, recommended: e.recommended };
    });
    return JSON.stringify(sorted, null, 2) + "\n";
  }

  // scripts/decide.py's markdown(), line for line.
  function markdown(rec) {
    var lines = ["# " + DECK.title, ""];
    DECISIONS.forEach(function (d) {
      var e = rec[d.id];
      lines.push("## " + d.id, "", d.question, "");
      if (!e) lines.push(applies(d, rec) ? "Not answered." : "Not asked: it applies only when " + opensText(d) + ".");
      else if (e.answer === "later") lines.push("Decide later. Recorded " + e.at + ".");
      else if (e.answer === "none") lines.push("None of these. Recorded " + e.at + ".");
      else lines.push("Answer: " + e.choice + (e.recommended ? " (the recommended option)" : " (not the recommended option)") +
                      ". Recorded " + e.at + ".");
      if (e && e.note) lines.push("", "Note: " + e.note);
      lines.push("");
    });
    return lines.join("\n");
  }

  function answerText(d, e) {
    if (!e) return applies(d, record) ? "Not answered yet." : "Not asked yet: it applies when " + opensText(d) + ".";
    if (e.answer === "later") return "Decide later.";
    if (e.answer === "none") return "None of these.";
    return e.choice + (e.recommended ? ", the recommended option." : ".");
  }

  // Every unanswered decision that applies gets its recommended option; a
  // child question opened by one of those is filled in the same pass.
  function withRecommended(rec) {
    var out = {}, added = [], changed = true;
    Object.keys(rec).forEach(function (k) { out[k] = rec[k]; });
    while (changed) {
      changed = false;
      DECISIONS.forEach(function (d) {
        if (out[d.id] || !applies(d, out) || !optionOf(d, d.recommended)) return;
        out[d.id] = entry(d, d.recommended, "");
        added.push(d.id);
        changed = true;
      });
    }
    return { record: out, added: added };
  }

  // ── reading and saving ────────────────────────────────────────────────
  function loadRecorded() {
    return new Promise(function (resolve) {
      delete window.JARVIS_DECISIONS;
      var s = document.createElement("script");
      s.src = RECORDED + "?t=" + Date.now();
      s.onload = function () { s.remove(); resolve(window.JARVIS_DECISIONS || null); };
      s.onerror = function () { s.remove(); resolve(null); };
      document.head.appendChild(s);
    });
  }

  var saveId = 0, pending = {}, failed = [], busy = 0, queue = Promise.resolve();
  var statusLine = document.getElementById("status");
  function say(words) { statusLine.textContent = words; }

  function post(name, data) {
    saveId += 1;
    pending[saveId] = name;
    window.parent.postMessage({ jarvis: "package", type: "save", id: saveId, name: name, data: data }, "*");
  }

  function persist() {
    if (!inFrame) { say("Outside JARVIS: nothing is saved."); return; }
    var text = dumps(record);
    post("decisions.json", text);
    post("decisions.md", markdown(record));
    post("decisions.js", "window.JARVIS_DECISIONS = " + text.trimEnd() + ";\n");
    say("Saving…");
  }

  // JARVIS's story says which question it is waiting on: that slide is
  // brought up and says so; any other state clears it.
  function asking(m) {
    DECISIONS.forEach(function (d) {
      var on = m.state === "waiting" && m.question === d.id;
      views[d.id].section.classList.toggle("asking", on);
      if (on && slides[current].decision !== d.id) show(slideOf(d.id));
    });
  }

  window.addEventListener("message", function (event) {
    var m = event.data;
    if (event.source === window.parent && m && m.jarvis === "package" && m.type === "story.state") {
      asking(m);
      return;
    }
    if (event.source !== window.parent || !m || m.jarvis !== "package" || m.type !== "saved") return;
    if (!(m.id in pending)) return;
    delete pending[m.id];
    if (!m.ok) failed.push(m.name + ": " + (m.error || "no reason given"));
    if (Object.keys(pending).length) return;
    say(failed.length ? "Not saved: " + failed.join("; ") : "Saved.");
    failed = [];
  });

  // One change at a time: read what is recorded now (a spoken answer may
  // have landed), make the change on it, save, draw.
  function change(apply) {
    busy += 1;
    queue = queue.then(function () { return loadRecorded(); }).then(function (fresh) {
      if (fresh) record = clean(fresh);
      apply();
      persist();
      render();
    }).finally(function () { busy -= 1; });
    return queue;
  }

  function follow() {
    if (busy || Object.keys(pending).length) return;
    loadRecorded().then(function (fresh) {
      if (!fresh || busy) return;
      var next = clean(fresh);
      if (JSON.stringify(next) === JSON.stringify(record)) return;
      record = next;
      render();
    });
  }

  // ── the slides ────────────────────────────────────────────────────────
  var track = document.getElementById("track");
  var strip = document.getElementById("strip");
  var slides = [];          // {el, title, decision?}
  var current = 0;
  var views = {};           // decision id -> its slide's parts

  function block(b) {
    if (b.p !== undefined) return withId(inline(el("p", "text"), b.p), b.id);
    if (b.lead !== undefined) return withId(inline(el("p", "lead"), b.lead), b.id);
    if (b.note !== undefined) return withId(inline(el("p", "note"), b.note), b.id);
    if (b.points) {
      var ul = withId(el("ul", "points"), b.id);
      b.points.forEach(function (pt) {
        var li = el("li");
        if (typeof pt === "string") inline(li, pt);
        else {
          if (pt.id) li.id = pt.id;
          if (pt.b) { li.appendChild(el("b", "", pt.b)); li.appendChild(document.createTextNode(" ")); }
          inline(li, pt.text);
        }
        ul.appendChild(li);
      });
      return ul;
    }
    if (b.facts) {
      var facts = withId(el("ul", "facts"), b.id);
      b.facts.forEach(function (f) {
        var li = withId(el("li"), f.id);
        li.appendChild(el("b", "", f.n));
        li.appendChild(inline(el("span"), f.text));
        facts.appendChild(li);
      });
      return facts;
    }
    if (b.table) {
      var t = b.table, table = withId(el("table", "table"), b.id);
      if (t.head) {
        var tr = el("tr");
        t.head.forEach(function (h) { tr.appendChild(inline(el("th"), h)); });
        table.appendChild(el("thead")).appendChild(tr);
      }
      var tbody = table.appendChild(el("tbody"));
      (t.rows || []).forEach(function (row, i) {
        var r = withId(el("tr"), t.rowIds && t.rowIds[i]);
        row.forEach(function (cell, j) { r.appendChild(inline(el(j === 0 && t.rowHeads ? "th" : "td"), cell)); });
        tbody.appendChild(r);
      });
      return table;
    }
    if (b.picture || b.pictures) {
      var list = b.pictures || [{ src: b.picture, alt: b.alt, caption: b.caption }];
      var row = withId(el("div", "pictures"), b.id);
      list.forEach(function (pic) { row.appendChild(figure(pic)); });
      return row;
    }
    return el("p", "text", "");
  }

  function figure(pic) {
    var f = withId(el("figure", "picture"), pic.id);
    var img = el("img");
    img.src = pic.src;
    img.alt = pic.alt || "";
    f.appendChild(img);
    if (pic.caption) f.appendChild(inline(el("figcaption"), pic.caption));
    return f;
  }

  function head(section, kicker, title, cls) {
    if (kicker) section.appendChild(el("p", "kicker", kicker));
    section.appendChild(inline(el("h2", cls || ""), title));
  }

  function contentSlide(s, n) {
    var section = el("section", "slide content");
    section.id = s.id || "s" + n;
    head(section, s.kicker, s.title);
    var body = section.appendChild(el("div", "body"));
    (s.blocks || []).forEach(function (b) { body.appendChild(block(b)); });
    return section;
  }

  function decisionSlide(d, index) {
    var section = el("section", "slide decision");
    section.id = "d-" + d.id;
    var kicker = "Decision " + (index + 1) + " of " + DECISIONS.length + " · " + d.id;
    if (PARENT[d.id]) kicker += " · asked when " + opensText(d);
    head(section, kicker, d.question, "question");
    // Shown while JARVIS's story is waiting for this answer (story.state).
    section.appendChild(el("p", "waiting", "Waiting for your answer: choose one, or say it"));
    var rec = optionOf(d, d.recommended);
    var why = el("p", "recommends");
    if (rec) {
      why.appendChild(document.createTextNode("Recommended: "));
      why.appendChild(el("b", "", rec.name));
      why.appendChild(document.createTextNode(". "));
    }
    inline(why, d.reason);
    section.appendChild(why);

    // A picture puts the context in a column beside the options; words
    // alone go in one line above them.
    if (d.context && !d.picture) section.appendChild(inline(el("p", "why"), d.context));
    var body = section.appendChild(el("div", "body decide"));
    // A slide with pictures gives them the room the words and the vote row
    // leave: they size to the space, never to a fixed thumbnail.
    var pictured = (d.options || []).some(function (o) { return o.picture; });
    if (pictured || d.picture || d.compare) section.classList.add("fill");
    if (d.picture) {
      body.classList.add("with-context");
      var side = body.appendChild(el("div", "context"));
      side.appendChild(figure(typeof d.picture === "string" ? { src: d.picture } : d.picture));
      if (d.context) side.appendChild(inline(el("p", "why"), d.context));
    }
    var main = d.picture ? body.appendChild(el("div", "main")) : body;
    if (d.compare) {
      var compare = figure(typeof d.compare === "string" ? { src: d.compare } : d.compare);
      compare.classList.add("compare");
      main.appendChild(compare);
    }
    var options = main.appendChild(el("div", "options" + (pictured ? " has-pictures" : "")));
    var n = (d.options || []).length;
    options.style.setProperty("--columns", String(pictured ? Math.min(4, n) : d.picture ? Math.min(2, n)
                                                : n === 4 ? 2 : Math.min(3, n)));
    var buttons = {};
    (d.options || []).forEach(function (o, i) {
      var card = el("article", "option" + (o.id === d.recommended ? " recommended" : ""));
      card.id = "opt-" + d.id + "-" + o.id;
      card.appendChild(el("span", "yours", "✓ Your choice"));
      if (o.picture) card.appendChild(figure(typeof o.picture === "string" ? { src: o.picture } : o.picture));
      var text = card.appendChild(el("div", "option-text"));
      // The letter is what he can say ("B"), as scripts/decide.py takes it.
      var name = text.appendChild(el("h3"));
      name.appendChild(el("span", "letter", LETTERS[i] + "."));
      name.appendChild(document.createTextNode(" " + o.name));
      if (o.id === d.recommended) name.appendChild(el("span", "badge", "Recommended"));
      text.appendChild(inline(el("p", "says"), o.says));
      if (o.opens && o.opens.length) text.appendChild(el("p", "opens", "Then asks: " + o.opens.map(function (c) {
        var child = byId(c);
        return child ? child.short || child.id : c;
      }).join(", ")));
      var b = text.appendChild(el("button", "choose", "Choose"));
      b.type = "button";
      b.setAttribute("aria-label", "Choose " + LETTERS[i] + ": " + o.name);
      b.addEventListener("click", function () { choose(d, o.id); });
      buttons[o.id] = { card: card, button: b };
      options.appendChild(card);
    });

    var vote = section.appendChild(el("div", "vote"));
    var later = vote.appendChild(el("button", "later", "Decide later"));
    later.type = "button";
    later.addEventListener("click", function () { choose(d, "later"); });
    var none = vote.appendChild(el("button", "none", "None of these"));
    none.type = "button";
    none.addEventListener("click", function () {
      if (!words.value.trim()) words.focus();
      choose(d, "none");
    });
    var words = vote.appendChild(el("input", "words"));
    words.type = "text";
    words.maxLength = 500;
    words.placeholder = "Your words (optional)";
    words.setAttribute("aria-label", "Your words on " + d.id);
    words.addEventListener("change", function () {
      if (!record[d.id]) return;            // kept until he chooses
      var said = words.value.trim();
      change(function () { if (record[d.id]) record[d.id].note = said; });
    });
    var recorded = section.appendChild(el("p", "recorded"));
    recorded.setAttribute("role", "status");
    views[d.id] = { section: section, options: options, buttons: buttons, later: later, none: none, words: words, recorded: recorded };
    return section;
  }

  function choose(d, answer) {
    var words = views[d.id].words.value.trim();
    change(function () { record[d.id] = entry(d, answer, words); });
  }

  // The last slide: every decision, its recorded answer, changed in place.
  var answerRows = {};
  var approve, confirmBox, confirmText;
  function answersSlide() {
    var section = el("section", "slide answers");
    section.id = "answers";
    head(section, "Your answers", "What is recorded");
    var list = section.appendChild(el("ol", "answer-list"));
    list.id = "answer-list";
    DECISIONS.forEach(function (d) {
      var li = el("li", PARENT[d.id] ? "child" : "");
      li.id = "answer-" + d.id;
      var go = li.appendChild(el("button", "go", d.short || d.id));
      go.type = "button";
      go.title = d.question;
      go.addEventListener("click", function () { show(slideOf(d.id)); });
      var pick = li.appendChild(el("select", "pick"));
      pick.setAttribute("aria-label", "Answer to " + d.id);
      var blank = pick.appendChild(el("option", "", "Not answered"));
      blank.value = "";
      blank.disabled = true;
      (d.options || []).forEach(function (o) {
        var opt = pick.appendChild(el("option", "", o.name + (o.id === d.recommended ? " (recommended)" : "")));
        opt.value = o.id;
      });
      pick.appendChild(el("option", "", "Decide later")).value = "later";
      pick.appendChild(el("option", "", "None of these")).value = "none";
      pick.addEventListener("change", function () {
        var answer = pick.value, note = record[d.id] ? record[d.id].note : "";
        change(function () { record[d.id] = entry(d, answer, note); });
      });
      var state = li.appendChild(el("span", "state"));
      answerRows[d.id] = { li: li, pick: pick, state: state };
      list.appendChild(li);
    });
    var row = section.appendChild(el("div", "approve-row"));
    approve = row.appendChild(el("button", "approve", "Approve all recommended"));
    approve.id = "approve-all";
    approve.type = "button";
    confirmBox = row.appendChild(el("div", "confirm"));
    confirmBox.hidden = true;
    confirmText = confirmBox.appendChild(el("span"));
    var yes = confirmBox.appendChild(el("button", "yes", "Yes, record them"));
    yes.type = "button";
    var no = confirmBox.appendChild(el("button", "no", "No"));
    no.type = "button";
    approve.addEventListener("click", function () {
      var n = withRecommended(record).added.length;
      confirmText.textContent = "Record the recommended option for the " + n + " decision" + (n === 1 ? "" : "s") +
        " with no answer yet? Answers already given, Decide later included, stay as they are.";
      confirmBox.hidden = false;
      approve.hidden = true;
      yes.focus();
    });
    function closeConfirm() { confirmBox.hidden = true; approve.hidden = false; }
    no.addEventListener("click", closeConfirm);
    yes.addEventListener("click", function () {
      closeConfirm();
      change(function () { record = withRecommended(record).record; });
    });
    return section;
  }

  function slideOf(id) {
    for (var i = 0; i < slides.length; i++) if (slides[i].decision === id) return i;
    return 0;
  }

  // Content slides in order; a {"decision": id} entry places that decision's
  // slide there, and every decision not placed follows the content; the
  // answers slide is last.
  function build() {
    var placed = {};
    (DECK.slides || []).forEach(function (s) { if (s.decision) placed[s.decision] = true; });
    var order = [];
    (DECK.slides || []).forEach(function (s) { order.push(s); });
    DECISIONS.forEach(function (d) { if (!placed[d.id]) order.push({ decision: d.id }); });
    order.forEach(function (s) {
      var n = slides.length + 1;
      if (s.decision) {
        var d = byId(s.decision);
        if (!d) return;
        slides.push({ el: decisionSlide(d, DECISIONS.indexOf(d)), title: d.short || d.id, decision: d.id });
      } else {
        slides.push({ el: contentSlide(s, n), title: s.short || s.title });
      }
    });
    // A presentation (no decisions) has nothing to answer: no answers slide.
    if (DECISIONS.length) slides.push({ el: answersSlide(), title: "Your answers" });
    slides.forEach(function (s, i) {
      track.appendChild(s.el);
      var b = el("button", "jump" + (s.decision ? " decision" : ""), String(i + 1));
      b.type = "button";
      b.id = "go-" + (i + 1);
      b.title = s.title;
      b.addEventListener("click", function () { show(i); });
      s.jump = b;
      strip.appendChild(b);
    });
  }

  function render() {
    if (!DECISIONS.length) return;          // a presentation records nothing
    var answered = 0;
    DECISIONS.forEach(function (d) {
      var e = record[d.id], v = views[d.id], row = answerRows[d.id], open = applies(d, record);
      if (e && e.answer !== "later") answered += 1;
      Object.keys(v.buttons).forEach(function (id) {
        var chosen = !!e && e.answer === id;
        v.buttons[id].card.classList.toggle("chosen", chosen);
        v.buttons[id].button.setAttribute("aria-pressed", String(chosen));
        v.buttons[id].button.textContent = chosen ? "✓ Chosen" : "Choose";
      });
      v.options.classList.toggle("answered", !!e && !!optionOf(d, e.answer));
      v.later.setAttribute("aria-pressed", String(!!e && e.answer === "later"));
      v.none.setAttribute("aria-pressed", String(!!e && e.answer === "none"));
      if (document.activeElement !== v.words) v.words.value = e ? e.note : v.words.value;
      v.recorded.textContent = (e ? "Recorded: " : "") + answerText(d, e) + (e && e.note ? " Your words: “" + e.note + "”" : "");
      v.recorded.classList.toggle("done", !!e && e.answer !== "later");
      v.section.classList.toggle("not-asked", !open && !e);
      row.pick.value = e ? e.answer : "";
      row.state.textContent = e || open ? "" : "Not asked yet";
      row.li.classList.toggle("done", !!e && e.answer !== "later");
      row.li.classList.toggle("not-asked", !open && !e);
      var jump = slides[slideOf(d.id)].jump;
      jump.classList.toggle("answered", !!e && e.answer !== "later");
      jump.classList.toggle("later", !!e && e.answer === "later");
      jump.classList.toggle("not-asked", !open && !e);
    });
    var left = withRecommended(record).added.length;
    approve.disabled = left === 0;
    document.getElementById("tally").textContent = answered + " of " + DECISIONS.length + " answered";
  }

  // ── moving between slides ─────────────────────────────────────────────
  var count = document.getElementById("count");
  function show(i) {
    current = Math.max(0, Math.min(slides.length - 1, i));
    slides.forEach(function (s, j) {
      s.el.classList.toggle("current", j === current);
      s.jump.setAttribute("aria-current", j === current ? "true" : "false");
    });
    var jump = slides[current].jump;
    if (jump.offsetLeft < strip.scrollLeft) strip.scrollLeft = jump.offsetLeft - 40;
    else if (jump.offsetLeft + jump.offsetWidth > strip.scrollLeft + strip.clientWidth)
      strip.scrollLeft = jump.offsetLeft + jump.offsetWidth - strip.clientWidth + 40;
    if (window.jarvisPackageObserver) window.jarvisPackageObserver.location({slideId: slides[current].el.id});
    count.textContent = (current + 1) + " of " + slides.length;
    document.getElementById("prev").disabled = current === 0;
    document.getElementById("next").disabled = current === slides.length - 1;
  }
  document.getElementById("prev").addEventListener("click", function () { show(current - 1); });
  document.getElementById("next").addEventListener("click", function () { show(current + 1); });
  document.addEventListener("keydown", function (e) {
    var t = e.target && e.target.tagName;
    if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return;
    if (e.key === "ArrowRight" || e.key === "PageDown") { show(current + 1); e.preventDefault(); }
    else if (e.key === "ArrowLeft" || e.key === "PageUp") { show(current - 1); e.preventDefault(); }
    else if (e.key === "Home") { show(0); e.preventDefault(); }
    else if (e.key === "End") { show(slides.length - 1); e.preventDefault(); }
  });

  // ── fitting the frame ─────────────────────────────────────────────────
  var stage = document.getElementById("stage");
  function fit() {
    // Never larger than MAX_SCALE: body text stays at most about 22 px.
    var s = Math.min(window.innerWidth / W, window.innerHeight / H, MAX_SCALE);
    stage.style.transform = "scale(" + s + ")";
    stage.style.left = Math.max(0, (window.innerWidth - W * s) / 2) + "px";
    stage.style.top = Math.max(0, (window.innerHeight - H * s) / 2) + "px";
  }
  window.addEventListener("resize", fit);

  // A decision slide's voting area is always in sight: its pictures give up
  // height until the slide fits the canvas.
  function shown(img) {
    var r = img.getBoundingClientRect();
    if (!img.naturalWidth || !r.width || !r.height) return 0;
    var k = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
    return img.naturalWidth * img.naturalHeight * k * k;
  }

  function cut(section) {
    if (section.scrollHeight > section.clientHeight + 1) return true;
    return [].slice.call(section.querySelectorAll(".option")).some(function (c) {
      return c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1;
    });
  }

  // Try each way in turn and keep the one that shows the smallest picture
  // on the slide largest, with nothing cut off.
  function arrange(section, box, ways) {
    var imgs = [].slice.call(section.querySelectorAll(".picture img"));
    var best = ways[0], bestArea = -1;
    ways.forEach(function (way) {
      ways.forEach(function (w) { box.classList.remove(w); });
      box.classList.add(way);
      if (cut(section)) return;
      var area = Math.min.apply(null, imgs.map(shown));
      if (area > bestArea) { best = way; bestArea = area; }
    });
    ways.forEach(function (w) { box.classList.remove(w); });
    box.classList.add(best);
  }

  function shrinkPictures() {
    DECISIONS.forEach(function (d) {
      var section = views[d.id].section;
      // Pictured options try each arrangement (picture above its words,
      // beside them, or, for two options, one option a row) and keep the one
      // that shows the smallest picture largest with nothing cut off.
      var opts = section.querySelector(".options.has-pictures");
      if (opts) arrange(section, opts, ["stacked", "side"].concat(opts.children.length === 2 ? ["rows"] : []));
      var compare = section.querySelector(".compare");
      if (compare) arrange(section, compare.parentNode, ["compare-above", "compare-side"]);
      if (section.classList.contains("fill")) return;   // pictures already fit the space
      var pics = [].slice.call(section.querySelectorAll(".picture img"));
      var limit = 220;
      while (section.scrollHeight > section.clientHeight + 1 && pics.length && limit > 48) {
        limit -= 12;
        pics.forEach(function (img) { img.style.maxHeight = limit + "px"; });
      }
    });
  }

  document.title = DECK.title || "Decisions";
  document.getElementById("deck-title").textContent = DECK.title || "";
  build();
  fit();
  show(0);
  render();
  shrinkPictures();
  window.addEventListener("load", shrinkPictures);   // once pictures have their sizes
  if (DECISIONS.length) loadRecorded().then(function (start) {
    if (start) { record = clean(start); render(); }
    say(inFrame ? (start ? "Read the answers recorded." : "Answers are saved in results.")
                : "Outside JARVIS: nothing is saved.");
    if (inFrame) setInterval(follow, FOLLOW_MS);
  });
})();
