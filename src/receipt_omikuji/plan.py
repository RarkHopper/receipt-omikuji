from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Literal, TypedDict, Unpack

Layout = Literal["horizontal", "vertical"]
PrintKind = Literal[
    "opening",
    "lead",
    "prefix",
    "dodge",
    "reannounce",
    "final_announce",
    "final_prefix",
    "result",
]
Decoration = Literal["header", "footer", "feed"]
ROW_DOTS = 32


@dataclass(frozen=True)
class DrawResult:
    pass


@dataclass(frozen=True)
class FixedResult:
    value: str


type ResultSelection = DrawResult | FixedResult


@dataclass(frozen=True)
class Setting:
    seed: str
    rounds: int
    max_wait_ms: int
    max_lines: int
    columns: int
    expose_lines: int
    result: ResultSelection
    layout: Layout
    width_dots: int
    character_ms: int
    tail_feed_lines: int

    def __post_init__(self) -> None:
        if self.layout not in ("horizontal", "vertical"):
            raise ValueError("layoutはhorizontalかverticalを指定してください")
        limits = {
            "rounds": (0, 10),
            "max_wait_ms": (0, 60000),
            "max_lines": (1, 4800 if self.layout == "vertical" else 120),
            "columns": (16, 80),
            "expose_lines": (0, 8),
            "width_dots": (192, 832),
            "character_ms": (0, 1000),
            "tail_feed_lines": (0, 32),
        }
        for name, (lower, upper) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name}は{lower}〜{upper}の整数を指定してください")
        if not isinstance(self.seed, str) or len(self.seed.encode("utf-8")) > 256:
            raise ValueError("seedはUTF-8で256bytes以内にしてください")
        if self.width_dots % 8 or (self.layout == "vertical" and self.expose_lines):
            raise ValueError("印字幅は8の倍数、縦書きのexpose-linesは0にしてください")


class SettingOption(TypedDict, total=False):
    seed: str
    rounds: int
    max_wait_ms: int
    max_lines: int
    columns: int
    expose_lines: int
    result: str
    layout: Layout
    width_dots: int
    character_ms: int
    tail_feed_lines: int


def make_setting(**option: Unpack[SettingOption]) -> Setting:
    layout = option.get("layout", "horizontal")
    vertical = layout == "vertical"
    selection: ResultSelection = (
        FixedResult(option["result"]) if "result" in option else DrawResult()
    )
    return Setting(
        seed=option.get("seed", "42"),
        rounds=option.get("rounds", 4),
        max_wait_ms=option.get("max_wait_ms", 20000),
        max_lines=option.get("max_lines", 2400 if vertical else 80),
        columns=option.get("columns", 32),
        expose_lines=option.get("expose_lines", 0),
        result=selection,
        layout=layout,
        width_dots=option.get("width_dots", 384),
        character_ms=option.get("character_ms", 80 if vertical else 0),
        tail_feed_lines=option.get("tail_feed_lines", 4 if vertical else 0),
    )


class PrintEvent(ABC):
    node: str
    kind: PrintKind
    lines: int

    @property
    @abstractmethod
    def text(self) -> str: ...

    @property
    @abstractmethod
    def blank_lines(self) -> int: ...

    @abstractmethod
    def to_dict(self) -> dict[str, object]:
        return {
            "type": "print",
            "node": self.node,
            "kind": self.kind,
            "text": self.text,
            "lines": self.lines,
            "blank_lines": self.blank_lines,
        }


@dataclass(frozen=True)
class TextEvent(PrintEvent):
    node: str
    kind: PrintKind
    content: str
    lines: int
    empty_lines: int

    @property
    def text(self) -> str:
        return self.content

    @property
    def blank_lines(self) -> int:
        return self.empty_lines

    def to_dict(self) -> dict[str, object]:
        return super().to_dict()


@dataclass(frozen=True)
class GlyphEvent(PrintEvent):
    node: str
    kind: PrintKind
    character: str
    lines: int

    def __post_init__(self) -> None:
        if len(self.character) != 1:
            raise ValueError("文字の印字には一文字を指定してください")

    @property
    def text(self) -> str:
        return self.character

    @property
    def blank_lines(self) -> int:
        return 0

    def to_dict(self) -> dict[str, object]:
        return {**super().to_dict(), "glyph": True}


@dataclass(frozen=True)
class DecorationEvent(PrintEvent):
    node: str
    kind: PrintKind
    decoration: Decoration
    lines: int

    @property
    def text(self) -> str:
        return ""

    @property
    def blank_lines(self) -> int:
        return 0

    def to_dict(self) -> dict[str, object]:
        return {**super().to_dict(), "decoration": self.decoration}


@dataclass(frozen=True)
class WaitEvent:
    node: str
    ms: int
    checkpoint: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "wait",
            "node": self.node,
            "ms": self.ms,
            "checkpoint": self.checkpoint,
        }


type Event = PrintEvent | WaitEvent


@dataclass(frozen=True)
class Plan:
    setting: Setting
    result: str
    events: tuple[Event, ...]
    lexicon_version: int
    path: tuple[str, ...]
    dodge_ids: tuple[str, ...]

    @property
    def paper_lines(self) -> int:
        return sum(
            event.lines for event in self.events if isinstance(event, PrintEvent)
        )

    @property
    def wait_ms(self) -> int:
        return sum(event.ms for event in self.events if isinstance(event, WaitEvent))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": 1,
            "lexicon_version": self.lexicon_version,
            "settings": {
                **asdict(self.setting),
                "result": self.setting.result.value
                if isinstance(self.setting.result, FixedResult)
                else None,
            },
            "result": self.result,
            "events": [event.to_dict() for event in self.events],
            "path": self.path,
            "dodge_ids": self.dodge_ids,
            "rounds": len(self.dodge_ids),
            "paper_lines": self.paper_lines,
            "paper_dots": self.paper_lines * ROW_DOTS,
            "wait_ms": self.wait_ms,
        }
