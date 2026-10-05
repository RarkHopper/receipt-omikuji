<?php
declare(strict_types=1);

namespace ReceiptOmikuji;

final class Preview
{
    public static function html(array $plan): string
    {
        $json = json_encode($plan, JSON_UNESCAPED_UNICODE | JSON_HEX_TAG | JSON_HEX_AMP | JSON_THROW_ON_ERROR);
        $template = file_get_contents(__DIR__ . '/../template/preview.html');
        return str_replace('__PLAN__', $json, $template);
    }
}
