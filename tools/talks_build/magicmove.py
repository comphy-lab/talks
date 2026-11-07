"""Magic Move transitions (docs/SPEC.md sections 1.2 and 3.2).

Keynote's Magic Move leaves its slide without a flattened final texture, so the
export used for the web sets that transition to Dissolve, and ``talk.yaml``
records the decision instead: ``magic_move: [{slide: <outgoing UUID>, duration: s}]``.
The first state of the following slide then carries ``transition: magic-move``
and ``transition_duration``; the player keeps every video whose source appears
in both states at a different rect playing and glides it into place.
"""

from __future__ import annotations


class MagicMoveError(ValueError):
    pass


def _key(m: dict) -> tuple:
    """(kind, ref, placement key): the key rounds as the player does (Math.round)."""
    from .manifest import placement_key

    return (m["kind"], m["ref"], placement_key(m))


def moved_videos(before: dict, after: dict) -> list[str]:
    """Refs of videos the player would glide: placements whose exact key is not
    kept, paired with an unkept placement of the same ref in the earlier state."""
    old = [_key(m) for m in before.get("media", []) if m["kind"] == "video"]
    new = [_key(m) for m in after.get("media", []) if m["kind"] == "video"]
    kept = set(old) & set(new)
    free_old = [k for k in old if k not in kept]
    moved = []
    for k in new:
        if k in kept:
            continue
        match = next((o for o in free_old if o[1] == k[1]), None)
        if match is not None:
            free_old.remove(match)
            moved.append(k[1])
    return moved


def apply_magic_moves(slides: list[dict], magic_move: dict[str, float]) -> list[dict]:
    """Mark the entering state of each slide that follows a listed slide; fail closed."""
    report = []
    index = {s["id"]: i for i, s in enumerate(slides)}
    for sid, duration in magic_move.items():
        i = index.get(sid)
        if i is None:
            raise MagicMoveError(f"magic_move: slide {sid} is not in the export")
        if i + 1 >= len(slides):
            raise MagicMoveError(f"magic_move: slide {sid} is the last slide")
        before, after = slides[i]["states"][-1], slides[i + 1]["states"][0]
        refs = moved_videos(before, after)
        if not refs:
            raise MagicMoveError(
                f"magic_move: no video moves from slide {i + 1} to slide {i + 2} (same source, new rect)"
            )
        if "glide_image" not in before or "glide_image" not in after:
            raise MagicMoveError(
                f"magic_move: slides {i + 1}-{i + 2} have no glide stills (states without per-object trees)"
            )
        after["transition"] = "magic-move"
        after["transition_duration"] = round(float(duration), 3)
        report.append({"from_slide": i + 1, "to_slide": i + 2, "videos": refs, "duration": after["transition_duration"]})
    return report
