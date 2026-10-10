import pytest

from talks_build.geometry import Rect, even_floor, scaled_video_size, snap, snap_tenth


def test_snap_is_two_stage_half_up():
    assert snap(1347.65) == 1348  # 1347.7 -> 1348
    assert snap(1347.649) == 1348  # tenth first: 1347.6 -> 1348
    assert snap(1347.449) == 1347  # 1347.4 -> 1347
    assert snap(0.5) == 1
    assert snap(-0.0) == 0
    assert snap(-0.04) == 0
    assert snap_tenth(410.7843933) == 410.8


def test_float_noise_between_copies_gives_identical_ints():
    a = Rect(1347.6999999, -0.00001, 567.0000001, 1080.0)
    b = Rect(1347.7000001, 0.00001, 566.9999999, 1080.0)
    assert a.snapped() == b.snapped() == (1348, 0, 567, 1080)


def test_snapped_rounds_edges_not_sizes():
    # Slide 27's movie spans 132.71..1024.28 px: rounding the width on its own
    # (891.56 -> 892) would push the right edge to 1025.
    assert Rect(132.7120, 157.0327, 891.5640, 765.9345).snapped() == (133, 157, 891, 766)
    # Slide 37's movie spans 307.35..772.65 px down: 465.30 alone rounds to 465
    # and would leave the bottom row of the still's poster frame uncovered.
    assert Rect(72.1473, 307.3498, 856.5756, 465.3004).snapped() == (72, 307, 857, 466)
    for r in (Rect(941.7663, 266.5139, 1155.6959, 650.079), Rect(1409.0697, 147.2893, 485.6711, 785.4213)):
        x, y, w, h = r.snapped()
        # Two-stage rounding: at most 0.05 px (to the tenth) plus 0.5 px.
        assert abs(x - r.x) <= 0.55 and abs(x + w - r.x2) <= 0.55
        assert abs(y - r.y) <= 0.55 and abs(y + h - r.y2) <= 0.55


def test_rect_from_layer_state_uses_anchor_and_parent():
    state = {
        "position": {"pointX": 1349.6, "pointY": 575.9},
        "width": 634.7,
        "height": 705.2,
        "anchorPoint": {"pointX": 0.5, "pointY": 0.5},
    }
    r = Rect.from_layer_state(state)
    assert r.tenths() == (1032.3, 223.3, 634.7, 705.2)  # 1032.25 rounds half-up
    child = Rect.from_layer_state(
        {
            "position": {"pointX": 10, "pointY": 20},
            "width": 20,
            "height": 40,
            "anchorPoint": {"pointX": 0, "pointY": 1},
        },
        parent_x=100,
        parent_y=200,
    )
    assert child == Rect(110, 180, 20, 40)


def test_rect_intersection_and_union():
    a, b = Rect(0, 0, 10, 10), Rect(5, 5, 10, 10)
    assert a.intersection(b) == Rect(5, 5, 5, 5)
    assert a.intersects(b)
    assert not a.intersects(Rect(10, 0, 5, 5))  # touching edges do not overlap
    assert a.union(b) == Rect(0, 0, 15, 15)
    assert Rect(0, 0, 100, 100).contains(Rect(10, 10, 5, 5))
    assert a.inflate(2) == Rect(-2, -2, 14, 14)


def test_even_floor():
    assert even_floor(183) == 182
    assert even_floor(1116.9) == 1116
    assert even_floor(1.0) == 2


@pytest.mark.parametrize(
    "src,boxes,expected",
    [
        ((2128, 2574), [(558.0, 675.0)], (1116, 1350)),  # 2x the box
        ((512, 828), [(646.5, 1045.6), (485.7, 785.4)], (512, 828)),  # never upscale
        ((218, 183), [(507.9, 426.4)], (218, 182)),  # odd height made even
        ((3280, 1260), [(1654.1, 635.4)], (3280, 1260)),  # 2x box exceeds source
        ((1280, 720), [(100.0, 200.0)], (710, 400)),  # per-axis: height needs 2 x 200 px
    ],
)
def test_scaled_video_size(src, boxes, expected):
    assert scaled_video_size(*src, boxes, 2.0) == expected


def test_scaled_video_size_uses_largest_box():
    assert scaled_video_size(2000, 1000, [(100, 50), (400, 200)], 2.0) == (800, 400)
