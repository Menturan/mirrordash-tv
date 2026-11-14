# mirrordash-tv

Live TV on the mirror: SVT's channels and your own sources in a card.

The card shows the channel's name and, under it, the picture. The picture isn't part of the page: the mirror
plays it in a window of its own over the card's rectangle (the module asks, the core runs the player), so
nothing on the page can paint over it.

Needs the MirrorDash core at master from commit `516c472` ("the core runs the video player a module asks
for") or the release that includes it, and an OS image with sound for modules (0.12 or later). Your own sources work
on MirrorDash 0.17 and later, but an address on your **home network** (a NAS, a camera) plays only from
MirrorDash 0.18: until then the mirror's player refuses it and the card says the channel can't be played.

## Channels

SVT1, SVT2, SVT24, Kunskapskanalen and Barnkanalen, from SVT's own address list (no account or key). SVT24
and Barnkanalen share one feed at SVT, so they show the same picture. SVT only shows its channels in Sweden.

## Settings

| Setting | What it does |
| :--- | :--- |
| Channel | What plays: one of the ready-made channels above, or one of your own sources by its name. Nothing is chosen at first: the card then says *Ingen kanal är vald* until you choose |
| Your Own Sources | A list of rows, each a Name (at most 40 characters) and an Address (`http://` or `https://`). See "Your own sources" |
| Size | The picture's width: 480, 640, 854, 960 (default) or 1280 pixels, always 16:9. 1280 (720p) is the most the Raspberry Pi shows smoothly |
| Speakers | The 3.5 mm jack or the screen's HDMI sound (a screen without speakers stays silent) |
| Volume | 0-100 on the same curve as the Radio module; the default is low (35) |
| Fullscreen | The picture covers the whole screen and no other card can be seen meanwhile |

Give the module **a screen position of its own**, for example bottom right. The picture is placed where the
card is when it starts and doesn't follow if other cards push the card around (a position other cards share
or a carousel won't work).

## Your own sources

Add a row under *Your Own Sources* (a name and an address), **save**, then pick the name in *Channel* and
save again. The name is the heading above the picture. The address plays as it is: the module looks nothing up.

Two rows with the same name: the first one plays. If you rename or remove the source that is chosen, choose
again in *Channel*; until you do, nothing plays and the card says no channel is chosen.

Examples to try (use your own address; a stream is yours to watch only if its publisher allows it):

- `http://192.168.1.20:8080/live/index.m3u8`: a NAS or media server on your home network (MirrorDash 0.18).
- `http://192.168.1.30/stream.mp4`: a camera or an encoder you run yourself (MirrorDash 0.18).
- A stream you publish yourself on the internet, `https://example.com/live/index.m3u8` (replace it with yours).

What plays: HLS (`.m3u8`) or a plain MP4 file, with H.264 video and AAC audio. From an HLS list the player picks
a version of at most 720p. Other formats show the failed line. **You are responsible for having the right to
watch a source you add**; the module only plays the address you give it. An address can hold a password or a
token: the module never writes it to the log, but it is saved in the mirror's settings like the rest.

## When it plays

The module has no switch: **it plays whenever the module is enabled and its card is shown**. The module's
schedule and Privacy Mode hide the card, and the video stops with it; it starts again when the card is back.
The screen's night rest stops it too.

- While it starts, the rectangle says *Startar SVT1* (or your source's name) (the mirror's language) until the first picture.
- When SVT or the stream fails, the card is one line, *SVT1 går inte att spela just nu*, and the module
  tries again after 5, 10, 30 and 60 seconds, then every minute.
- While a video plays the other cards update once a minute (the Clock shows no seconds): every redraw costs
  the Raspberry Pi a video frame.

## Not yet

Start and stop from Home Assistant, a button or a GPIO pin; more ready-made channels (DW and others need an OS update first); programme
names; subtitles; pausing the Radio module while the TV plays.

## Develop

```bash
uv run pytest                    # the module's tests (no network: SVT's answer is saved in tests/svt1.json)
uvx mirrordash-sdk validate .    # checks the module before you push it
```

Try it on a local mirror: see the [MirrorDash SDK](https://github.com/Menturan/mirrordash-sdk#quick-start).
The video itself needs a Raspberry Pi with the mirror's OS image (a decoder, labwc and GStreamer): on another
computer the card shows its failure line.
