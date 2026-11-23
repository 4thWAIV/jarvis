"""How JARVIS lays out a diagram (presentation_schema.DiagramObject): every
node's box, every connector's route and the place of its words, every
group's box, worked out by program from the source and the layout spec
alone (layout-spec.json "diagram"), never from the author's coordinates
(construction-drafts.md "deck/0.1 families: diagrams, shapes, groups,
connectors"). Pure: no I/O, no browser, deterministic.

The one layout: presentation_source runs it as the deck is read (its
problems refused as over capacity, deck_capacity), presentation_proof
records it and checks every word in its place at every frame, and
presentation.js draws exactly it, every box and point a share of the
diagram's slot.

It is laid out where its slot holds least for its words across the frame
range (`room`): the slot's width and height measured in the node style's
size at every frame layout_spec gives, the least of each, at the style's
floor (13 px). At every other frame each node, label and gap is as large or
larger in its words' size, so what fits here fits wherever it is drawn.
A word's width is the capacity model's (deck_capacity.lines_of: a
character is capacity.char_em of its size).

- flow: layered. Ranks by the longest path, back links (found in node
  order) and links that skip a rank routed round the outside, order within
  a rank by barycentre sweeps; across when the slot is wider than tall,
  else down; lanes (its groups) as rows across or columns down;
  connectors square-cornered, through the gaps between ranks.
- hierarchy: a tree, top down, leaves evenly spaced and each parent over
  its children; elbow connectors.
- cycle: its nodes on the ellipse the slot holds, from the top, clockwise
  in their order; arcs between them.
- timeline: one axis, its nodes evenly along it in order, each `when`
  beside the axis and its words beyond, on alternating sides when that
  lets them be wider; across when the slot is wider than tall, else down;
  phases (its groups) as bands on the axis.

All nodes of one diagram are one size, its longest words' need.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace

from jarvis import deck_capacity, layout_spec

# Each kind's connector when a link names no style.
DEFAULT_STYLE = {"flow": "arrow", "cycle": "arrow", "hierarchy": "line", "timeline": "line"}


def spec() -> dict:
    return layout_spec.spec()["diagram"]


def _style(name: str) -> dict:
    return layout_spec.spec()["styles"][name]


def _char(px: float) -> float:
    return deck_capacity.char_px(px)


def lines_of(text: str, width: float, px: float) -> int:
    # A hair over, so a width worked out to hold a line exactly holds it at
    # every frame, whatever the rounding.
    return deck_capacity.lines_of(text, max(width, 1.0) * (1 + 1e-9) + 1e-6, px)


@dataclass(frozen=True)
class Room:
    """Where a diagram is laid out: its slot's inner width and height, and
    its words' sizes, all in px at the node style's floor."""
    width: float
    height: float
    px: float          # the node style's size
    link_px: float     # the link style's, never larger than it is beside the node style


def room(box) -> Room:
    """The least a slot `box` (deck units) holds for a diagram's words
    across the frame range, said at the node style's floor."""
    d = spec()
    floor = _style(d["node"])["min_px"]
    ws, hs, ratios = [], [], []
    for frame in layout_spec.frames():
        u = layout_spec.unit(frame)
        node = layout_spec.style_px(d["node"], u)
        w, h = layout_spec.frame_box(box, frame)
        ws.append(w / node)
        hs.append(h / node)
        ratios.append(layout_spec.style_px(d["link"], u) / node)
    return Room(min(ws) * floor, min(hs) * floor, floor, max(ratios) * floor)


class _M:
    """The spec's lengths in px for one room."""

    def __init__(self, r: Room):
        d = spec()
        self.d = d
        self.W, self.H, self.px, self.lpx = r.width, r.height, r.px, r.link_px
        em = r.px
        self.em = em
        self.lh = r.px * _style(d["node"])["line"]
        self.llh = r.link_px * _style(d["link"])["line"]
        self.padx, self.pady = d["pad"][0] * em, d["pad"][1] * em
        self.stroke = d["stroke_px"]
        self.char, self.lchar = _char(r.px), _char(r.link_px)
        self.lpad = d["link_pad"] * em
        inner = 2 * self.padx + 2 * self.stroke
        self.w_min = d["node_min_chars"] * self.char + inner
        self.w_max = d["node_max_chars"] * self.char + inner
        self.h1 = self.lh + 2 * self.pady + 2 * self.stroke
        self.cross_gap = d["cross_gap"] * em
        self.lines_max = d["label_lines_max"]
        self.link_w_max = d["link_chars_max"] * self.lchar + 2 * self.lpad


# -- what a node needs ------------------------------------------------------------------

def _shape(node) -> tuple[float, float]:
    return tuple(spec()["shapes"][node.shape])


def _need(node, w: float, m: _M) -> tuple[float, int]:
    """A node's height at width `w`, and the lines its words take there."""
    fx, fy = _shape(node)
    lines = lines_of(node.label, w * fx - 2 * m.padx - 2 * m.stroke, m.px)
    return (lines * m.lh + 2 * m.pady + 2 * m.stroke) / fy, lines


def _one_line(node, m: _M) -> float:
    fx, _ = _shape(node)
    return (len(node.label) * m.char + 2 * m.padx + 2 * m.stroke) / fx + 1.0


def _width(nodes, available: float, m: _M) -> float:
    """Every node's width: what the arrangement gives each, no more than
    the spec's widest or than its longest words need on one line."""
    wanted = min(m.w_max, max(m.w_min, max(_one_line(n, m) for n in nodes)))
    return min(available, wanted)


