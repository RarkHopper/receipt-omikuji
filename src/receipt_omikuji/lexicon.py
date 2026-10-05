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


def _sentence(value: object) -> str:
    text = _text(value)
    if text[-1] not in "。！？!?":
        raise ValueError("発話は句点または感嘆符、疑問符で終えてください")
    return text


@dataclass(frozen=True)
class Dodge:
    id: str
    prefix: str
    continuation: str
    join: str


@dataclass(frozen=True)
class Lexicon:
    version: int
    announce: tuple[str, ...]
    dodge: tuple[Dodge, ...]
    result: tuple[str, ...]
    dots: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> "Lexicon":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def load_builtin(cls) -> "Lexicon":
        source = files("receipt_omikuji").joinpath("asset/語彙.json")
        return cls.from_dict(json.loads(source.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, value: object) -> "Lexicon":
        data = _mapping(value)
        version = data.get("version")
        if type(version) is not int:
            raise ValueError("語彙のversionは整数にしてください")
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
                or not (dodge.prefix + dodge.continuation).startswith(dodge.join)
                or not dodge.join.startswith(dodge.prefix)
                or dodge.join == dodge.prefix
            ):
                raise ValueError("肩透かしの接続が不正です: " + dodge.id)
            _sentence(dodge.prefix + dodge.continuation)
            ids.add(dodge.id)
            dodges.append(dodge)
        lexicon = cls(
            version,
            tuple(_sentence(text) for text in _texts(data.get("announce"))),
            tuple(dodges),
            _texts(data.get("result")),
            _texts(data.get("dots")),
        )
        speeches = list(lexicon.announce)
        speeches.extend(dodge.prefix + dodge.continuation for dodge in dodges)
        for speech in speeches:
            # 「大吉から大凶まで」は範囲の予告。
            # 運勢名の有無ではなく、冒頭で結果を言い切る文を拒否する。
            if any(
                re.match(re.escape(result) + r"(?:[！!。]|です|$)", speech)
                for result in lexicon.result
            ):
                raise ValueError("確定前の文で運勢を言い切っています")
        if any(set(dots) - {".", "…"} for dots in lexicon.dots):
            raise ValueError("待ちの点には.か…を使ってください")
        return lexicon


class SpeechGraph:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon
        self.texts: dict[str, str] = {}
        announcements = tuple(
            "announce:" + str(index) for index in range(len(lexicon.announce))
        )
        prefixes = tuple(
            dict.fromkeys(
                [dodge.prefix for dodge in lexicon.dodge]
                + [result[0] for result in lexicon.result]
            )
        )
        self.edges: dict[str, tuple[str, ...]] = {
            "start": ("announce",),
            "announce": announcements,
            "gate": tuple("prefix:" + prefix for prefix in prefixes),
            "result": (),
        }
        for node, text in zip(announcements, lexicon.announce):
            self.texts[node] = text
            self.edges[node] = ("gate",)
        for prefix in prefixes:
            node = "prefix:" + prefix
            self.texts[node] = prefix
            self.edges[node] = tuple(
                "dodge:" + dodge.id for dodge in lexicon.dodge if dodge.prefix == prefix
            ) + tuple(
                "confirmed:" + result
                for result in lexicon.result
                if result[0] == prefix
            )
        for result in lexicon.result:
            result_id = "confirmed:" + result
            self.texts[result_id] = result[1:] + "！\n【確定】今日の運勢：" + result
            self.edges[result_id] = ("result",)
        for dodge in lexicon.dodge:
            node = "dodge:" + dodge.id
            self.texts[node] = dodge.continuation
            self.edges[node] = ("announce",)

    def dot(self) -> str:
        output = ["digraph omikuji {", "  rankdir=LR;"]
        for node in self.edges:
            label = node + ("\n" + self.texts[node] if node in self.texts else "")
            output.append(
                f"  {json.dumps(node)} [label={json.dumps(label, ensure_ascii=False)}];"
            )
        for source, targets in self.edges.items():
            output.extend(
                f"  {json.dumps(source)} -> {json.dumps(target)};" for target in targets
            )
        return "\n".join(output + ["}", ""])
