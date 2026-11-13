import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from jinja2 import Environment, FileSystemLoader

from mirrordash_tv import plugin
from mirrordash_tv.channels import CHANNELS, hls_url
from mirrordash_tv.plugin import SIZES, TvModule, cubic

PACKAGE = Path(plugin.__file__).parent
SCHEMA = json.loads((PACKAGE / "config_schema.json").read_text())
SVT1 = json.loads((Path(__file__).parent / "svt1.json").read_text())  # SVT's answer for ch-svt1, saved 2026-11-13
DEFAULTS = {key: prop["default"] for key, prop in SCHEMA["properties"].items()}


# ── The channels ──

def test_the_stream_is_the_hls_master_in_svts_answer():
    assert hls_url(SVT1) == "https://svt-live-channel.akamaized.net/l4/se/svt1/master-fmp4.m3u8?format=hls&defaultSubLang=1"
    for no_stream in (None, [], {}, {"videoReferences": []}, {"videoReferences": [{"format": "dash", "url": "x"}]},
                      {"videoReferences": [{"format": "hls"}, "junk"]}):
        assert hls_url(no_stream) is None


def test_the_settings_offer_what_the_code_knows():
    assert SCHEMA["properties"]["channel"]["enum"] == list(CHANNELS)
    assert SCHEMA["properties"]["size"]["enum"] == list(SIZES)
    assert len(SCHEMA["properties"]["channel"]["enum_titles"]) == len(CHANNELS)
    assert all(abs(width * 9 / 16 - height) < 1 for width, height in SIZES.items())


def test_the_volume_slider_is_on_radios_curve():
    assert cubic(0) == 0 and cubic(100) == 1 and cubic(50) == 0.125 and cubic(150) == 1


# ── The states ──

class Video:
    """What `async with self.video_window(...)` gives: the first picture, then it ends (or not) when the test says."""
    def __init__(self, picture: bool, failed: bool, reason: str):
        self.picture, self.failed, self.reason = picture, failed, reason
        self.finished = asyncio.Event()

    async def started(self) -> bool:
        return self.picture


def make_module(**settings):
    """The module as the mirror sets it up, with the mirror's helpers replaced by fakes."""
    module = TvModule({**DEFAULTS, **settings})
    module.render_template = MagicMock(side_effect=lambda name, **context: context)  # returns what the template gets
    module.translate = lambda key, default=None: default
    module.fetch_json = AsyncMock(return_value=(SVT1, None))
    module.windows = []  # (what it asked for, the video) of each block
    module.left = []  # the blocks that were left, in order
    module.script = []  # the videos the next blocks give, one each

    @asynccontextmanager
    async def video_window(**options):
        video = module.script.pop(0)
        module.windows.append((options, video))
        try:
            yield video
        finally:
            module.left.append(video)
    module.video_window = video_window
    return module


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(plugin, "RETRY", (0.1, 0.2))
    monkeypatch.setattr(plugin, "CARD_FIRST", 0)


