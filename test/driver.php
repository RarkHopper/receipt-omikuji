<?php
declare(strict_types=1);

require __DIR__ . '/../src/bootstrap.php';

use ReceiptOmikuji\AsciiEncoder;
use ReceiptOmikuji\ByteTransport;
use ReceiptOmikuji\Cancellation;
use ReceiptOmikuji\Generator;
use ReceiptOmikuji\Playback;
use ReceiptOmikuji\PrintSink;
use ReceiptOmikuji\RasterEncoder;
use ReceiptOmikuji\SpeechGraph;
use ReceiptOmikuji\VerticalEncoder;
use function ReceiptOmikuji\printEscpos;

final class 観測先 implements PrintSink
{
    public array $events = [];

    public function __construct(private readonly ?string $cancelBefore = null, private readonly ?string $cancelAfter = null) {}

    public function print(array $event, Cancellation $cancel): bool
    {
        if ($event['kind'] === $this->cancelBefore) {
            $cancel->cancel();
        }
        if ($cancel->isCancelled()) {
            return false;
        }
        $this->events[] = $event;
        if ($event['kind'] === $this->cancelAfter) {
            $cancel->cancel();
        }
        return true;
    }
}

function ラスタ画像を読む(string $bytes): array
{
    $offset = 0;
    $width = null;
    $heights = [];
    $payload = '';
    while ($offset < strlen($bytes)) {
        if (substr($bytes, $offset, 4) !== "\x1d\x76\x30\x00") {
            throw new RuntimeException('Invalid raster command');
        }
        $header = unpack('vwidth/vheight', substr($bytes, $offset + 4, 4));
        $width ??= $header['width'];
        if ($header['width'] !== $width || $header['height'] < 1) {
            throw new RuntimeException('Invalid raster dimensions');
        }
        $size = $width * $header['height'];
        $data = substr($bytes, $offset + 8, $size);
        if (strlen($data) !== $size) {
            throw new RuntimeException('Truncated raster payload');
        }
        $offset += 8 + $size;
        if (substr($bytes, $offset, 3) !== "\x1b\x4a\x00") {
            throw new RuntimeException('Missing raster flush command');
        }
        $offset += 3;
        $heights[] = $header['height'];
        $payload .= $data;
    }
    return ['command' => '1d763000', 'width_bytes' => $width,
        'height_dots' => array_sum($heights), 'strip_heights' => $heights,
        'payload_bytes' => strlen($payload), 'nonwhite' => trim($payload, "\0") !== '',
        'payload' => $payload];
}