def _height(nodes, w: float, m: _M, problems: list) -> float:
    """Every node's height at `w`, the most any needs; a node whose words
    take more lines than the spec allows is a problem."""
    most = 0.0
    for node in nodes:
        h, lines = _need(node, w, m)
        if lines > m.lines_max:
            problems.append(f"the words of node {node.id} ({node.label!r}) take {lines} lines in a node "
                            f"{w:.0f} px wide, at most {m.lines_max}: shorten them")
        most = max(most, h)
    return most


# -- geometry helpers -------------------------------------------------------------------

def _overlap(a, b, gap: float = 0.0) -> bool:
    return (a[0] < b[0] + b[2] + gap - 1e-6 and b[0] < a[0] + a[2] + gap - 1e-6
            and a[1] < b[1] + b[3] + gap - 1e-6 and b[1] < a[1] + a[3] + gap - 1e-6)


def _inside(a, W: float, H: float) -> bool:
    return a[0] >= -1e-6 and a[1] >= -1e-6 and a[0] + a[2] <= W + 1e-6 and a[1] + a[3] <= H + 1e-6


def _crosses(box, path, gap: float = 1.0) -> bool:
    """Whether the polyline `path` passes through `box` grown by `gap`
    (Liang-Barsky clipping of each segment)."""
    x0, y0 = box[0] - gap, box[1] - gap
    x1, y1 = box[0] + box[2] + gap, box[1] + box[3] + gap
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        dx, dy = bx - ax, by - ay
        lo, hi = 0.0, 1.0
        for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
            if abs(p) < 1e-12:
                if q < 0:
                    break
                continue
            t = q / p
            if p < 0:
                lo = max(lo, t)
            else:
                hi = min(hi, t)
            if lo > hi:
                break
        else:
            return True
    return False


