# カスのおみくじ

運勢が出ると思わせて、「大……丈夫！」や「あた……たかいお茶でも」のように別の言葉へ逃げるレシート用おみくじです。予告と肩透かしを繰り返し、最後の確定欄で運勢を一つ発表します。

## 起動とプレビュー

PHP 8.2以上で、文面生成とドライランを実行できます。Composerの依存導入は不要です。
GD、FFI、pcntlはPHPの拡張で、それぞれラスタ描画、USB転送、信号による取消に使います。

```sh
cd /Users/rarkhopper/dev/hiu/omikuji
php bin/omikuji.php demo --seed 42
php bin/omikuji.php demo --seed 42 --realtime
php bin/omikuji.php demo --seed 42 --realtime --cancel-after-ms 3500
```

`demo`は標準出力へ文面を出します。即時実行では待ちを計算し、`--realtime`では実際に待ちます。Ctrl-Cで以後の印字を取り消せます。取消時の終了コードは130、設定や出力のエラーは2です。信号による取消にはPHPのpcntl拡張を使います。

```sh
mkdir -p build
php bin/omikuji.php generate --seed 朝 --format html --out build/preview.html
open build/preview.html
php bin/omikuji.php generate --seed 朝 --format json --out build/plan.json
php bin/omikuji.php graph --out build/graph.dot
```

HTMLは通信せずに開けるプレビューです。実時間再生、即時表示、取消、やり直しを選べます。紙は下へ伸び、最新の部分を追います。JSONは文面と待ちの予定、DOTは発話グラフを出します。既存の出力ファイルは保護するため、再生成時は別名を指定してください。

## 縦書きとEnterでの進行

縦書きにするには、`--layout vertical`を指定します。文字は正立させます。印字幅の半分を目安にした一文字を紙の送り方向へ並べ、接頭辞、点、続きを一列につなぎます。「あた……」から別の言葉へ続き、左右の二重線と上下の飾りが文面を囲みます。

```sh
php bin/omikuji.php demo --seed 朝 --layout vertical --step
php bin/omikuji.php generate --seed 朝 --layout vertical --format html --out build/vertical-preview.html
php bin/omikuji.php generate --seed 朝 --layout vertical --format png --out build/vertical-receipt.png
```

`demo`と`print`に`--step`を付けると、演出の停止位置でEnterを待ちます。Enterで続行します。既に印字した位置から進み、文字間は自動で流れます。間隔は`--character-ms`で0〜1000msに調整でき、縦書きの既定値は80msです。Ctrl-Cや入力の終了では、その後の印字を取り消します。HTMLでも「Enterで進める」から開始し、停止位置でEnterまたは続行ボタンを押せます。

紙量は32dotsを一単位に数えます。縦書きの予算は既定で2400、上限は4800です。文字画像だけでなく外縁と末尾の余白も予算に数え、文字間の待ちを時間予算に含めて、どちらかが不足する場合は肩透かしの回数を減らします。必須の発表が待ち予算に収まるよう、文字間隔を短くする場合もあります。全文には長い紙が必要です。Enterを待つ時間は操作者が決めます。

末尾には`--tail-feed-lines`で指定した余白を付けます。縦書きの既定値は4、範囲は0〜32で、一単位は32dotsです。最後の文字が出口から見える量へ実機で調整してください。接頭辞と続きを連ねるため、縦書きでは`--expose-lines`を0にします。

印刷コマンドにも`--layout vertical --step`を追加します。機種に合うUSB識別子、転送先、印字幅は引き続き指定してください。

## 引き伸ばしを調整する

```sh
php bin/omikuji.php demo --seed 123 --rounds 6 --max-wait-ms 25000 --max-lines 80 --columns 32
php bin/omikuji.php generate --seed 123 --result 大吉
make example
```