$input = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
$graph = new SpeechGraph(__DIR__ . '/../data/語彙.json');
$generator = new Generator($graph);
try {
    switch ($input['action']) {
        case 'plans':
            $output = array_map(fn ($settings) => $generator->generate($settings), $input['settings']);
            break;
        case 'graph':
            $output = ['nodes' => $graph->nodes, 'edges' => $graph->edges, 'lexicon' => $graph->lexicon];
            break;
        case 'play':
            $plan = $generator->generate($input['settings'] ?? []);
            $cancel = new Cancellation();
            if ($input['cancel_pre'] ?? false) {
                $cancel->cancel();
            }
            $sink = new 観測先($input['cancel_before'] ?? null, $input['cancel_after'] ?? null);
            $checkpoints = [];
            $advance = ($input['manual'] ?? false)
                ? static function (array $event) use (&$checkpoints, $sink, $input): bool {
                    $checkpoints[] = ['node' => $event['node'], 'printed' => count($sink->events)];
                    return count($checkpoints) !== ($input['stop_at_checkpoint'] ?? null);
                } : null;
            $run = (new Playback())->run($plan, $sink, $cancel, $input['realtime'] ?? false, $input['cancel_ms'] ?? null, $advance);
            $observed = $sink->events;
            if ($cancel->isCancelled()) {
                (new Playback())->run($plan, $sink, $cancel);
            }
            $output = ['plan' => $plan, 'run' => $run, 'observed' => $observed,
                'resume_delta' => count($sink->events) - count($observed), 'checkpoints' => $checkpoints];
            break;
        case 'wire':
            $cancel = new Cancellation();
            if ($input['cancel_pre'] ?? false) { $cancel->cancel(); }
            $transport = new class($input['cancel_after_writes'] ?? null, $input['fail_write'] ?? false) implements ByteTransport {
                public array $writes = [];
                public bool $closed = false;
                public function __construct(private readonly ?int $limit, private readonly bool $failWrite) {}
                public function write(string $bytes, Cancellation $cancel): bool
                {
                    if ($cancel->isCancelled()) { return false; }
                    if ($this->failWrite) { throw new RuntimeException('Transfer failed'); }
                    $this->writes[] = $bytes;
                    if ($this->limit === count($this->writes)) { $cancel->cancel(); }
                    return true;
                }
                public function close(): void { $this->closed = true; }
            };
            $events = [
                ['type' => 'print', 'node' => 'prefix', 'kind' => 'prefix', 'text' => 'DAI.....', 'blank_lines' => 0, 'lines' => 1],
                ['type' => 'wait', 'node' => 'prefix', 'ms' => 1200],
                ['type' => 'print', 'node' => 'dodge', 'kind' => 'dodge', 'text' => 'JOBU!', 'blank_lines' => 1, 'lines' => 2],
                ['type' => 'print', 'node' => 'result', 'kind' => 'result', 'text' => 'RESULT: DAIKICHI', 'blank_lines' => 0, 'lines' => 1],
            ];
            if ($input['fail_render'] ?? false) { $events[3]['text'] = 'あ'; }
            $plan = ['events' => $events, 'result' => '大吉'];
            $opened = 0;
            $connect = static function () use ($transport, &$opened): ByteTransport {
                $opened++;
                return $transport;
            };
            $run = $error = null;
            try {
                $run = printEscpos($plan, new AsciiEncoder(), $connect, $cancel);
            } catch (Throwable $failure) {
                $error = $failure::class;
            }
            $output = ['run' => $run, 'writes' => $transport->writes, 'opened' => $opened,
                'closed' => $transport->closed, 'error' => $error];
            break;
        case 'raster':
            $plan = $generator->generate($input['settings'] ?? []);
            $encoder = $plan['settings']['layout'] === 'vertical'
                ? new VerticalEncoder($input['font'], $plan['settings']['width_dots'])
                : new RasterEncoder($input['font'], $plan['settings']['columns'], $input['width'] ?? 384);
            $frames = [];
            foreach ($plan['events'] as $event) {
                if ($event['type'] !== 'print') { continue; }
                $bytes = $encoder->encode($event);
                $decoded = ラスタ画像を読む($bytes);
                unset($decoded['payload']);
                $frames[] = $decoded + ['paper_lines' => $event['lines'], 'kind' => $event['kind'],
                    'glyph' => $event['glyph'] ?? false, 'decoration' => $event['decoration'] ?? null];
            }
            $output = ['plan' => $plan, 'frames' => $frames];
            break;
        case 'bitmap':
            $image = imagecreatetruecolor(16, 49);
            imagefill($image, 0, 0, 0xffffff);
            foreach ([[0, 0], [7, 23], [8, 24], [15, 47], [3, 48]] as [$x, $y]) {
                imagesetpixel($image, $x, $y, 0);
            }
            $output = ラスタ画像を読む(RasterEncoder::bitmap($image));
            $output['payload_hex'] = bin2hex($output['payload']);
            unset($output['payload']);
            break;
        default:
            throw new InvalidArgumentException('Unknown test action');
    }
    echo json_encode($output, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
} catch (Throwable $error) {
    echo json_encode(['error' => $error::class, 'message' => $error->getMessage()], JSON_UNESCAPED_UNICODE);
    exit(2);
}
