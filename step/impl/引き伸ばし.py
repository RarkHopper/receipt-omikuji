import json
import pathlib
import subprocess
import sys
from dataclasses import replace
from importlib.resources import files

from getgauge.python import step

from receipt_omikuji.fortune import generate
from receipt_omikuji.lexicon import Lexicon, SpeechGraph
from receipt_omikuji.paper import character_width
from receipt_omikuji.plan import Plan, PrintEvent, make_setting

ROOT = pathlib.Path(__file__).resolve().parents[2]
GRAPH = SpeechGraph(Lexicon.load_builtin())


def prints(plan: Plan) -> list[PrintEvent]:
    return [event for event in plan.events if isinstance(event, PrintEvent)]


def cli(
    arguments: list[str], input_text: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "receipt_omikuji"] + arguments,
        input=input_text,
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=30,
    )


@step("予告から接頭辞と肩透かしを経て再予告に戻り、最後は結果に到達する")
def speech_graph() -> None:
    for seed in range(12):
        plan = generate(GRAPH, make_setting(seed=str(seed)))
        assert plan.path[0] == "start" and plan.path[-1] == "result"
        assert len(plan.dodge_ids) == 4
        for source, target in zip(plan.path, plan.path[1:]):
            assert target in GRAPH.edges[source], (source, target)
        for pos, node in enumerate(plan.path):
            if node.startswith("prefix:"):
                assert plan.path[pos - 1].startswith("lead:")
                assert plan.path[pos + 1].startswith("dodge:")
                assert plan.path[pos + 2].startswith("reannounce:")


@step("導入、接頭辞、肩透かし、再予告は組ごとに接続し、全候補をグラフから辿れる")
def registered_pairs() -> None:
    reached = set()
    pending = ["start"]
    while pending:
        node = pending.pop()
        if node not in reached:
            reached.add(node)
            pending.extend(GRAPH.edges[node])
    assert reached == set(GRAPH.edges)
    assert {GRAPH.texts[node] for node in GRAPH.edges["start"]} == set(
        GRAPH.lexicon.opening
    )
    for entry in GRAPH.lexicon.dodge:
        lead, prefix, dodge, again = (
            kind + ":" + entry.id for kind in ("lead", "prefix", "dodge", "reannounce")
        )
        assert lead in GRAPH.edges["gate"]
        assert GRAPH.edges[lead] == (prefix,)
        assert GRAPH.edges[prefix] == (dodge,)
        assert GRAPH.edges[dodge] == (again,)
        assert GRAPH.edges[again] == ("gate",)
        assert (
            GRAPH.texts[lead],
            GRAPH.texts[prefix] + GRAPH.texts[dodge],
            GRAPH.texts[again],
        ) == (entry.lead, entry.prefix + entry.continuation, entry.reannounce)
    for seed in range(24):
        plan = generate(GRAPH, make_setting(seed=str(seed)))
        for event in prints(plan):
            text = event.text.replace("\n", "")
            registered = GRAPH.texts[event.node].replace("\n", "")
            if event.kind in ("prefix", "final_prefix"):
                assert text.startswith(registered)
                assert text[len(registered) :] in GRAPH.lexicon.dots
            else:
                assert text == registered


@step(
    "「大」と「丈夫！」や「運」と「動。まず背伸びをしよう。」は、印字した語の続きとしてつながる"
)
def shared_prefix() -> None:
    entries = {"dodge:" + entry.id: entry for entry in GRAPH.lexicon.dodge}
    assert {entry.join for entry in entries.values()} >= {
        "大丈夫",
        "大切",
        "運動",
        "あたる",
        "あたり",
        "大吉から大凶まで",
    }
    for entry in entries.values():
        assert (entry.prefix + entry.continuation).startswith(entry.join)
        assert entry.join.startswith(entry.prefix) and entry.join != entry.prefix
        assert "dodge:" + entry.id in GRAPH.edges["prefix:" + entry.id]
    observed = set()
    for seed in range(20):
        plan = generate(
            GRAPH,
            make_setting(seed=str(seed), rounds=10, max_wait_ms=60000, max_lines=120),
        )
        for previous, event in zip(prints(plan), prints(plan)[1:]):
            if event.kind == "dodge":
                entry = entries[event.node]
                assert previous.kind == "prefix" and previous.text.startswith(
                    entry.prefix
                )
                assert event.text.replace("\n", "") == entry.continuation
                observed.add(entry.id)
    assert len(observed) >= 24
    assert {entries["dodge:" + key].prefix for key in observed} == {
        entry.prefix for entry in GRAPH.lexicon.dodge
    }


@step("直近三回の肩透かしを避け、同じ接頭辞を続けて選ばない")
def recent_speech() -> None:
    entries = {entry.id: entry for entry in GRAPH.lexicon.dodge}
    for seed in range(24):
        plan = generate(
            GRAPH,
            make_setting(seed=str(seed), rounds=10, max_wait_ms=60000, max_lines=120),
        )
        prefixes = [entries[key].prefix for key in plan.dodge_ids]
        for index, key in enumerate(plan.dodge_ids):
            assert key not in plan.dodge_ids[max(0, index - 3) : index]
        assert all(first != second for first, second in zip(prefixes, prefixes[1:]))


