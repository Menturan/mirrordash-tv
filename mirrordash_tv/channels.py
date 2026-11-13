"""SVT's live channels and how to find the stream of one."""

# SVT's own API (no key, no account) answers https://api.svt.se/video/ch-<id> for each of them. SVT24 and
# Barnkanalen share one feed (SVT's address for both has /svtb/ in it): the API gives both the same stream.
CHANNELS = {"svt1": "SVT1", "svt2": "SVT2", "svt24": "SVT24", "kunskapskanalen": "Kunskapskanalen",
            "barnkanalen": "Barnkanalen"}
API = "https://api.svt.se/video/ch-{}"


def hls_url(answer) -> str | None:
    """The HLS master playlist's address in the API's answer, or None when it has none (a channel off the air,
    or an answer that isn't SVT's usual one)."""
    references = answer.get("videoReferences") if isinstance(answer, dict) else None
    for reference in references if isinstance(references, list) else []:
        if isinstance(reference, dict) and reference.get("format") == "hls" and isinstance(reference.get("url"), str):
            return reference["url"]
    return None
