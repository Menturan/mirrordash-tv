import asyncio
import contextlib
import logging

from mirrordash_tv.channels import API, CHANNELS, hls_url

logger = logging.getLogger("mirrordash.modules.mirrordash_tv")

SIZES = {480: 270, 640: 360, 854: 480, 960: 540, 1280: 720}  # width -> height, 16:9 (the Size setting)
RETRY = (5, 10, 30, 60)  # seconds before a stream that ended or failed is started again, then every minute (as Radio)
CARD_FIRST = 1  # seconds the card gets on the page before the mirror asks where its picture goes


def cubic(volume: int) -> float:
    """The Volume setting (0-100) as playbin's linear 0-1, on the curve Radio uses: a quarter of the slider is
    quiet, not a quarter of the loudness."""
    return round((max(0, min(100, volume)) / 100) ** 3, 4)


class TvModule:
    def __init__(self, config):
        self.config = config
        self.name = "mirrordash_tv"
        self.interval = 60  # the card again once a minute at most (a change comes at once anyway)
        self.changed = asyncio.Event()
        self.preview = config.get("preview", False)  # the admin page's preview: never plays
        self.channel = config["channel"] if config["channel"] in CHANNELS else "svt1"
        self.width = config["size"] if config["size"] in SIZES else 960
        self.output = config["output"]
        self.volume = int(config["volume"])
        self.fullscreen = config["fullscreen"]
        self.shown = False  # nothing plays until the mirror says the card is on the screen (on_shown)
        self.state = "off"  # "off", "starting" (until the first picture), "playing" or "failed"
        self.task = None

    # ── The card ──
    async def run_loop(self, broadcast_func):
        try:
            while True:
                await broadcast_func(self.name, await self.render())
                self.changed.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.changed.wait(), self.interval)
        except asyncio.CancelledError:
            await self.stop()
            raise

    async def render(self) -> str:
        """Nothing (the card takes no space) while off, and in fullscreen while the picture plays or starts."""
        state = "starting" if self.preview else self.state
        if state == "off" or (self.fullscreen and state != "failed" and not self.preview):
            return ""
        return self.render_template("widget.html", state=state, channel=CHANNELS[self.channel], width=self.width,
                                    height=SIZES[self.width])

    # ── Playing ── v0: whenever the card is shown (no switch for it yet)
    async def on_shown(self, shown: bool):
        """The mirror: the card is on the screen or not (the screen off, the module's schedule, privacy mode).
        Hidden, the video stops; shown again, it starts."""
        self.shown = shown
        if shown and not self.preview:
            if not (self.task and not self.task.done()):
                self.task = asyncio.create_task(self.keep_playing())
        else:
            await self.stop()
        self.changed.set()

    async def stop(self):
        """Leave the video block (the mirror then ends the player and takes the window away) and go quiet."""
        task, self.task = self.task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.state = "off"

    async def keep_playing(self):
        """The video while the card is shown: started again after it ends or fails, a little later each time."""
        tries = 0
        while self.shown:
            picture, failed, reason = False, True, ""
            self.state = "starting"
            self.changed.set()
            try:
                answer, _ = await self.fetch_json(API.format(self.channel), max_age=0)  # an address of today
                url = hls_url(answer)
                if url is None:
                    raise RuntimeError("SVT gave no stream")
                if not self.fullscreen:
                    # ponytail: the mirror takes the picture's place from the page when the block starts, so the
                    # card with its rectangle must be there first; a pause is the smallest way to make sure
                    await asyncio.sleep(CARD_FIRST)
                async with self.video_window(url=url, output=self.output, volume=cubic(self.volume),
                                             fullscreen=self.fullscreen, hz=50) as video:
                    picture = await video.started()
                    if picture:
                        self.state = "playing"
                        self.changed.set()  # the window covers the rectangle: the text under it can go
                    await video.finished.wait()
                    failed, reason = video.failed, video.reason
            except (RuntimeError, TimeoutError) as e:  # the mirror refused the video, or SVT didn't answer
                reason = str(e) or type(e).__name__
            if not self.shown:
                return
            if picture:
                tries = 0
            if failed:
                logger.warning(f"{self.channel} can't be played: {reason}")  # the reason only: no address in the log
                self.state = "failed"
                self.changed.set()
            await asyncio.sleep(RETRY[min(tries, len(RETRY) - 1)])
            tries += 1
