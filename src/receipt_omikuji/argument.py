from dataclasses import dataclass
from typing import Literal

from receipt_omikuji.plan import Setting
from receipt_omikuji.playback import Advance, Deadline
from receipt_omikuji.usb import UsbConfig


@dataclass(frozen=True)
class StandardOutput:
    pass


@dataclass(frozen=True)
class FileOutput:
    path: str


type Output = StandardOutput | FileOutput
type TextFormat = Literal["text", "json"]


@dataclass(frozen=True)
class GraphArgument:
    output: Output


@dataclass(frozen=True)
class GenerateArgument:
    setting: Setting
    sample: bool
    format: TextFormat
    output: Output


@dataclass(frozen=True)
class PngArgument:
    setting: Setting
    sample: bool
    font: str
    output: FileOutput


@dataclass(frozen=True)
class DryRunArgument:
    setting: Setting
    sample: bool
    format: TextFormat
    output: Output
    realtime: bool
    advance: Advance
    deadline: Deadline


@dataclass(frozen=True)
class PrintArgument:
    setting: Setting
    sample: bool
    font: str
    usb: UsbConfig
    advance: Advance
    deadline: Deadline


@dataclass(frozen=True)
class ExportArgument:
    setting: Setting
    sample: bool
    font: str
    output: FileOutput
    realtime: bool
    deadline: Deadline


type Argument = (
    GraphArgument
    | GenerateArgument
    | PngArgument
    | DryRunArgument
    | PrintArgument
    | ExportArgument
)
