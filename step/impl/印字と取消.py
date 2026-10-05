import json
import os
import pathlib
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from unittest.mock import patch

from getgauge.python import step
from PIL import Image

from receipt_omikuji.argument import DryRunArgument
from receipt_omikuji.cli import parse_argument
from receipt_omikuji.escpos import (
    AsciiEncoder,
    RasterEncoder,
    VerticalEncoder,
    bitmap,
    print_escpos,
)
from receipt_omikuji.fortune import generate
from receipt_omikuji.lexicon import Lexicon, SpeechGraph
from receipt_omikuji.plan import (
    DecorationEvent,
    GlyphEvent,
    Plan,
    PrintEvent,
    TextEvent,
    WaitEvent,
    make_setting,
)
from receipt_omikuji.playback import (
    ByteTransport,
    CancelAfter,
    Cancellation,
    CancelledRun,
    ManualAdvance,
    PrintSink,
    play,
)
from receipt_omikuji.usb import UsbConfig, UsbTransport

ROOT = pathlib.Path(__file__).resolve().parents[2]
FONT = os.environ.get("OMIKUJI_FONT", "/Library/Fonts/Arial Unicode.ttf")
GRAPH = SpeechGraph(Lexicon.load_builtin())


def prints(plan: Plan) -> list[PrintEvent]:
    return [event for event in plan.events if isinstance(event, PrintEvent)]


class TraceSink(PrintSink):
    def __init__(self, before: str | None = None, after: str | None = None) -> None:
        self.events: list[PrintEvent] = []
        self.before = before
        self.after = after

    def print(self, event: PrintEvent, cancel: Cancellation) -> bool:
        if event.kind == self.before:
            cancel.cancel()
        if cancel.cancelled:
            return False
        self.events.append(event)
        if event.kind == self.after:
            cancel.cancel()
        return True


class MemoryTransport(ByteTransport):
    def __init__(self, limit: int | None = None, fail: bool = False) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self.opened = 0
        self.limit = limit
        self.fail = fail

    def write(self, data: bytes, cancel: Cancellation) -> bool:
        if cancel.cancelled:
            return False
        if self.fail:
            raise OSError("送信に失敗しました")
        self.writes.append(data)
        if len(self.writes) == self.limit:
            cancel.cancel()
        return True

    def close(self) -> None:
        self.closed = True

    def connect(self) -> "MemoryTransport":
        self.opened += 1
        return self


def wire_plan(fail_render: bool = False) -> Plan:
    return Plan(
        make_setting(),
        "大吉",
        (
            TextEvent("prefix", "prefix", "DAI.....", 1, 0),
            WaitEvent("prefix", 1200, True),
            TextEvent("dodge", "dodge", "JOBU!", 2, empty_lines=1),
            TextEvent(
                "result", "result", "あ" if fail_render else "RESULT: DAIKICHI", 1, 0
            ),
        ),
        0,
        (),
        (),
    )


@dataclass
class Raster:
    width_bytes: int
    heights: list[int]
    payload: bytes

    @property
    def height(self) -> int:
        return sum(self.heights)


def decode_raster(data: bytes) -> Raster:
    width = None
    heights = []
    payload = bytearray()
    while data:
        assert data[:4] == b"\x1d\x76\x30\x00"
        current_width = int.from_bytes(data[4:6], "little")
        height = int.from_bytes(data[6:8], "little")
        width = current_width if width is None else width
        assert current_width == width and 1 <= height <= 24
        size = width * height
        block = data[8 : 8 + size]
        assert len(block) == size
        assert data[8 + size : 11 + size] == b"\x1b\x4a\x00"
        heights.append(height)
        payload.extend(block)
        data = data[11 + size :]
    assert width is not None
    return Raster(width, heights, bytes(payload))


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


