<?php
declare(strict_types=1);

namespace ReceiptOmikuji;

use FFI;
use InvalidArgumentException;
use RuntimeException;
use Throwable;

final class UsbTransport implements ByteTransport
{
    private FFI $usb;
    private mixed $context = null;
    private mixed $handle = null;
    private bool $claimed = false;

    public static function validate(array $config): void
    {
        foreach (['vid', 'pid', 'interface', 'endpoint'] as $key) {
            if (!isset($config[$key]) || !is_int($config[$key])) {
                throw new InvalidArgumentException('Explicit USB configuration required: ' . $key);
            }
        }
        if ($config['vid'] < 1 || $config['vid'] > 65535 || $config['pid'] < 1 || $config['pid'] > 65535
            || $config['interface'] < 0 || $config['interface'] > 255
            || $config['endpoint'] < 1 || $config['endpoint'] > 15) {
            throw new InvalidArgumentException('Invalid USB OUT configuration');
        }
    }

    public function __construct(private readonly array $config)
    {
        self::validate($config);
        if (!class_exists(FFI::class)) {
            throw new RuntimeException('USB requires PHP FFI and libusb');
        }
        $library = $config['library'] ?? (PHP_OS_FAMILY === 'Darwin'
            ? '/opt/homebrew/lib/libusb-1.0.dylib' : 'libusb-1.0.so.0');
        $this->usb = FFI::cdef(<<<'C'
typedef struct libusb_context libusb_context;
typedef struct libusb_device libusb_device;
typedef struct libusb_device_handle libusb_device_handle;
struct libusb_device_descriptor {
    unsigned char bLength, bDescriptorType;
    unsigned short bcdUSB;
    unsigned char bDeviceClass, bDeviceSubClass, bDeviceProtocol, bMaxPacketSize0;
    unsigned short idVendor, idProduct, bcdDevice;
    unsigned char iManufacturer, iProduct, iSerialNumber, bNumConfigurations;
};
int libusb_init(libusb_context **ctx);
void libusb_exit(libusb_context *ctx);
long libusb_get_device_list(libusb_context *ctx, libusb_device ***list);
void libusb_free_device_list(libusb_device **list, int unref_devices);
int libusb_get_device_descriptor(libusb_device *dev, struct libusb_device_descriptor *desc);
int libusb_open(libusb_device *dev, libusb_device_handle **handle);
int libusb_claim_interface(libusb_device_handle *dev, int interface_number);
int libusb_release_interface(libusb_device_handle *dev, int interface_number);
void libusb_close(libusb_device_handle *dev);
int libusb_bulk_transfer(libusb_device_handle *dev, unsigned char endpoint,
    unsigned char *data, int length, int *transferred, unsigned int timeout);
const char *libusb_error_name(int code);
C, $library);
        $context = $this->usb->new('libusb_context *');
        $this->check($this->usb->libusb_init(FFI::addr($context)));
        $this->context = $context;
        try {
            $list = $this->usb->new('libusb_device **');
            $count = $this->usb->libusb_get_device_list($context, FFI::addr($list));
            $this->check($count < 0 ? (int) $count : 0);
            try {
                $matches = [];
                $desc = $this->usb->new('struct libusb_device_descriptor');
                for ($i = 0; $i < $count; $i++) {
                    if ($this->usb->libusb_get_device_descriptor($list[$i], FFI::addr($desc)) === 0
                        && $desc->idVendor === $config['vid'] && $desc->idProduct === $config['pid']) {
                        $matches[] = $list[$i];
                    }
                }
                if (count($matches) !== 1) {
                    throw new RuntimeException('Exactly one matching USB device is required');
                }
                $handle = $this->usb->new('libusb_device_handle *');
                $this->check($this->usb->libusb_open($matches[0], FFI::addr($handle)));
                $this->handle = $handle;
                $this->check($this->usb->libusb_claim_interface($handle, $config['interface']));
                $this->claimed = true;
            } finally {
                $this->usb->libusb_free_device_list($list, 1);
            }
        } catch (Throwable $error) {
            $this->close();
            throw $error;
        }
    }

    private function check(int $code): void
    {
        if ($code < 0) {
            $name = $this->usb->libusb_error_name($code);
            throw new RuntimeException('libusb: ' . (is_string($name) ? $name : FFI::string($name)));
        }
    }

    public function write(string $bytes, Cancellation $cancel): bool
    {
        $offset = 0;
        while ($offset < strlen($bytes) && !$cancel->isCancelled()) {
            $part = substr($bytes, $offset, 4096);
            $buffer = $this->usb->new('unsigned char[' . strlen($part) . ']');
            FFI::memcpy($buffer, $part, strlen($part));
            $transferred = $this->usb->new('int');
            if ($cancel->isCancelled()) {
                return false;
            }
            $code = $this->usb->libusb_bulk_transfer($this->handle, $this->config['endpoint'],
                $buffer, strlen($part), FFI::addr($transferred), 1000);
            // 失敗時は再送しない。部分転送の二重印字を避ける。
            $this->check($code);
            if ($transferred->cdata <= 0) {
                throw new RuntimeException('USB transfer made no progress');
            }
            $offset += $transferred->cdata;
        }
        return $offset === strlen($bytes);
    }

    public function close(): void
    {
        if ($this->handle !== null) {
            if ($this->claimed) {
                $this->usb->libusb_release_interface($this->handle, $this->config['interface']);
                $this->claimed = false;
            }
            $this->usb->libusb_close($this->handle);
            $this->handle = null;
        }
        if ($this->context !== null) {
            $this->usb->libusb_exit($this->context);
            $this->context = null;
        }
    }
}
