import io
import math
import struct
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from receipt_omikuji.paper import character_width
from receipt_omikuji.plan import ROW_DOTS, DecorationEvent, Plan, PrintEvent
from receipt_omikuji.playback import (
    Advance,
    AutomaticAdvance,
    ByteTransport,
    CancelAfter,
    Cancellation,
    CancelledRun,
    Deadline,
    NoDeadline,
    PrintSink,
    RunResult,
    play,
)


class Encoder(ABC):
    @abstractmethod
    def encode(self, event: PrintEvent) -> bytes: ...


class ImageEncoder(Encoder):
    @abstractmethod
    def image(self, event: PrintEvent) -> Image.Image: ...


class AsciiEncoder(Encoder):
    def encode(self, event: PrintEvent) -> bytes:
        if any(char != "\n" and not " " <= char <= "~" for char in event.text):
            raise ValueError("日本語は画像に変換してください")
        return (event.text + "\n" + "\n" * event.blank_lines).encode("ascii")


def bitmap(image: Image.Image) -> bytes:
    width, height = image.size
    if width < 8 or width % 8 or not 1 <= height <= 2047:
        raise ValueError("ラスタ画像の寸法が範囲外です")
    ink = image.convert("L").point(lambda value: 255 if value < 160 else 0, mode="1")
    payload = ink.tobytes()
    width_bytes = width // 8
    frames = bytearray()
    # 画像を一括送信すると、POS58では次の画像が文字として印刷された。
    # 24dotsで分割する。各画像の後にESC J 0を送る。
    # https://github.com/klirichek/zj-58/blob/master/rastertozj.c
    for start in range(0, height, 24):
        strip_height = min(24, height - start)
        frames.extend(
            b"\x1d\x76\x30\x00" + struct.pack("<HH", width_bytes, strip_height)
        )
        frames.extend(
            payload[start * width_bytes : (start + strip_height) * width_bytes]
        )
        frames.extend(b"\x1b\x4a\x00")
    return bytes(frames)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if not Path(path).is_file():
        raise ValueError("日本語フォントのファイルを指定してください")
    return ImageFont.truetype(path, size=size)


