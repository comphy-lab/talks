"""Read a Keynote HTML export and model its click states.

Export layout (see docs/SPEC.md, section 1.1)::

    assets/header.json                     slideList, slideWidth, slideHeight
    assets/<UUID>/<UUID>.json              events[] with baseLayer trees
    assets/<UUID>/assets/<UUID>.pdf        texture atlas, one page per texture
    assets/<UUID>/assets/<movie files>     movie copies (one per slide)

State model
-----------
Each event's ``baseLayer`` is the slide *before* that event fires. The last
event is the outgoing transition; its ``baseLayer`` is a single flattened
texture of the final state. Events with ``automaticPlay`` fire without a
click, so the presenter rests only in front of click events: one rest
(click) state per non-automatic event, whose picture is that event's
``baseLayer``. Automatic events fold into the state they follow.

Mid-slide ``baseLayer`` trees are composites of per-object textures with
translate-only geometry and ``hidden`` flags for objects not yet built in.
For a flattened state we can still recover a per-object tree by taking the
latest per-object ``baseLayer`` and replaying the visibility builds of the
events in between (verified against the flattened texture by the build).
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import Rect

MOVIE_START = "apple:movie-start"
RENDER_MOVIE = "renderMovie"
VISIBILITY_BUILDS = {"apple:appear", "apple:bc-appear", "apple:dissolve"}
ANIMATED_BUILDS = {"apple:dissolve"}


class ExportError(RuntimeError):
    """The export does not have the structure this pipeline understands."""


@dataclass
class Layer:
    """One node of a ``baseLayer`` tree, with absolute geometry."""

    path: tuple[int, ...]
    object_id: str | None
    rect: Rect
    hidden: bool
    opacity: float
    texture: str | None
    is_video: bool
    children: list[Layer] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True)
class Leaf:
    """A drawable texture in z-order (``order`` 0 is the bottom)."""

    order: int
    path: tuple[int, ...]
    rect: Rect
    texture: str
    object_id: str | None
    visible: bool
    opacity: float


@dataclass(frozen=True)
class Effect:
    type: str | None
    name: str | None
    object_id: str | None
    duration: float
    movie: dict | None
    base_layer: dict | None


@dataclass
class Event:
    index: int
    auto: bool
    effects: list[Effect]
    base: Layer
    accessibility: list[dict]
    flattened: bool


@dataclass(frozen=True)
class MovieUse:
    """A movie started on a slide (from its ``renderMovie`` effect)."""

    object_id: str
    asset_id: str
    file: Path
    rect: Rect
    start: float
    end: float
    loop_mode: str | None
    native_width: int
    native_height: int
    event_index: int


@dataclass
class State:
    slide: Slide
    index: int
    event_index: int
    is_final: bool
    tree: Layer | None
    movies: list[MovieUse]
    transition: str

    def leaves(self) -> list[Leaf]:
        return leaves_of(self.tree) if self.tree is not None else []


@dataclass
class Slide:
    n: int
    id: str
    dir: Path
    pdf: Path
    assets: dict
    events: list[Event]
    states: list[State] = field(default_factory=list)

    @property
    def final_event(self) -> Event:
        return self.events[-1]

    def texture_page(self, texture_id: str) -> int:
        """0-based page index of a texture in the slide's PDF atlas."""
        asset = self.assets.get(texture_id)
        if not asset or asset.get("type") != "texture":
            raise ExportError(f"slide {self.n}: texture {texture_id} not in assets")
        return int(asset["index"])

    def texture_pdf(self, texture_id: str) -> Path:
        asset = self.assets[texture_id]
        native = asset.get("url", {}).get("native")
        if not native:
            raise ExportError(f"slide {self.n}: texture {texture_id} has no native url")
        return self.dir / native

    def movies(self) -> list[MovieUse]:
        out: list[MovieUse] = []
        for ev in self.events:
            for eff in ev.effects:
                if eff.name == RENDER_MOVIE and eff.movie:
                    out.append(self._movie_use(ev.index, eff))
        return out

    def _movie_use(self, event_index: int, eff: Effect) -> MovieUse:
        movie = eff.movie or {}
        asset_id = movie.get("asset")
        asset = self.assets.get(asset_id or "")
        if not asset or asset.get("type") != "video":
            raise ExportError(f"slide {self.n}: movie asset {asset_id!r} missing")
        native = asset.get("url", {}).get("native")
        if not eff.base_layer:
            raise ExportError(f"slide {self.n}: renderMovie without baseLayer")
        rect = Rect.from_layer_state(eff.base_layer["initialState"])
        return MovieUse(
            object_id=eff.object_id or "",
            asset_id=asset_id,
            file=self.dir / native,
            rect=rect,
            start=float(movie.get("startTime") or 0.0),
            end=float(movie.get("endTime") or 0.0),
            loop_mode=movie.get("loopMode"),
            native_width=int(asset.get("width") or 0),
            native_height=int(asset.get("height") or 0),
            event_index=event_index,
        )


