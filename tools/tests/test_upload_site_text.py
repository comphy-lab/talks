"""Upload planning (never overwrite), page template, talks index, text helpers."""

import json
from types import SimpleNamespace

import pytest

from talks_build import site, upload
from talks_build.geometry import Rect
from talks_build.text import clean_text, replace_spaced, title_from_text
from talks_build.youtube import choose_rect, contrast_ratio

A = "0123456789abcdef"
B = "fedcba9876543210"


def _media(tmp_path):
    d = tmp_path / "media"
    d.mkdir()
    (d / f"{A}.webp").write_bytes(b"a")
    (d / f"{B}.mp4").write_bytes(b"b")
    (d / "notes.txt").write_text("ignored")
    return d


class FakeCF:
    """Stands in for the ``cf`` CLI against one bucket.

    ``objects list`` answers as cf 1.0.0-beta.12 prints it: a bare JSON array
    of objects (no ``result_info``, so no cursor), honouring ``--prefix``,
    ``--start-after`` and ``--per-page`` (capped at ``server_cap``, as a
    server may cap pages below what was asked).
    """

    def __init__(self, keys=(), server_cap=1000, raw=None):
        self.keys = sorted(keys)
        self.server_cap = server_cap
        self.raw = list(raw) if raw is not None else None
        self.calls = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)

        def opt(name, default=None):
            return args[args.index(name) + 1] if name in args else default

        if args[:4] == ["cf", "r2", "objects", "list"]:
            if self.raw is not None:
                return self.raw.pop(0)
            prefix, after = opt("--prefix", ""), opt("--start-after")
            per_page = min(int(opt("--per-page", 1000)), self.server_cap)
            page = [k for k in self.keys if k.startswith(prefix) and (after is None or k > after)][:per_page]
            return json.dumps([{"key": k, "size": 1} for k in page])
        if args[:4] == ["cf", "r2", "objects", "put"]:
            return "{}"
        raise AssertionError(args)


def test_upload_skips_existing_keys_and_sets_content_type(tmp_path):
    media = _media(tmp_path)
    cf = FakeCF([f"talks/demo/{A}.webp", f"talks/other/{B}.mp4"])
    done = upload.upload(media, "demo", dry_run=False, runner=cf, log=lambda *_: None)
    assert done == [f"talks/demo/{B}.mp4"]
    puts = [c for c in cf.calls if c[3] == "put"]
    assert len(puts) == 1
    put = puts[0]
    assert put[4] == f"talks/demo/{B}.mp4"
    assert put[put.index("--bucket-name") + 1] == "comphy-media"
    assert put[put.index("--content-type") + 1] == "video/mp4"


