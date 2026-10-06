import math
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TextIO

from receipt_omikuji.plan import Plan, PrintEvent, WaitEvent


class Cancellation:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class PrintSink(ABC):
    @abstractmethod
    def print(self, event: PrintEvent, cancel: Cancellation) -> bool: ...


class ByteTransport(ABC):
    @abstractmethod
    def write(self, data: bytes, cancel: Cancellation) -> bool: ...
    @abstractmethod
    def close(self) -> None: ...


class TerminalSink(PrintSink):
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def print(self, event: PrintEvent, cancel: Cancellation) -> bool:
        if cancel.cancelled:
            return False
        if event.text:
            self.stream.write(event.text + "\n" + "\n" * event.blank_lines)
            self.stream.flush()
        return True


class SilentSink(PrintSink):
    def print(self, event: PrintEvent, cancel: Cancellation) -> bool:
        return not cancel.cancelled


class RunResult(ABC):
    prints: tuple[PrintEvent, ...]
    waited_ms: int
    elapsed_ms: float

    @property
    @abstractmethod
    def status(self) -> Literal["completed", "cancelled"]: ...

    @property
    def paper_lines(self) -> int:
        return sum(event.lines for event in self.prints)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "prints": [event.to_dict() for event in self.prints],
            "paper_lines": self.paper_lines,
            "waited_ms": self.waited_ms,
            "elapsed_ms": self.elapsed_ms,
            "result": self.result if isinstance(self, CompletedRun) else None,
        }


@dataclass(frozen=True)
class CompletedRun(RunResult):
    prints: tuple[PrintEvent, ...]
    waited_ms: int
    elapsed_ms: float
    result: str

    @property
    def status(self) -> Literal["completed"]:
        return "completed"


@dataclass(frozen=True)
class CancelledRun(RunResult):
    prints: tuple[PrintEvent, ...]
    waited_ms: int
    elapsed_ms: float

    @property
    def status(self) -> Literal["cancelled"]:
        return "cancelled"


@dataclass(frozen=True)
class NoDeadline:
    pass


@dataclass(frozen=True)
class CancelAfter:
    ms: int

    def __post_init__(self) -> None:
        if self.ms < 0:
            raise ValueError("取消までの時間は0以上にしてください")


type Deadline = NoDeadline | CancelAfter


@dataclass(frozen=True)
class AutomaticAdvance:
    pass


@dataclass(frozen=True)
class ManualAdvance:
    wait: Callable[[WaitEvent, Cancellation, Callable[[], bool]], bool]


type Advance = AutomaticAdvance | ManualAdvance


def play(
    plan: Plan,
    sink: PrintSink,
    cancel: Cancellation,
    *,
    realtime: bool = False,
    deadline: Deadline = NoDeadline(),
    advance: Advance = AutomaticAdvance(),
) -> RunResult:
    start = time.monotonic_ns()
    virtual_ms = waited_ms = executed = 0
    prints: list[PrintEvent] = []

    def check() -> bool:
        elapsed = (time.monotonic_ns() - start) / 1_000_000 if realtime else virtual_ms
        if isinstance(deadline, CancelAfter) and elapsed >= deadline.ms:
            cancel.cancel()
        return not cancel.cancelled

    for event in plan.events:
        if not check():
            break
        if isinstance(event, PrintEvent):
            if not sink.print(event, cancel):
                cancel.cancel()
                break
            prints.append(event)
            executed += 1
            continue
        if isinstance(advance, ManualAdvance) and event.checkpoint:
            if not advance.wait(event, cancel, check):
                cancel.cancel()
            if not check():
                break
            executed += 1
            continue
        remaining = event.ms
        end = time.monotonic_ns() + event.ms * 1_000_000
        while remaining > 0 and check():
            duration = min(25, remaining)
            if realtime:
                time.sleep(duration / 1000)
                remaining = max(0, math.ceil((end - time.monotonic_ns()) / 1_000_000))
            else:
                virtual_ms += duration
                remaining -= duration
            waited_ms += duration
        if not check():
            break
        executed += 1
    completed = (
        not cancel.cancelled
        and executed == len(plan.events)
        and bool(prints)
        and prints[-1].kind == "result"
    )
    elapsed = (time.monotonic_ns() - start) / 1_000_000
    if completed:
        return CompletedRun(tuple(prints), waited_ms, elapsed, plan.result)
    return CancelledRun(tuple(prints), waited_ms, elapsed)


class FileTransport(ByteTransport):
    def __init__(self, path: str) -> None:
        self.handle = open(path, "xb")

    def write(self, data: bytes, cancel: Cancellation) -> bool:
        offset = 0
        while offset < len(data) and not cancel.cancelled:
            written = self.handle.write(data[offset:])
            if written <= 0:
                raise OSError("ファイルへの書き込みが進みません")
            offset += written
        self.handle.flush()
        return offset == len(data)

    def close(self) -> None:
        self.handle.close()
