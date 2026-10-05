#!/usr/bin/env php
<?php
declare(strict_types=1);

require __DIR__ . '/../src/bootstrap.php';

use ReceiptOmikuji\Cancellation;
use ReceiptOmikuji\FileTransport;
use ReceiptOmikuji\Generator;
use ReceiptOmikuji\Paper;
use ReceiptOmikuji\Playback;
use ReceiptOmikuji\Preview;
use ReceiptOmikuji\PrintSink;
use ReceiptOmikuji\RasterEncoder;
use ReceiptOmikuji\SpeechGraph;
use ReceiptOmikuji\TerminalSink;
use ReceiptOmikuji\UsbTransport;
use ReceiptOmikuji\VerticalEncoder;
use function ReceiptOmikuji\printEscpos;

function options(array $arguments): array
{
    $values = [];
    $flags = ['realtime', 'allow-print', 'help', 'step', 'sample'];
    $allowed = ['seed', 'rounds', 'max-wait-ms', 'max-lines', 'columns', 'result', 'format',
        'out', 'font', 'width-dots', 'expose-lines', 'cancel-after-ms', 'vid', 'pid', 'interface', 'endpoint', 'usb-library',
        'layout', 'character-ms', 'tail-feed-lines'];
    for ($i = 0; $i < count($arguments); $i++) {
        if (!str_starts_with($arguments[$i], '--')) {
            throw new InvalidArgumentException('Expected --option');
        }
        $pair = explode('=', substr($arguments[$i], 2), 2);
        $key = $pair[0];
        if (isset($values[$key])) {
            throw new InvalidArgumentException('Duplicate option: ' . $key);
        }
        if (in_array($key, $flags, true)) {
            if (count($pair) !== 1) {
                throw new InvalidArgumentException('Flag does not take a value');
            }
            $values[$key] = true;
        } elseif (in_array($key, $allowed, true)) {
            $value = $pair[1] ?? $arguments[++$i] ?? null;
            if ($value === null || str_starts_with($value, '--')) {
                throw new InvalidArgumentException('Option needs a value: ' . $key);
            }
            $values[$key] = $value;
        } else {
            throw new InvalidArgumentException('Unknown option: ' . $key);
        }
    }
    return $values;
}

function integer(string $value): int
{
    if (!preg_match('/^(0|[1-9][0-9]{0,8})$/D', $value)) {
        throw new InvalidArgumentException('Expected non-negative integer');
    }
    return (int) $value;
}

function output(string $content, ?string $path): void
{
    if ($path === null) {
        fwrite(STDOUT, $content);
        return;
    }
    $file = new FileTransport($path);
    try {
        $file->write($content, new Cancellation());
    } finally {
        $file->close();
    }
}

function waitForEnter(array $event, Cancellation $cancel, callable $check): bool
{
    fwrite(STDERR, "停止中。Enterで続行、Ctrl-Cで取消\n");
    while ($check()) {
        if (feof(STDIN)) {
            return false;
        }
        $read = [STDIN];
        $write = $except = [];
        $ready = @stream_select($read, $write, $except, 0, 25000);
        if ($ready === false) {
            if (!$check()) {
                return false;
            }
            throw new RuntimeException('Cannot wait for terminal input');
        }
        if ($ready > 0) {
            $input = fread(STDIN, 256);
            if ($input === false) {
                throw new RuntimeException('Cannot read terminal input');
            }
            if (str_contains($input, "\n")) {
                return !$cancel->isCancelled();
            }
        }
    }
    return false;
}

function samplePlan(array $settings): array
{
    $settings += ['layout' => 'vertical', 'width_dots' => 384, 'character_ms' => 80, 'tail_feed_lines' => 4];
    if ($settings['layout'] !== 'vertical' || $settings['width_dots'] < 192 || $settings['width_dots'] > 832
        || $settings['width_dots'] % 8 !== 0 || $settings['character_ms'] < 0 || $settings['character_ms'] > 1000
        || $settings['tail_feed_lines'] < 0 || $settings['tail_feed_lines'] > 32) {
        throw new InvalidArgumentException('Invalid vertical sample settings');
    }
    $text = 'あいうえお';
    $events = Paper::verticalEvents([
        ['type' => 'print', 'node' => 'sample', 'kind' => 'result', 'text' => $text, 'blank_lines' => 0, 'lines' => 1],
    ], $settings['width_dots'], $settings['character_ms'], $settings['tail_feed_lines']);
    return ['settings' => $settings, 'events' => $events, 'result' => $text];
}

