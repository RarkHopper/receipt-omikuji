import json
import os
import pathlib
import subprocess
from getgauge.python import step

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONT = os.environ.get("OMIKUJI_FONT", "/Library/Fonts/Arial Unicode.ttf")


def invoke(action, **values):
    process = subprocess.run(
        ["php", str(ROOT / "test/driver.php")],
        input=json.dumps({"action": action, **values}, ensure_ascii=False),
        text=True, capture_output=True, cwd=ROOT, timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads(process.stdout)


def plan_for(**settings):
    return invoke("plans", settings=[settings])[0]


@step("予告から接頭辞と肩透かしを経て再予告に戻り、最後は結果に到達する")
def speech_graph():
    graph = invoke("graph")
    plans = invoke("plans", settings=[{"seed": str(seed)} for seed in range(12)])
    for plan in plans:
        path = plan["path"]
        assert path[0] == "start" and path[-1] == "result"
        for source, target in zip(path, path[1:]):
            assert target in graph["edges"][source], (source, target)
        assert plan["rounds"] == 4
        for pos, node in enumerate(path):
            if node.startswith("prefix:"):
                assert path[pos - 1].startswith("lead:")
                assert path[pos + 1].startswith("dodge:")
                assert path[pos + 2] == "reannounce"


@step("「大」と「丈夫！」や「運」と「動も忘れずに。」は、印字した語の続きとしてつながる")
def shared_prefix():
    graph = invoke("graph")
    entries = graph["lexicon"]["dodge"]
    assert {entry["join"] for entry in entries} >= {"大丈夫", "大切", "運動", "あたる"}
    for entry in entries:
        assert (entry["prefix"] + entry["continuation"]).startswith(entry["join"])
        assert entry["join"].startswith(entry["prefix"])
        assert len(entry["join"]) > len(entry["prefix"])
        assert "dodge:" + entry["id"] in graph["edges"]["prefix:" + entry["prefix"]]
    plans = invoke("plans", settings=[{"seed": str(seed), "rounds": 10, "max_wait_ms": 60000, "max_lines": 120} for seed in range(20)])
    observed = set()
    for plan in plans:
        prints = [event for event in plan["events"] if event["type"] == "print"]
        for previous, event in zip(prints, prints[1:]):
            if event["kind"] == "dodge":
                entry = graph["nodes"][event["node"]]
                assert previous["kind"] == "prefix"
                assert previous["text"].startswith(entry["prefix"])
                assert event["text"].replace("\n", "") == entry["continuation"]
                observed.add(entry["id"])
    assert len(observed) >= 24


@step("直近三回の肩透かしを避け、同じ接頭辞を続けて選ばない")
def recent_speech():
    graph = invoke("graph")
    plans = invoke("plans", settings=[{"seed": str(seed), "rounds": 10, "max_wait_ms": 60000, "max_lines": 120} for seed in range(24)])
    for plan in plans:
        ids = plan["dodge_ids"]
        prefixes = [graph["nodes"]["dodge:" + key]["prefix"] for key in ids]
        for index, key in enumerate(ids):
            assert key not in ids[max(0, index - 3):index]
        assert all(a != b for a, b in zip(prefixes, prefixes[1:]))


@step("複数のseedで、接頭辞、肩透かし、再予告、点と空行に異なる表現が現れる")
def variation():
    plans = invoke("plans", settings=[{"seed": str(seed)} for seed in range(20)])
    kinds = {kind: set() for kind in ["prefix", "dodge", "reannounce"]}
    blanks = set()
    for plan in plans:
        for event in plan["events"]:
            if event["type"] == "print":
                if event["kind"] in kinds:
                    kinds[event["kind"]].add(event["text"])
                blanks.add(event["blank_lines"])
    assert all(len(values) >= 5 for values in kinds.values())
    assert blanks >= {0, 1}


@step("同じseedと設定と語彙では、文面、運勢、印字と待ちの順序を再現できる")
def reproducibility():
    values = [{"seed": "朝の紙"}, {"seed": "朝の紙"}, {"seed": "別の朝"}]
    first, again, different = invoke("plans", settings=values)
    assert first == again
    assert first["events"] != different["events"]
    shorter = plan_for(seed="朝の紙", rounds=0)
    assert first["result"] == shorter["result"]


@step("回数、待ち時間、紙量の予算内で循環し、最後の確定に必要な分を確保する")
def bounded_budget():
    settings = [
        {"seed": str(seed), "rounds": rounds, "max_wait_ms": wait, "max_lines": lines, "columns": columns}
        for seed in range(5)
        for rounds, wait, lines, columns in [(0, 0, 20, 16), (4, 20000, 80, 32), (10, 60000, 120, 48), (10, 3000, 20, 32)]
    ]
    settings.append({"rounds": 10, "max_lines": 60, "expose_lines": 4})
    for plan in invoke("plans", settings=settings):
        config = plan["settings"]
        assert plan["rounds"] <= config["rounds"]
        assert plan["wait_ms"] <= config["max_wait_ms"]
        assert plan["paper_lines"] <= config["max_lines"]
        assert plan["events"][-1]["kind"] == "result"
        actual_lines = 0
        for event in plan["events"]:
            if event["type"] == "print":
                actual_lines += len(event["text"].split("\n")) + event["blank_lines"]
                assert all(sum(1 if 32 <= ord(c) <= 126 else 2 for c in line) <= config["columns"] for line in event["text"].split("\n"))
        assert actual_lines == plan["paper_lines"]


@step("紙量や時間が少ないときは肩透かしを減らし、待ちがゼロでも結果を出せる")
def reserved_result():
    regular = plan_for(seed="budget", rounds=10, max_wait_ms=60000, max_lines=120)
    for values in [{"max_wait_ms": 0}, {"max_wait_ms": 1000}, {"max_lines": 10}]:
        limited = plan_for(seed="budget", rounds=10, **values)
        assert limited["events"][-1]["kind"] == "result"
        if values.get("max_wait_ms") != 0:
            assert limited["rounds"] < regular["rounds"]
        else:
            assert limited["wait_ms"] == 0


@step("最終発表すら収まらない紙量や、許容範囲外の設定は印字前に拒否する")
def invalid_settings():
    for values in [{"max_lines": 1}, {"rounds": 11}, {"max_wait_ms": 60001}, {"max_lines": 121}, {"columns": 15}, {"result": "超大吉"}]:
        process = subprocess.run(["php", str(ROOT / "test/driver.php")], input=json.dumps({"action": "plans", "settings": [values]}), text=True, capture_output=True)
        assert process.returncode == 2
        assert json.loads(process.stdout)["error"] == "InvalidArgumentException"


@step("大吉、吉、中吉、小吉、末吉、凶、大凶のいずれも、確定欄に一度だけ現れ、結果の後に印字を続けない")
def unique_result():
    names = ["大吉", "吉", "中吉", "小吉", "末吉", "凶", "大凶"]
    plans = invoke("plans", settings=[{"seed": "result", "result": name} for name in names])
    for name, plan in zip(names, plans):
        events = [event for event in plan["events"] if event["type"] == "print"]
        result_events = [event for event in events if event["kind"] == "result"]
        assert len(result_events) == 1 and result_events[0] == events[-1]
        text = "".join(event["text"].replace("\n", "") for event in events)
        assert text.count("【確定】") == 1 and text.endswith("【確定】今日の運勢：" + name)
        assert plan["result"] == name
        assert events[-2]["text"].startswith(name[0])
        if len(name) > 1:
            assert events[-1]["text"].startswith(name[1:])


@step("肩透かしには運勢の確定欄も完成した運勢名も含まれない")
def dodge_semantics():
    graph = invoke("graph")
    for entry in graph["lexicon"]["dodge"]:
        speech = entry["prefix"] + entry["continuation"]
        assert "【確定】" not in speech
        assert not any(speech.startswith(name + punctuation) for name in graph["lexicon"]["result"] for punctuation in ["！", "!", "。", "です"])


@step("待ちの前に接頭辞が印字済みになり、待ちによって紙量が増えない")
def paper_and_time():
    plan = plan_for()
    for previous, event in zip(plan["events"], plan["events"][1:]):
        if event["type"] == "wait":
            assert previous["type"] == "print"
            assert "lines" not in event and "text" not in event
    wire = invoke("wire")
    buffered = ""
    printed = []
    for chunk in wire["writes"]:
        buffered += chunk
        while "\n" in buffered:
            line, buffered = buffered.split("\n", 1)
            printed.append(line)
    assert wire["writes"][0].endswith("\n")
    assert printed[0] == "DAI....." and buffered == ""


@step("即時と実時間のドライランで同じ文面を同じ順序に出す")
def playback_modes():
    settings = {"rounds": 0, "max_wait_ms": 150}
    immediate = invoke("play", settings=settings)
    realtime = invoke("play", settings=settings, realtime=True)
    assert immediate["observed"] == realtime["observed"]
    assert immediate["run"]["status"] == realtime["run"]["status"] == "completed"
    assert realtime["run"]["elapsed_ms"] >= 140
    assert immediate["run"]["waited_ms"] == 150


@step("ESC/POSの文字出力は改行で確定し、日本語はラスタ出力で行幅と紙量を保つ")
def escpos_frames():
    wire = invoke("wire")
    assert all(chunk.endswith("\n") for chunk in wire["writes"])
    raster = invoke("raster", font=FONT, settings={"rounds": 1})
    for frame in raster["frames"]:
        assert frame["command"] == "1d763000"
        assert frame["width_bytes"] == 48
        assert frame["height_dots"] == frame["paper_lines"] * 32
        assert frame["payload_bytes"] == frame["width_bytes"] * frame["height_dots"]
        assert frame["nonwhite"]
        assert all(1 <= height <= 24 for height in frame["strip_heights"])
    bitmap = invoke("bitmap")
    assert bitmap["width_bytes"] == 2 and bitmap["height_dots"] == 49
    assert bitmap["strip_heights"] == [24, 24, 1]
    rows = [b"\x00\x00"] * 49
    for row, data in {0: b"\x80\x00", 23: b"\x01\x00", 24: b"\x00\x80", 47: b"\x00\x01", 48: b"\x10\x00"}.items():
        rows[row] = data
    assert bytes.fromhex(bitmap["payload_hex"]) == b"".join(rows)


@step("開始前、接頭辞の後、待ちの途中、結果の直前の取消では、その後の印字を行わない")
def cancellation_boundaries():
    for values, last_kind in [({"cancel_pre": True}, None), ({"cancel_after": "prefix"}, "prefix"), ({"cancel_before": "result"}, "final_prefix")]:
        outcome = invoke("play", **values)
        assert outcome["run"]["status"] == "cancelled"
        assert outcome["run"]["result"] is None
        assert (outcome["observed"][-1]["kind"] if outcome["observed"] else None) == last_kind
    plan = plan_for()
    elapsed = 0
    for event in plan["events"]:
        if event["type"] == "wait":
            if event["node"].startswith("prefix:"):
                deadline = elapsed + event["ms"] // 2
                break
            elapsed += event["ms"]
    mid_wait = invoke("play", cancel_ms=deadline)
    assert mid_wait["observed"][-1]["kind"] == "prefix"
    assert mid_wait["run"]["waited_ms"] < plan["wait_ms"]
    realtime = invoke("play", settings={"rounds": 0}, realtime=True, cancel_ms=80)
    assert realtime["run"]["status"] == "cancelled"
    assert realtime["observed"][-1]["kind"] == "final_prefix"
    assert realtime["run"]["elapsed_ms"] < 1000
    wire = invoke("wire", cancel_after_writes=1)
    assert len(wire["writes"]) == 1 and wire["run"]["status"] == "cancelled"


@step("取消済みの実行を再開しても追加印字を行わない")
def persistent_cancellation():
    outcome = invoke("play", cancel_after="prefix")
    assert outcome["resume_delta"] == 0


@step("プリンター未接続でもデモ、予定のJSON、HTMLプレビュー、ESC/POSファイルを生成できる")
def offline_cli():
    destination = ROOT / "build" / "acceptance"
    destination.mkdir(parents=True, exist_ok=True)
    for filename, arguments in [
        ("demo.json", ["demo", "--format", "json"]),
        ("plan.json", ["generate", "--format", "json"]),
        ("preview.html", ["generate", "--format", "html"]),
        ("receipt.bin", ["export", "--font", FONT]),
    ]:
        target = destination / filename
        if target.exists():
            target.unlink()
        result = subprocess.run(["php", str(ROOT / "bin/omikuji.php")] + arguments + ["--out", str(target)], text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert target.stat().st_size > 0
    assert json.loads((destination / "demo.json").read_text())["status"] == "completed"
    assert json.loads((destination / "plan.json").read_text())["schema"] == 1
    assert b"\x1d\x76\x30\x00" == (destination / "receipt.bin").read_bytes()[:4]
    preserved = (destination / "plan.json").read_bytes()
    duplicate = subprocess.run(["php", str(ROOT / "bin/omikuji.php"), "generate", "--out", str(destination / "plan.json")], capture_output=True)
    assert duplicate.returncode == 2
    assert (destination / "plan.json").read_bytes() == preserved


@step("USB識別や印刷の明示が足りない場合は、デバイスを開く前に拒否する")
def explicit_print_config():
    for sample in [[], ["--sample"]]:
        for arguments in [[], ["--allow-print"], ["--allow-print", "--vid", "0x1234", "--pid", "0x5678", "--endpoint", "0x81", "--interface", "0"]]:
            result = subprocess.run(["php", "-d", "ffi.enable=false", str(ROOT / "bin/omikuji.php"), "print"] + sample + arguments, text=True, capture_output=True)
            assert result.returncode == 2
            assert any(line.startswith("InvalidArgumentException:") for line in result.stderr.splitlines()), result.stderr


@step("試し刷りをファイルへ出力すると、五文字と上下の飾りと末尾の余白がラスタ画像で並ぶ")
def sample_export():
    target = ROOT / "build" / "acceptance" / "sample.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    outcome = subprocess.run(["php", str(ROOT / "bin/omikuji.php"), "export", "--sample", "--font", FONT, "--out", str(target)], capture_output=True, timeout=30)
    assert outcome.returncode == 0, outcome.stderr
    data = target.read_bytes()
    frames = []
    while data:
        assert data[:4] == b"\x1d\x76\x30\x00"
        width = int.from_bytes(data[4:6], "little")
        height = int.from_bytes(data[6:8], "little")
        payload = data[8:8 + width * height]
        assert width == 48 and 1 <= height <= 24 and len(payload) == width * height
        assert data[8 + width * height:11 + width * height] == b"\x1b\x4a\x00"
        frames.append((height, any(payload)))
        data = data[11 + width * height:]
    heights = [24] * 4 + ([24] * 9 + [8]) * 5 + [24, 24, 16] + [24] * 5 + [8]
    assert [height for height, _ in frames] == heights
    assert all(nonwhite for _, nonwhite in frames[:-6])
    assert not any(nonwhite for _, nonwhite in frames[-6:])
    invalid = subprocess.run(["php", str(ROOT / "bin/omikuji.php"), "export", "--sample", "--layout", "horizontal", "--out", str(target)], capture_output=True)
    assert invalid.returncode == 2


@step("描画エラーと開始前の取消では接続せず、接続後は完了、取消、送信エラーで閉じる")
def print_connection_lifetime():
    rendered_error = invoke("wire", fail_render=True)
    assert rendered_error["error"] == "InvalidArgumentException"
    assert rendered_error["opened"] == 0 and rendered_error["writes"] == []
    cancelled = invoke("wire", cancel_pre=True)
    assert cancelled["opened"] == 0 and cancelled["run"]["status"] == "cancelled"
    for values in [{}, {"cancel_after_writes": 1}, {"fail_write": True}]:
        outcome = invoke("wire", **values)
        assert outcome["opened"] == 1 and outcome["closed"]
        if values.get("fail_write"):
            assert outcome["error"] == "RuntimeException"


@step("縦書きは接頭辞と続きを空行で分けず、点を挟んで同じ列へ一文字ずつ印字する")
def vertical_continuation():
    graph = invoke("graph")
    plans = invoke("plans", settings=[{"layout": "vertical", "seed": str(seed), "rounds": 2, "max_wait_ms": 60000} for seed in range(8)])
    for plan in plans:
        glyphs = [event for event in plan["events"] if event.get("glyph")]
        assert all(len(event["text"]) == 1 and event["blank_lines"] == 0 for event in glyphs)
        for key in plan["dodge_ids"]:
            entry = graph["nodes"]["dodge:" + key]
            start = next(index for index, event in enumerate(glyphs) if event["node"] == "dodge:" + key)
            preceding = []
            index = start - 1
            while index >= 0 and glyphs[index]["kind"] == "prefix":
                preceding.insert(0, glyphs[index]["text"])
                index -= 1
            continuation = "".join(event["text"] for event in glyphs if event["node"] == "dodge:" + key)
            assert "".join(preceding).startswith(entry["prefix"])
            assert continuation == entry["continuation"]
            assert (entry["prefix"] + continuation).startswith(entry["join"])
        assert plan["wait_ms"] == sum(event.get("ms", 0) for event in plan["events"])
        assert plan["wait_ms"] <= plan["settings"]["max_wait_ms"]


@step("縦書きの文字画像、外縁、末尾の紙送りは紙量予算に含める")
def vertical_paper_budget():
    raster = invoke("raster", font=FONT, settings={"layout": "vertical", "rounds": 1, "max_wait_ms": 60000})
    plan = raster["plan"]
    frames = raster["frames"]
    assert frames[0]["decoration"] == "header"
    assert frames[-2]["decoration"] == "footer"
    assert frames[-1]["decoration"] == "feed" and not frames[-1]["nonwhite"]
    assert frames[-1]["height_dots"] == plan["settings"]["tail_feed_lines"] * 32
    assert sum(frame["height_dots"] for frame in frames) == plan["paper_dots"] == plan["paper_lines"] * 32
    for frame in frames:
        assert frame["command"] == "1d763000" and frame["width_bytes"] == 48
        assert frame["height_dots"] == frame["paper_lines"] * 32
        assert frame["payload_bytes"] == frame["width_bytes"] * frame["height_dots"]
    minimum = plan_for(layout="vertical", seed="紙量", rounds=0)
    limited = plan_for(layout="vertical", seed="紙量", rounds=10, max_wait_ms=60000, max_lines=minimum["paper_lines"])
    assert limited["rounds"] == 0 and limited["paper_lines"] <= limited["settings"]["max_lines"]
    assert limited["result"] == minimum["result"]
    for wait in [0, 1, 100, 250]:
        short = plan_for(layout="vertical", max_wait_ms=wait)
        assert short["wait_ms"] <= wait and short["events"][-1]["kind"] == "result"


@step("Enterで続行しても既に印字した文字を送り直さず、文字間の待ちでは停止しない")
def manual_progression():
    outcome = invoke("play", manual=True, settings={"layout": "vertical", "rounds": 1, "max_wait_ms": 60000})
    events = outcome["plan"]["events"]
    assert outcome["run"]["status"] == "completed"
    assert outcome["observed"] == [event for event in events if event["type"] == "print"]
    assert len(outcome["checkpoints"]) == sum(event.get("checkpoint", False) for event in events)
    assert any(event.get("checkpoint") is False for event in events)


@step("Enter待ちで取り消した場合や入力が終了した場合は、続きと末尾の紙送りを行わない")
def manual_cancellation():
    stopped = invoke("play", manual=True, stop_at_checkpoint=2, settings={"layout": "vertical", "rounds": 1, "max_wait_ms": 60000})
    assert stopped["run"]["status"] == "cancelled" and stopped["run"]["result"] is None
    assert stopped["observed"][-1]["kind"] == "prefix" and stopped["resume_delta"] == 0
    assert not any(event.get("decoration") == "feed" for event in stopped["observed"])
    ended = subprocess.run(
        ["php", str(ROOT / "bin/omikuji.php"), "demo", "--layout", "vertical", "--step", "--rounds", "0", "--max-wait-ms", "0", "--format", "json"],
        input="", text=True, capture_output=True, timeout=5, cwd=ROOT,
    )
    assert ended.returncode == 130
    run = json.loads(ended.stdout)
    assert run["status"] == "cancelled" and not any(event["kind"] == "result" for event in run["prints"])
