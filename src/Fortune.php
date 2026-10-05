<?php
declare(strict_types=1);

namespace ReceiptOmikuji;

use InvalidArgumentException;
use RuntimeException;

final class SeedRandom
{
    private int $counter = 0;

    public function __construct(private readonly string $seed) {}

    public function choose(array $values): mixed
    {
        if ($values === []) {
            throw new InvalidArgumentException('Empty choice');
        }
        $hash = hash('sha256', $this->seed . ':' . $this->counter++);
        return $values[(int) hexdec(substr($hash, 0, 8)) % count($values)];
    }
}

final class Paper
{
    public const ROW_DOTS = 32;

    public static function verticalCells(string $text, int $widthDots): array
    {
        $cells = [];
        foreach (preg_split('//u', str_replace("\n", '', $text), -1, PREG_SPLIT_NO_EMPTY) ?: [] as $char) {
            $height = match ($char) {
                '.' => 32,
                '…' => 64,
                default => (int) (ceil((intdiv($widthDots, 2) + 16) / self::ROW_DOTS) * self::ROW_DOTS),
            };
            $cells[] = ['text' => $char, 'lines' => intdiv($height, self::ROW_DOTS)];
        }
        return $cells;
    }

    public static function verticalEvents(array $events, int $widthDots, int $characterMs, int $tailLines): array
    {
        $decoration = static fn (string $name, int $lines, string $kind): array => [
            'type' => 'print', 'node' => 'frame:' . $name, 'kind' => $kind,
            'text' => '', 'blank_lines' => 0, 'lines' => $lines, 'decoration' => $name,
        ];
        $out = [$decoration('header', 3, 'opening')];
        foreach ($events as $event) {
            if ($event['type'] === 'wait') {
                $out[] = $event + ['checkpoint' => true];
                continue;
            }
            $cells = self::verticalCells($event['text'], $widthDots);
            foreach ($cells as $i => $cell) {
                $out[] = ['type' => 'print', 'node' => $event['node'], 'kind' => $event['kind'],
                    'text' => $cell['text'], 'blank_lines' => 0, 'lines' => $cell['lines'], 'glyph' => true];
                if ($i < count($cells) - 1 && $characterMs > 0) {
                    $out[] = ['type' => 'wait', 'node' => $event['node'], 'ms' => $characterMs, 'checkpoint' => false];
                }
            }
        }
        $out[] = $decoration('footer', 2, 'result');
        if ($tailLines > 0) {
            $out[] = $decoration('feed', $tailLines, 'result');
        }
        return $out;
    }

    public static function width(string $char): int
    {
        return preg_match('/^[\x20-\x7e]$/D', $char) === 1 ? 1 : 2;
    }

    public static function lines(string $text, int $columns): array
    {
        $lines = [];
        foreach (explode("\n", $text) as $line) {
            $current = '';
            $width = 0;
            foreach (preg_split('//u', $line, -1, PREG_SPLIT_NO_EMPTY) ?: [] as $char) {
                $size = self::width($char);
                if ($width + $size > $columns) {
                    $carry = '';
                    if (str_contains('、。！？：；）」』】〕〉》ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー', $char)) {
                        $previous = preg_split('//u', $current, -1, PREG_SPLIT_NO_EMPTY);
                        do {
                            $carry = array_pop($previous) . $carry;
                            $first = preg_split('//u', $carry, -1, PREG_SPLIT_NO_EMPTY)[0];
                        } while ($previous !== [] && str_contains('、。！？：；）」』】〕〉》ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー', $first));
                        $current = implode('', $previous);
                    }
                    $lines[] = $current;
                    $current = $carry;
                    $width = array_sum(array_map(self::width(...), preg_split('//u', $carry, -1, PREG_SPLIT_NO_EMPTY)));
                }
                $current .= $char;
                $width += $size;
            }
            $lines[] = $current;
        }
        return $lines;
    }
}

final class SpeechGraph
{
    public readonly array $lexicon;
    public readonly array $nodes;
    public readonly array $edges;

