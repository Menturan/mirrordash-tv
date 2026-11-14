"""The owner's own sources (the "Your Own Sources" setting) and how a saved choice finds its stream."""
import re

OWN = "own:"  # an own source's id is this and its name: ready-made channel ids never start with it
MAX_NAME = 40  # the setting's maxLength
ADDRESS = re.compile(r"https?://\S+")  # the setting's pattern


def own_sources(rows) -> dict[str, str]:
    """name -> address of the usable rows. Saved data is checked again here: the form's rules stop a bad row,
    but a hand-edited config or an old save mustn't crash the card. A row with no name, a name over
    MAX_NAME, no http(s) address or a name an earlier row has is skipped (the first of two equal names plays)."""
    sources = {}
    for row in rows if isinstance(rows, list) else []:
        name, address = (row.get("name"), row.get("url")) if isinstance(row, dict) else (None, None)
        if not (isinstance(name, str) and isinstance(address, str)):
            continue
        name, address = name.strip(), address.strip()
        if name and len(name) <= MAX_NAME and ADDRESS.fullmatch(address):
            sources.setdefault(name, address)
    return sources


def chosen_id(choice) -> str:
    """The id in the saved Channel setting ({"id", "name"}, or {} for none), "" when there is none."""
    return choice.get("id", "") if isinstance(choice, dict) and isinstance(choice.get("id"), str) else ""
