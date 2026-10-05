# レシートおみくじ

「大……丈夫！」や「あた……たかいお茶でも」と期待を引き伸ばし、最後の確定欄で運勢を発表するレシート用おみくじです。大きな文字を一文字ずつ縦に印刷し、左右の二重線と上下の飾りで囲みます。

## 起動する

Python3.12以上とuvを用意し、このディレクトリで実行します。

```sh
make setup
make dry-run
```

`make dry-run`は端末へ一文字ずつ表示し、演出の停止位置でEnterを待ちます。Enterで印字済みの位置から続行し、Ctrl-Cや入力の終了で取り消します。
直接起動するときは、`receipt-omikuji`の後に`dry-run`、`generate`、`print`などのコマンドを指定します。

即時に全文を確認する場合は、次のコマンドを使います。

```sh
uv run receipt-omikuji dry-run --layout vertical --seed 朝
uv run receipt-omikuji generate --seed 朝 --format json
```

同じseed、設定、語彙では同じ文面と運勢を再現できます。運勢は開始前に選び、演出の回数を変えても同じseedの結果を保ちます。

## 印刷する

`make start`は「POS58 Printer USB」（VID`0x0416`、PID`0x5011`）のinterface0、Bulk OUT endpoint`0x03`へ送ります。印字幅は384dotsです。libusbと日本語フォントが必要で、Macでは`/opt/homebrew/lib/libusb-1.0.dylib`と`/Library/Fonts/Arial Unicode.ttf`を使います。

```sh
make start ARGS="--sample"
make start
```

`--sample`では「あいうえお」と外枠を印刷します。通常の印刷は停止位置でEnterを待ち、文字間は自動で進みます。末尾に紙送りを加え、最後の文字を出口から見える位置まで送ります。

別の機器を使う場合は、確認したUSB設定で`PRINTER_ARGS`を変更します。フォント、文字間隔、末尾の余白は`ARGS`で指定できます。

```sh
make start PRINTER_ARGS="--vid 0x1234 --pid 0x5678 --interface 0 --endpoint 0x01" \
  ARGS="--font /path/to/Japanese.ttf --character-ms 80 --tail-feed-lines 4"
```

同じVID/PIDの機器が複数ある場合は送信先を選べないため停止します。カットや初期化は送信しません。取消は以後の送信を止めますが、プリンターへ送信済みのデータは排出される場合があります。

## 文面と紙量を調整する

`make dry-run`と`make start`の`ARGS`に、次の設定を指定できます。

| 設定 | 既定値 | 指定範囲 |
| --- | --- | --- |
| `--rounds` | 4回 | 0〜10回 |
| `--max-wait-ms` | 20000ms | 0〜60000ms |
| `--max-lines` | 2400単位 | 1〜4800単位 |
| `--character-ms` | 80ms | 0〜1000ms |
| `--tail-feed-lines` | 4単位 | 0〜32単位 |

縦書きの紙量は32dotsを一単位として数え、文字、外枠、末尾の余白を含めます。時間と紙量が足りない場合は肩透かしの回数を減らし、最終発表を優先します。最終発表も収まらない紙量は印刷前に拒否します。時間予算は演出の待ち時間で、通信や機器の印刷時間、Enterを待つ時間は別です。

```sh
make dry-run ARGS="--seed 朝 --rounds 6 --max-wait-ms 25000"
```

語彙を追加する場合は、`src/receipt_omikuji/data/語彙.json`の`dodge`に固有の`id`、`prefix`、`continuation`、`join`を登録します。たとえば「大」と「丈夫！」を「大丈夫」としてつなぎます。新しい接頭辞には`group`の導入文も必要です。直近三回の肩透かしと、直前と同じ接頭辞は避けます。

## ファイルへ出力する

PNGとESC/POSファイルは、印刷と同じ画像変換を使います。出力先には新しいファイル名を指定してください。

```sh
mkdir -p build
uv run receipt-omikuji generate --layout vertical --rounds 0 --format png --out build/receipt.png
uv run receipt-omikuji export --sample --out build/sample.bin
```

`export`はファイルを作成します。USBへ送信する場合は`print`または`make start`を使います。

## 検査する

Gauge本体とPython plugin0.5.1を用意して実行します。

```sh
make check
```

Ruffのformatとlint、mypyの型検査、Gaugeの受け入れ試験を実行します。GaugeはPythonの実装を直接呼び、文面の継続、予算、取消、画像の送信形式、接続の解放を検査します。USB送信は代替の送信先で検査し、実際の印字は`make start ARGS="--sample"`で確認します。
