<?php
declare(strict_types=1);

namespace ReceiptOmikuji;

use InvalidArgumentException;
use RuntimeException;

interface Encoder
{
    public function encode(array $event): string;
}

final class AsciiEncoder implements Encoder
{
    public function encode(array $event): string
    {
        if (preg_match('/[^\x20-\x7e\n]/', $event['text'])) {
            throw new InvalidArgumentException('ASCII encoder requires ASCII; use raster for Japanese');
        }
        return $event['text'] . "\n" . str_repeat("\n", $event['blank_lines']);
    }
}

final class RasterEncoder implements Encoder
{
    public const ROW_DOTS = 32;
    private const STRIP_DOTS = 24;

    public function __construct(private readonly string $font, private readonly int $columns = 32,
        private readonly int $widthDots = 384)
    {
        if (!function_exists('imagettftext')) {
            throw new RuntimeException('Raster requires PHP GD with FreeType');
        }
        if (!is_file($font) || !is_readable($font)) {
            throw new InvalidArgumentException('Readable Japanese font required');
        }
        if ($columns < 16 || $columns > 80 || $widthDots < 192 || $widthDots > 832
            || $widthDots % 8 !== 0 || $widthDots < $columns * 8) {
            throw new InvalidArgumentException('Invalid raster width/columns');
        }
    }

    public function image(array $event): mixed
    {
        $lines = explode("\n", $event['text']);
        $height = (count($lines) + $event['blank_lines']) * self::ROW_DOTS;
        $image = imagecreatetruecolor($this->widthDots, $height);
        if ($image === false) {
            throw new RuntimeException('Raster image allocation failed');
        }
        $white = imagecolorallocate($image, 255, 255, 255);
        $black = imagecolorallocate($image, 0, 0, 0);
        imagefill($image, 0, 0, $white);
        $cell = $this->widthDots / $this->columns;
        $fontSize = min(18, $cell * 1.3);
        foreach ($lines as $row => $line) {
            $column = 0;
            foreach (preg_split('//u', $line, -1, PREG_SPLIT_NO_EMPTY) ?: [] as $char) {
                $charWidth = Paper::width($char);
                $box = imagettfbbox($fontSize, 0, $this->font, $char);
                if ($box === false) {
                    throw new RuntimeException('Font cannot render text');
                }
                $glyphWidth = max($box[0], $box[2], $box[4], $box[6]) - min($box[0], $box[2], $box[4], $box[6]);
                if ($glyphWidth > $charWidth * $cell) {
                    throw new InvalidArgumentException('Glyph too wide for configured column cell');
                }
                $x = (int) round($column * $cell - min($box[0], $box[2], $box[4], $box[6]));
                if (imagettftext($image, $fontSize, 0, $x, $row * self::ROW_DOTS + 25, $black, $this->font, $char) === false) {
                    throw new RuntimeException('Font rendering failed');
                }
                $column += $charWidth;
            }
        }
        return $image;
    }

    public static function bitmap(mixed $image): string
    {
        $width = imagesx($image);
        $height = imagesy($image);
        if ($width % 8 !== 0 || $height > 2047) {
            throw new InvalidArgumentException('Raster dimensions exceed supported frame');
        }
        // POS58で見出しに続く画像が文字として出るため、連続印刷を確認した形式を使う
        // https://github.com/klirichek/zj-58/blob/master/rastertozj.c
        $frames = '';
        $bytes = '';
        $stripHeight = 0;
        for ($y = 0; $y < $height; $y++) {
            for ($x = 0; $x < $width; $x += 8) {
                $byte = 0;
                for ($bit = 0; $bit < 8; $bit++) {
                    $rgb = imagecolorat($image, $x + $bit, $y);
                    $luma = (($rgb >> 16) & 255) * 299 + (($rgb >> 8) & 255) * 587 + ($rgb & 255) * 114;
                    if ($luma < 160000) {
                        $byte |= 0x80 >> $bit;
                    }
                }
                $bytes .= chr($byte);
            }
            $stripHeight++;
            if ($stripHeight === self::STRIP_DOTS || $y === $height - 1) {
                $frames .= "\x1d\x76\x30\x00" . pack('vv', intdiv($width, 8), $stripHeight)
                    . $bytes . "\x1b\x4a\x00";
                $bytes = '';
                $stripHeight = 0;
            }
        }
        return $frames;
    }

    public function encode(array $event): string
    {
        return self::bitmap($this->image($event));
    }
}