@pytest.mark.parametrize("server_cap", [1000, 2, 1])
def test_listing_reads_every_page_without_a_cursor(server_cap):
    """More keys than one page: all are found, so none is uploaded again."""
    keys = [f"talks/demo/{i:016x}.webp" for i in range(2500)]
    cf = FakeCF(keys + ["talks/demo-two/0000000000000000.webp"], server_cap=min(server_cap, 1000))
    if server_cap < 1000:
        keys = keys[:7]
        cf.keys = sorted(keys)
    assert upload.remote_keys("demo", runner=cf) == set(keys)
    lists = [c for c in cf.calls if c[3] == "list"]
    assert all("--cursor" not in c for c in lists)
    assert lists[0][lists[0].index("--per-page") + 1] == "1000"
    # One more call than there are pages: the last one comes back empty.
    assert len(lists) == -(-len(keys) // min(server_cap, 1000)) + 1


def test_listing_that_never_advances_is_refused(monkeypatch):
    monkeypatch.setattr(upload, "MAX_PAGES", 5)

    def endless(args):
        after = args[args.index("--start-after") + 1] if "--start-after" in args else "talks/demo/0"
        return json.dumps([{"key": after + "0"}])

    with pytest.raises(RuntimeError, match="did not terminate"):
        upload.remote_keys("demo", runner=endless)


def test_listing_accepts_the_api_envelope_too():
    page = json.dumps({"result": [{"key": f"talks/demo/{A}.webp"}], "result_info": {"cursor": "c"}})
    cf = FakeCF(raw=[page, json.dumps([])])
    assert upload.remote_keys("demo", runner=cf) == {f"talks/demo/{A}.webp"}


@pytest.mark.parametrize("slug", ["demo/../demo", "Demo", "demo-", "-demo", "de--mo", "", "demo talk"])
def test_bad_slugs_are_refused(tmp_path, slug):
    cf = FakeCF()
    with pytest.raises(ValueError):
        upload.upload(_media(tmp_path), slug, dry_run=True, check_remote=False, runner=cf, log=lambda *_: None)
    with pytest.raises(ValueError):
        upload.remote_keys(slug, runner=cf)
    assert cf.calls == []


def test_upload_dry_run_never_puts(tmp_path):
    media = _media(tmp_path)
    cf = FakeCF()
    done = upload.upload(media, "demo", dry_run=True, check_remote=False, runner=cf, log=lambda *_: None)
    assert sorted(done) == [f"talks/demo/{A}.webp", f"talks/demo/{B}.mp4"]
    assert cf.calls == []


def test_upload_refuses_when_listing_is_not_json(tmp_path):
    media = _media(tmp_path)
    cf = FakeCF(raw=["not json"])
    with pytest.raises(RuntimeError):
        upload.upload(media, "demo", dry_run=False, runner=cf, log=lambda *_: None)
    assert not [c for c in cf.calls if c[3] == "put"]


def test_upload_real_run_requires_listing(tmp_path):
    with pytest.raises(ValueError):
        upload.upload(_media(tmp_path), "demo", dry_run=False, check_remote=False, runner=FakeCF())


def test_upload_only_manifest_files(tmp_path):
    media = _media(tmp_path)
    cf = FakeCF()
    done = upload.upload(media, "demo", dry_run=False, runner=cf, only={f"{A}.webp"}, log=lambda *_: None)
    assert done == [f"talks/demo/{A}.webp"]


TALK = SimpleNamespace(
    slug="demo-talk",
    title='Bubbles & "jets" <live>',
    subtitle="Sub",
    speaker="Speaker",
    venue_note="First given somewhere.",
    updated="2026-10-10",
)


def test_render_page_escapes_and_requires_tokens():
    out = site.render_page("<title>{{TITLE}}</title><meta content=\"{{DESCRIPTION}}\">{{SLUG}}", title=TALK.title,
                           description=site.description(TALK), slug=TALK.slug)  # fmt: skip
    assert "Bubbles &amp; &quot;jets&quot; &lt;live&gt;" in out
    assert "{{" not in out
    assert site.description(TALK) == "Sub. A talk by Speaker. First given somewhere."
    with pytest.raises(ValueError):
        site.render_page("<title>{{TITLE}}</title>", title="a", description="b", slug="c")


def test_talks_index_replaces_and_sorts(tmp_path):
    (tmp_path / "talks.json").write_text(
        json.dumps(
            [
                {"slug": "demo-talk", "title": "old", "updated": "2020-01-01"},
                {"slug": "older", "title": "x", "updated": "2025-01-01"},
            ]
        )  # fmt: skip
    )
    site.update_index(tmp_path, TALK)
    data = json.loads((tmp_path / "talks.json").read_text())
    assert [d["slug"] for d in data] == ["demo-talk", "older"]
    assert data[0] == {
        "slug": "demo-talk", "title": TALK.title, "subtitle": "Sub", "speaker": "Speaker",
        "updated": "2026-10-10", "url": "demo-talk/",
    }  # fmt: skip


def test_clean_text_drops_filenames_and_fixes_ligature():
    raw = "Viscosity  enhances\nthe jet pasted-image.pdf hot_r.pdf ￼ 542_02.mp4 di!erentiate Wow! Yes"
    assert clean_text(raw) == "Viscosity enhances the jet differentiate Wow! Yes"
    assert title_from_text("\n0\n-2\nViscosity enhances the jet\nmore") == "Viscosity enhances the jet"
    assert (
        replace_spaced("Gabache et al.\n(2016), Sc. Rep.", "Gabache et al. (2016), Sc. Rep.", "G (2016)") == "G (2016)"
    )


def test_choose_rect_avoids_blockers_and_is_16_9():
    blockers = [Rect(183, 82, 1460, 77), Rect(224, 1032, 1350, 48)]
    x, y, w, h = choose_rect(blockers, 1920, 1080)
    assert w * 9 == h * 16
    r = Rect(x, y, w, h)
    assert all(not r.intersects(b.inflate(16)) for b in blockers)
    assert 0 <= x and x + w <= 1920 and 0 <= y and y + h <= 1080
    assert choose_rect(blockers, 1920, 1080) == (x, y, w, h)  # deterministic


def test_contrast_ratio():
    assert contrast_ratio((255, 255, 255), (255, 255, 255)) == pytest.approx(1.0)
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)


def test_erase_box_transparent_and_opaque():
    import numpy as np
    from PIL import Image

    from talks_build.patches import erase_box

    clear = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    clear.paste((0, 0, 0, 255), (5, 5, 15, 15))
    out = np.asarray(erase_box(clear, (5, 5, 15, 15)))
    assert out[5:15, 5:15, 3].max() == 0
    solid = Image.new("RGBA", (20, 20), (200, 100, 50, 255))
    solid.paste((0, 0, 0, 255), (5, 5, 15, 15))
    out = np.asarray(erase_box(solid, (5, 5, 15, 15)))
    assert (out[5:15, 5:15] == [200, 100, 50, 255]).all()


def test_cli_upload_refuses_a_slug_that_differs_from_the_manifest(tmp_path, capsys):
    """A manifest under site/<slug>/ must name that slug; the R2 prefix comes from it."""
    from test_manifest import H, good

    from talks_build.cli import main

    m = good()  # slug demo-talk
    (tmp_path / "site" / "other-talk").mkdir(parents=True)
    (tmp_path / "site" / "other-talk" / "manifest.json").write_text(json.dumps(m))
    media = tmp_path / "build" / "other-talk" / "media"
    media.mkdir(parents=True)
    for name in (f"{H}.webp", f"{H}.mp4"):
        (media / name).write_bytes(b"x")
    assert main(["upload", "--slug", "other-talk", "--root", str(tmp_path), "--dry-run"]) == 1
    assert "manifest's slug is 'demo-talk'" in capsys.readouterr().err
    assert main(["upload", "--slug", "other-talk/../other-talk", "--root", str(tmp_path), "--dry-run"]) == 1
    assert "bad slug" in capsys.readouterr().err