@dataclass
class Deck:
    root: Path
    width: int
    height: int
    slides: list[Slide]
    header: dict

    @property
    def states(self) -> list[State]:
        return [s for slide in self.slides for s in slide.states]


# --------------------------------------------------------------------------
# Parsing


def parse_layer(raw: dict, parent_x: float = 0.0, parent_y: float = 0.0, path: tuple[int, ...] = ()) -> Layer:
    state = raw.get("initialState", {})
    rect = Rect.from_layer_state(state, parent_x, parent_y)
    _check_transform(state)
    layer = Layer(
        path=path,
        object_id=raw.get("objectID"),
        rect=rect,
        hidden=bool(state.get("hidden", False)),
        opacity=float(state.get("opacity", 1.0)),
        texture=raw.get("texture"),
        is_video=bool(raw.get("isVideoLayer", False)),
    )
    for i, child in enumerate(raw.get("layers", []) or []):
        layer.children.append(parse_layer(child, rect.x, rect.y, path + (i,)))
    return layer


def _check_transform(state: dict) -> None:
    affine = state.get("affineTransform", [1, 0, 0, 1, 0, 0])
    if list(affine) != [1, 0, 0, 1, 0, 0] or state.get("rotation", 0) or state.get("scale", 1) != 1:
        raise ExportError("non-translate layer transform; this pipeline only handles translate-only layers")
    crop = state.get("contentsRect", {"x": 0, "y": 0, "width": 1, "height": 1})
    if (crop.get("x", 0), crop.get("y", 0), crop.get("width", 1), crop.get("height", 1)) != (0, 0, 1, 1):
        raise ExportError("contentsRect crop is not supported")


def flatten_effects(effects: list[dict]) -> list[Effect]:
    out: list[Effect] = []
    for eff in effects or []:
        out.append(
            Effect(
                type=eff.get("type"),
                name=eff.get("name"),
                object_id=eff.get("objectID"),
                duration=float(eff.get("duration") or 0.0),
                movie=eff.get("movie") if isinstance(eff.get("movie"), dict) else None,
                base_layer=eff.get("baseLayer"),
            )
        )
        out.extend(flatten_effects(eff.get("effects", [])))
    return out


def leaves_of(tree: Layer) -> list[Leaf]:
    """Textured layers in paint order with effective visibility."""
    out: list[Leaf] = []

    def visit(layer: Layer, hidden: bool, opacity: float, owner: str | None) -> None:
        hid = hidden or layer.hidden
        op = opacity * layer.opacity
        own = layer.object_id if layer.object_id not in (None, "0") else owner
        if layer.texture:
            out.append(
                Leaf(
                    order=len(out),
                    path=layer.path,
                    rect=layer.rect,
                    texture=layer.texture,
                    object_id=own,
                    visible=(not hid) and op > 0.0,
                    opacity=op,
                )
            )
        for child in layer.children:
            visit(child, hid, op, own)

    visit(tree, False, 1.0, None)
    return out


def apply_builds(tree: Layer, effects: list[Effect]) -> Layer:
    """Return a copy of ``tree`` with the visibility builds of ``effects`` applied."""
    tree = copy.deepcopy(tree)
    by_object: dict[str, list[Layer]] = {}
    for layer in tree.walk():
        if layer.object_id:
            by_object.setdefault(layer.object_id, []).append(layer)
    for eff in effects:
        if eff.type not in ("buildIn", "buildOut") or not eff.object_id:
            continue
        if eff.name in (MOVIE_START, RENDER_MOVIE):
            continue
        for layer in by_object.get(eff.object_id, []):
            if eff.type == "buildIn":
                for node in layer.walk():
                    node.hidden = False
            else:
                layer.hidden = True
    return tree


def _is_flattened(tree: Layer, width: int, height: int) -> bool:
    visible = [leaf for leaf in leaves_of(tree) if leaf.visible]
    return len(visible) == 1 and round(visible[0].rect.w) == width and round(visible[0].rect.h) == height


def export_digest(export_dir: Path) -> str:
    """SHA-256 of what a build reads for slide pictures: header, slide JSON and texture atlases."""
    export_dir = Path(export_dir)
    header = export_dir / "assets" / "header.json"
    h = hashlib.sha256(header.read_bytes())
    for sid in json.loads(header.read_text(encoding="utf-8"))["slideList"]:
        sdir = export_dir / "assets" / sid
        for path in (sdir / f"{sid}.json", sdir / "assets" / f"{sid}.pdf"):
            h.update(f"\0{sid}/{path.name}\0".encode())
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
    return h.hexdigest()


