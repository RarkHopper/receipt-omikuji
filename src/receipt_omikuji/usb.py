from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

import usb.backend.libusb1
import usb.core
import usb.util

from receipt_omikuji.playback import ByteTransport, Cancellation


@dataclass(frozen=True)
class UsbConfig:
    vid: int
    pid: int
    interface: int
    endpoint: int
    library: str

    def __post_init__(self) -> None:
        for name, value, lower, upper in (
            ("vid", self.vid, 1, 65535),
            ("pid", self.pid, 1, 65535),
            ("interface", self.interface, 0, 255),
            ("endpoint", self.endpoint, 1, 15),
        ):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"USBの{name}は{lower}〜{upper}を指定してください")


class UsbTransport(ByteTransport):
    def __init__(self, config: UsbConfig) -> None:
        self.config = config
        self.closed = False
        self.claimed = False
        backend = usb.backend.libusb1.get_backend(find_library=lambda _: config.library)
        if backend is None:
            raise OSError(
                "libusbを読み込めません。--usb-libraryで場所を指定してください"
            )
        devices = list(
            cast(
                Iterable[usb.core.Device],
                usb.core.find(
                    find_all=True,
                    idVendor=config.vid,
                    idProduct=config.pid,
                    backend=backend,
                ),
            )
        )
        if len(devices) != 1:
            raise OSError(
                f"同じVID/PIDの機器が{len(devices)}台あります。1台にしてください"
            )
        self.device: usb.core.Device = devices[0]
        try:
            endpoints = [
                (interface.bInterfaceNumber, endpoint)
                for interface in self.device.get_active_configuration()
                for endpoint in interface
                if endpoint.bEndpointAddress == config.endpoint
            ]
            # 指定したinterfaceを別の送信先と取り違えないよう、PyUSBがアドレスから一意に選べることを確認する。
            if (
                len(endpoints) != 1
                or endpoints[0][0] != config.interface
                or endpoints[0][1].bmAttributes & 3 != 2
            ):
                raise ValueError("指定interfaceに一意のBulk OUT endpointがありません")
            usb.util.claim_interface(self.device, config.interface)
            self.claimed = True
        except BaseException:
            self.close()
            raise

    def write(self, data: bytes, cancel: Cancellation) -> bool:
        if self.closed:
            raise OSError("USB接続は閉じています")
        for offset in range(0, len(data), 4096):
            if cancel.cancelled:
                return False
            chunk = data[offset : offset + 4096]
            written = cast(
                int, self.device.write(self.config.endpoint, chunk, timeout=1000)
            )
            # timeoutで転送済みのbyte数が返ることがある。
            # 何文字まで印字されたか分からないため、残りを自動で送らない。
            if written != len(chunk):
                raise OSError(
                    f"USB転送が途中で止まりました: {written}/{len(chunk)}bytes"
                )
        return True

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.claimed:
                self.claimed = False
                usb.util.release_interface(self.device, self.config.interface)
        finally:
            usb.util.dispose_resources(self.device)