`--rounds`は肩透かしの回数で0〜10、`--max-wait-ms`は待ちの合計で0〜60000、横書きの`--max-lines`は紙量で1〜120です。横書きの既定値は四回、二十秒、八十行。紙量には折り返しと空行を含めます。予算が足りないときは回数を減らし、最後の発表を優先します。最終発表も収まらない設定は拒否します。待ち予算は演出の時間で、通信や印字の処理時間は含みません。

`--columns`は一行の幅で16〜80、既定値は32です。ASCIIは一桁、日本語などは二桁で数えます。語彙はこの幅の規則に合う文字を使います。絵文字や結合文字を含む自由文の組版は対象外です。`--expose-lines`は接頭辞と結果の後に送る空行数で0〜8、既定値は0です。ヘッドから出口までの距離がある場合に調整し、この空行も紙量予算に含めます。

同じseed、設定、語彙では同じ予定になります。運勢の抽選は演出の抽選と独立しているため、同じseedで回数を変えても運勢は同じです。`--result`はデモなどで運勢を指定するための設定です。

## 日本語と印刷設定

日本語はホストで画像へ変換し、ESC/POSのラスタ命令 `GS v 0` を出力します。PHP GDのFreeType対応と、日本語を含むフォントが必要です。Macの既定フォントは `/Library/Fonts/Arial Unicode.ttf`。別の環境では `--font` または `OMIKUJI_FONT` を指定してください。フォントはプロジェクトに配布していません。

