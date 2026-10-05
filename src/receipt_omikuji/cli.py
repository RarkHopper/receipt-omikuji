import argparse
import ctypes.util
import json
import os
import re
import select
import signal
import sys
from collections.abc import Callable, Sequence
from types import FrameType
from typing import cast

from receipt_omikuji.argument import (
    Argument,
    DryRunArgument,
    ExportArgument,
    FileOutput,
    GenerateArgument,
    GraphArgument,
    Output,
    PngArgument,
    PrintArgument,
    StandardOutput,
    TextFormat,
)
from receipt_omikuji.escpos import (
    ImageEncoder,
    RasterEncoder,
    VerticalEncoder,
    print_escpos,
    receipt_png,
)
from receipt_omikuji.fortune import generate, sample_plan
from receipt_omikuji.lexicon import Lexicon, SpeechGraph
from receipt_omikuji.plan import (
    PrintEvent,
    Setting,
    SettingOption,
    WaitEvent,
    make_setting,
)
from receipt_omikuji.playback import (
    Advance,
    AutomaticAdvance,
    ByteTransport,
    CancelAfter,
    Cancellation,
    CompletedRun,
    Deadline,
    FileTransport,
    ManualAdvance,
    NoDeadline,
    SilentSink,
    TerminalSink,
    play,
)
from receipt_omikuji.usb import UsbConfig, UsbTransport


def _integer(value: str) -> int:
    if re.fullmatch(r"0|[1-9][0-9]{0,8}", value) is None:
        raise argparse.ArgumentTypeError("0以上の整数を指定してください")
    return int(value)


def _hex(value: str) -> int:
    if re.fullmatch(r"0x[0-9a-fA-F]{1,4}", value) is None:
        raise argparse.ArgumentTypeError("0xで始まるUSB識別子を指定してください")
    return int(value, 16)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="receipt-omikuji", description="レシートおみくじ"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("dry-run", "端末で文面と停止位置を確認する"),
        ("demo", "dry-runと同じ動作"),
        ("generate", "予定、文面、PNGを生成する"),
        ("graph", "発話グラフをDOTで出す"),
        ("export", "ESC/POSデータをファイルへ出す"),
        ("print", "USBへ印刷する"),
    ):
        command = commands.add_parser(
            name,
            help=description,
            description=description,
            argument_default=argparse.SUPPRESS,
        )
        if name != "print":
            command.add_argument(
                "--out", required=name == "export", help="新規出力ファイル"
            )
        if name == "graph":
            continue
        command.add_argument("--seed", help="同じ文面を再現するseed")
        for option, help_text in (
            ("rounds", "肩透かしの回数、0〜10"),
            ("max-wait-ms", "待ち時間の合計の上限"),
            ("max-lines", "紙量の上限、縦書きは32dots単位"),
            ("columns", "横書きの桁数"),
            ("expose-lines", "接頭辞と結果の後に送る空行"),
            ("width-dots", "印字幅"),
            ("character-ms", "文字間の待ち時間"),
            ("tail-feed-lines", "末尾の余白"),
        ):
            command.add_argument("--" + option, type=_integer, help=help_text)
        command.add_argument("--result", help="運勢を指定する")
        command.add_argument(
            "--layout", choices=("horizontal", "vertical"), help="横書きか縦書き"
        )
        command.add_argument(
            "--sample", action="store_true", help="あいうえおを試し刷りする"
        )
        if name in ("dry-run", "demo", "generate"):
            command.add_argument(
                "--format", choices=("text", "json", "png"), help="出力形式"
            )
        if name in ("generate", "print", "export"):
            command.add_argument("--font", help="日本語フォントのファイル")
        if name in ("dry-run", "demo", "print", "export"):
            command.add_argument(
                "--cancel-after-ms", type=_integer, help="指定時間で取消"
            )
        if name in ("dry-run", "demo", "export"):
            command.add_argument(
                "--realtime", action="store_true", help="待ち時間を再現する"
            )
        if name in ("dry-run", "demo", "print"):
            command.add_argument(
                "--step", action="store_true", help="停止位置でEnterを待つ"
            )
        if name == "print":
            command.add_argument(
                "--allow-print",
                action="store_true",
                required=True,
                help="USBへ送信する",
            )
            for option in ("vid", "pid", "endpoint"):
                command.add_argument(
                    "--" + option, type=_hex, required=True, help="USBの" + option
                )
            command.add_argument(
                "--interface",
                type=_integer,
                required=True,
                help="USBインターフェイス番号",
            )
            command.add_argument("--usb-library", help="libusbのファイル")
    return parser