    public function __construct(string $path)
    {
        $lexicon = json_decode(file_get_contents($path) ?: '', true, 512, JSON_THROW_ON_ERROR);
        $nodes = [
            'start' => ['kind' => 'opening', 'text' => $lexicon['opening']],
            'gate' => ['kind' => 'choice'],
            'reannounce' => ['kind' => 'reannounce', 'text' => $lexicon['reannounce']],
            'final_announce' => ['kind' => 'final_announce', 'text' => $lexicon['final_announce']],
            'final_prefix' => ['kind' => 'final_prefix'],
            'result' => ['kind' => 'result'],
        ];
        $edges = [
            'start' => ['gate'], 'gate' => ['final_announce'],
            'reannounce' => ['gate'], 'final_announce' => ['final_prefix'],
            'final_prefix' => ['result'], 'result' => [],
        ];
        foreach ($lexicon['group'] as $prefix => $group) {
            $lead = 'lead:' . $prefix;
            $node = 'prefix:' . $prefix;
            $nodes[$lead] = ['kind' => 'lead', 'text' => $group['lead']];
            $nodes[$node] = ['kind' => 'prefix', 'prefix' => $prefix];
            $edges['gate'][] = $lead;
            $edges[$lead] = [$node];
            $edges[$node] = [];
        }
        $ids = [];
        foreach ($lexicon['dodge'] as $dodge) {
            $id = $dodge['id'];
            $prefix = $dodge['prefix'];
            if (isset($ids[$id]) || !isset($lexicon['group'][$prefix])
                || !str_starts_with($prefix . $dodge['continuation'], $dodge['join'])
                || !str_starts_with($dodge['join'], $prefix)
                || $dodge['join'] === $prefix) {
                throw new InvalidArgumentException('Invalid dodge: ' . $id);
            }
            $ids[$id] = true;
            $node = 'dodge:' . $id;
            $nodes[$node] = ['kind' => 'dodge'] + $dodge;
            $edges['prefix:' . $prefix][] = $node;
            $edges[$node] = ['reannounce'];
        }
        foreach ($nodes as $node) {
            foreach ((array) ($node['text'] ?? $node['continuation'] ?? []) as $text) {
                if (!is_string($text) || !preg_match('//u', $text)
                    || preg_match('/[\x00-\x1f\x7f]/', $text) || str_contains($text, '【確定】')) {
                    throw new InvalidArgumentException('Invalid speech text');
                }
                // 単独の「吉」「凶」は接頭辞。完成した運勢を含む継続句は採用しない。
                if (($node['kind'] ?? '') !== 'prefix') {
                    foreach ($lexicon['result'] as $result) {
                        if (str_contains($text, $result)) {
                            throw new InvalidArgumentException('Fortune in non-final speech');
                        }
                    }
                }
            }
        }
        $this->lexicon = $lexicon;
        $this->nodes = $nodes;
        $this->edges = $edges;
    }

    public function dot(): string
    {
        $out = ["digraph omikuji {", '  rankdir=LR;'];
        foreach ($this->nodes as $id => $node) {
            $label = $id . (isset($node['continuation']) ? '\n' . $node['continuation'] : '');
            $out[] = '  ' . json_encode($id) . ' [label=' . json_encode($label, JSON_UNESCAPED_UNICODE) . '];';
        }
        foreach ($this->edges as $from => $targets) {
            foreach ($targets as $to) {
                $out[] = '  ' . json_encode($from) . ' -> ' . json_encode($to) . ';';
            }
        }
        return implode("\n", [...$out, '}']) . "\n";
    }
}

final class Generator
{
    public function __construct(private readonly SpeechGraph $graph) {}