def _label(text: str, centre, w_max: float, m: _M, along, obstacles: list, both: bool = True,
           paths: tuple = ()):
    """The words on a connector: wrapped in at most `w_max` px, placed
    nearest `centre` along the direction `along` where it is inside the
    diagram and clear of every obstacle and of every path in `paths` (the
    other connectors and the axis: the words are backed, so they would hide
    a line under them). (box, lines) or None."""
    tw = max(w_max - 2 * m.lpad, m.lchar)
    lines = lines_of(text, tw, m.lpx)
    w = (min(tw, len(text) * m.lchar + 1.0) if lines == 1 else tw) + 2 * m.lpad
    h = lines * m.llh + 2 * m.lpad
    step = m.llh / 3
    for k in range(240):
        off = ((k + 1) // 2) * (1 if k % 2 else -1) * step if both else k * step
        box = (centre[0] + along[0] * off - w / 2, centre[1] + along[1] * off - h / 2, w, h)
        if (_inside(box, m.W, m.H) and not any(_overlap(box, o, 1.0) for o in obstacles)
                and not any(_crosses(box, path) for path in paths)):
            return box, lines
    return None


def _place_labels(requests: list, m: _M, obstacles: list, problems: list, out: dict,
                  routes: dict) -> None:
    """Each request (link, text, centre, w_max, along, both) placed in turn,
    each placed label an obstacle for the next, and every connector's route
    in `routes` (link id: px points) but its own an obstacle too."""
    limit = spec()["link_lines_max"]
    for link, text, centre, w_max, along, both in requests:
        others = tuple(path for other, path in routes.items() if other != link)
        placed = _label(text, centre, w_max, m, along, obstacles, both, others)
        if placed is None:
            problems.append(f"the words on link {link} ({text!r}) have no room clear of the nodes, "
                            f"the other connectors and the other words: shorten them or show fewer "
                            f"nodes")
            continue
        box, lines = placed
        if lines > limit:
            problems.append(f"the words on link {link} ({text!r}) take {lines} lines, at most {limit}: "
                            f"shorten them")
        obstacles.append(box)
        out[link] = (box, lines)


def _node_out(node, box) -> dict:
    return {"id": node.id, "label": node.label, "shape": node.shape, "tone": node.tone,
            "when": getattr(node, "when", None), "box": list(box)}


# -- flow -------------------------------------------------------------------------------

def _flow(obj, m: _M, problems: list, held) -> dict | None:
    d = m.d
    nodes = obj.nodes
    ids = [n.id for n in nodes]
    order = {i: k for k, i in enumerate(ids)}
    links = obj.links
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for link in links:
        adj[link.source].append(link.to)
    state: dict[str, int] = {}
    back: set[tuple[str, str]] = set()

    def visit(u):
        state[u] = 1
        for v in adj[u]:
            if state.get(v) == 1:
                back.add((u, v))
            elif v not in state:
                visit(v)
        state[u] = 2

    for i in ids:
        if i not in state:
            visit(i)
    forward = [(l.source, l.to) for l in links if (l.source, l.to) not in back]
    rank = {i: 0 for i in ids}
    for _ in ids:
        for a, b in forward:
            rank[b] = max(rank[b], rank[a] + 1)
    R = max(rank.values()) + 1
    lane_of = ({n: k for k, g in enumerate(obj.groups) for n in g.nodes} if obj.groups
               else {i: 0 for i in ids})
    L = max(1, len(obj.groups))
    layers = [sorted((i for i in ids if rank[i] == r), key=lambda i: (lane_of[i], order[i]))
              for r in range(R)]
    pos = {i: k for layer in layers for k, i in enumerate(layer)}
    near: dict[str, set] = {i: set() for i in ids}
    for a, b in forward:
        near[a].add(b)
        near[b].add(a)
    for sweep in range(4):
        down = sweep % 2 == 0
        for r in (range(1, R) if down else range(R - 2, -1, -1)):
            ref = r - 1 if down else r + 1

            def bary(i):
                seen = [pos[j] for j in near[i] if rank[j] == ref]
                return sum(seen) / len(seen) if seen else pos[i]

            keys = {i: (lane_of[i], bary(i), pos[i], order[i]) for i in layers[r]}
            layers[r].sort(key=keys.get)
            for k, i in enumerate(layers[r]):
                pos[i] = k
    slot: dict[str, int] = {}
    count = [[0] * L for _ in range(R)]
    for r, layer in enumerate(layers):
        for i in layer:
            slot[i] = count[r][lane_of[i]]
            count[r][lane_of[i]] += 1
    lane_slots = [max(1, max(count[r][l] for r in range(R))) for l in range(L)]

    across = m.W >= m.H
    adjacent = [l for l in links if (l.source, l.to) not in back and rank[l.to] - rank[l.source] == 1]
    skipping = [l for l in links if (l.source, l.to) not in back and rank[l.to] - rank[l.source] > 1]
    backs = [l for l in links if (l.source, l.to) in back]
    labelled = any(l.label for l in adjacent)
    rank_gap = m.em * (d["rank_gap_labelled" if labelled else "rank_gap"] if across
                       else d["rank_gap_down_labelled" if labelled else "rank_gap_down"])
    channel = d["channel"] * m.em
    label_room = spec()["link_lines_max"] * m.llh + 2 * m.lpad

    def band(routed):
        if not routed:
            return 0.0, 0.0
        room_for_words = label_room if any(l.label for l in routed) else 0.0
        return room_for_words, len(routed) * channel + room_for_words

    over_words, over = band(skipping)
    under_words, under = band(backs)
    margin = d["edge_margin"] * m.em if (skipping or backs) else 0.0
    grouped = bool(obj.groups)
    gpad = d["group_pad"] * m.em if grouped else 0.0
    header = (m.llh + 2 * m.lpad) if grouped else 0.0
    ggap = d["group_gap"] * m.em
    cg = m.cross_gap
    if across:
        w = _width(nodes, (m.W - 2 * margin - 2 * gpad - (R - 1) * rank_gap) / R, m)
        if w < m.w_min:
            most = int((m.W - 2 * margin - 2 * gpad + rank_gap) // (m.w_min + rank_gap))
            problems.append(f"the flow has {R} ranks in a row; with its links' words and routes its "
                            f"slot holds {most} at most: split it across slides")
            return None
        h = _height(nodes, w, m, problems)
        ms, cs = w, h
        lane_band = [2 * gpad + header + s * h + (s - 1) * cg for s in lane_slots]
        main_total = R * w + (R - 1) * rank_gap + 2 * gpad + 2 * margin
    else:
        slots = sum(lane_slots)
        avail = m.W - over - under - (L - 1) * ggap - L * 2 * gpad - (slots - L) * cg
        w = _width(nodes, avail / slots, m)
        if w < m.w_min:
            # n nodes in L lanes take n * w_min + (n - L) * cross_gap of what is
            # left once the lanes' pads, the gaps between them and the routes
            # are taken: avail + (slots - L) * cross_gap.
            most = int((avail + slots * cg) // (m.w_min + cg))
            problems.append(f"the flow has {slots} nodes side by side"
                            + (f" across its {L} lanes" if grouped else "")
                            + f"; with its lanes and routes its slot holds {max(1, most)} at most: "
                              f"split it across slides")
            return None
        h = _height(nodes, w, m, problems)
        ms, cs = h, w
        lane_band = [2 * gpad + s * w + (s - 1) * cg for s in lane_slots]
        main_total = header + 2 * gpad + R * h + (R - 1) * rank_gap + 2 * margin
    cross_total = over + sum(lane_band) + (L - 1) * ggap + under
    MAIN, CROSS = (m.W, m.H) if across else (m.H, m.W)
    if main_total > MAIN + 0.5:
        problems.append(f"the flow has {R} ranks, {main_total:.0f} px of the {MAIN:.0f} its slot "
                        f"holds: split it across slides")
    if cross_total > CROSS + 0.5:
        problems.append(f"the flow's nodes side by side take {cross_total:.0f} px of the "
                        f"{CROSS:.0f} its slot holds ({max(lane_slots)} in a rank"
                        + (f", {L} lanes" if grouped else "") + "): split it across slides")
    if problems:
        return None
    main0 = (MAIN - main_total) / 2
    cross0 = (CROSS - cross_total) / 2

    def xy(mm, cc):
        return (mm, cc) if across else (cc, mm)

    def rect(mm, cc, a, b):
        return (mm, cc, a, b) if across else (cc, mm, b, a)

    first_rank = main0 + margin + gpad + (0 if across else header)
    lane_start = []
    c = cross0 + over
    for l in range(L):
        lane_start.append(c)
        c += lane_band[l] + ggap
    box_of: dict[str, tuple] = {}
    mc: dict[str, tuple] = {}
    for r, layer in enumerate(layers):
        mstart = first_rank + r * (ms + rank_gap)
        for i in layer:
            l = lane_of[i]
            centre_off = (lane_slots[l] - count[r][l]) / 2 * (cs + cg)
            cstart = lane_start[l] + gpad + (header if across else 0) + centre_off + slot[i] * (cs + cg)
            mc[i] = (mstart, cstart)
            box_of[i] = rect(mstart, cstart, ms, cs)
    groups = []
    lane_main = (main0 + margin, main_total - 2 * margin)
    for l, group in enumerate(obj.groups):
        box = rect(lane_main[0], lane_start[l], lane_main[1], lane_band[l])
        words = (box[0] + gpad, box[1] + gpad, box[2] - 2 * gpad, header)
        lines = lines_of(group.label, words[2] - 2 * m.lpad, m.lpx)
        if lines > 1:
            problems.append(f"the label of group {group.id} ({group.label!r}) takes {lines} lines in "
                            f"{words[2]:.0f} px, at most 1: shorten it")
        groups.append({"id": group.id, "label": group.label, "nodes": list(group.nodes),
                       "box": list(box), "label_box": list(words), "label_lines": lines})
    # Connectors through the gaps between ranks: gap g lies before rank g.
    def gap_span(g):
        if g == 0:
            end = first_rank
            return end - margin, end
        start = first_rank + (g - 1) * (ms + rank_gap) + ms
        return start, start + (rank_gap if g < R else margin)

    uses: dict[int, list] = {}
    for n, link in enumerate(links):
        a, b = rank[link.source], rank[link.to]
        key = (mc[link.source][1], mc[link.to][1], n)
        if link in adjacent:
            uses.setdefault(a + 1, []).append((key, n, "in"))
        elif link in skipping:
            uses.setdefault(a + 1, []).append((key, n, "out"))
            uses.setdefault(b, []).append((key, n, "in"))
        else:
            uses.setdefault(a + 1, []).append((key, n, "out"))
            uses.setdefault(b, []).append((key, n, "in"))
    at: dict[tuple[int, str], float] = {}
    for g, items in uses.items():
        lo, hi = gap_span(g)
        items.sort()
        for k, (_, n, end) in enumerate(items):
            at[(n, end)] = lo + (k + 1) * (hi - lo) / (len(items) + 1)
    out_links, requests = [], []
    cross_end = cross0 + cross_total
    for n, link in enumerate(links):
        sm, sc = mc[link.source]
        tm, tc = mc[link.to]
        c1, c2 = sc + cs / 2, tc + cs / 2
        exit_m, entry_m = sm + ms, tm
        if link in adjacent:
            g = at[(n, "in")]
            route = [(exit_m, c1), (g, c1), (g, c2), (entry_m, c2)] if abs(c1 - c2) > 0.01 else \
                [(exit_m, c1), (entry_m, c2)]
            lo, hi = gap_span(rank[link.to])
            centre = xy((lo + hi) / 2, (c1 + c2) / 2)
            w_max = (hi - lo - 0.4 * m.em) if across else m.link_w_max
            along = (0, 1) if across else (1, 0)
        else:
            outer = link in skipping
            k = (skipping if outer else backs).index(link)
            cb = (cross0 + over_words + (k + 0.5) * channel) if outer else \
                (cross_end - under_words - (k + 0.5) * channel)
            g1, g2 = at[(n, "out")], at[(n, "in")]
            route = [(exit_m, c1), (g1, c1), (g1, cb), (g2, cb), (g2, c2), (entry_m, c2)]
            centre = xy((g1 + g2) / 2, cb)
            w_max = min(abs(g2 - g1), m.link_w_max)
            along = (0, 1) if across else (1, 0)
        out_links.append({"id": link.id, "from": link.source, "to": link.to,
                          "style": link.style or DEFAULT_STYLE["flow"],
                          "points": [list(xy(*p)) for p in route], "label": link.label})
        if link.label:
            requests.append((link.id, link.label, centre, w_max, along, True))
    obstacles = list(box_of.values()) + [tuple(g["label_box"]) for g in groups]
    placed: dict = {}
    _place_labels(requests, m, obstacles, problems, placed,
                  {link["id"]: link["points"] for link in out_links})
    for link in out_links:
        if link["id"] in placed:
            box, lines = placed[link["id"]]
            link.update(label_box=list(box), label_lines=lines)
    return {"orientation": "across" if across else "down",
            "nodes": [_node_out(n, box_of[n.id]) for n in nodes],
            "links": out_links, "groups": groups}


# -- hierarchy --------------------------------------------------------------------------

def _hierarchy(obj, m: _M, problems: list, held) -> dict | None:
    d = m.d
    nodes = obj.nodes
    order = {n.id: k for k, n in enumerate(nodes)}
    parent = {l.to: l.source for l in obj.links}
    children: dict[str, list[str]] = {n.id: [] for n in nodes}
    for link in obj.links:
        children[link.source].append(link.to)
    for kids in children.values():
        kids.sort(key=order.get)
    root = next(n.id for n in nodes if n.id not in parent)
    depth = {root: 0}
    leaves: list[str] = []

    def walk(i):
        if not children[i]:
            leaves.append(i)
        for c in children[i]:
            depth[c] = depth[i] + 1
            walk(c)

    walk(root)
    D = max(depth.values()) + 1
    labelled = any(l.label for l in obj.links)
    sg = d["sibling_gap"] * m.em
    lg = d["level_gap_labelled" if labelled else "level_gap"] * m.em
    w = _width(nodes, (m.W - (len(leaves) - 1) * sg) / len(leaves), m)
    if w < m.w_min:
        problems.append(f"the hierarchy has {len(leaves)} nodes at its widest, its slot holds "
                        f"{held()['leaves']} side by side at most: split it across slides")
        return None
    h = _height(nodes, w, m, problems)
    total_h = D * h + (D - 1) * lg
    if total_h > m.H + 0.5:
        problems.append(f"the hierarchy is {D} levels deep, {total_h:.0f} px of the {m.H:.0f} its "
                        f"slot holds ({held()['levels']} levels at most): split it across slides")
    if problems:
        return None
    total_w = len(leaves) * w + (len(leaves) - 1) * sg
    x0, y0 = (m.W - total_w) / 2, (m.H - total_h) / 2
    centre: dict[str, float] = {leaf: x0 + w / 2 + k * (w + sg) for k, leaf in enumerate(leaves)}

    def place(i):
        if children[i]:
            for c in children[i]:
                place(c)
            centre[i] = (centre[children[i][0]] + centre[children[i][-1]]) / 2

    place(root)
    box_of = {i: (centre[i] - w / 2, y0 + depth[i] * (h + lg), w, h) for i in centre}
    out_links, requests = [], []
    bus = 0.3 if labelled else 0.5
    for link in obj.links:
        p, c = box_of[link.source], box_of[link.to]
        px_, cx_ = centre[link.source], centre[link.to]
        top = p[1] + h
        ybus = top + lg * bus
        route = [(px_, top), (px_, ybus), (cx_, ybus), (cx_, c[1])] if abs(px_ - cx_) > 0.01 else \
            [(px_, top), (cx_, c[1])]
        out_links.append({"id": link.id, "from": link.source, "to": link.to,
                          "style": link.style or DEFAULT_STYLE["hierarchy"],
                          "points": [list(q) for q in route], "label": link.label})
        if link.label:
            requests.append((link.id, link.label, (cx_, (ybus + c[1]) / 2),
                             min(w + sg - 2, m.link_w_max), (0, 1), True))
    placed: dict = {}
    _place_labels(requests, m, list(box_of.values()), problems, placed,
                  {link["id"]: link["points"] for link in out_links})
    for link in out_links:
        if link["id"] in placed:
            box, lines = placed[link["id"]]
            link.update(label_box=list(box), label_lines=lines)
    return {"orientation": "down", "nodes": [_node_out(n, box_of[n.id]) for n in nodes],
            "links": out_links, "groups": []}


# -- cycle ------------------------------------------------------------------------------

def _ring(n: int, w: float, h: float, m: _M):
    """The node boxes and arcs of a ring of `n` nodes `w` by `h`, or None
    when they do not keep apart."""
    rx, ry = (m.W - w) / 2, (m.H - h) / 2
    if rx <= 0 or ry <= 0:
        return None
    cx, cy = m.W / 2, m.H / 2
    angle = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]
    boxes = [(cx + rx * math.cos(a) - w / 2, cy + ry * math.sin(a) - h / 2, w, h) for a in angle]
    for i in range(n):
        for j in range(i + 1, n):
            if _overlap(boxes[i], boxes[j], m.cross_gap):
                return None
    gap = m.cross_gap / 2
    arcs = []
    for i in range(n):
        j = (i + 1) % n
        a0 = angle[i]
        points = [(cx + rx * math.cos(a0 + 2 * math.pi / n * t / 48),
                   cy + ry * math.sin(a0 + 2 * math.pi / n * t / 48)) for t in range(49)]

        def clear(p, box):
            return not (box[0] - gap <= p[0] <= box[0] + box[2] + gap
                        and box[1] - gap <= p[1] <= box[1] + box[3] + gap)

        while points and not clear(points[0], boxes[i]):
            points.pop(0)
        while points and not clear(points[-1], boxes[j]):
            points.pop()
        if len(points) < 3 or math.dist(points[0], points[-1]) < m.em:
            return None
        arcs.append(points)
    return boxes, arcs, (cx, cy)


def _cycle(obj, m: _M, problems: list, held) -> dict | None:
    nodes = obj.nodes
    n = len(nodes)
    step = 0.25 * m.em
    w = _width(nodes, m.w_max, m)
    found = None
    while w >= m.w_min - 1e-6:
        quiet: list = []
        h = _height(nodes, w, m, quiet)
        ring = _ring(n, w, h, m)
        if ring is not None:
            found = (w, h, ring, quiet)
            break
        w -= step
    if found is None:
        problems.append(f"the cycle has {n} nodes, its slot holds {held()['nodes']} round the ring at "
                        f"most: split it across slides or shorten their words")
        return None
    w, h, (boxes, arcs, (cx, cy)), quiet = found
    problems.extend(quiet)
    if problems:
        return None
    index = {node.id: k for k, node in enumerate(nodes)}
    out_links, requests = [], []
    for link in obj.links:
        arc = arcs[index[link.source]]
        out_links.append({"id": link.id, "from": link.source, "to": link.to,
                          "style": link.style or DEFAULT_STYLE["cycle"],
                          "points": [list(p) for p in arc], "label": link.label})
        if link.label:
            mid = arc[len(arc) // 2]
            v = (cx - mid[0], cy - mid[1])
            norm = math.hypot(*v) or 1.0
            requests.append((link.id, link.label, mid, min(m.link_w_max, m.W / 2),
                             (v[0] / norm, v[1] / norm), False))
    placed: dict = {}
    _place_labels(requests, m, list(boxes), problems, placed,
                  {link["id"]: link["points"] for link in out_links})
    for link in out_links:
        if link["id"] in placed:
            box, lines = placed[link["id"]]
            link.update(label_box=list(box), label_lines=lines)
    return {"orientation": "ring", "nodes": [_node_out(x, b) for x, b in zip(nodes, boxes)],
            "links": out_links, "groups": []}


# -- timeline ---------------------------------------------------------------------------

def _timeline(obj, m: _M, problems: list, held) -> dict | None:
    d = m.d
    nodes = obj.nodes
    n = len(nodes)
    gap = m.cross_gap
    wgap = d["when_gap"] * m.em
    marker = d["marker"] * m.em
    phases = obj.groups
    bp = d["band_pad"] * m.em
    # Across, a phase's band holds its words over the markers; down, the
    # band is the axis's own width and its words are beside it.
    label_part = (m.llh + 2 * bp) if phases else 0.0
    zone = marker + label_part
    when_max = d["when_lines_max"]

    def when_lines(width):
        return max(lines_of(x.when or "", width - 2 * m.lpad, m.lpx) for x in nodes)

    if m.W >= m.H:
        def option(w, sides):
            quiet: list = []
            h = _height(nodes, w, m, quiet)
            wl = when_lines(w)
            when_h = wl * m.llh
            stack = 2 * wgap + when_h + h
            total = sides * stack + zone
            lines = max(_need(x, w, m)[1] for x in nodes)
            ok = w >= m.w_min - 1e-6 and not quiet and wl <= when_max and total <= m.H + 0.5
            return {"w": w, "h": h, "when_h": when_h, "wl": wl, "stack": stack, "total": total,
                    "lines": lines, "ok": ok, "sides": sides, "quiet": quiet}

        single = option(_width(nodes, (m.W - gap * (n - 1)) / n, m), 1)
        alternate = option(_width(nodes, (2 * m.W - gap * (n - 1)) / (n + 1), m), 2)
        choice = (alternate if alternate["ok"] and (not single["ok"] or alternate["lines"] < single["lines"])
                  else single if single["ok"] else None)
        if choice is None:
            best = alternate if alternate["w"] >= m.w_min else single
            if best["w"] < m.w_min - 1e-6:
                problems.append(f"the timeline has {n} nodes, its slot holds {held()['nodes']} along "
                                f"its axis at most: split it across slides")
            elif best["quiet"]:
                problems.extend(best["quiet"])
            elif best["wl"] > when_max:
                problems.append(f"a timeline's when takes {best['wl']} lines in {best['w']:.0f} px, "
                                f"at most {when_max}: shorten it")
            else:
                problems.append(f"the timeline takes {best['total']:.0f} px of the {m.H:.0f} its "
                                f"slot holds: split it across slides or shorten its words")
            return None
        w, h, when_h, stack = choice["w"], choice["h"], choice["when_h"], choice["stack"]
        s = (m.W - w) / (n - 1)
        y0 = (m.H - choice["total"]) / 2
        ya = y0 + stack + label_part + marker / 2
        boxes, whens, markers = [], [], []
        for k, node in enumerate(nodes):
            x = w / 2 + k * s
            markers.append([x, ya])
            if choice["sides"] == 1 or k % 2 == 0:
                wy = ya - marker / 2 - label_part - wgap - when_h
                boxes.append((x - w / 2, wy - wgap - h, w, h))
            else:
                wy = ya + marker / 2 + wgap
                boxes.append((x - w / 2, wy + when_h + wgap, w, h))
            whens.append((x - w / 2, wy, w, when_h))
        axis = {"from": [0.0, ya], "to": [m.W, ya], "markers": markers, "zone": zone,
                "orientation": "across"}
        groups = []
        for group in phases:
            ks = [k for k, node in enumerate(nodes) if node.id in group.nodes]
            a = max(0.0, markers[ks[0]][0] - s / 2) + 1
            b = min(m.W, markers[ks[-1]][0] + s / 2) - 1
            box = (a, ya - marker / 2 - label_part, b - a, zone)
            words = (a, box[1] + bp, b - a, m.llh)
            lines = lines_of(group.label, words[2] - 2 * m.lpad, m.lpx)
            if lines > 1:
                problems.append(f"the label of phase {group.id} ({group.label!r}) takes {lines} lines "
                                f"in {words[2]:.0f} px, at most 1: shorten it")
            groups.append({"id": group.id, "label": group.label, "nodes": list(group.nodes),
                           "box": list(box), "label_box": list(words), "label_lines": lines})
        orientation = "across"
    else:
        phase_w = (min(max(len(g.label) for g in phases) * m.lchar + 2 * m.lpad + 1.0, m.W * 0.3)
                   if phases else 0.0)
        when_w = min(max(len(x.when or "") for x in nodes) * m.lchar + 2 * m.lpad + 1.0, m.W * 0.28)
        zone = marker * 1.5 if phases else marker
        lead = phase_w + (gap if phases else 0.0)
        x_axis = lead + zone / 2
        x_when = lead + zone + wgap
        x_node = x_when + when_w + wgap
        w = _width(nodes, m.W - x_node, m)
        if w < m.w_min - 1e-6:
            problems.append(f"the timeline's words have {m.W - x_node:.0f} px beside its axis, "
                            f"too little for a node: give it a wider slot")
            return None
        h = _height(nodes, w, m, problems)
        wl = max(lines_of(x.when or "", when_w - 2 * m.lpad, m.lpx) for x in nodes)
        if wl > when_max:
            problems.append(f"a timeline's when takes {wl} lines in {when_w:.0f} px, at most "
                            f"{when_max}: shorten it")
        h = max(h, wl * m.llh + 2 * m.lpad)
        s = (m.H - h) / (n - 1)
        if s < h + gap - 1e-6:
            problems.append(f"the timeline has {n} nodes, its slot holds {held()['nodes']} along its "
                            f"axis at most: split it across slides")
        if problems:
            return None
        boxes, whens, markers = [], [], []
        for k in range(n):
            y = h / 2 + k * s
            markers.append([x_axis, y])
            boxes.append((x_node, y - h / 2, w, h))
            whens.append((x_when, y - h / 2, when_w, h))
        axis = {"from": [x_axis, 0.0], "to": [x_axis, m.H], "markers": markers, "zone": zone,
                "orientation": "down"}
        groups = []
        for group in phases:
            ks = [k for k, node in enumerate(nodes) if node.id in group.nodes]
            a = max(0.0, markers[ks[0]][1] - s / 2) + 1
            b = min(m.H, markers[ks[-1]][1] + s / 2) - 1
            box = (x_axis - zone / 2, a, zone, b - a)
            lines = lines_of(group.label, phase_w - 2 * m.lpad, m.lpx)
            words_h = lines * m.llh + 2 * m.lpad
            words = (0.0, (a + b) / 2 - words_h / 2, phase_w, words_h)
            if words_h > b - a + 0.5:
                problems.append(f"the label of phase {group.id} ({group.label!r}) takes {lines} lines, "
                                f"more than its phase's length: shorten it")
            groups.append({"id": group.id, "label": group.label, "nodes": list(group.nodes),
                           "box": list(box), "label_box": list(words), "label_lines": lines})
        orientation = "down"
    if problems:
        return None
    out_nodes = []
    for node, box, when in zip(nodes, boxes, whens):
        item = _node_out(node, box)
        item["when_box"] = list(when)
        out_nodes.append(item)
    return {"orientation": orientation, "nodes": out_nodes, "links": [], "groups": groups,
            "axis": axis}


LAYOUTS = {"flow": _flow, "hierarchy": _hierarchy, "cycle": _cycle, "timeline": _timeline}


# -- what a slot holds ------------------------------------------------------------------

def _short(n: int, when: bool = False) -> list:
    words = "x" * spec()["node_min_chars"]
    return [SimpleNamespace(id=f"n{k}", label=words, shape="box", tone="neutral",
                            when=("x" * 6 if when else None)) for k in range(n)]


def holds(kind: str, r: Room) -> dict:
    """How many nodes a diagram of `kind` holds in room `r`, each node the
    spec's narrowest with words of one line: a flow's ranks in a row and
    nodes side by side, a hierarchy's leaves side by side and levels, a
    cycle's or a timeline's nodes. Worked out from the spec, never a
    constant."""
    m = _M(r)
    d = m.d
    if kind == "flow":
        across = m.W >= m.H
        rg = d["rank_gap"] * m.em if across else d["rank_gap_down"] * m.em
        if across:
            return {"ranks": int((m.W + rg) // (m.w_min + rg)),
                    "side_by_side": int((m.H + m.cross_gap) // (m.h1 + m.cross_gap))}
        return {"ranks": int((m.H + rg) // (m.h1 + rg)),
                "side_by_side": int((m.W + m.cross_gap) // (m.w_min + m.cross_gap))}
    if kind == "hierarchy":
        sg, lg = d["sibling_gap"] * m.em, d["level_gap"] * m.em
        return {"leaves": int((m.W + sg) // (m.w_min + sg)),
                "levels": int((m.H + lg) // (m.h1 + lg))}
    if kind == "cycle":
        most = 1
        for n in range(2, 25):
            if _ring(n, m.w_min, m.h1, m) is None:
                break
            most = n
        return {"nodes": most}
    most = 1
    for n in range(2, 25):
        obj = SimpleNamespace(kind="timeline", nodes=_short(n, when=True), links=[], groups=[])
        if _timeline(obj, m, [], lambda: {"nodes": "?"}) is None:
            break
        most = n
    return {"nodes": most}


# -- the structure each kind takes ------------------------------------------------------

def structure(obj) -> list[tuple[str, str]]:
    """What a diagram's nodes, links and groups break of its kind's rules,
    each (member, words for the author)."""
    out: list[tuple[str, str]] = []
    ids = [n.id for n in obj.nodes]
    known = set(ids)
    kind = obj.kind
    seen: set = set()
    for link in obj.links:
        for end in (link.source, link.to):
            if end not in known:
                out.append((link.id, f"Link {link.id} names {end!r}, which is not a node of {obj.id}."))
        if link.source == link.to:
            out.append((link.id, f"Link {link.id} runs from {link.source} to itself."))
        if (link.source, link.to) in seen:
            out.append((link.id, f"Link {link.id} runs from {link.source} to {link.to} again."))
        seen.add((link.source, link.to))
    for node in obj.nodes:
        if node.when is not None and kind != "timeline":
            out.append((node.id, f"Only a timeline's nodes say when; {obj.id} is a {kind}."))
        if kind == "timeline" and node.when is None:
            out.append((node.id, f"Every node of the timeline {obj.id} says `when`."))
    if out:
        return out
    if kind == "timeline" and obj.links:
        out.append((obj.links[0].id, f"A timeline's order is its nodes' order: {obj.id} takes no links."))
    if kind in ("cycle", "hierarchy") and obj.groups:
        out.append((obj.groups[0].id, f"A {kind} has no groups: lanes are a flow's, phases a timeline's."))
    member_of: dict[str, str] = {}
    for group in obj.groups:
        for node in group.nodes:
            if node not in known:
                out.append((group.id, f"Group {group.id} names {node!r}, which is not a node of {obj.id}."))
            elif node in member_of:
                out.append((group.id, f"Node {node} is in group {member_of[node]} and {group.id}: "
                                      f"a node is in one group."))
            member_of[node] = group.id
    if out:
        return out
    if kind == "flow" and obj.groups:
        loose = [i for i in ids if i not in member_of]
        if loose:
            out.append((obj.id, f"Once a flow has lanes every node is in one: {', '.join(loose)} "
                                f"{'is' if len(loose) == 1 else 'are'} in none."))
    if kind == "timeline":
        index = {i: k for k, i in enumerate(ids)}
        last = -1
        for group in obj.groups:
            ks = sorted(index[n] for n in group.nodes)
            if ks != list(range(ks[0], ks[-1] + 1)) or [index[n] for n in group.nodes] != ks:
                out.append((group.id, f"Phase {group.id} names nodes next to each other, in their "
                                      f"order on the timeline."))
            elif ks[0] <= last:
                out.append((group.id, f"Phase {group.id} begins before the phase ahead of it ends."))
            last = ks[-1] if ks else last
    if kind == "hierarchy":
        parents: dict[str, list[str]] = {}
        for link in obj.links:
            parents.setdefault(link.to, []).append(link.source)
        roots = [i for i in ids if i not in parents]
        twice = [i for i, p in parents.items() if len(p) > 1]
        if len(roots) != 1:
            out.append((obj.id, f"A hierarchy has one root, a node no link runs to; {obj.id} has "
                                f"{len(roots)}" + (f" ({', '.join(roots)})" if roots else "") + "."))
        for i in twice:
            out.append((i, f"Node {i} of the hierarchy {obj.id} has {len(parents[i])} parents, "
                           f"at most one."))
        if len(roots) == 1 and not twice:
            reach, todo = {roots[0]}, [roots[0]]
            while todo:
                u = todo.pop()
                for link in obj.links:
                    if link.source == u and link.to not in reach:
                        reach.add(link.to)
                        todo.append(link.to)
            if reach != known:
                out.append((obj.id, f"Every node of the hierarchy {obj.id} hangs from its root; "
                                    f"{', '.join(i for i in ids if i not in reach)} do not."))
    if kind == "cycle":
        ring = {(ids[k], ids[(k + 1) % len(ids)]) for k in range(len(ids))}
        given = {(l.source, l.to) for l in obj.links}
        if given != ring or len(obj.links) != len(ids):
            out.append((obj.id, f"A cycle's links are its ring, each node to the next in their order "
                                f"and the last to the first: {', '.join(f'{a} to {b}' for a, b in sorted(ring, key=lambda p: ids.index(p[0])))}."))
    return out


# -- the layout ------------------------------------------------------------------------

def layout(obj, box) -> dict:
    """`obj` laid out in its slot `box` (deck units): every node's, label's,
    group's box and every connector's points as shares of the slot's inner
    box, with the room it was laid out in and `problems` (what does not fit,
    in the author's words; then nothing else is given)."""
    r = room(box)
    m = _M(r)
    problems: list[str] = []
    drawn = LAYOUTS[obj.kind](obj, m, problems, lambda: holds(obj.kind, r))
    out = {"kind": obj.kind, "problems": problems,
           "room": {"width": round(r.width, 2), "height": round(r.height, 2),
                    "node_px": r.px, "link_px": round(r.link_px, 3)},
           "holds": holds(obj.kind, r)}
    if drawn is None or problems:
        return out
    W, H = r.width, r.height

    def share_box(b):
        return [round(b[0] / W, 6), round(b[1] / H, 6), round(b[2] / W, 6), round(b[3] / H, 6)]

    def share_point(p):
        return [round(p[0] / W, 6), round(p[1] / H, 6)]

    for node in drawn["nodes"]:
        node["lines"] = _need(SimpleNamespace(label=node["label"], shape=node["shape"]),
                              node["box"][2], m)[1]
        node["box"] = share_box(node["box"])
        if "when_box" in node:
            node["when_box"] = share_box(node["when_box"])
    for link in drawn["links"]:
        link["points"] = [share_point(p) for p in link["points"]]
        if "label_box" in link:
            link["label_box"] = share_box(link["label_box"])
    for group in drawn["groups"]:
        group["box"] = share_box(group["box"])
        group["label_box"] = share_box(group["label_box"])
    if "axis" in drawn:
        axis = drawn["axis"]
        axis["from"], axis["to"] = share_point(axis["from"]), share_point(axis["to"])
        axis["markers"] = [share_point(p) for p in axis["markers"]]
    out.update(drawn)
    return out


# -- every word in its place at a frame -------------------------------------------------

def frame_problems(drawn: dict, box, frame) -> list[str]:
    """What of a laid-out diagram does not hold its words at `frame`: every
    node's words in its box, each connector's and group's words in theirs,
    each when in its place, at that frame's sizes."""
    d = spec()
    u = layout_spec.unit(frame)
    W, H = layout_spec.frame_box(box, frame)
    npx, lpx = layout_spec.style_px(d["node"], u), layout_spec.style_px(d["link"], u)
    lh, llh = npx * _style(d["node"])["line"], lpx * _style(d["link"])["line"]
    padx, pady, lpad = d["pad"][0] * npx, d["pad"][1] * npx, d["link_pad"] * npx
    stroke = d["stroke_px"]
    out = []
    for node in drawn.get("nodes") or []:
        fx, fy = d["shapes"][node["shape"]]
        bw, bh = node["box"][2] * W, node["box"][3] * H
        lines = lines_of(node["label"], bw * fx - 2 * padx - 2 * stroke, npx)
        if lines * lh > bh * fy - 2 * pady - 2 * stroke + 0.5:
            out.append(f"node {node['id']}'s words take {lines} lines, more than its "
                       f"{bh:.0f} px box holds")
        if node.get("when_box"):
            ww, wh = node["when_box"][2] * W, node["when_box"][3] * H
            if lines_of(node["when"], ww - 2 * lpad, lpx) * llh > wh + 0.5:
                out.append(f"node {node['id']}'s when does not fit its place")
    for item in [*(drawn.get("links") or []), *(drawn.get("groups") or [])]:
        if not item.get("label_box"):
            continue
        bw, bh = item["label_box"][2] * W, item["label_box"][3] * H
        lines = lines_of(item["label"], bw - 2 * lpad, lpx)
        if lines * llh > bh + 0.5:
            out.append(f"the words of {item['id']} take {lines} lines, more than their "
                       f"{bh:.0f} px place holds")
    return out