async def until(condition, seconds=3):
    for _ in range(int(seconds * 100)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


@pytest.mark.asyncio
async def test_it_starts_when_shown_plays_and_leaves_when_hidden():
    module = make_module(output="hdmi", volume=50)
    assert await module.render() == ""  # not shown yet: the card takes no space
    video = Video(True, False, "")
    module.script.append(video)
    await module.on_shown(True)
    await until(lambda: module.state == "playing")
    options = module.windows[0][0]
    assert options == {"url": hls_url(SVT1), "output": "hdmi", "volume": 0.125, "fullscreen": False, "hz": 50}
    module.fetch_json.assert_awaited_with("https://api.svt.se/video/ch-svt1", max_age=0)
    shown = await module.render()
    assert (shown["state"], shown["channel"], shown["width"], shown["height"]) == ("playing", "SVT1", 960, 540)

    await module.on_shown(False)  # schedule or privacy mode
    assert module.left == [video] and await module.render() == ""
    await module.on_shown(False)  # twice is harmless


@pytest.mark.asyncio
async def test_it_waits_for_the_first_picture_and_a_failure_shows_one_line_then_it_tries_again():
    module = make_module(channel="svt2", size=640)
    first, second = Video(False, True, "no such stream"), Video(True, False, "")
    module.script += [first, second]
    await module.on_shown(True)
    await until(lambda: module.windows)
    assert (await module.render())["state"] == "starting"  # until video.started() says otherwise
    first.finished.set()  # it ended before any picture: failed
    await until(lambda: module.state == "failed")
    shown = await module.render()
    assert shown["state"] == "failed" and shown["channel"] == "SVT2" and module.left == [first]
    await until(lambda: len(module.windows) == 2)  # the retry, after RETRY[0]
    await until(lambda: module.state == "playing")
    assert module.windows[1][0]["url"] == hls_url(SVT1)
    await module.on_shown(False)


@pytest.mark.asyncio
async def test_no_stream_from_svt_or_a_refused_video_is_a_failure_with_retries():
    module = make_module()
    module.fetch_json.return_value = ({}, "offline")
    await module.on_shown(True)
    await until(lambda: module.state == "failed")
    assert module.windows == []
    module.fetch_json.return_value = (SVT1, None)

    @asynccontextmanager
    async def refused(**options):
        raise RuntimeError("the mirror said no")
        yield
    module.video_window = refused
    await until(lambda: module.fetch_json.await_count >= 2)  # tried again, a step later
    await until(lambda: module.state == "failed")
    await module.on_shown(False)


@pytest.mark.asyncio
async def test_the_retry_steps_grow_and_start_over_after_a_picture(monkeypatch):
    waits = []
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        waits.append(seconds)
        await real_sleep(0.001)
    monkeypatch.setattr(plugin.asyncio, "sleep", sleep)
    module = make_module()
    ends = [Video(False, True, "x") for _ in range(3)] + [Video(True, True, "y"), Video(False, True, "z")]
    for video in ends:
        video.finished.set()
    module.script += ends
    await module.on_shown(True)
    await until(lambda: len(module.windows) == 5)
    await module.on_shown(False)
    assert [w for w in waits if w in (0.1, 0.2)][:4] == [0.1, 0.2, 0.2, 0.1]  # the picture of the 4th started the steps over


@pytest.mark.asyncio
async def test_fullscreen_shows_nothing_while_it_plays_and_one_line_when_it_fails():
    module = make_module(fullscreen=True)
    video = Video(True, False, "")
    module.script.append(video)
    await module.on_shown(True)
    await until(lambda: module.state == "playing")
    assert module.windows[0][0]["fullscreen"] is True and await module.render() == ""
    video.failed = True
    video.finished.set()
    await until(lambda: module.state == "failed")
    assert (await module.render())["state"] == "failed"
    await module.on_shown(False)


@pytest.mark.asyncio
async def test_the_admin_preview_shows_the_card_and_never_plays():
    module = make_module(preview=True, fullscreen=True)
    await module.on_shown(True)
    assert module.windows == [] and (await module.render())["state"] == "starting"


# ── The card ──

def render(state, language="sv", channel="SVT1", width=960, height=540):
    translations = json.loads((PACKAGE / "translations" / f"{language}.json").read_text())
    environment = Environment(loader=FileSystemLoader(PACKAGE / "templates"), autoescape=True)
    return environment.get_template("widget.html").render(state=state, channel=channel, width=width, height=height,
                                                         translations=translations)


def test_starting_is_still_text_in_the_rectangle_playing_is_a_plain_one_and_failing_one_line():
    starting = render("starting")
    assert "Startar SVT1" in starting and 'data-video-window style="width: 960px; height: 540px"' in starting
    assert "Starting SVT1" in render("starting", "en")
    playing = render("playing")
    assert "data-video-window" in playing and "Startar" not in playing and "<h2 class=\"module-header\">SVT1</h2>" in playing
    failed = render("failed", channel="SVT2")
    assert "SVT2 går inte att spela just nu" in failed and "module-message" in failed and "data-video-window" not in failed