    public function generate(array $settings = []): array
    {
        $layout = $settings['layout'] ?? 'horizontal';
        if (!in_array($layout, ['horizontal', 'vertical'], true)) {
            throw new InvalidArgumentException('Invalid layout');
        }
        $vertical = $layout === 'vertical';
        $settings += ['seed' => '42', 'rounds' => 4, 'max_wait_ms' => 20000,
            'max_lines' => $vertical ? 2400 : 80, 'columns' => 32, 'expose_lines' => 0, 'result' => null,
            'layout' => $layout, 'width_dots' => 384, 'character_ms' => $vertical ? 80 : 0,
            'tail_feed_lines' => $vertical ? 4 : 0];
        foreach (['rounds' => [0, 10], 'max_wait_ms' => [0, 60000],
            'max_lines' => [1, $vertical ? 4800 : 120], 'columns' => [16, 80], 'expose_lines' => [0, 8],
            'width_dots' => [192, 832], 'character_ms' => [0, 1000], 'tail_feed_lines' => [0, 32]] as $key => [$min, $max]) {
            if (!is_int($settings[$key]) || $settings[$key] < $min || $settings[$key] > $max) {
                throw new InvalidArgumentException('Out of range: ' . $key);
            }
        }
        if (!is_string($settings['seed']) || strlen($settings['seed']) > 256) {
            throw new InvalidArgumentException('Invalid seed');
        }
        if ($settings['width_dots'] % 8 !== 0 || ($vertical && $settings['expose_lines'] !== 0)) {
            throw new InvalidArgumentException('Invalid layout width or exposure');
        }
        $random = new SeedRandom($settings['seed']);
        $resultRandom = new SeedRandom($settings['seed'] . ':fortune');
        $result = $settings['result'] ?? $resultRandom->choose($this->graph->lexicon['result']);
        if (!in_array($result, $this->graph->lexicon['result'], true)) {
            throw new InvalidArgumentException('Invalid result');
        }
        $columns = $settings['columns'];
        $exposeLines = $settings['expose_lines'];
        $widthDots = $settings['width_dots'];
        $tailLines = $settings['tail_feed_lines'];
        $make = static function (string $node, string $kind, string $text, int $gap = 0) use ($columns, $exposeLines, $vertical, $widthDots, $tailLines): array {
            if ($vertical) {
                $cells = Paper::verticalCells($text, $widthDots);
                $decorationLines = ($kind === 'opening' ? 3 : 0) + ($kind === 'result' ? 2 + $tailLines : 0);
                return ['type' => 'print', 'node' => $node, 'kind' => $kind,
                    'text' => str_replace("\n", '', $text), 'blank_lines' => 0,
                    'lines' => array_sum(array_column($cells, 'lines')) + $decorationLines];
            }
            if (in_array($kind, ['prefix', 'final_prefix', 'result'], true)) {
                $gap += $exposeLines;
            }
            if ($kind === 'result') {
                $gap += $tailLines;
            }
            $lines = Paper::lines($text, $columns);
            return ['type' => 'print', 'node' => $node, 'kind' => $kind,
                'text' => implode("\n", $lines), 'blank_lines' => $gap,
                'lines' => count($lines) + $gap];
        };
        $wait = static fn (string $node, int $ms): array => ['type' => 'wait', 'node' => $node, 'ms' => $ms];
        $prefix = preg_split('//u', $result, -1, PREG_SPLIT_NO_EMPTY)[0];
        $suffix = substr($result, strlen($prefix));
        $final = [
            $make('final_announce', 'final_announce', $random->choose($this->graph->lexicon['final_announce'])),
            $make('final_prefix', 'final_prefix', $prefix . $random->choose($this->graph->lexicon['dots'])),
            $wait('final_prefix', min($settings['max_wait_ms'], $random->choose([1600, 2000, 2400]))),
            $make('result', 'result', ($suffix !== '' ? $suffix . '！' . "\n" : '') . '【確定】今日の運勢：' . $result),
        ];
        $events = [$make('start', 'opening', $random->choose($this->graph->lexicon['opening']))];
        $countLines = static fn (array $items): int => array_sum(array_column($items, 'lines'));
        $characterMs = $settings['max_wait_ms'] === 0 ? 0 : $settings['character_ms'];
        $countWait = static function (array $items) use ($vertical, $widthDots, &$characterMs): int {
            $total = array_sum(array_column($items, 'ms'));
            if ($vertical) {
                foreach ($items as $item) {
                    if ($item['type'] === 'print') {
                        $total += max(0, count(Paper::verticalCells($item['text'], $widthDots)) - 1) * $characterMs;
                    }
                }
            }
            return $total;
        };
        if ($vertical) {
            $mandatoryGaps = 0;
            foreach ([...$events, ...$final] as $item) {
                if ($item['type'] === 'print') {
                    $mandatoryGaps += max(0, count(Paper::verticalCells($item['text'], $widthDots)) - 1);
                }
            }
            $remainingWait = $settings['max_wait_ms'] - array_sum(array_column($final, 'ms'));
            $characterMs = min($characterMs, intdiv($remainingWait, max(1, $mandatoryGaps)));
            $settings['character_ms'] = $characterMs;
        }
        $reservedLines = $countLines($final);
        $reservedWait = $countWait($final);
        if ($countLines($events) + $reservedLines > $settings['max_lines']) {
            throw new InvalidArgumentException('Paper budget cannot hold final result');
        }
        $recent = [];
        $lastPrefix = null;
        $recentSpeech = [];
        $chosen = [];
        $path = ['start', 'gate'];
        for ($round = 0; $round < $settings['rounds']; $round++) {
            $eligible = array_values(array_filter($this->graph->lexicon['dodge'],
                static fn (array $d): bool => !in_array($d['id'], $recent, true) && $d['prefix'] !== $lastPrefix));
            $accepted = null;
            while ($eligible !== []) {
                $dodge = $random->choose($eligible);
                $eligible = array_values(array_filter($eligible, static fn ($d) => $d['id'] !== $dodge['id']));
                $p = $dodge['prefix'];
                $leadId = 'lead:' . $p;
                $prefixId = 'prefix:' . $p;
                $dodgeId = 'dodge:' . $dodge['id'];
                $pickSpeech = static function (array $values) use ($random, $recentSpeech): string {
                    $fresh = array_values(array_diff($values, $recentSpeech));
                    return $random->choose($fresh !== [] ? $fresh : $values);
                };
                $lead = $pickSpeech($this->graph->nodes[$leadId]['text']);
                $again = $pickSpeech($this->graph->nodes['reannounce']['text']);
                $noWait = $settings['max_wait_ms'] === 0;
                $cycle = [
                    $make($leadId, 'lead', $lead),
                    $wait($leadId, $noWait ? 0 : $random->choose([250, 400, 600])),
                    $make($prefixId, 'prefix', $p . $random->choose($this->graph->lexicon['dots'])),
                    $wait($prefixId, $noWait ? 0 : $random->choose([1200, 1800, 2200, 2800])),
                    $make($dodgeId, 'dodge', $dodge['continuation'], $random->choose([0, 0, 1])),
                    $wait($dodgeId, $noWait ? 0 : $random->choose([350, 600, 900])),
                    $make('reannounce', 'reannounce', $again),
                ];
                if ($countLines([...$events, ...$cycle]) + $reservedLines <= $settings['max_lines']
                    && $countWait([...$events, ...$cycle]) + $reservedWait <= $settings['max_wait_ms']) {
                    $accepted = [$dodge, $cycle, $lead, $again];
                    break;
                }
            }
            if ($accepted === null) {
                break;
            }
            [$dodge, $cycle, $lead, $again] = $accepted;
            $events = [...$events, ...$cycle];
            $path = [...$path, 'lead:' . $dodge['prefix'], 'prefix:' . $dodge['prefix'],
                'dodge:' . $dodge['id'], 'reannounce', 'gate'];
            $chosen[] = $dodge['id'];
            $recent = array_slice([...$recent, $dodge['id']], -3);
            $recentSpeech = array_slice([...$recentSpeech, $lead, $again], -6);
            $lastPrefix = $dodge['prefix'];
        }
        $events = [...$events, ...$final];
        if ($vertical) {
            $events = Paper::verticalEvents($events, $widthDots, $characterMs, $tailLines);
        }
        $path = [...$path, 'final_announce', 'final_prefix', 'result'];
        for ($i = 1; $i < count($path); $i++) {
            if (!in_array($path[$i], $this->graph->edges[$path[$i - 1]], true)) {
                throw new RuntimeException('Invalid graph transition');
            }
        }
        return ['schema' => 1, 'lexicon_version' => $this->graph->lexicon['version'],
            'settings' => $settings, 'result' => $result, 'events' => $events, 'path' => $path,
            'dodge_ids' => $chosen, 'rounds' => count($chosen),
            'paper_lines' => $countLines($events), 'paper_dots' => $countLines($events) * Paper::ROW_DOTS,
            'wait_ms' => $countWait($events)];
    }
}
