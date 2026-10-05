from receipt_omikuji.plan import (
    ROW_DOTS,
    Decoration,
    DecorationEvent,
    Event,
    GlyphEvent,
    PrintEvent,
    PrintKind,
    WaitEvent,
)

LINE_START_PROHIBITED = (
    "、。！？：；）」』】〕〉》ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー"
)


def character_width(char: str) -> int:
    return 1 if " " <= char <= "~" else 2


def wrap_lines(text: str, columns: int) -> tuple[str, ...]:
    lines: list[str] = []
    for line in text.split("\n"):
        current = ""
        width = 0
        for char in line:
            size = character_width(char)
            if width + size > columns:
                carry = ""
                if char in LINE_START_PROHIBITED:
                    while current:
                        carry = current[-1] + carry
                        current = current[:-1]
                        if carry[0] not in LINE_START_PROHIBITED:
                            break
                lines.append(current)
                current = carry
                width = sum(character_width(c) for c in carry)
            current += char
            width += size
        lines.append(current)
    return tuple(lines)


def vertical_cells(text: str, width_dots: int) -> tuple[tuple[str, int], ...]:
    ordinary = (width_dots // 2 + 16 + ROW_DOTS - 1) // ROW_DOTS
    return tuple(
        (char, 1 if char == "." else 2 if char == "…" else ordinary)
        for char in text.replace("\n", "")
    )


def vertical_events(
    events: tuple[Event, ...],
    width_dots: int,
    character_ms: int,
    tail_lines: int,
) -> tuple[Event, ...]:
    def decoration(name: Decoration, lines: int, kind: PrintKind) -> PrintEvent:
        return DecorationEvent("frame:" + name, kind, name, lines)

    output: list[Event] = [decoration("header", 3, "opening")]
    for event in events:
        if isinstance(event, WaitEvent):
            output.append(event)
            continue
        cells = vertical_cells(event.text, width_dots)
        for index, (char, lines) in enumerate(cells):
            output.append(GlyphEvent(event.node, event.kind, char, lines))
            if index < len(cells) - 1 and character_ms:
                output.append(WaitEvent(event.node, character_ms, checkpoint=False))
    output.append(decoration("footer", 2, "result"))
    if tail_lines:
        output.append(decoration("feed", tail_lines, "result"))
    return tuple(output)