@step("待ちの前に接頭辞が印字済みになり、待ちによって紙量が増えない")
def paper_and_time() -> None:
    plan = generate(GRAPH, make_setting())
    for previous, event in zip(plan.events, plan.events[1:]):
        if isinstance(event, WaitEvent):
            assert isinstance(previous, PrintEvent)
    transport = MemoryTransport()
    run = print_escpos(wire_plan(), AsciiEncoder(), transport.connect, Cancellation())
    assert transport.writes[0] == b"DAI.....\n"
    assert all(chunk.endswith(b"\n") for chunk in transport.writes)
    assert run.paper_lines == 4


@step("即時と実時間のドライランで同じ文面を同じ順序に出す")
def playback_modes() -> None:
    plan = generate(GRAPH, make_setting(rounds=0, max_wait_ms=150))
    first, second = TraceSink(), TraceSink()
    immediate = play(plan, first, Cancellation())
    realtime = play(plan, second, Cancellation(), realtime=True)
    assert first.events == second.events
    assert immediate.status == realtime.status == "completed"
    assert realtime.elapsed_ms >= 140 and immediate.waited_ms == 150


@step("ESC/POSの文字出力は改行で確定し、日本語はラスタ出力で行幅と紙量を保つ")
def escpos_frames() -> None:
    encoder = RasterEncoder(FONT)
    plan = generate(GRAPH, make_setting(rounds=1))
    for event in prints(plan):
        frame = decode_raster(encoder.encode(event))
        assert frame.width_bytes == 48 and frame.height == event.lines * 32
        assert len(frame.payload) == frame.width_bytes * frame.height and any(
            frame.payload
        )
    image = Image.new("L", (16, 49), 255)
    for x, y in ((0, 0), (7, 23), (8, 24), (15, 47), (3, 48)):
        image.putpixel((x, y), 0)
    frame = decode_raster(bitmap(image))
    assert frame.width_bytes == 2 and frame.heights == [24, 24, 1]
    rows = [b"\x00\x00"] * 49
    for row, data in {
        0: b"\x80\x00",
        23: b"\x01\x00",
        24: b"\x00\x80",
        47: b"\x00\x01",
        48: b"\x10\x00",
    }.items():
        rows[row] = data
    assert frame.payload == b"".join(rows)


@step("開始前、接頭辞の後、待ちの途中、結果の直前の取消では、その後の印字を行わない")
def cancellation_boundaries() -> None:
    plan = generate(GRAPH, make_setting())
    for before, after, pre, last_kind in (
        (None, None, True, None),
        (None, "prefix", False, "prefix"),
        ("result", None, False, "final_prefix"),
    ):
        cancel = Cancellation()
        if pre:
            cancel.cancel()
        sink = TraceSink(before, after)
        run = play(plan, sink, cancel)
        assert (
            run.status == "cancelled"
            and isinstance(run, CancelledRun)
            and run.to_dict()["result"] is None
        )
        assert (sink.events[-1].kind if sink.events else None) == last_kind
    elapsed = 0
    for event in plan.events:
        if isinstance(event, WaitEvent):
            if event.node.startswith("prefix:"):
                deadline = elapsed + event.ms // 2
                break
            elapsed += event.ms
    sink = TraceSink()
    run = play(plan, sink, Cancellation(), deadline=CancelAfter(deadline))
    assert sink.events[-1].kind == "prefix" and run.waited_ms < plan.wait_ms
    realtime = play(
        generate(GRAPH, make_setting(rounds=0)),
        TraceSink(),
        Cancellation(),
        realtime=True,
        deadline=CancelAfter(80),
    )
    assert realtime.status == "cancelled" and realtime.prints[-1].kind == "final_prefix"
    assert realtime.elapsed_ms < 1000
    transport = MemoryTransport(limit=1)
    run = print_escpos(wire_plan(), AsciiEncoder(), transport.connect, Cancellation())
    assert len(transport.writes) == 1 and run.status == "cancelled"