try {
    $command = $argv[1] ?? 'demo';
    $opts = options(array_slice($argv, 2));
    if ($command === '--help' || isset($opts['help'])) {
        output(<<<'HELP'
カスのおみくじ（PHP 8.2+）
  php bin/omikuji.php demo [--realtime] [--cancel-after-ms 1000] [--format json]
  php bin/omikuji.php generate [--format text|json|html|png] [--out new-file]
  php bin/omikuji.php graph [--out new-file.dot]
  php bin/omikuji.php export --out new-file.bin [--font Japanese.ttf] [--width-dots 384]
  php -d ffi.enable=1 bin/omikuji.php print --allow-print --vid 0xVVVV --pid 0xPPPP --interface 0 --endpoint 0x01 --font Japanese.ttf
共通: --seed 42 --rounds 4 --max-wait-ms 20000 --max-lines 80 --columns 32
      --expose-lines 0（ヘッドから出口までの距離を補う空行数、0〜8）
      --result 大吉（デモなどで結果を指定する場合）
縦書き: --layout vertical --character-ms 80 --tail-feed-lines 4
        --max-lines 2400（32dots単位の紙量、上限4800）
試し刷り: printまたはexportに --sample（縦書きの「あいうえお」、外縁と末尾の余白付き）
手動進行: demoまたはprintに --step（停止位置でEnter、Ctrl-Cで取消）
exportはファイルだけを作成。printだけがUSBへ接続。カット・初期化は送らない。

HELP, null);
        exit(0);
    }
    if (!in_array($command, ['demo', 'generate', 'graph', 'export', 'print'], true)) {
        throw new InvalidArgumentException('Unknown command');
    }
    if (isset($opts['step']) && !in_array($command, ['demo', 'print'], true)) {
        throw new InvalidArgumentException('Step playback requires demo or print');
    }
    if (isset($opts['sample']) && !in_array($command, ['print', 'export'], true)) {
        throw new InvalidArgumentException('Sample requires print or export');
    }
    $settings = ['seed' => (string) ($opts['seed'] ?? '42')];
    foreach (['rounds', 'max-wait-ms', 'max-lines', 'columns', 'expose-lines', 'width-dots', 'character-ms', 'tail-feed-lines'] as $key) {
        if (isset($opts[$key])) {
            $settings[str_replace('-', '_', $key)] = integer($opts[$key]);
        }
    }
    if (isset($opts['result'])) {
        $settings['result'] = $opts['result'];
    }
    if (isset($opts['layout'])) {
        $settings['layout'] = $opts['layout'];
    }
    $graph = new SpeechGraph(__DIR__ . '/../data/語彙.json');
    if ($command === 'graph') {
        output($graph->dot(), $opts['out'] ?? null);
        exit(0);
    }
    $plan = isset($opts['sample']) ? samplePlan($settings) : (new Generator($graph))->generate($settings);
    $format = $opts['format'] ?? 'text';
    if (!in_array($format, ['text', 'json', 'html', 'png'], true)) {
        throw new InvalidArgumentException('Unknown format');
    }
    $cancel = new Cancellation();
    $cancelAfter = isset($opts['cancel-after-ms']) ? integer($opts['cancel-after-ms']) : null;
    if (function_exists('pcntl_async_signals')) {
        pcntl_async_signals(true);
        pcntl_signal(SIGINT, static fn () => $cancel->cancel());
        pcntl_signal(SIGTERM, static fn () => $cancel->cancel());
    }
    $advance = null;
    if (isset($opts['step'])) {
        if (!stream_set_blocking(STDIN, false)) {
            throw new RuntimeException('Cannot configure terminal input');
        }
        $advance = waitForEnter(...);
    }
    if ($command === 'generate') {
        if ($format === 'png') {
            if (!isset($opts['out'])) {
                throw new InvalidArgumentException('PNG requires --out');
            }
            $font = $opts['font'] ?? (getenv('OMIKUJI_FONT') ?: '/Library/Fonts/Arial Unicode.ttf');
            if ($plan['settings']['layout'] === 'vertical') {
                $encoder = new VerticalEncoder($font, $plan['settings']['width_dots']);
                $image = imagecreatetruecolor($plan['settings']['width_dots'], $plan['paper_dots']);
                if ($image === false) {
                    throw new RuntimeException('Receipt image allocation failed');
                }
                $y = 0;
                foreach ($plan['events'] as $event) {
                    if ($event['type'] !== 'print') { continue; }
                    $cell = $encoder->image($event);
                    imagecopy($image, $cell, 0, $y, 0, 0, imagesx($cell), imagesy($cell));
                    $y += imagesy($cell);
                    unset($cell);
                }
            } else {
                $encoder = new RasterEncoder($font, $plan['settings']['columns'], $plan['settings']['width_dots']);
                $content = '';
                foreach ($plan['events'] as $event) {
                    if ($event['type'] === 'print') {
                        $content .= $event['text'] . "\n" . str_repeat("\n", $event['blank_lines']);
                    }
                }
                $image = $encoder->image(['text' => substr($content, 0, -1), 'blank_lines' => 0]);
            }
            ob_start();
            try {
                if (!imagepng($image)) {
                    throw new RuntimeException('PNG encoding failed');
                }
                $png = ob_get_contents();
            } finally {
                ob_end_clean();
                unset($image);
            }
            output($png, $opts['out']);
            exit(0);
        }
        $content = match ($format) {
            'json' => json_encode($plan, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR) . "\n",
            'html' => Preview::html($plan),
            default => implode('', array_map(static fn ($e) => $e['type'] === 'print'
                ? $e['text'] . "\n" . str_repeat("\n", $e['blank_lines']) : '', $plan['events'])),
        };
        output($content, $opts['out'] ?? null);
        exit(0);
    }
    if ($command === 'demo') {
        if (!in_array($format, ['text', 'json'], true)) {
            throw new InvalidArgumentException('Use generate for HTML');
        }
        $sink = $format === 'json' ? new class implements PrintSink {
            public function print(array $event, Cancellation $cancel): bool { return !$cancel->isCancelled(); }
        } : new TerminalSink();
        $run = (new Playback())->run($plan, $sink, $cancel, isset($opts['realtime']) || $advance !== null, $cancelAfter, $advance);
        if ($format === 'json') {
            output(json_encode($run, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR) . "\n", $opts['out'] ?? null);
        } else {
            fwrite(STDERR, sprintf("%s · seed=%s · %d行 · 予定の待ち=%dms\n",
                $run['status'], $settings['seed'], $run['paper_lines'], $plan['wait_ms']));
        }
        exit($run['status'] === 'completed' ? 0 : 130);
    }
    $usbConfig = [];
    if ($command === 'print') {
        if (!isset($opts['allow-print'])) {
            throw new InvalidArgumentException('print requires --allow-print');
        }
        foreach (['vid', 'pid', 'endpoint'] as $key) {
            if (!isset($opts[$key]) || !preg_match('/^0x[0-9a-fA-F]{1,4}$/D', $opts[$key])) {
                throw new InvalidArgumentException('USB hex option required: ' . $key);
            }
            $usbConfig[$key] = hexdec(substr($opts[$key], 2));
        }
        if (isset($opts['interface'])) {
            $usbConfig['interface'] = integer($opts['interface']);
        }
        if (isset($opts['usb-library'])) {
            $usbConfig['library'] = $opts['usb-library'];
        }
        UsbTransport::validate($usbConfig);
    } elseif (!isset($opts['out'])) {
        throw new InvalidArgumentException('export requires --out');
    }
    $font = $opts['font'] ?? getenv('OMIKUJI_FONT') ?: '/Library/Fonts/Arial Unicode.ttf';
    $encoder = $plan['settings']['layout'] === 'vertical'
        ? new VerticalEncoder($font, $plan['settings']['width_dots'])
        : new RasterEncoder($font, $plan['settings']['columns'], $plan['settings']['width_dots']);
    $connect = $command === 'print'
        ? static fn () => new UsbTransport($usbConfig)
        : static fn () => new FileTransport($opts['out']);
    $run = printEscpos($plan, $encoder, $connect, $cancel,
        $command === 'print' || isset($opts['realtime']), $cancelAfter, $advance);
    fwrite(STDERR, sprintf("%s · %d行 · %d dots/行\n", $run['status'], $run['paper_lines'], RasterEncoder::ROW_DOTS));
    exit($run['status'] === 'completed' ? 0 : 130);
} catch (Throwable $error) {
    fwrite(STDERR, $error::class . ': ' . $error->getMessage() . "\n");
    exit(2);
}
