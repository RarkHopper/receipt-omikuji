import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import cast


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("語彙にはオブジェクトが必要です")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("語彙には制御文字を含まない文字列が必要です")
    value.encode("utf-8")
    if "【確定】" in value:
        raise ValueError("語彙に確定欄は含められません")
    return value


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("語彙には空でない文字列の配列が必要です")
    return tuple(_text(item) for item in value)


@dataclass(frozen=True)
class Dodge:
    id: str
    prefix: str
    continuation: str
    join: str


@dataclass(frozen=True)
class Lexicon:
    version: int
    opening: tuple[str, ...]
    reannounce: tuple[str, ...]
    final_announce: tuple[str, ...]
    group: dict[str, tuple[str, ...]]
    dodge: tuple[Dodge, ...]
    result: tuple[str, ...]
    dots: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> "Lexicon":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def load_builtin(cls) -> "Lexicon":
        source = files("receipt_omikuji").joinpath("data/語彙.json")
        return cls.from_dict(json.loads(source.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, value: object) -> "Lexicon":
        data = _mapping(value)
        version = data.get("version")
        if type(version) is not int:
            raise ValueError("語彙のversionは整数にしてください")
        group = {
            prefix: _texts(_mapping(entry).get("lead"))
            for prefix, entry in _mapping(data.get("group")).items()
        }
        raw_dodge = data.get("dodge")
        if not isinstance(raw_dodge, list) or not raw_dodge:
            raise ValueError("肩透かしを登録してください")
        dodges: list[Dodge] = []
        ids: set[str] = set()
        for raw in raw_dodge:
            entry = _mapping(raw)
            dodge = Dodge(
                *(
                    _text(entry.get(key))
                    for key in ("id", "prefix", "continuation", "join")
                )
            )
            if (
                dodge.id in ids
                or dodge.prefix not in group
                or not (dodge.prefix + dodge.continuation).startswith(dodge.join)
                or not dodge.join.startswith(dodge.prefix)
                or dodge.join == dodge.prefix
            ):
                raise ValueError("肩透かしの接続が不正です: " + dodge.id)
            ids.add(dodge.id)
            dodges.append(dodge)
        lexicon = cls(
            version,
            _texts(data.get("opening")),
            _texts(data.get("reannounce")),
            _texts(data.get("final_announce")),
            group,
            tuple(dodges),
            _texts(data.get("result")),
            _texts(data.get("dots")),
        )
        speeches = list(lexicon.opening + lexicon.reannounce + lexicon.final_announce)
        speeches.extend(text for values in group.values() for text in values)
        speeches.extend(dodge.continuation for dodge in dodges)
        for prefix in group:
            _text(prefix)
        for speech in speeches:
            if any(result in speech for result in lexicon.result):
                raise ValueError("確定前の文に運勢を含められません")
        if any(set(dots) - {".", "…"} for dots in lexicon.dots):
            raise ValueError("待ちの点には.か…を使ってください")
        return lexicon


class SpeechGraph:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon
        self.edges: dict[str, tuple[str, ...]] = {
            "start": ("gate",),
            "gate": ("final_announce",)
            + tuple("lead:" + prefix for prefix in lexicon.group),
            "reannounce": ("gate",),
            "final_announce": ("final_prefix",),
            "final_prefix": ("result",),
            "result": (),
        }
        for prefix in lexicon.group:
            self.edges["lead:" + prefix] = ("prefix:" + prefix,)
            self.edges["prefix:" + prefix] = tuple(
                "dodge:" + d.id for d in lexicon.dodge if d.prefix == prefix
            )
        for dodge in lexicon.dodge:
            self.edges["dodge:" + dodge.id] = ("reannounce",)

    def dot(self) -> str:
        output = ["digraph omikuji {", "  rankdir=LR;"]
        continuations = {"dodge:" + d.id: d.continuation for d in self.lexicon.dodge}
        for node in self.edges:
            label = node + ("\n" + continuations[node] if node in continuations else "")
            output.append(
                f"  {json.dumps(node)} [label={json.dumps(label, ensure_ascii=False)}];"
            )
        for source, targets in self.edges.items():
            output.extend(
                f"  {json.dumps(source)} -> {json.dumps(target)};" for target in targets
            )
        return "\n".join(output + ["}", ""])
