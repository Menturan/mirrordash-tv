import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from jinja2 import Environment, FileSystemLoader

from mirrordash_tv import plugin
from mirrordash_tv.channels import CHANNELS, hls_url
from mirrordash_tv.plugin import TvModule, cubic
from mirrordash_tv.sources import ADDRESS, MAX_NAME, own_sources

PACKAGE = Path(plugin.__file__).parent
SCHEMA = json.loads((PACKAGE / "config_schema.json").read_text())
SVT1 = json.loads((Path(__file__).parent / "svt1.json").read_text())  # SVT's answer for ch-svt1, saved 2026-11-13
DEFAULTS = {key: prop["default"] for key, prop in SCHEMA["properties"].items() if "default" in prop}


# ── The channels ──

def test_the_stream_is_the_hls_master_in_svts_answer():
    assert hls_url(SVT1) == "https://svt-live-channel.akamaized.net/l4/se/svt1/master-fmp4.m3u8?format=hls&defaultSubLang=1"
    for no_stream in (None, [], {}, {"videoReferences": []}, {"videoReferences": [{"format": "dash", "url": "x"}]},
                      {"videoReferences": [{"format": "hls"}, "junk"]}):
        assert hls_url(no_stream) is None


def test_the_settings_offer_what_the_code_knows():
    assert SCHEMA["properties"]["source"]["format"] == "list" and "channel" not in SCHEMA["properties"]
    assert callable(TvModule.settings_list_source)  # the method the core calls for the list
    assert "size" not in SCHEMA["properties"] and SCHEMA["properties"]["width_percent"] == {"default": 50}  # the card sets the width
    row = SCHEMA["properties"]["own_sources"]["items"]
    assert row["required"] == ["name", "url"] and row["properties"]["url"]["format"] == "lan-url"
    assert row["properties"]["name"]["maxLength"] == MAX_NAME
    assert re.fullmatch(row["properties"]["url"]["pattern"], "http://nas:8080/a.m3u8")
    assert not re.fullmatch(row["properties"]["url"]["pattern"], "ftp://nas/a")
    assert all(ADDRESS.fullmatch(a) for a in ("https://a/b.m3u8", "http://192.168.1.20:8080/live/index.m3u8"))


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
    module = TvModule({**DEFAULTS, "source": {"id": "svt1", "name": "SVT1"}, **settings})
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
    assert (shown["state"], shown["channel"]) == ("playing", "SVT1")

    await module.on_shown(False)  # schedule or privacy mode
    assert module.left == [video] and await module.render() == ""
    await module.on_shown(False)  # twice is harmless


@pytest.mark.asyncio
async def test_it_waits_for_the_first_picture_and_a_failure_shows_one_line_then_it_tries_again():
    module = make_module(source={"id": "svt2", "name": "SVT2"})
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

def render(state, language="sv", channel="SVT1", show_header=True, heading=""):
    translations = json.loads((PACKAGE / "translations" / f"{language}.json").read_text())
    environment = Environment(loader=FileSystemLoader(PACKAGE / "templates"), autoescape=True)
    return environment.get_template("widget.html").render(state=state, channel=channel, show_header=show_header,
                                                         heading=heading, translations=translations)  # the last two: the core's


def test_starting_is_still_text_in_the_rectangle_playing_is_a_plain_one_and_failing_one_line():
    starting = render("starting")
    assert "Startar SVT1" in starting and 'data-video-window>' in starting and "px" not in starting.split("<div")[-1]
    assert "Starting SVT1" in render("starting", "en")
    playing = render("playing")
    assert "data-video-window" in playing and "Startar" not in playing and "<h2 class=\"module-header\">SVT1</h2>" in playing
    assert "module-header" not in render("playing", show_header=False)  # the heading is the core's Show heading
    assert '<h2 class="module-header">My TV</h2>' in render("playing", heading="My TV")
    failed = render("failed", channel="SVT2")
    assert "SVT2 går inte att spela just nu" in failed and "module-message" in failed and "data-video-window" not in failed


def test_the_card_escapes_a_name_the_owner_typed():
    name = '<b>"Camera" & \'hall\'</b>'
    for state in ("starting", "playing", "failed"):
        html = render(state, "en", channel=name)
        assert "<b>" not in html and "&lt;b&gt;" in html and "&#34;Camera&#34;" in html
    assert "Ingen kanal är vald" in render("no_channel", channel=None) and "No channel is chosen" in render("no_channel", "en", None)
    assert "module-message" in render("no_channel") and "data-video-window" not in render("no_channel")


# ── Own sources ──

ROWS = [{"name": "Camera hall", "url": "http://192.168.1.20:8080/live/index.m3u8?token=SECRET"},
        {"name": "Camera hall", "url": "http://elsewhere/x.m3u8"},  # the same name: the first plays
        {"name": "Radio", "url": " https://example.org/a.mp4 "}]
