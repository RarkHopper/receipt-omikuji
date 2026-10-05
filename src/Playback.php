<?php
declare(strict_types=1);

namespace ReceiptOmikuji;

use RuntimeException;

final class Cancellation
{
    private bool $cancelled = false;

    public function cancel(): void { $this->cancelled = true; }
    public function isCancelled(): bool { return $this->cancelled; }
}

interface PrintSink
{
    public function print(array $event, Cancellation $cancel): bool;
}

final class TerminalSink implements PrintSink
{
    public function print(array $event, Cancellation $cancel): bool
    {
        if (!$cancel->isCancelled()) {
            fwrite(STDOUT, $event['text'] . "\n" . str_repeat("\n", $event['blank_lines']));
            fflush(STDOUT);
            return true;
        }
        return false;
    }
}

final class Playback
{
    public function run(array $plan, PrintSink $sink, Cancellation $cancel,
        bool $realtime = false, ?int $cancelAfterMs = null, ?callable $advance = null): array
    {
        $start = hrtime(true);
        $virtualMs = 0;
        $prints = [];
        $waitMs = 0;
        $executed = 0;
        $check = static function () use ($cancel, $realtime, $start, &$virtualMs, $cancelAfterMs): bool {
            $elapsed = $realtime ? (hrtime(true) - $start) / 1000000 : $virtualMs;
            if ($cancelAfterMs !== null && $elapsed >= $cancelAfterMs) {
                $cancel->cancel();
            }
            return !$cancel->isCancelled();
        };
        foreach ($plan['events'] as $event) {
            if (!$check()) {
                break;
            }
            if ($event['type'] === 'print') {
                if (!$sink->print($event, $cancel)) {
                    $cancel->cancel();
                    break;
                }
                $prints[] = $event;
                $executed++;
                continue;
            }
            if ($advance !== null && ($event['checkpoint'] ?? true)) {
                if (!$advance($event, $cancel, $check)) {
                    $cancel->cancel();
                }
                if (!$check()) {
                    break;
                }
                $executed++;
                continue;
            }
            $duration = $event['ms'];
            $end = hrtime(true) + $duration * 1000000;
            $remaining = $duration;
            while ($remaining > 0 && $check()) {
                $slice = min(25, $remaining);
                if ($realtime) {
                    usleep($slice * 1000);
                    $remaining = max(0, (int) ceil(($end - hrtime(true)) / 1000000));
                } else {
                    $virtualMs += $slice;
                    $remaining -= $slice;
                }
                $waitMs += $slice;
            }
            if (!$check()) {
                break;
            }
            $executed++;
        }
        $completed = !$cancel->isCancelled() && $executed === count($plan['events'])
            && ($prints[array_key_last($prints)]['kind'] ?? '') === 'result';
        return ['status' => $completed ? 'completed' : 'cancelled', 'prints' => $prints,
            'paper_lines' => array_sum(array_column($prints, 'lines')),
            'waited_ms' => $waitMs, 'elapsed_ms' => (hrtime(true) - $start) / 1000000,
            'result' => $completed ? $plan['result'] : null];
    }
}

interface ByteTransport
{
    public function write(string $bytes, Cancellation $cancel): bool;
    public function close(): void;
}

final class FileTransport implements ByteTransport
{
    private mixed $handle;

    public function __construct(string $path)
    {
        $this->handle = fopen($path, 'xb');
        if ($this->handle === false) {
            throw new RuntimeException('Cannot create output; existing paths are protected');
        }
    }

    public function write(string $bytes, Cancellation $cancel): bool
    {
        $offset = 0;
        while ($offset < strlen($bytes) && !$cancel->isCancelled()) {
            $written = fwrite($this->handle, substr($bytes, $offset));
            if ($written === false || $written === 0) {
                throw new RuntimeException('Output write failed');
            }
            $offset += $written;
        }
        if (!fflush($this->handle)) {
            throw new RuntimeException('Output flush failed');
        }
        return $offset === strlen($bytes);
    }

    public function close(): void
    {
        if (is_resource($this->handle)) {
            fclose($this->handle);
        }
    }
}