final class VerticalEncoder implements Encoder
{
    public function __construct(private readonly string $font, private readonly int $widthDots = 384)
    {
        if (!function_exists('imagettftext')) {
            throw new RuntimeException('Raster requires PHP GD with FreeType');
        }
        if (!is_file($font) || !is_readable($font)) {
            throw new InvalidArgumentException('Readable Japanese font required');
        }
        if ($widthDots < 192 || $widthDots > 832 || $widthDots % 8 !== 0) {
            throw new InvalidArgumentException('Invalid raster width');
        }
    }

    private function text(mixed $image, string $text, float $size, int $x, int $y, int $black): void
    {
        $box = imagettfbbox($size, 0, $this->font, $text);
        if ($box === false) {
            throw new RuntimeException('Font cannot render text');
        }
        $left = min($box[0], $box[2], $box[4], $box[6]);
        $right = max($box[0], $box[2], $box[4], $box[6]);
        $top = min($box[1], $box[3], $box[5], $box[7]);
        $bottom = max($box[1], $box[3], $box[5], $box[7]);
        if (imagettftext($image, $size, 0, (int) round($x - ($left + $right) / 2),
            (int) round($y - ($top + $bottom) / 2), $black, $this->font, $text) === false) {
            throw new RuntimeException('Font rendering failed');
        }
    }

    private function punctuation(mixed $image, string $char, int $height): bool
    {
        $corner = str_contains('、。，．', $char);
        $opening = str_contains('「『【〔（(［[｛{〈《', $char);
        $closing = str_contains('」』】〕）)］]｝}〉》', $char);
        $rotate = $opening || $closing || str_contains('ー―〜～：；', $char);
        if (!$corner && !$rotate) {
            return false;
        }
        $em = intdiv($this->widthDots, 2);
        $glyph = imagecreatetruecolor($em, $em);
        if ($glyph === false) {
            throw new RuntimeException('Glyph image allocation failed');
        }
        $white = imagecolorallocate($glyph, 255, 255, 255);
        $black = imagecolorallocate($glyph, 0, 0, 0);
        imagefill($glyph, 0, 0, $white);
        $this->text($glyph, $char, $em * 0.75, intdiv($em, 2), intdiv($em, 2), $black);
        if ($rotate) {
            $glyph = imagerotate($glyph, -90, $white);
            if ($glyph === false) {
                throw new RuntimeException('Glyph rotation failed');
            }
        }
        // imagettfbboxは字送りの余白も含むため、配置には描画済みの範囲を使う。
        $left = $top = $em;
        $right = $bottom = -1;
        for ($y = 0; $y < $em; $y++) {
            for ($x = 0; $x < $em; $x++) {
                if ((imagecolorat($glyph, $x, $y) & 0xffffff) !== 0xffffff) {
                    $left = min($left, $x); $right = max($right, $x);
                    $top = min($top, $y); $bottom = max($bottom, $y);
                }
            }
        }
        if ($right < $left) {
            return true;
        }
        $width = $right - $left + 1;
        $inkHeight = $bottom - $top + 1;
        $cellTop = intdiv($height - $em, 2);
        $x = $corner ? intdiv($this->widthDots + $em, 2) - $width : intdiv($this->widthDots - $width, 2);
        $y = $corner || $closing ? $cellTop : ($opening ? $cellTop + $em - $inkHeight : intdiv($height - $inkHeight, 2));
        imagecopy($image, $glyph, $x, $y, $left, $top, $width, $inkHeight);
        return true;
    }