def _take[T](raw: argparse.Namespace, name: str, expected: type[T]) -> T:
    if name not in vars(raw):
        raise ValueError("--" + name.replace("_", "-") + "を指定してください")
    value: object = vars(raw)[name]
    if not isinstance(value, expected):
        raise ValueError("引数の型が不正です: " + name)
    return value


def _flag(raw: argparse.Namespace, name: str) -> bool:
    return name in vars(raw) and _take(raw, name, bool)


def _output_target(raw: argparse.Namespace) -> Output:
    return (
        FileOutput(_take(raw, "out", str)) if "out" in vars(raw) else StandardOutput()
    )


def _setting(raw: argparse.Namespace) -> Setting:
    keys = SettingOption.__annotations__
    option = cast(
        SettingOption, {key: value for key, value in vars(raw).items() if key in keys}
    )
    if _flag(raw, "sample"):
        if "layout" in option and option["layout"] != "vertical":
            raise ValueError("試し刷りは縦書きです")
        option["layout"] = "vertical"
    return make_setting(**option)


def _font_path(raw: argparse.Namespace) -> str:
    if "font" in vars(raw):
        return _take(raw, "font", str)
    if "OMIKUJI_FONT" in os.environ:
        return os.environ["OMIKUJI_FONT"]
    return "/Library/Fonts/Arial Unicode.ttf"


def _usb_config(raw: argparse.Namespace) -> UsbConfig:
    if "usb_library" in vars(raw):
        library = _take(raw, "usb_library", str)
    elif sys.platform == "darwin":
        library = "/opt/homebrew/lib/libusb-1.0.dylib"
    else:
        found = ctypes.util.find_library("usb-1.0")
        if found is None:
            raise ValueError("--usb-libraryでlibusbの場所を指定してください")
        library = found
    return UsbConfig(
        _take(raw, "vid", int),
        _take(raw, "pid", int),
        _take(raw, "interface", int),
        _take(raw, "endpoint", int),
        library,
    )


def _advance(raw: argparse.Namespace) -> Advance:
    return ManualAdvance(_wait_for_enter) if _flag(raw, "step") else AutomaticAdvance()


def _deadline(raw: argparse.Namespace) -> Deadline:
    return (
        CancelAfter(_take(raw, "cancel_after_ms", int))
        if "cancel_after_ms" in vars(raw)
        else NoDeadline()
    )


def parse_argument(argv: Sequence[str]) -> Argument:
    raw = _parser().parse_args(argv)
    command = _take(raw, "command", str)
    if command == "graph":
        return GraphArgument(_output_target(raw))
    setting = _setting(raw)
    sample = _flag(raw, "sample")
    if command == "print":
        return PrintArgument(
            setting,
            sample,
            _font_path(raw),
            _usb_config(raw),
            _advance(raw),
            _deadline(raw),
        )
    if command == "export":
        return ExportArgument(
            setting,
            sample,
            _font_path(raw),
            FileOutput(_take(raw, "out", str)),
            _flag(raw, "realtime"),
            _deadline(raw),
        )
    selected = _take(raw, "format", str) if "format" in vars(raw) else "text"
    if command == "generate" and selected == "png":
        return PngArgument(
            setting, sample, _font_path(raw), FileOutput(_take(raw, "out", str))
        )
    if selected not in ("text", "json"):
        raise ValueError("PNGはgenerateで出力してください")
    text_format: TextFormat = "text" if selected == "text" else "json"
    if command == "generate":
        return GenerateArgument(setting, sample, text_format, _output_target(raw))
    if command in ("dry-run", "demo"):
        return DryRunArgument(
            setting,
            sample,
            text_format,
            _output_target(raw),
            _flag(raw, "realtime") or _flag(raw, "step"),
            _advance(raw),
            _deadline(raw),
        )
    raise ValueError("未対応のコマンドです: " + command)