@step("取消済みの実行を再開しても追加印字を行わない")
def persistent_cancellation() -> None:
    plan = generate(GRAPH, make_setting())
    sink, cancel = TraceSink(after="prefix"), Cancellation()
    play(plan, sink, cancel)
    before = list(sink.events)
    play(plan, sink, cancel)
    assert sink.events == before


@step("出力方法を選ぶコマンドは必須で、省略した入力を拒否する")
def explicit_command() -> None:
    for argument in ([], ["--seed", "朝"], ["--format", "json"]):
        outcome = cli(argument)
        assert outcome.returncode == 2 and not outcome.stdout


@step("プリンター未接続でもドライラン、予定のJSON、PNG、ESC/POSファイルを生成できる")
def offline_cli() -> None:
    directory = ROOT / "build" / "acceptance"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as destination:
        targets = pathlib.Path(destination)
        for filename, arguments in (
            ("demo.json", ["dry-run", "--format", "json"]),
            ("plan.json", ["generate", "--format", "json"]),
            (
                "receipt.png",
                [
                    "generate",
                    "--layout",
                    "vertical",
                    "--rounds",
                    "0",
                    "--format",
                    "png",
                    "--font",
                    FONT,
                ],
            ),
            ("receipt.bin", ["export", "--font", FONT]),
        ):
            result = cli(arguments + ["--out", str(targets / filename)])
            assert result.returncode == 0, result.stderr
            assert (targets / filename).stat().st_size > 0
        assert json.loads((targets / "demo.json").read_text())["status"] == "completed"
        assert json.loads((targets / "plan.json").read_text())["schema"] == 1
        decode_raster((targets / "receipt.bin").read_bytes())
        with Image.open(targets / "receipt.png") as image:
            assert image.width == 384 and image.height > 0
        preserved = (targets / "plan.json").read_bytes()
        duplicate = cli(["generate", "--out", str(targets / "plan.json")])
        assert (
            duplicate.returncode == 2
            and (targets / "plan.json").read_bytes() == preserved
        ), (duplicate.returncode, duplicate.stderr)
        missing_font = cli(
            [
                "generate",
                "--format",
                "png",
                "--font",
                str(targets / "missing.ttf"),
                "--out",
                str(targets / "missing.png"),
            ]
        )
        assert missing_font.returncode == 2 and not (targets / "missing.png").exists()


@step("ドライランはseed未指定なら毎回抽選し、指定したseedでは同じ文面を再現する")
def dry_run_seed() -> None:
    with patch(
        "receipt_omikuji.cli.secrets.token_hex",
        side_effect=("first", "second", "third", "fourth"),
    ) as entropy:
        for command in ("dry-run", "demo"):
            first = parse_argument([command])
            second = parse_argument([command])
            assert isinstance(first, DryRunArgument) and isinstance(
                second, DryRunArgument
            )
            assert first.setting.seed != second.setting.seed
            fixed = [parse_argument([command, "--seed", "朝"]) for _ in range(2)]
            assert all(argument.setting.seed == "朝" for argument in fixed)
            assert generate(GRAPH, fixed[0].setting) == generate(
                GRAPH, fixed[1].setting
            )
        assert entropy.call_count == 4


@step("USB識別や印刷の明示が足りない場合は、デバイスを開く前に拒否する")
def explicit_print_config() -> None:
    for sample in ([], ["--sample"]):
        for arguments in (
            [],
            ["--allow-print"],
            [
                "--allow-print",
                "--vid",
                "0x1234",
                "--pid",
                "0x5678",
                "--endpoint",
                "0x81",
                "--interface",
                "0",
            ],
        ):
            result = cli(["print"] + sample + arguments)
            assert result.returncode == 2 and not result.stdout