def _glyph(text: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    left, top, right, bottom = font.getbbox(text)
    left, top, right, bottom = (
        math.floor(left),
        math.floor(top),
        math.ceil(right),
        math.ceil(bottom),
    )
    image = Image.new("L", (max(1, right - left), max(1, bottom - top)), 255)
    ImageDraw.Draw(image).text((-left, -top), text, font=font, fill=0)
    box = ImageOps.invert(image).getbbox()
    return image.crop(box) if box is not None else image


def _center(image: Image.Image, glyph: Image.Image, x: int, y: int) -> None:
    image.paste(glyph, (x - glyph.width // 2, y - glyph.height // 2))


class RasterEncoder(ImageEncoder):
    def __init__(self, font: str, columns: int = 32, width_dots: int = 384) -> None:
        if (
            not 16 <= columns <= 80
            or not 192 <= width_dots <= 832
            or width_dots % 8
            or width_dots < columns * 8
        ):
            raise ValueError("印字幅と桁数が合っていません")
        self.width_dots = width_dots
        self.columns = columns
        self.font = _font(font, min(24, int(width_dots / columns * 1.733)))

    def image(self, event: PrintEvent) -> Image.Image:
        lines = event.text.split("\n")
        image = Image.new(
            "L", (self.width_dots, (len(lines) + event.blank_lines) * ROW_DOTS), 255
        )
        cell = self.width_dots / self.columns
        for row, line in enumerate(lines):
            column = 0
            for char in line:
                char_width = character_width(char)
                glyph = _glyph(char, self.font)
                if (
                    glyph.width > char_width * cell
                    or column + char_width > self.columns
                ):
                    raise ValueError("文字が指定した桁の幅に収まりません")
                _center(
                    image,
                    glyph,
                    round((column + char_width / 2) * cell),
                    row * ROW_DOTS + 16,
                )
                column += char_width
        return image

    def encode(self, event: PrintEvent) -> bytes:
        return bitmap(self.image(event))


class VerticalEncoder(ImageEncoder):
    def __init__(self, font: str, width_dots: int = 384) -> None:
        if not 192 <= width_dots <= 832 or width_dots % 8:
            raise ValueError("印字幅は192〜832dotsの8の倍数にしてください")
        self.width_dots = width_dots
        self.font = _font(font, width_dots // 2)
        self.heading_font = _font(font, 27)

    def image(self, event: PrintEvent) -> Image.Image:
        height = event.lines * ROW_DOTS
        if not 1 <= height <= 2047:
            raise ValueError("縦書き画像の高さが範囲外です")
        image = Image.new("L", (self.width_dots, height), 255)
        if isinstance(event, DecorationEvent) and event.decoration == "feed":
            return image
        draw = ImageDraw.Draw(image)
        top = (
            12
            if isinstance(event, DecorationEvent) and event.decoration == "header"
            else 0
        )
        bottom = (
            height - 13
            if isinstance(event, DecorationEvent) and event.decoration == "footer"
            else height - 1
        )
        for x in (10, self.width_dots - 11):
            draw.line((x, top, x, bottom), fill=0, width=2)
        for x in (17, self.width_dots - 18):
            draw.line((x, top, x, bottom), fill=0)
        center = self.width_dots // 2
        if isinstance(event, DecorationEvent) and event.decoration in (
            "header",
            "footer",
        ):
            edge = top if event.decoration == "header" else bottom
            inner = edge + 7 if event.decoration == "header" else edge - 7
            draw.line((10, edge, self.width_dots - 11, edge), fill=0)
            draw.line((17, inner, self.width_dots - 18, inner), fill=0)
            flower_y = 40 if event.decoration == "header" else 25
            for dx, dy in ((0, -8), (8, 0), (0, 8), (-8, 0)):
                draw.ellipse(
                    (
                        center + dx - 8,
                        flower_y + dy - 8,
                        center + dx + 7,
                        flower_y + dy + 7,
                    ),
                    outline=0,
                )
            draw.ellipse((center - 3, flower_y - 3, center + 2, flower_y + 2), fill=0)
            if event.decoration == "header":
                _center(image, _glyph("御神籤", self.heading_font), center, 75)
            return image
        if len(event.text) != 1:
            raise ValueError("縦書きの画像には一文字を指定してください")
        char = event.text
        if char in (".", "…"):
            for y in (16,) if char == "." else (12, 32, 52):
                draw.ellipse((center - 3, y - 3, center + 3, y + 3), fill=0)
            return image
        glyph = _glyph(char, self.font)
        corner = char in "、。，．"
        opening = char in "「『【〔（(［[｛{〈《"
        closing = char in "」』】〕）)］]｝}〉》"
        if opening or closing or char in "ー―〜～：；":
            glyph = glyph.transpose(Image.Transpose.ROTATE_270)
        em = self.width_dots // 2
        if glyph.width > em or glyph.height > height - 16:
            scale = min(em / glyph.width, (height - 16) / glyph.height)
            glyph = glyph.resize(
                (max(1, int(glyph.width * scale)), max(1, int(glyph.height * scale))),
                Image.Resampling.LANCZOS,
            )
        cell_top = (height - em) // 2
        if corner:
            image.paste(glyph, ((self.width_dots + em) // 2 - glyph.width, cell_top))
        elif opening:
            image.paste(
                glyph,
                ((self.width_dots - glyph.width) // 2, cell_top + em - glyph.height),
            )
        elif closing:
            image.paste(glyph, ((self.width_dots - glyph.width) // 2, cell_top))
        else:
            _center(image, glyph, center, height // 2)
        return image

    def encode(self, event: PrintEvent) -> bytes:
        return bitmap(self.image(event))


def receipt_png(plan: Plan, encoder: ImageEncoder) -> bytes:
    image = Image.new("L", (plan.setting.width_dots, plan.paper_lines * ROW_DOTS), 255)
    y = 0
    for event in plan.events:
        if isinstance(event, PrintEvent):
            frame = encoder.image(event)
            image.paste(frame, (0, y))
            y += frame.height
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class EscposSink(PrintSink):
    def __init__(
        self, frames: dict[PrintEvent, bytes], transport: ByteTransport
    ) -> None:
        self.frames = frames
        self.transport = transport

    def print(self, event: PrintEvent, cancel: Cancellation) -> bool:
        return not cancel.cancelled and self.transport.write(self.frames[event], cancel)


def print_escpos(
    plan: Plan,
    encoder: Encoder,
    connect: Callable[[], ByteTransport],
    cancel: Cancellation,
    *,
    realtime: bool = False,
    deadline: Deadline = NoDeadline(),
    advance: Advance = AutomaticAdvance(),
) -> RunResult:
    if isinstance(deadline, CancelAfter) and deadline.ms == 0:
        cancel.cancel()
    if cancel.cancelled:
        return CancelledRun((), 0, 0)
    # 途中まで印刷してからフォントや幅の不一致に気付かないよう、接続前に全画像を変換する。
    frames = {
        event: encoder.encode(event)
        for event in plan.events
        if isinstance(event, PrintEvent)
    }
    if cancel.cancelled:
        return CancelledRun((), 0, 0)
    transport = connect()
    try:
        return play(
            plan,
            EscposSink(frames, transport),
            cancel,
            realtime=realtime,
            deadline=deadline,
            advance=advance,
        )
    finally:
        transport.close()