def load_deck(export_dir: Path) -> Deck:
    export_dir = Path(export_dir)
    assets_dir = export_dir / "assets"
    header_path = assets_dir / "header.json"
    if not header_path.is_file():
        raise ExportError(f"not a Keynote HTML export (missing assets/header.json): {export_dir}")
    header = json.loads(header_path.read_text(encoding="utf-8"))
    width = int(header.get("slideWidth", 1920))
    height = int(header.get("slideHeight", 1080))
    slides: list[Slide] = []
    for n, sid in enumerate(header["slideList"], 1):
        sdir = assets_dir / sid
        data = json.loads((sdir / f"{sid}.json").read_text(encoding="utf-8"))
        events: list[Event] = []
        raw_events = data.get("events", [])
        if not raw_events:
            raise ExportError(f"slide {n} ({sid}) has no events")
        for i, ev in enumerate(raw_events):
            base = parse_layer(ev["baseLayer"])
            events.append(
                Event(
                    index=i,
                    auto=bool(ev.get("automaticPlay", False)),
                    effects=flatten_effects(ev.get("effects", [])),
                    base=base,
                    accessibility=list(ev.get("accessibility", []) or []),
                    flattened=(i == len(raw_events) - 1) and _is_flattened(base, width, height),
                )
            )
        if not events[-1].flattened:
            raise ExportError(f"slide {n} ({sid}): last event is not a single flattened texture")
        slide = Slide(
            n=n,
            id=sid,
            dir=sdir,
            pdf=sdir / "assets" / f"{sid}.pdf",
            assets=data.get("assets", {}),
            events=events,
        )
        slides.append(slide)
    deck = Deck(root=export_dir, width=width, height=height, slides=slides, header=header)
    for i, slide in enumerate(slides):
        prev = slides[i - 1] if i > 0 else None
        slide.states = compute_states(slide, prev)
    return deck


# --------------------------------------------------------------------------
# States


def click_event_indices(events: list[Event]) -> list[int]:
    clicks = [ev.index for ev in events if not ev.auto]
    return clicks or [events[-1].index]


def _transition_name(effects: list[Effect]) -> str:
    for eff in effects:
        if eff.type == "transition":
            return "none" if eff.name in (None, "none") else "dissolve"
    return "none"


def _builds_transition(events: list[Event]) -> str:
    for ev in events:
        for eff in ev.effects:
            if eff.type in ("buildIn", "buildOut") and eff.name in ANIMATED_BUILDS and eff.duration > 0.05:
                return "dissolve"
    return "none"


def tree_for_event(slide: Slide, event_index: int) -> Layer | None:
    """Per-object tree of the rest state in front of ``event_index``."""
    ev = slide.events[event_index]
    if not ev.flattened:
        return ev.base
    for j in range(event_index - 1, -1, -1):
        if not slide.events[j].flattened:
            effects: list[Effect] = []
            for k in range(j, event_index):
                effects.extend(slide.events[k].effects)
            return apply_builds(slide.events[j].base, effects)
    return None


def playing_movies(slide: Slide, event_index: int) -> list[MovieUse]:
    """Movies started before ``event_index`` and not built out since."""
    playing: dict[str, MovieUse] = {}
    for ev in slide.events[:event_index]:
        for eff in ev.effects:
            if eff.name == RENDER_MOVIE and eff.movie:
                use = slide._movie_use(ev.index, eff)
                playing[use.object_id] = use
            elif eff.type == "buildOut" and eff.object_id in playing:
                playing.pop(eff.object_id, None)
    return list(playing.values())


def compute_states(slide: Slide, prev: Slide | None) -> list[State]:
    clicks = click_event_indices(slide.events)
    states: list[State] = []
    last = slide.events[-1].index
    for k, c in enumerate(clicks):
        if k == 0:
            entering = _transition_name(prev.final_event.effects) if prev else "none"
            builds = _builds_transition(slide.events[:c])
            transition = "dissolve" if "dissolve" in (entering, builds) else "none"
        else:
            transition = _builds_transition(slide.events[clicks[k - 1] : c])
        tree = tree_for_event(slide, c)
        if tree is None and c == last:
            # Only the flattened final texture exists: it is a one-leaf tree.
            tree = slide.events[-1].base
        movies = playing_movies(slide, c)
        if tree is not None:
            order = {leaf.object_id: leaf.order for leaf in reversed(leaves_of(tree)) if leaf.object_id}
            movies.sort(key=lambda m: order.get(m.object_id, 1 << 30))
        states.append(
            State(
                slide=slide,
                index=k,
                event_index=c,
                is_final=(c == last),
                tree=tree,
                movies=movies,
                transition=transition,
            )
        )
    return states


def movie_leaf_orders(tree: Layer, object_id: str) -> tuple[int, int] | None:
    """(first, last) paint order of the leaves belonging to a movie object."""
    orders = [leaf.order for leaf in leaves_of(tree) if leaf.object_id == object_id]
    if not orders:
        return None
    return min(orders), max(orders)