@step(
    "試し刷りをファイルへ出力すると、五文字と上下の飾りと末尾の余白がラスタ画像で並ぶ"
)
def sample_export() -> None:
    directory = ROOT / "build" / "acceptance"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as destination:
        target = pathlib.Path(destination) / "sample.bin"
        outcome = cli(["export", "--sample", "--font", FONT, "--out", str(target)])
        assert outcome.returncode == 0, outcome.stderr
        frame = decode_raster(target.read_bytes())
        assert frame.width_bytes == 48
        assert frame.height == 928
        image = Image.frombytes("1", (384, frame.height), frame.payload)
        assert image.getbbox() is not None
        assert image.crop((0, frame.height - 128, 384, frame.height)).getbbox() is None
        invalid = cli(
            ["export", "--sample", "--layout", "horizontal", "--out", str(target)]
        )
        assert invalid.returncode == 2


@step("描画エラーと開始前の取消では接続せず、接続後は完了、取消、送信エラーで閉じる")
def print_connection_lifetime() -> None:
    transport = MemoryTransport()
    try:
        print_escpos(
            wire_plan(fail_render=True),
            AsciiEncoder(),
            transport.connect,
            Cancellation(),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("描画エラーが無視されました")
    assert transport.opened == 0 and not transport.writes
    cancel = Cancellation()
    cancel.cancel()
    run = print_escpos(wire_plan(), AsciiEncoder(), transport.connect, cancel)
    assert transport.opened == 0 and run.status == "cancelled"
    for limit, fail in ((None, False), (1, False), (None, True)):
        transport = MemoryTransport(limit=limit, fail=fail)
        try:
            print_escpos(wire_plan(), AsciiEncoder(), transport.connect, Cancellation())
        except OSError:
            assert fail
        else:
            assert not fail
        assert transport.opened == 1 and transport.closed


@step("縦書きは接頭辞と続きを空行で分けず、点を挟んで同じ列へ一文字ずつ印字する")
def vertical_continuation() -> None:
    entries = {entry.id: entry for entry in GRAPH.lexicon.dodge}
    for seed in range(8):
        plan = generate(
            GRAPH,
            make_setting(
                layout="vertical", seed=str(seed), rounds=2, max_wait_ms=60000
            ),
        )
        glyphs = [event for event in prints(plan) if isinstance(event, GlyphEvent)]
        assert all(len(event.text) == 1 and event.blank_lines == 0 for event in glyphs)
        for key in plan.dodge_ids:
            entry = entries[key]
            start = next(
                index
                for index, event in enumerate(glyphs)
                if event.node == "dodge:" + key
            )
            preceding = []
            index = start - 1
            while index >= 0 and glyphs[index].kind == "prefix":
                preceding.insert(0, glyphs[index].text)
                index -= 1
            continuation = "".join(
                event.text for event in glyphs if event.node == "dodge:" + key
            )
            assert (
                "".join(preceding).startswith(entry.prefix)
                and continuation == entry.continuation
            )
            assert (entry.prefix + continuation).startswith(entry.join)
        assert plan.wait_ms == sum(
            event.ms for event in plan.events if isinstance(event, WaitEvent)
        )
        assert plan.wait_ms <= plan.setting.max_wait_ms


@step("縦書きの文字画像、外縁、末尾の紙送りは紙量予算に含める")
def vertical_paper_budget() -> None:
    plan = generate(GRAPH, make_setting(layout="vertical", rounds=1, max_wait_ms=60000))
    events = prints(plan)
    encoder = VerticalEncoder(FONT)
    frames = [decode_raster(encoder.encode(event)) for event in events]
    assert events[0].decoration == "header" and events[-2].decoration == "footer"
    assert events[-1].decoration == "feed" and not any(frames[-1].payload)
    assert frames[-1].height == plan.setting.tail_feed_lines * 32
    assert sum(frame.height for frame in frames) == plan.paper_lines * 32
    for event, frame in zip(events, frames):
        assert frame.width_bytes == 48 and frame.height == event.lines * 32
        assert len(frame.payload) == frame.width_bytes * frame.height
        ink = Image.frombytes("1", (384, frame.height), frame.payload)
        box = ink.getbbox()
        if box is not None:
            assert box[0] >= 100 and box[2] <= 284
        if isinstance(event, GlyphEvent) and event.text not in (".", "…"):
            assert frame.height == 128
            glyph = ink.crop((112, 0, 272, frame.height)).getbbox()
            if glyph is not None:
                assert glyph[2] - glyph[0] <= 96 and glyph[3] - glyph[1] <= 96
    header = encoder.image(events[0])
    logo = decode_raster(bitmap(header.crop((112, 20, 272, 84))))
    assert any(logo.payload)
    minimum = generate(GRAPH, make_setting(layout="vertical", seed="紙量", rounds=0))
    limited = generate(
        GRAPH,
        make_setting(
            layout="vertical",
            seed="紙量",
            rounds=10,
            max_wait_ms=60000,
            max_lines=minimum.paper_lines,
        ),
    )
    assert not limited.dodge_ids and limited.paper_lines <= limited.setting.max_lines
    assert limited.result == minimum.result
    for wait in (0, 1, 100, 250):
        short = generate(GRAPH, make_setting(layout="vertical", max_wait_ms=wait))
        assert short.wait_ms <= wait and prints(short)[-1].kind == "result"


@step("Enterで続行しても既に印字した文字を送り直さず、文字間の待ちでは停止しない")
def manual_progression() -> None:
    plan = generate(GRAPH, make_setting(layout="vertical", rounds=1, max_wait_ms=60000))
    sink = TraceSink()
    checkpoints = []

    def advance(event, cancel, check):
        checkpoints.append((event.node, len(sink.events)))
        return True

    run = play(plan, sink, Cancellation(), advance=ManualAdvance(advance))
    assert run.status == "completed" and sink.events == prints(plan)
    assert len(checkpoints) == sum(
        event.checkpoint for event in plan.events if isinstance(event, WaitEvent)
    )
    assert any(
        not event.checkpoint for event in plan.events if isinstance(event, WaitEvent)
    )


@step("Enter待ちで取り消した場合や入力が終了した場合は、続きと末尾の紙送りを行わない")
def manual_cancellation() -> None:
    plan = generate(GRAPH, make_setting(layout="vertical", rounds=1, max_wait_ms=60000))
    sink, cancel = TraceSink(), Cancellation()

    def advance(event, token, check):
        return not event.node.startswith("prefix:")

    run = play(plan, sink, cancel, advance=ManualAdvance(advance))
    assert (
        run.status == "cancelled"
        and isinstance(run, CancelledRun)
        and run.to_dict()["result"] is None
        and sink.events[-1].kind == "prefix"
    )
    before = list(sink.events)
    play(plan, sink, cancel)
    assert sink.events == before and not any(
        isinstance(event, DecorationEvent) and event.decoration == "feed"
        for event in sink.events
    )
    ended = cli(
        [
            "dry-run",
            "--layout",
            "vertical",
            "--step",
            "--rounds",
            "0",
            "--max-wait-ms",
            "0",
            "--format",
            "json",
        ],
        input_text="",
    )
    assert ended.returncode == 130
    outcome = json.loads(ended.stdout)
    assert outcome["status"] == "cancelled" and not any(
        event["kind"] == "result" for event in outcome["prints"]
    )


@step("縦書きの読点と句点は右上に置き、括弧と長音は縦書きの向きにする")
def vertical_punctuation() -> None:
    encoder = VerticalEncoder(FONT)
    for char in ("、", "。", "「", "」", "ー"):
        image = encoder.image(GlyphEvent("punctuation", "dodge", char, 4))
        frame = decode_raster(bitmap(image))
        ink = Image.frombytes("1", (384, 128), frame.payload).crop((112, 0, 272, 128))
        box = ink.getbbox()
        assert box is not None
        left, top, right, bottom = box
        if char in ("、", "。"):
            assert left + 112 > 192 and top < 64
        elif char == "「":
            assert top >= 64 and right - left > bottom - top
        elif char == "」":
            assert bottom <= 64 and right - left > bottom - top
        else:
            assert bottom - top > right - left


@dataclass
class Endpoint:
    bEndpointAddress: int
    bmAttributes: int


@dataclass
class Interface:
    bInterfaceNumber: int
    endpoint: Endpoint

    def __iter__(self):
        return iter([self.endpoint])


class UsbDevice:
    def __init__(self, partial: int | None = None, fail: bool = False) -> None:
        self.partial = partial
        self.fail = fail
        self.writes = []
        self.interfaces = [Interface(0, Endpoint(3, 2))]

    def get_active_configuration(self):
        return self.interfaces

    def write(self, endpoint: int, data: bytes, timeout: int) -> int:
        assert endpoint == 3 and timeout == 1000
        self.writes.append(data)
        if self.fail:
            raise OSError("USB転送失敗")
        return self.partial if self.partial is not None else len(data)


@step("USB転送が途中で止まった場合は残りを再送せず、取消や送信エラーでも接続を解放する")
def usb_transfer_lifetime() -> None:
    config = UsbConfig(0x0416, 0x5011, 0, 3, "/test/libusb")
    for partial, fail, cancelled in (
        (None, False, False),
        (0, False, False),
        (17, False, False),
        (None, True, False),
        (None, False, True),
    ):
        device = UsbDevice(partial, fail)
        with ExitStack() as stack:
            stack.enter_context(
                patch("usb.backend.libusb1.get_backend", return_value=object())
            )
            stack.enter_context(patch("usb.core.find", return_value=[device]))
            claim = stack.enter_context(patch("usb.util.claim_interface"))
            release = stack.enter_context(patch("usb.util.release_interface"))
            dispose = stack.enter_context(patch("usb.util.dispose_resources"))
            transport = UsbTransport(config)
            cancel = Cancellation()
            if cancelled:
                cancel.cancel()
            try:
                written = transport.write(b"x" * 8193, cancel)
            except OSError:
                assert partial is not None or fail
                assert len(device.writes) == 1
            else:
                assert partial is None and not fail and written != cancelled
                assert [len(data) for data in device.writes] == (
                    [] if cancelled else [4096, 4096, 1]
                )
            finally:
                transport.close()
            claim.assert_called_once_with(device, 0)
            release.assert_called_once_with(device, 0)
            dispose.assert_called_once_with(device)
    with ExitStack() as stack:
        stack.enter_context(
            patch("usb.backend.libusb1.get_backend", return_value=object())
        )
        claim = stack.enter_context(patch("usb.util.claim_interface"))
        for devices in ([], [UsbDevice(), UsbDevice()]):
            with patch("usb.core.find", return_value=devices):
                try:
                    UsbTransport(config)
                except OSError:
                    pass
                else:
                    raise AssertionError("機器が一台に定まっていません")
        claim.assert_not_called()
    for interfaces in (
        [Interface(1, Endpoint(3, 2))],
        [Interface(0, Endpoint(3, 2)), Interface(1, Endpoint(3, 2))],
        [Interface(0, Endpoint(3, 3))],
    ):
        device = UsbDevice()
        device.interfaces = interfaces
        with ExitStack() as stack:
            stack.enter_context(
                patch("usb.backend.libusb1.get_backend", return_value=object())
            )
            stack.enter_context(patch("usb.core.find", return_value=[device]))
            claim = stack.enter_context(patch("usb.util.claim_interface"))
            dispose = stack.enter_context(patch("usb.util.dispose_resources"))
            try:
                UsbTransport(config)
            except ValueError:
                pass
            else:
                raise AssertionError("指定した送信先ではありません")
            claim.assert_not_called()
            dispose.assert_called_once_with(device)
            assert device.writes == []