    public function image(array $event): mixed
    {
        $height = $event['lines'] * Paper::ROW_DOTS;
        if ($height < 1 || $height > 2047) {
            throw new InvalidArgumentException('Invalid vertical frame height');
        }
        $image = imagecreatetruecolor($this->widthDots, $height);
        if ($image === false) {
            throw new RuntimeException('Raster image allocation failed');
        }
        $white = imagecolorallocate($image, 255, 255, 255);
        $black = imagecolorallocate($image, 0, 0, 0);
        imagefill($image, 0, 0, $white);
        $decoration = $event['decoration'] ?? null;
        if ($decoration === 'feed') {
            return $image;
        }
        $top = $decoration === 'header' ? 12 : 0;
        $bottom = $decoration === 'footer' ? $height - 13 : $height - 1;
        foreach ([10, $this->widthDots - 11] as $x) {
            imagesetthickness($image, 2);
            imageline($image, $x, $top, $x, $bottom, $black);
        }
        imagesetthickness($image, 1);
        foreach ([17, $this->widthDots - 18] as $x) {
            imageline($image, $x, $top, $x, $bottom, $black);
        }
        $center = intdiv($this->widthDots, 2);
        if ($decoration === 'header' || $decoration === 'footer') {
            $edge = $decoration === 'header' ? $top : $bottom;
            imageline($image, 10, $edge, $this->widthDots - 11, $edge, $black);
            $inner = $decoration === 'header' ? $edge + 7 : $edge - 7;
            imageline($image, 17, $inner, $this->widthDots - 18, $inner, $black);
            $flowerY = $decoration === 'header' ? 40 : 25;
            foreach ([[0, -8], [8, 0], [0, 8], [-8, 0]] as [$dx, $dy]) {
                imageellipse($image, $center + $dx, $flowerY + $dy, 16, 16, $black);
            }
            imagefilledellipse($image, $center, $flowerY, 6, 6, $black);
            if ($decoration === 'header') {
                $this->text($image, '御神籤', 20, $center, 75, $black);
            }
            return $image;
        }
        $chars = preg_split('//u', $event['text'], -1, PREG_SPLIT_NO_EMPTY) ?: [];
        if (count($chars) !== 1) {
            throw new InvalidArgumentException('Vertical frame requires one character');
        }
        if ($event['text'] === '.' || $event['text'] === '…') {
            foreach ($event['text'] === '.' ? [16] : [12, 32, 52] as $y) {
                imagefilledellipse($image, $center, $y, 7, 7, $black);
            }
            return $image;
        }
        if ($this->punctuation($image, $event['text'], $height)) {
            return $image;
        }
        $size = intdiv($this->widthDots, 2) * 0.75;
        $box = imagettfbbox($size, 0, $this->font, $event['text']);
        if ($box === false) {
            throw new RuntimeException('Font cannot render text');
        }
        $glyphWidth = max($box[0], $box[2], $box[4], $box[6]) - min($box[0], $box[2], $box[4], $box[6]);
        $glyphHeight = max($box[1], $box[3], $box[5], $box[7]) - min($box[1], $box[3], $box[5], $box[7]);
        $size *= min(1, intdiv($this->widthDots, 2) / max(1, $glyphWidth), ($height - 16) / max(1, $glyphHeight));
        $this->text($image, $event['text'], $size, $center, intdiv($height, 2), $black);
        return $image;
    }

    public function encode(array $event): string
    {
        return RasterEncoder::bitmap($this->image($event));
    }
}

final class EscposSink implements PrintSink
{
    public static function prepare(array $plan, Encoder $encoder): array
    {
        $frames = [];
        // 全文を先に変換し、フォントや幅のエラーで半端なレシートを出さない。
        foreach ($plan['events'] as $event) {
            if ($event['type'] === 'print') {
                $frames[json_encode($event, JSON_THROW_ON_ERROR)] = $encoder->encode($event);
            }
        }
        return $frames;
    }

    public function __construct(private readonly array $frames, private readonly ByteTransport $transport) {}

    public function print(array $event, Cancellation $cancel): bool
    {
        if ($cancel->isCancelled()) {
            return false;
        }
        $frame = $this->frames[json_encode($event, JSON_THROW_ON_ERROR)] ?? null;
        if ($frame === null) {
            throw new InvalidArgumentException('Print event was not prepared');
        }
        return $this->transport->write($frame, $cancel);
    }
}

function printEscpos(array $plan, Encoder $encoder, callable $connect, Cancellation $cancel,
    bool $realtime = false, ?int $cancelAfterMs = null, ?callable $advance = null): array
{
    if ($cancelAfterMs === 0) {
        $cancel->cancel();
    }
    // 接続前に全文を変換し、描画エラーでは機器や出力ファイルを開かない。
    $frames = EscposSink::prepare($plan, $encoder);
    if ($cancel->isCancelled()) {
        return ['status' => 'cancelled', 'prints' => [], 'paper_lines' => 0,
            'waited_ms' => 0, 'elapsed_ms' => 0, 'result' => null];
    }
    $transport = $connect();
    try {
        return (new Playback())->run($plan, new EscposSink($frames, $transport), $cancel,
            $realtime, $cancelAfterMs, $advance);
    } finally {
        $transport->close();
    }
}