画像は最大24dotsの高さに分け、各部分の後に`ESC J 0`を送ります。「POS58 Printer USB」では、見出しをまとめて送ると次の文字画像が記号として印字されました。この送り方は、[ZJ-58/XP-58向けドライバー](https://github.com/klirichek/zj-58/blob/master/rastertozj.c)と実機での比較印刷を根拠に採用しています。画像の分割によって文字の大きさや紙量は変わりません。

```sh
php bin/omikuji.php generate --seed 朝 --format png --font /path/to/Japanese.ttf --out build/receipt.png
php bin/omikuji.php export --seed 朝 --font /path/to/Japanese.ttf --width-dots 384 --out build/receipt.bin
```

PNGはESC/POSと同じラスタ描画を使い、フォントと幅を確認できます。`export`はファイルへ出力します。プリンターへは接続しません。一行は32 dotsで、384 dotsなら32桁が既定です。576 dotsを使う場合は `--columns 48 --width-dots 576` を合わせて指定してください。用紙幅と実際の印字可能幅は別なので、機種の仕様を確認してください。

文字出力用のadapterはASCIIに限定しています。日本語のプリンターフォントを使う場合は、機種の漢字モード、Shift-JIS/CP932、コードページ、対応グリフを確認したうえでadapterを追加します。UTF-8やCP932を無条件に送る方法は採用していません。

USBへ直接送る場合はPHP FFIとlibusb 1.0が必要です。現在のMacには `/opt/homebrew/lib/libusb-1.0.dylib` があります。別の配置なら `--usb-library` で指定できます。以下は機種とendpointを確認した後に使う印刷コマンドの形です。
プロジェクトのセットアップではPHP拡張やlibusbをインストールしません。既存のランタイムを使い、不足する場合は印刷機能の準備が必要と報告します。

```sh
php -d ffi.enable=1 bin/omikuji.php print --allow-print \
  --vid 0xVVVV --pid 0xPPPP --interface 0 --endpoint 0x01 \
  --font /path/to/Japanese.ttf --width-dots 384 --columns 32
```

VID/PID、インターフェイス番号、Bulk OUT endpointを明示します。同じVID/PIDの機器が複数あるときは拒否します。OSやドライバーがインターフェイスを占有している場合も、構成変更やドライバーの切り離しをせずエラーで止めます。カット、ドロワー、逆送り、初期化、USB構成変更は送りません。`print`は実時間で各印字単位を送り、待ちを再現します。全文をキューへ投入するCUPSなどでは、この時間演出が保たれるとは限りません。

接続中の「POS58 Printer USB」では、以下の設定で試し刷りできます。`--sample`は「あいうえお」を縦書きで一文字ずつ出し、外縁と末尾の余白を付けます。通常の印刷と同じ画像変換、再生、USB送信を使います。

```sh
php -d ffi.enable=1 bin/omikuji.php print --sample --allow-print \
  --vid 0x0416 --pid 0x5011 --interface 0 --endpoint 0x03
```

`export --sample --out build/sample.bin`で同じ送信データをファイルへ出力できます。試し刷りの幅、文字間隔、末尾の余白は`--width-dots`、`--character-ms`、`--tail-feed-lines`で調整します。

取消後は新しいデータを送りません。待ちは25ms以内の区切りで確認し、USB転送にも一秒のtimeoutを設けています。送信済みのデータや実行中の転送はプリンター側に残り得るため、物理的な排出の即時停止は保証できません。

## 検査

Gauge本体とPython plugin 0.5.1を用意し、`make setup`でこのディレクトリの`.venv/`にGauge用のPythonパッケージとRuffを導入してください。

```sh
make setup
make check
```

`make check`はPHPの構文検査、Pythonの未使用import・未定義名・import順序などの検査、PythonからPHP本体とCLIを呼ぶGauge試験を実行します。lintだけなら`make lint`、Gauge試験だけなら`make test`を使ってください。

受け入れ試験は、グラフの接続、接頭辞の継続、直近のネタの抑制、seed再現、回数・時間・紙量の予算、結果の一意性、取消境界、行バッファ、ラスタフレーム、プリンター未接続のCLIを確認します。日本語ラスタの試験でもGDとフォントを使います。結果とログは `build/` に出ます。

## 語彙を追加する

`data/語彙.json` の `dodge` に、固有の `id`、印字する `prefix`、続きの `continuation`、つながる語の `join` を登録します。たとえば `prefix` が「大」、`continuation` が「丈夫！」、`join` が「大丈夫」です。既存の接頭辞を使えばグラフの分岐が増えます。新しい接頭辞は `group` に、それと自然につながる導入文も追加してください。

直近三回のネタと、直前と同じ接頭辞を候補から外します。導入と再予告も最近の表現を避けます。運勢の確定を意味する表現は肩透かしに入れず、確定欄は最後の発表が担当します。文法や言葉遊びの自然さは、語彙を追加した人がプレビューで確認してください。

## PHPを選んだ根拠と未検証事項

2026-10-04に確認した主要ライブラリ [mike42/escpos-php](https://packagist.org/packages/mike42/escpos-php) はv5.0、PHP 8.2以上を要求します。[ライブラリの対応表](https://github.com/mike42/escpos-php)ではmacOSの直接USBは未試験で、CUPSには対応しています。画像印字と文字出力の改行要件も説明されています。

読みやすさを優先してPHPを選びました。生成部分は標準機能だけで動かし、ラスタ出力をGD、USB転送を[PHP FFI](https://www.php.net/manual/en/book.ffi.php)と[libusbのBulk転送](https://libusb.sourceforge.io/api-1.0/group__libusb__syncio.html)で分けています。今回必要な画像命令だけを出力するため、Composer依存は導入していません。

「POS58 Printer USB」（VID`0x0416`、PID`0x5011`）で、インターフェイス0とBulk OUT endpoint`0x03`へ送った幅384dotsの画像印字を確認しています。`print --sample`の試し刷りで、上下の飾りと左右の枠を含む「あいうえお」を正常に印字できました。メーカーと型番に対応する仕様書、長い文面での速度、フォントの全グリフ、用紙切れや通信失敗時の挙動は未確認です。[Epsonの資料](https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/gs_lv_0.html)では`GS v 0`は旧式命令で、対応機種が限られます。標準モードで行頭かつ空のバッファという前提もあります。非対応機種では、その機種の画像命令へadapterを変更してください。生成、ドライラン、HTMLはこの機種差から独立して使えます。