def _output(content: str | bytes, target: Output) -> None:
    data = content.encode("utf-8") if isinstance(content, str) else content
    if isinstance(target, StandardOutput):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    else:
        transport = FileTransport(target.path)
        try:
            transport.write(data, Cancellation())
        finally:
            transport.close()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _wait_for_enter(
    event: WaitEvent, cancel: Cancellation, check: Callable[[], bool]
) -> bool:
    print("停止中。Enterで続行、Ctrl-Cで取消", file=sys.stderr, flush=True)
    descriptor = sys.stdin.fileno()
    while check():
        ready, _, _ = select.select([descriptor], [], [], 0.025)
        if ready:
            char = os.read(descriptor, 1)
            if not char:
                return False
            if char == b"\n":
                return not cancel.cancelled
    return False


def _encoder(font: str, setting: Setting) -> ImageEncoder:
    if setting.layout == "vertical":
        return VerticalEncoder(font, setting.width_dots)
    return RasterEncoder(font, setting.columns, setting.width_dots)


def _text(events: Sequence[PrintEvent]) -> str:
    return "".join(
        event.text + "\n" + "\n" * event.blank_lines for event in events if event.text
    )


def execute(argument: Argument) -> int:
    graph = SpeechGraph(Lexicon.load_builtin())
    if isinstance(argument, GraphArgument):
        _output(graph.dot(), argument.output)
        return 0
    setting = argument.setting
    plan = sample_plan(setting) if argument.sample else generate(graph, setting)
    if isinstance(argument, GenerateArgument):
        content = (
            _json(plan.to_dict())
            if argument.format == "json"
            else _text(
                tuple(event for event in plan.events if isinstance(event, PrintEvent))
            )
        )
        _output(content, argument.output)
        return 0
    if isinstance(argument, PngArgument):
        _output(receipt_png(plan, _encoder(argument.font, setting)), argument.output)
        return 0
    cancel = Cancellation()

    def interrupt(signum: int, frame: FrameType | None) -> None:
        cancel.cancel()

    previous = {
        signum: signal.signal(signum, interrupt)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        if isinstance(argument, DryRunArgument):
            sink = (
                SilentSink()
                if argument.format == "json" or isinstance(argument.output, FileOutput)
                else TerminalSink(sys.stdout)
            )
            run = play(
                plan,
                sink,
                cancel,
                realtime=argument.realtime,
                deadline=argument.deadline,
                advance=argument.advance,
            )
            if argument.format == "json":
                _output(_json(run.to_dict()), argument.output)
            elif isinstance(argument.output, FileOutput):
                _output(_text(run.prints), argument.output)
        else:
            encoder = _encoder(argument.font, setting)
            if isinstance(argument, PrintArgument):
                config = argument.usb

                def connect() -> ByteTransport:
                    return UsbTransport(config)

                realtime = True
                advance = argument.advance
            else:
                target = argument.output

                def connect() -> ByteTransport:
                    return FileTransport(target.path)

                realtime = argument.realtime
                advance = AutomaticAdvance()
            run = print_escpos(
                plan,
                encoder,
                connect,
                cancel,
                realtime=realtime,
                deadline=argument.deadline,
                advance=advance,
            )
        if not isinstance(argument, DryRunArgument) or argument.format != "json":
            print(
                f"{run.status} · seed={setting.seed} · 紙量={run.paper_lines} · 予定の待ち={plan.wait_ms}ms",
                file=sys.stderr,
            )
        return 0 if isinstance(run, CompletedRun) else 130
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main() -> int:
    try:
        return execute(parse_argument(sys.argv[1:]))
    except (ValueError, OSError, RuntimeError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