@step("複数のseedで、接頭辞、肩透かし、再予告、点と空行に異なる表現が現れる")
def variation() -> None:
    kinds = {kind: set() for kind in ("prefix", "dodge", "reannounce")}
    blanks = set()
    for seed in range(20):
        for event in prints(generate(GRAPH, make_setting(seed=str(seed)))):
            if event.kind in kinds:
                kinds[event.kind].add(event.text)
            blanks.add(event.blank_lines)
    assert all(len(values) >= 5 for values in kinds.values())
    assert blanks >= {0, 1}


@step("同じseedと設定と語彙では、文面、運勢、印字と待ちの順序を再現できる")
def reproducibility() -> None:
    first = generate(GRAPH, make_setting(seed="朝の紙"))
    again = generate(GRAPH, make_setting(seed="朝の紙"))
    different = generate(GRAPH, make_setting(seed="別の朝"))
    shorter = generate(GRAPH, make_setting(seed="朝の紙", rounds=0))
    assert first == again and first.events != different.events
    assert first.result == shorter.result


@step("回数、待ち時間、紙量の予算内で循環し、最後の確定に必要な分を確保する")
def bounded_budget() -> None:
    configs = [
        make_setting(
            seed=str(seed),
            rounds=rounds,
            max_wait_ms=wait,
            max_lines=lines,
            columns=columns,
        )
        for seed in range(5)
        for rounds, wait, lines, columns in (
            (0, 0, 20, 16),
            (4, 20000, 80, 32),
            (10, 60000, 120, 48),
            (10, 3000, 20, 32),
        )
    ]
    configs.append(make_setting(rounds=10, max_lines=60, expose_lines=4))
    for config in configs:
        plan = generate(GRAPH, config)
        assert (
            len(plan.dodge_ids) <= config.rounds and plan.wait_ms <= config.max_wait_ms
        )
        assert (
            plan.paper_lines <= config.max_lines and prints(plan)[-1].kind == "result"
        )
        actual_lines = 0
        for event in prints(plan):
            actual_lines += len(event.text.split("\n")) + event.blank_lines
            assert all(
                sum(character_width(c) for c in line) <= config.columns
                for line in event.text.split("\n")
            )
        assert actual_lines == plan.paper_lines


@step("紙量や時間が少ないときは肩透かしを減らし、待ちがゼロでも結果を出せる")
def reserved_result() -> None:
    config = make_setting(seed="budget", rounds=10, max_wait_ms=60000, max_lines=120)
    regular = generate(GRAPH, config)
    for limited in (
        replace(config, max_wait_ms=0),
        replace(config, max_wait_ms=1000),
        replace(config, max_lines=10),
    ):
        plan = generate(GRAPH, limited)
        assert prints(plan)[-1].kind == "result"
        if limited.max_wait_ms == 0:
            assert plan.wait_ms == 0
        else:
            assert len(plan.dodge_ids) < len(regular.dodge_ids)


@step("最終発表すら収まらない紙量や、許容範囲外の設定は印字前に拒否する")
def invalid_settings() -> None:
    for option, value in (
        ("max-lines", "1"),
        ("rounds", "11"),
        ("max-wait-ms", "60001"),
        ("max-lines", "121"),
        ("columns", "15"),
        ("result", "超大吉"),
    ):
        result = cli(["generate", "--" + option, value])
        assert result.returncode == 2 and not result.stdout


@step(
    "大吉、吉、中吉、小吉、末吉、凶、大凶のいずれも、確定欄に一度だけ現れ、結果の後に印字を続けない"
)
def unique_result() -> None:
    for name in ("大吉", "吉", "中吉", "小吉", "末吉", "凶", "大凶"):
        plan = generate(GRAPH, make_setting(seed="result", result=name))
        events = prints(plan)
        result_events = [event for event in events if event.kind == "result"]
        assert result_events == [events[-1]]
        text = "".join(event.text.replace("\n", "") for event in events)
        assert text.count("【確定】") == 1 and text.endswith(
            "【確定】今日の運勢：" + name
        )
        assert plan.result == name and events[-2].text.startswith(name[0])
        if len(name) > 1:
            assert events[-1].text.startswith(name[1:])


@step("肩透かしは運勢名を含んでも、結果を言い切る書き出しや確定欄を含まない")
def dodge_semantics() -> None:
    for entry in GRAPH.lexicon.dodge:
        speech = entry.prefix + entry.continuation
        assert "【確定】" not in speech and speech not in GRAPH.lexicon.result
        assert not any(
            speech.startswith(name + punctuation)
            for name in GRAPH.lexicon.result
            for punctuation in ("！", "!", "。", "です")
        )
    source = json.loads(
        files("receipt_omikuji").joinpath("data/語彙.json").read_text(encoding="utf-8")
    )
    for premature in ("吉です。", "吉！", "吉", "【確定】吉"):
        invalid = dict(source, opening=[premature])
        try:
            Lexicon.from_dict(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("確定前の運勢が受理されました")
