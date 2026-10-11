"""Invalid runtime assets fail before work; worker pools close on every exit."""

from concurrent.futures import ThreadPoolExecutor
from threading import current_thread

import pytest
import synthetic_export as sx

from talks_build import build as buildmod


def _record_pools(monkeypatch):
    pools = []

    class RecordingExecutor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.submissions = []
            pools.append(self)

        def submit(self, *args, **kwargs):
            self.submissions.append((args, kwargs))
            return super().submit(*args, **kwargs)

    monkeypatch.setattr(buildmod, "ThreadPoolExecutor", RecordingExecutor)
    return pools


@pytest.mark.parametrize("asset", ["../player/missing.js", "../../private.js"])
def test_bad_asset_ref_preserves_published_outputs_without_media_work(tmp_path, monkeypatch, asset):
    talk = sx.write_talk(tmp_path)
    (tmp_path / "private.js").write_text("Private outside asset")
    outputs = {
        "site/player/talk.template.html": f'<title>{{{{TITLE}}}}</title><script src="{asset}"></script>',
        "site/index.html": "Previous talks index",
        "site/talks.json": '[{"slug": "fixture-talk", "title": "Previous talk"}]',
        "site/fixture-talk/index.html": "Previous published talk page",
        "site/fixture-talk/manifest.json": '{"previous": "manifest"}',
        "build/fixture-talk/report.json": '{"previous": "report"}',
        "build/fixture-talk/media/0123456789abcdef.webp": "Previous published media",
    }
    for name, content in outputs.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def snapshot():
        return {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    before = snapshot()
    pools = _record_pools(monkeypatch)

    def unexpected_export_read(*_):
        pytest.fail("invalid site assets must be rejected before reading the export or starting media work")

    monkeypatch.setattr(buildmod, "load_deck", unexpected_export_read)
    with pytest.raises(ValueError, match="owned asset (is missing|escapes the site root)"):
        buildmod.build(tmp_path / "unread-export", talk, log=lambda *_: None)
    assert snapshot() == before
    assert len(pools) == 1 and pools[0].submissions == []
    with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
        pools[0].submit(lambda: None)


@pytest.mark.parametrize("fail", [False, True])
def test_build_wrapper_joins_workers_on_success_and_failure(tmp_path, monkeypatch, fail):
    talk = sx.write_talk(tmp_path)
    pools = _record_pools(monkeypatch)
    workers = []

    def run_with_worker(builder):
        workers.append(builder.pool.submit(current_thread).result(timeout=5))
        if fail:
            raise buildmod.BuildError("failure after a media job")
        return {"completed": True}

    monkeypatch.setattr(buildmod.Builder, "run", run_with_worker)
    if fail:
        with pytest.raises(buildmod.BuildError, match="failure after a media job"):
            buildmod.build(tmp_path / "unused-export", talk, log=lambda *_: None)
    else:
        assert buildmod.build(tmp_path / "unused-export", talk, log=lambda *_: None) == {"completed": True}
    assert len(workers) == 1 and not workers[0].is_alive()
    with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
        pools[0].submit(lambda: None)