BAD_ROWS = [{}, {"name": "", "url": "http://a/b"}, {"name": "x", "url": ""}, {"name": "x", "url": "ftp://a/b"},
            {"name": "y" * 41, "url": "http://a/b"}, {"name": "  ", "url": "http://a/b"}, {"name": 5, "url": "http://a/b"},
            {"name": "z", "url": None}, "junk", None, 7, {"name": "w", "url": "http://a b/c"}]


def test_only_usable_rows_count_the_first_of_equal_names_wins_and_junk_is_ignored():
    assert own_sources(ROWS) == {"Camera hall": ROWS[0]["url"], "Radio": "https://example.org/a.mp4"}
    assert own_sources(BAD_ROWS) == {} and own_sources(None) == {} and own_sources("x") == {} and own_sources({}) == {}
    assert own_sources(BAD_ROWS + ROWS[2:]) == {"Radio": "https://example.org/a.mp4"}


@pytest.mark.asyncio
async def test_the_list_is_the_ready_made_channels_then_the_names_of_the_rows_as_typed_now():
    module = make_module(source={})
    listed = await module.settings_list_source({"own_sources": ROWS + BAD_ROWS})
    assert [c["id"] for c in listed] == [*CHANNELS, "own:Camera hall", "own:Radio"]
    assert [c["name"] for c in listed[:5]] == ["SVT1", "SVT2", "SVT24", "Kunskapskanalen", "Barnkanalen"]
    assert listed[-1] == {"id": "own:Radio", "name": "Radio"}
    assert [c["id"] for c in await module.settings_list_source({})] == list(CHANNELS)  # no row typed: no key at all


@pytest.mark.asyncio
async def test_an_own_source_plays_its_address_under_its_name_with_no_lookup():
    module = make_module(source={"id": "own:Camera hall", "name": "Camera hall"}, own_sources=ROWS)
    video = Video(True, False, "")
    module.script.append(video)
    await module.on_shown(True)
    await until(lambda: module.state == "playing")
    assert module.windows[0][0]["url"] == ROWS[0]["url"]
    module.fetch_json.assert_not_awaited()
    assert (await module.render())["channel"] == "Camera hall"
    await module.on_shown(False)


@pytest.mark.asyncio
async def test_a_failing_own_source_is_named_in_the_log_but_its_address_never_is(caplog):
    module = make_module(source={"id": "own:Camera hall", "name": "Camera hall"}, own_sources=ROWS)
    video = Video(False, True, f"could not open {ROWS[0]['url']}")
    video.finished.set()
    module.script.append(video)
    with caplog.at_level(logging.DEBUG):
        await module.on_shown(True)
        await until(lambda: module.state == "failed")
        await module.on_shown(False)

    @asynccontextmanager
    async def refused(**options):
        raise RuntimeError(f"refused {options['url']}")
        yield
    module = make_module(source={"id": "own:Camera hall", "name": "Camera hall"}, own_sources=ROWS)
    module.video_window = refused
    with caplog.at_level(logging.DEBUG):
        await module.on_shown(True)
        await until(lambda: module.state == "failed")
        await module.on_shown(False)
    assert "Camera hall" in caplog.text and "SECRET" not in caplog.text and "192.168" not in caplog.text
    assert await module.render() == ""


@pytest.mark.parametrize("choice", [{}, None, "svt1", {"id": "own:Gone", "name": "Gone"}, {"id": "nonsense"}, {"id": 5},
                                    {"id": "own:Bad"}])
@pytest.mark.asyncio
async def test_no_valid_choice_plays_nothing_and_says_so(choice):
    module = make_module(source=choice, own_sources=[{"name": "Bad", "url": "ftp://x"}])
    module.script.append(Video(True, False, ""))
    await module.on_shown(True)
    await asyncio.sleep(0.05)
    assert module.windows == [] and module.fetch_json.await_count == 0 and module.task is None
    assert (await module.render())["state"] == "no_channel"
    fullscreen = make_module(source=choice, fullscreen=True)
    await fullscreen.on_shown(True)
    assert (await fullscreen.render())["state"] == "no_channel"  # in fullscreen too: it is one line, not a picture
    await module.on_shown(False)
    assert await module.render() == ""
    assert (await make_module(source=choice, preview=True).render())["state"] == "no_channel"


@pytest.mark.asyncio
async def test_the_form_not_saved_yet_has_no_own_sources_key_and_still_builds():
    config = {k: v for k, v in DEFAULTS.items()}  # no "source", no "own_sources": a throwaway instance's form
    module = TvModule(config)
    assert module.title is None and await module.settings_list_source({}) is not None
