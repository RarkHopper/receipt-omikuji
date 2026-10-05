import hashlib
from collections.abc import Sequence
from dataclasses import replace

from receipt_omikuji.lexicon import SpeechGraph
from receipt_omikuji.paper import (
    VERTICAL_FOOTER_LINES,
    VERTICAL_HEADER_LINES,
    vertical_cells,
    vertical_events,
    wrap_lines,
)
from receipt_omikuji.plan import (
    Event,
    FixedResult,
    Plan,
    PrintEvent,
    PrintKind,
    Setting,
    TextEvent,
    WaitEvent,
)


class SeedRandom:
    def __init__(self, seed: str) -> None:
        self.seed = seed
        self.counter = 0

    def choose[T](self, values: Sequence[T]) -> T:
        if not values:
            raise ValueError("抽選候補が空です")
        digest = hashlib.sha256(
            f"{self.seed}:{self.counter}".encode("utf-8")
        ).hexdigest()
        self.counter += 1
        return values[int(digest[:8], 16) % len(values)]


def generate(graph: SpeechGraph, setting: Setting) -> Plan:
    lexicon = graph.lexicon
    random = SeedRandom(setting.seed)
    result = (
        setting.result.value
        if isinstance(setting.result, FixedResult)
        else SeedRandom(setting.seed + ":fortune").choose(lexicon.result)
    )
    if result not in lexicon.result:
        raise ValueError("語彙に登録された運勢を指定してください")
    vertical = setting.layout == "vertical"

    def make(node: str, kind: PrintKind, text: str, gap: int = 0) -> PrintEvent:
        if vertical:
            frame_lines = (VERTICAL_HEADER_LINES if kind == "opening" else 0) + (
                VERTICAL_FOOTER_LINES + setting.tail_feed_lines
                if kind == "result"
                else 0
            )
            return TextEvent(
                node,
                kind,
                text.replace("\n", ""),
                sum(lines for _, lines in vertical_cells(text, setting.width_dots))
                + frame_lines,
                0,
            )
        if kind in ("prefix", "final_prefix", "result"):
            gap += setting.expose_lines
        if kind == "result":
            gap += setting.tail_feed_lines
        lines = wrap_lines(text, setting.columns)
        return TextEvent(node, kind, "\n".join(lines), len(lines) + gap, gap)

    final: list[Event] = [
        make("final_announce", "final_announce", random.choose(lexicon.final_announce)),
        make("final_prefix", "final_prefix", result[0] + random.choose(lexicon.dots)),
        WaitEvent(
            "final_prefix",
            min(setting.max_wait_ms, random.choose((1600, 2000, 2400))),
            True,
        ),
        make(
            "result",
            "result",
            (result[1:] + "！\n" if len(result) > 1 else "")
            + "【確定】今日の運勢："
            + result,
        ),
    ]
    events: list[Event] = [make("start", "opening", random.choose(lexicon.opening))]
    character_ms = 0 if setting.max_wait_ms == 0 else setting.character_ms

    def count_lines(items: Sequence[Event]) -> int:
        return sum(item.lines for item in items if isinstance(item, PrintEvent))

    def count_gaps(items: Sequence[Event]) -> int:
        return (
            sum(
                max(0, len(vertical_cells(item.text, setting.width_dots)) - 1)
                for item in items
                if isinstance(item, PrintEvent)
            )
            if vertical
            else 0
        )

    def count_wait(items: Sequence[Event]) -> int:
        return (
            sum(item.ms for item in items if isinstance(item, WaitEvent))
            + count_gaps(items) * character_ms
        )

    if vertical:
        remaining = setting.max_wait_ms - sum(
            item.ms for item in final if isinstance(item, WaitEvent)
        )
        character_ms = min(
            character_ms, remaining // max(1, count_gaps(events + final))
        )
        setting = replace(setting, character_ms=character_ms)
    reserved_lines = count_lines(final)
    reserved_wait = count_wait(final)
    if count_lines(events) + reserved_lines > setting.max_lines:
        raise ValueError("紙量予算に最終発表が収まりません")
    recent: list[str] = []
    dodge_by_id = {d.id: d for d in lexicon.dodge}
    recent_speech: list[str] = []
    chosen: list[str] = []
    path = ["start", "gate"]
    for _ in range(setting.rounds):
        eligible = [
            d
            for d in lexicon.dodge
            if d.id not in recent
            and (not chosen or d.prefix != dodge_by_id[chosen[-1]].prefix)
        ]
        while eligible:
            dodge = random.choose(eligible)
            eligible.remove(dodge)

            leads = [
                text
                for text in lexicon.group[dodge.prefix]
                if text not in recent_speech
            ]
            announcements = [
                text for text in lexicon.reannounce if text not in recent_speech
            ]
            if not leads or not announcements:
                continue
            lead = random.choose(leads)
            again = random.choose(announcements)
            lead_id, prefix_id, dodge_id = (
                "lead:" + dodge.prefix,
                "prefix:" + dodge.prefix,
                "dodge:" + dodge.id,
            )
            no_wait = setting.max_wait_ms == 0
            cycle: list[Event] = [
                make(lead_id, "lead", lead),
                WaitEvent(
                    lead_id, 0 if no_wait else random.choose((250, 400, 600)), True
                ),
                make(prefix_id, "prefix", dodge.prefix + random.choose(lexicon.dots)),
                WaitEvent(
                    prefix_id,
                    0 if no_wait else random.choose((1200, 1800, 2200, 2800)),
                    True,
                ),
                make(dodge_id, "dodge", dodge.continuation, random.choose((0, 0, 1))),
                WaitEvent(
                    dodge_id, 0 if no_wait else random.choose((350, 600, 900)), True
                ),
                make("reannounce", "reannounce", again),
            ]
            if (
                count_lines(events + cycle) + reserved_lines <= setting.max_lines
                and count_wait(events + cycle) + reserved_wait <= setting.max_wait_ms
            ):
                break
        else:
            break
        events.extend(cycle)
        path.extend(
            (
                "lead:" + dodge.prefix,
                "prefix:" + dodge.prefix,
                "dodge:" + dodge.id,
                "reannounce",
                "gate",
            )
        )
        chosen.append(dodge.id)
        recent = (recent + [dodge.id])[-3:]
        recent_speech = (recent_speech + [lead, again])[-6:]
    events.extend(final)
    path.extend(("final_announce", "final_prefix", "result"))
    if any(target not in graph.edges[source] for source, target in zip(path, path[1:])):
        raise RuntimeError("発話グラフにない接続です")
    output = (
        vertical_events(
            tuple(events), setting.width_dots, character_ms, setting.tail_feed_lines
        )
        if vertical
        else tuple(events)
    )
    return Plan(setting, result, output, lexicon.version, tuple(path), tuple(chosen))


def sample_plan(setting: Setting) -> Plan:
    if setting.layout != "vertical":
        raise ValueError("試し刷りは縦書きです")
    events = vertical_events(
        (TextEvent("sample", "result", "あいうえお", 1, 0),),
        setting.width_dots,
        setting.character_ms,
        setting.tail_feed_lines,
    )
    return Plan(setting, "あいうえお", events, 0, (), ())
