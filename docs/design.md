# Laminite 詳細設計書

## 1. 概要

Laminite は、Python 向けのミニマルな ASGI Web フレームワークである。
名前は皮膜（Lamina）に由来し、アプリケーションと ASGI サーバーのあいだに挟まる層を極限まで薄くすることを目指す。

対象は、高い並行性とスループット、低レイテンシが求められるマイクロサービスや内部 API である。
FastAPI が提供する機能のうち、OpenAPI の自動生成やリクエストごとの動的な依存解決は持たない。
その代わりに、ハンドラの解析と依存関係の解決をすべて起動時に済ませ、リクエスト処理の経路には事前に組み立てた処理だけを残す。

```python
import laminite
from msgspec import Struct

app = laminite.Laminite()

class Item(Struct):
    name: str
    price: float
    is_offer: bool = False

@app.get("/")
def read_root():
    return {"message": "Hello from Laminite"}

@app.get("/items/{item_id}")
async def read_item(item_id: int, q: str | None = None):
    return {"item_id": item_id, "q": q}

@app.post("/items", status_code=201)
async def create_item(body: Item):
    return {"name": body.name, "total": body.price * 1.1}
```

## 2. 目標と非目標

### 2.1 目標

- PyPI で公開する OSS として、v0.1.0 をリリースする。
- 必須の実行時依存を msgspec だけにする。
- フレームワーク自身のオーバーヘッドを、in-process のマイクロベンチマークで Starlette 単体より小さくする（全シナリオ）。
  シナリオと計測方法は「16. 性能設計とベンチマーク」で定める。
- 設定の誤り（未登録の依存、ルートの衝突、解釈できない引数など）を、リクエスト時ではなく起動時に検出する。
- Python 3.12 以上をサポートする。

### 2.2 v0.1 の機能範囲

- 静的パスと動的パスのルーティング（404、405、HEAD の GET へのフォールバック）
- path、query、header の型付きバインドと検証（422）
- msgspec によるリクエストボディのデコードと検証（400、413、422）
- 戻り値の型に応じたレスポンスの生成（JSON、テキスト、バイナリ、空、`Response`）
- 型をキーにした依存注入（app スコープ、request スコープ）
- Lifespan
- ASGI ミドルウェア
- 例外ハンドラ
- テストクライアント

### 2.3 非目標

v0.1 では次の機能を持たない。
いずれも必要になった時点で、性能の原則を崩さない形で追加を検討する。

- OpenAPI スキーマの生成
- WebSocket（接続は即座に閉じる）
- ストリーミングレスポンス、ファイル配信
- サブアプリケーションのマウント、`root_path` を考慮したルーティング
- 末尾スラッシュの自動リダイレクト
- `{name:path}` のような複数セグメントにまたがるワイルドカード
- Content-Type ヘッダーの厳密な検査
- フォーム、マルチパート
- 同梱のミドルウェア（CORS、圧縮など）

## 3. 設計原則

**起動時に決められることは起動時に決める**：ハンドラのシグネチャ解析、引数の取得元の決定、依存グラフの解決、ミドルウェアスタックの合成は、すべて起動時に一度だけ行う。
リクエスト処理中に `inspect` や `typing.get_type_hints` を呼ばない。

**使わない機能のコストを払わない**：パラメータを持たないルートは query string もヘッダーも読まず、`Request` を引数に取らないルートは `Request` オブジェクトを生成しない。
ルートごとに必要な処理だけを組み込んだパイプラインを、起動時に組み立てる。

**検証は C 実装に寄せる**：ボディの検証には `msgspec.json.Decoder`、path、query、header の検証には `msgspec.convert` を使い、検証ロジックを Python で書かない。

**ASGI に直接対応づける**：Starlette などの中間層を挟まず、`scope`、`receive`、`send` を直接扱う。

**設定の誤りは起動時に失敗させる**：リクエストを受けて初めて発覚する設定ミスを残さない。
起動時の検証で落とせる誤りは、すべてそこで落とす。

## 4. 全体構成

```
Client
  │
  ▼
ASGI Server (Granian / Uvicorn)
  │  scope, receive, send
  ▼
┌──────────────────────────────────────────────────────────┐
│ Laminite.__call__                                        │
│   scope["type"] で分岐                                   │
│   ├─ "lifespan"  → Lifespan 処理（ミドルウェアを通さない）│
│   ├─ "websocket" → websocket.close                       │
│   └─ "http"      → ミドルウェアスタック                  │
│                      │                                   │
│                      ▼                                   │
│   Dispatcher                                             │
│     1. Router.resolve(method, path)                      │
│        ├─ 静的マップ: dict 1 回の参照                    │
│        └─ 動的トライ: セグメント単位の探索               │
│     2. RoutePipeline（ルートごとに起動時に組み立て）     │
│        ├─ パラメータ収集 + msgspec.convert               │
│        ├─ ボディ受信 + msgspec.json.Decoder              │
│        ├─ request スコープの依存を解決                   │
│        ├─ ハンドラ呼び出し                               │
│        ├─ 依存の後処理（逆順）                           │
│        └─ レスポンス生成 → send()                        │
└──────────────────────────────────────────────────────────┘
```

アプリケーションには「構成中」と「確定済み」の二つの状態がある。
ルートやミドルウェア、依存の登録は構成中にだけ受け付ける。
最初の HTTP リクエストを受けたとき、または Lifespan の startup が完了したときのどちらか早いほうで、アプリケーションは確定済みに移る。
この移行を **確定（freeze）** と呼ぶ。
確定の時点で、全ルートのパイプラインとミドルウェアスタックを組み立てる。
確定済みのアプリケーションに登録しようとすると `RuntimeError` を送出する。

確定を Lifespan の startup 完了まで遅らせる理由は、app スコープの依存（DB の接続プールなど）が Lifespan の中で生成されるからである。
startup の完了前にパイプラインを組み立てると、Lifespan で登録される依存をまだ解決できない。

## 5. パッケージ構成

```
src/laminite/
├── __init__.py      # 公開 API の再エクスポート
├── app.py           # Laminite: 登録 API、確定、__call__、Lifespan
├── router.py        # Router: 静的マップと動的トライ
├── params.py        # 引数マーカー: Query, Header, Body
├── compiler.py      # ハンドラ解析と RoutePipeline の組み立て
├── di.py            # 依存の登録と依存グラフの解決
├── request.py       # Request
├── response.py      # Response, JSONResponse, 戻り値のレンダリング
├── exceptions.py    # HTTPException と標準エラーレスポンス
├── testing.py       # TestClient
└── py.typed
```

モジュールの依存方向は次のとおりで、循環させない。

```
app ──► compiler ──► di, params, request, response, exceptions
  └───► router
testing ──► app
```

`router` はハンドラの中身を知らない。
ルートに対応づける値として任意のオブジェクトを受け取り、マッチした値とパスパラメータを返すだけである。
これによりルーターを単体でテストし、単体でベンチマークできる。

## 6. 公開 API

### 6.1 `laminite` パッケージが公開する名前

```python
from laminite import (
    Laminite,
    Request,
    Response,
    JSONResponse,
    HTTPException,
    Query,
    Header,
    Body,
)
from laminite.testing import TestClient
```

### 6.2 `Laminite`

```python
class Laminite:
    def __init__(
        self,
        *,
        lifespan: Callable[[Laminite], AbstractAsyncContextManager[None]] | None = None,
        max_body_size: int = 1_048_576,
    ) -> None: ...

    # ルート登録（デコレータは元の関数をそのまま返す）
    def route(self, path: str, methods: Sequence[str], *, status_code: int | None = None, offload: bool = False): ...
    def get(self, path: str, *, status_code: int | None = None, offload: bool = False): ...
    def post(self, path: str, *, status_code: int | None = None, offload: bool = False): ...
    def put(self, path: str, *, status_code: int | None = None, offload: bool = False): ...
    def patch(self, path: str, *, status_code: int | None = None, offload: bool = False): ...
    def delete(self, path: str, *, status_code: int | None = None, offload: bool = False): ...

    # 依存注入
    def provide(self, tp: type[T], instance: T) -> None: ...
    def provider(self, func: F) -> F: ...

    # ミドルウェアと例外ハンドラ
    def add_middleware(self, cls: Callable[..., ASGIApp], **options: Any) -> None: ...
    def exception_handler(self, exc_type: type[E]) -> Callable[[H], H]: ...

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None: ...
```

デコレータは登録した関数をそのまま返す。
ハンドラは Laminite を介さずに普通の関数として単体テストできる。

`status_code` を省略したルートは、戻り値が `None` なら 204、それ以外なら 200 を返す。
`status_code` を指定したルートは、戻り値によらずその値を返す。

`offload=True` を指定した同期ハンドラは `asyncio.to_thread` で実行する。
指定しない同期ハンドラは、イベントループ上で直接呼び出す。
ブロッキング処理を含む同期ハンドラをそのまま登録するとイベントループが止まる点は、利用者向けドキュメントに明記する。
async 関数に `offload=True` を指定した場合は、登録時に `TypeError` を送出する。

### 6.3 引数マーカー

```python
class Query:
    def __init__(self, *, alias: str | None = None) -> None: ...

class Header:
    def __init__(self, *, alias: str | None = None) -> None: ...

class Body:
    def __init__(self) -> None: ...
```

マーカーは `typing.Annotated` のメタデータとして使う。

```python
@app.get("/search")
async def search(
    q: str,
    page_size: Annotated[int, Query(alias="page-size")] = 20,
    request_id: Annotated[str | None, Header()] = None,
): ...

@app.post("/bulk")
async def bulk(items: Annotated[list[Item], Body()]): ...
```

## 7. ルーティング

### 7.1 パステンプレート

パステンプレートは `/` で始まる文字列で、`{name}` の形でパスパラメータを書く。
パラメータはセグメント全体を占めなければならず、`/files/{name}.json` のようにセグメントの一部だけをパラメータにすることはできない。
`name` は Python の識別子として有効でなければならない。

末尾スラッシュは区別する。
`/items` と `/items/` は別のパスであり、リダイレクトもしない。

### 7.2 データ構造

ルーターは二つの構造を持つ。

**静的マップ**：パラメータを含まないパスを `dict[str, dict[str, V]]`（パス → メソッド → 値）で保持する。
**動的トライ**：すべてのルートを、セグメント単位のトライに保持する。
各ノードは、静的な子を `dict[str, Node]` で、パラメータの子を高々一つ持つ。
終端ノードは `dict[str, V]`（メソッド → 値）と、パラメータ名の並びを持つ。

文字単位の Radix Tree を採らない理由は、Pure Python ではノードをたどる操作そのものが高くつくからである。
文字単位でたどると、比較と分岐の回数がパスの長さに比例して増える。
セグメント単位であれば、一段ごとの処理は `dict` の参照一回で済み、その参照は C で実装されている。

静的なルートを動的トライにも登録するのは、静的マップでメソッドが見つからなかったときに、405 と 404 の判定をトライ側で一貫して行うためである。

### 7.3 探索手順

```
resolve(method, path):
  1. methods = static.get(path)
     methods があり、method が含まれる → 値を返す（パスパラメータは空）
  2. path を "/" で分割し、トライを探索する
     各段で静的な子を優先し、その部分木で一致しなければパラメータの子へ戻って探索する
  3. 終端ノードが見つかった
     method が含まれる → 値とパスパラメータを返す
     method が HEAD で GET が含まれる → GET の値を返し、HEAD であることを示す
     それ以外 → MethodNotAllowed（許可メソッドの一覧を持つ）
  4. 見つからない → NotFound
```

HEAD のフォールバックは静的マップの段階でも同様に行う。
パスパラメータの値は ASGI の `scope["path"]`（パーセントデコード済み）から切り出した文字列で、型変換はルーターでは行わない。
`%2F` はデコード後に `/` になるため、セグメントの区切りとして扱われる。

### 7.4 登録時の検証

次の場合は登録時に `ValueError` を送出する。

- 同じパスとメソッドの組を二度登録した。
- 同じ位置のパラメータに異なる名前を付けた（`/items/{id}` と `/items/{item_id}/tags`）。
- 一つのテンプレートに同じパラメータ名が二回現れた。
- テンプレートの構文が不正である。

### 7.5 405 と `Allow` ヘッダー

パスは一致してもメソッドが登録されていない場合は、405 を返す。
`Allow` ヘッダーには、そのパスに登録されたメソッドを並べる。
GET が登録されていれば HEAD も含める。
OPTIONS への自動応答は行わない。

## 8. ハンドラ解析とパラメータバインド

### 8.1 引数の取得元を決める規則

確定時に、各ハンドラの引数を `inspect.signature` と `typing.get_type_hints(include_extras=True)` で解析し、取得元を一つに決める。
規則は上から順に評価し、最初に当てはまったものを採る。

1. 名前がパステンプレートのパラメータに含まれる → **path**
2. 型が `Request` → **request**
3. `Annotated` のメタデータに `Header`、`Query`、`Body` のいずれかがある → そのマーカーの取得元
4. 型が依存として登録されている（`provide`、`provider`、テスト用の差し替え） → **dependency**
5. 型が `msgspec.Struct` のサブクラス → **body**
6. それ以外 → **query**

型注釈のない引数は `str` として扱う。
`*args`、`**kwargs`、位置専用引数を持つハンドラは、確定時に `TypeError` で拒否する。
ハンドラは常にキーワード引数で呼び出すからである。

次の場合も、確定時に `TypeError` で拒否する。

- body の引数が二つ以上ある。
- パステンプレートのパラメータに、対応する引数がない。
- path の引数に `list` など、単一の文字列から変換できない型が付いている。

### 8.2 path、query、header の検証

ルートごとに、path、query、header の引数をまとめた `TypedDict` を確定時に合成する。
デフォルト値を持たない引数は `Required`、持つ引数は `NotRequired` にする。

リクエスト時には、各取得元から集めた文字列を一つの `dict` にまとめ、`msgspec.convert(values, ParamsType, strict=False)` を一回だけ呼ぶ。
`strict=False` により、`"42"` から `int`、`"true"` から `bool` への変換を msgspec が行う。
変換結果は `dict` で返るため、そのままハンドラのキーワード引数に使える。
キーが欠けていた `NotRequired` の引数は結果に含まれず、ハンドラ側のデフォルト値が使われる。

この方式を採ったのは、引数ごとに変換関数を呼ぶ方式と比べて、Python と C の境界を越える回数が一回で済むからである。
path、query、header のどれも持たないルートでは、この処理をパイプラインに組み込まない。

検証に失敗すると、`msgspec.ValidationError` のメッセージをそのまま `detail` に入れて 422 を返す。

```json
{"detail": "Expected `int`, got `str` - at `$.item_id`"}
```

メッセージ中の名前は、エイリアスではなく Python の引数名になる。

### 8.3 query の収集

query の引数を持つルートだけが `scope["query_string"]` を読む。
解析には `urllib.parse.parse_qsl(keep_blank_values=True)` を使う。
同じキーが複数回現れた場合は、型が `list[T]` の引数ならすべての値を集め、それ以外なら最後の値を採る。
`list[T]` の引数かどうかは確定時に判定しておく。
宣言されていないキーは捨てる。

### 8.4 header の収集

header の引数を持つルートだけが `scope["headers"]` を走査する。
ヘッダー名は、エイリアスがあればエイリアスを小文字にしたもの、なければ引数名の `_` を `-` に置き換えたもの（`request_id` → `request-id`）とする。
対象のヘッダー名は確定時に小文字のバイト列の集合として用意しておき、走査中は集合の参照だけを行う。
値は latin-1 でデコードする。
同名のヘッダーが複数ある場合は、最後の値を採る。

## 9. リクエストボディ

### 9.1 受信

body の引数を持つルートと、ハンドラが `Request.body()` を呼んだ場合にだけ、`receive()` からボディを受信する。

最初の `http.request` メッセージで `more_body` が偽であれば、受け取った `bytes` をそのまま msgspec に渡し、結合のためのコピーを行わない。
チャンクが複数に分かれた場合だけ、`bytearray` に連結する。
小さな JSON ボディは一回のメッセージで届くことが多いため、多くのリクエストはコピーなしの経路を通る。

受信量の累計が `max_body_size` を超えた時点で受信を打ち切り、413 を返す。
受信中に `http.disconnect` を受け取った場合は、レスポンスを送らずに処理を終える。

### 9.2 デコードと検証

ルートごとに `msgspec.json.Decoder(BodyType)` を確定時に生成し、リクエスト時にはその `decode` を呼ぶだけにする。
デコード時に起きた例外は、次のように区別する。
`msgspec.ValidationError` は `msgspec.DecodeError` のサブクラスなので、先に判定する。

| 例外 | ステータス | 意味 |
|---|---|---|
| `msgspec.ValidationError` | 422 | JSON として正しいが、型が一致しない |
| `msgspec.DecodeError` | 400 | JSON として不正（空のボディを含む） |

body の引数にデフォルト値があり、ボディが空の場合は、デコードせずにデフォルト値を使う。

## 10. 依存注入

### 10.1 目的

DB クライアントや設定、認証済みユーザーのような値を、ハンドラの引数として受け取れるようにする。
FastAPI の `Depends` のように、リクエストのたびに依存関係を解決する方式は採らない。
依存関係は型をキーにして確定時に解決し、リクエスト時にはあらかじめ決めた順序で provider を呼ぶだけにする。

### 10.2 二つのスコープ

**app スコープ**：`app.provide(T, instance)` で登録する、アプリケーション全体で共有するインスタンスである。
典型的には Lifespan の中で接続プールなどを生成して登録する。

**request スコープ**：`@app.provider` で登録する関数が、リクエストごとに生成する値である。
provider の戻り値の型注釈が、その依存のキーになる。

```python
@asynccontextmanager
async def lifespan(app: laminite.Laminite):
    pool = await asyncpg.create_pool(DSN)
    app.provide(asyncpg.Pool, pool)
    yield
    await pool.close()

app = laminite.Laminite(lifespan=lifespan)

@app.provider
async def connection(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    async with pool.acquire() as conn:
        yield conn

@app.provider
def current_user(request: laminite.Request) -> User:
    return authenticate(request.headers.get("authorization"))

@app.get("/me/orders")
async def my_orders(user: User, conn: asyncpg.Connection) -> list[Order]:
    ...
```

### 10.3 provider の形式

provider には次の四つの形式を使える。
形式は登録時に判定しておき、リクエスト時に判定し直さない。

| 形式 | キーになる型 | 後処理 |
|---|---|---|
| 関数 | 戻り値の型 | なし |
| async 関数 | 戻り値の型 | なし |
| ジェネレータ関数 | `Iterator[T]` または `Generator[T, None, None]` の `T` | `yield` の後ろ |
| async ジェネレータ関数 | `AsyncIterator[T]` または `AsyncGenerator[T, None]` の `T` | `yield` の後ろ |

ジェネレータ形式の provider は、登録時に `contextlib.contextmanager` または `contextlib.asynccontextmanager` で包む。
リクエスト時には `AsyncExitStack` に積み、終了処理を contextlib に任せる。
例外の伝播（ハンドラで起きた例外を `yield` の位置で送出し、`async with` のロールバックを動かす）の実装を、自前で抱え込まないためである。

provider の引数に取れるのは、他の依存と `Request` だけである。
path、query、header、body を provider の引数に取ることはできない。
認証のようにヘッダーを読む provider は、`Request` を受け取ってそこから読む。

### 10.4 確定時の解決

確定時に、各ルートが必要とする依存を推移的にたどり、トポロジカル順に並べた provider の列を作る。
同じ provider は、一つのリクエストの中で高々一回しか呼ばない。
二つの依存先が同じ型を要求した場合は、同じ値を共有する。

確定時には、次の誤りを検出して例外を送出する。

- ハンドラや provider が、登録されていない型を要求している。
- 依存関係が循環している。
- 同じ型に `provide` と `provider` の両方が登録されている。

同じ型に `provide` を二回、または `provider` を二回登録しようとした場合は、その登録の時点で `ValueError` を送出する。
後から登録したほうが黙って勝つと、どの値が注入されるのかを読み手が追えなくなるからである。

同じ型を二種類使い分けたい場合（読み書き用と読み取り専用の接続プールなど）は、`typing.NewType` で別の型を作る。

### 10.5 リクエスト時の処理

app スコープの依存は、確定時にキーワード引数のテンプレート `dict` に埋め込む。
リクエスト時にはテンプレートを複製するだけで、app スコープの依存のための追加の処理はない。

request スコープの依存があるルートでは、トポロジカル順に provider を呼び、結果を値の表に書き込む。
ハンドラが完了すると、ジェネレータ形式の provider の後処理を、生成とは逆の順序で実行する。
後処理を実行するのはレスポンスを送る前である。
これによりトランザクションの commit が失敗した場合も、例外としてエラーレスポンスに変換できる。

依存を一つも使わないルートには、これらの処理を一切組み込まない。

### 10.6 テストでの差し替え

`TestClient(app, overrides={T: instance})` で、任意の依存を固定のインスタンスに差し替えられる。
差し替えは `provide` と `provider` のどちらの登録よりも優先する。
差し替えた型の provider は呼ばないので、その provider が要求していた依存もたどらない。

この差し替えは本番と同じ依存注入の経路を通る。
テストのためだけにハンドラ側で `None` を許容したり、分岐を設けたりする必要はない。

## 11. リクエストとレスポンス

### 11.1 `Request`

`Request` は、ハンドラか provider が引数に取った場合にだけ生成する。
`__slots__` を持つ軽量なクラスで、各プロパティは初回のアクセス時に計算してキャッシュする。

```python
class Request:
    scope: Scope
    method: str
    path: str
    path_params: dict[str, str]
    headers: Headers          # 小文字のヘッダー名で引ける読み取り専用の対応表
    query_params: QueryParams # 同じキーの複数の値を保持する読み取り専用の対応表
    state: dict[str, Any]     # scope["state"] を指す。ミドルウェアからハンドラへ値を渡すのに使う

    async def body(self) -> bytes: ...
    async def json(self, tp: type[T] = Any) -> T: ...
```

同じルートで body の引数と `Request` の両方を使った場合、受信したボディは `Request` にもキャッシュされ、`body()` は二度目の受信を行わない。

### 11.2 戻り値のレンダリング

ハンドラの戻り値の型に応じて、レスポンスを次のように生成する。

| 戻り値 | Content-Type | 本文 |
|---|---|---|
| `Response`（`JSONResponse` を含む） | `Response` が指定したもの | `Response` が保持するもの |
| `None` | なし | 空 |
| `bytes` | `application/octet-stream` | そのまま |
| `str` | `text/plain; charset=utf-8` | UTF-8 でエンコード |
| それ以外（Struct、dict、list など） | `application/json` | `msgspec.json.Encoder` でエンコード |

判定は `type(result)` をキーにした `dict` の参照で行う。
`isinstance` を何度も重ねると、最も多い JSON のケースが最後まで判定を待たされるからである。
参照で見つからなかった場合は `isinstance(result, Response)` で `Response` とそのサブクラスを拾い、それにも当てはまらなければ JSON として扱う。

エンコーダはアプリケーションで一つの `msgspec.json.Encoder` を共有する。
msgspec がエンコードできない値が返された場合は、500 として扱う。

### 11.3 `Response` と `JSONResponse`

```python
class Response:
    def __init__(
        self,
        content: bytes | str = b"",
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> None: ...

class JSONResponse(Response):
    def __init__(
        self,
        content: Any,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None: ...
```

ステータスコードやヘッダーをリクエストごとに変えたい場合に使う。
ハンドラが `Response` を返した場合、ルートの `status_code` は使わない。

### 11.4 送信

レスポンスは常に `http.response.start` と `http.response.body` の二つのメッセージで送る。
`content-length` は常に付与する。
Content-Type ヘッダーのタプルはモジュールの定数として事前に作っておき、リクエストごとに作り直すのは `content-length` の値だけにする。

HEAD リクエストでは、GET のハンドラを実行し、本文の長さを `content-length` に入れたうえで、本文を空にして送る。

## 12. エラー処理

### 12.1 標準のエラーレスポンス

フレームワークが生成するエラーレスポンスは、すべて `{"detail": ...}` の形の JSON にそろえる。

| 状況 | ステータス | `detail` |
|---|---|---|
| ルートが見つからない | 404 | `"Not Found"` |
| メソッドが登録されていない | 405 | `"Method Not Allowed"`（`Allow` ヘッダー付き） |
| ボディが JSON として不正 | 400 | msgspec のメッセージ |
| ボディが `max_body_size` を超えた | 413 | `"Payload Too Large"` |
| path、query、header、body の検証に失敗 | 422 | msgspec のメッセージ |
| `HTTPException` が送出された | 指定値 | 指定値 |
| 上記以外の例外 | 500 | `"Internal Server Error"` |

### 12.2 `HTTPException`

```python
class HTTPException(Exception):
    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None: ...
```

`detail` を省略すると、`http.HTTPStatus` の標準の理由句を使う。
`detail` には JSON にエンコードできる任意の値を渡せる。

### 12.3 例外ハンドラ

```python
@app.exception_handler(UserNotFound)
def user_not_found(request: Request, exc: UserNotFound) -> Response:
    return JSONResponse({"detail": "user not found"}, status_code=404)
```

ドメインの例外を HTTP のレスポンスに対応づけるために使う。
これがないと、ドメイン層のコードが `HTTPException` を直接送出することになり、HTTP の関心事がドメイン層に漏れる。

ハンドラか provider で例外が起きたとき、例外の型の MRO を順にたどり、最初に登録が見つかった例外ハンドラを呼ぶ。
見つからない場合は、`HTTPException` なら 12.2 の形で、それ以外なら 500 で応答する。
例外ハンドラは同期関数と async 関数のどちらでもよい。
例外の経路でしか動かないため、MRO の探索のコストは通常のリクエストの性能に影響しない。

`HTTPException` に対しても例外ハンドラを登録でき、登録すれば標準の応答より優先する。

### 12.4 500 の扱い

想定外の例外は、ロガー `laminite` に `exception` レベルで記録したうえで、500 を返す。
例外の内容はレスポンスに含めない。
例外ハンドラ自身が例外を送出した場合も、同様に 500 とする。

## 13. Lifespan

```python
@asynccontextmanager
async def lifespan(app: laminite.Laminite) -> AsyncIterator[None]:
    ...  # startup
    yield
    ...  # shutdown
```

`lifespan.startup` を受け取ったら、コンテキストマネージャに入り、続けてアプリケーションを確定する。
どちらかで例外が起きた場合は、例外のメッセージを付けて `lifespan.startup.failed` を送る。
設定の誤りは起動の失敗として現れるため、リクエストを受け付ける前に気付ける。

`lifespan.shutdown` を受け取ったら、コンテキストマネージャから抜け、`lifespan.shutdown.complete` を送る。
抜ける途中で例外が起きた場合は、`lifespan.shutdown.failed` を送る。

`lifespan` を指定しない場合も、startup のメッセージを受けた時点で確定を行い、`lifespan.startup.complete` を送る。
Lifespan に対応していないサーバーの場合は、最初の HTTP リクエストの時点で確定する。

Lifespan のメッセージはミドルウェアを通さず、`Laminite.__call__` が直接処理する。
ミドルウェアは HTTP のリクエストだけを扱えばよく、実装が単純になる。

## 14. ミドルウェア

ミドルウェアは、ASGI アプリケーションを受け取って ASGI アプリケーションを返す、純粋な ASGI ミドルウェアとする。

```python
class Timing:
    def __init__(self, app: ASGIApp, *, header: str = "x-elapsed") -> None:
        self.app = app
        self.header = header.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        ...

app.add_middleware(Timing, header="x-response-time")
```

`add_middleware` は登録内容を記録するだけで、ミドルウェアのインスタンスは確定時にまとめて生成する。
先に登録したものほど外側に置く。
コードを上から読んだ順に、リクエストがミドルウェアを通過するようにするためである。

確定後の HTTP リクエストは、`__call__` からミドルウェアスタックの最外層を直接呼ぶ。
ミドルウェアがない場合は、ディスパッチャを直接呼ぶ。
確定前かどうかの判定をリクエストのたびに行わないよう、確定前は「確定してから処理を続ける関数」を、確定後はスタックの最外層を、同じ属性に入れておき、その属性を呼ぶだけにする。

```python
async def __call__(self, scope, receive, send):
    if scope["type"] == "http":
        await self._http(scope, receive, send)
    elif scope["type"] == "lifespan":
        await self._lifespan(receive, send)
    else:
        await send({"type": "websocket.close", "code": 1000})
```

## 15. テストクライアント

```python
from laminite.testing import TestClient

def test_read_item():
    with TestClient(app, overrides={asyncpg.Pool: FakePool()}) as client:
        res = client.get("/items/1", query={"q": "x"}, headers={"x-request-id": "abc"})
        assert res.status_code == 200
        assert res.json() == {"item_id": 1, "q": "x"}
```

`TestClient` は同期の API を持ち、HTTP を経由せずにアプリケーションの `__call__` を直接呼ぶ。
httpx などの追加の依存は持たない。

`TestClient` は内部に `asyncio.Runner` を一つ持ち、クライアントの生存期間を通じて同じイベントループを使う。
リクエストごとに `asyncio.run` を呼ぶと、Lifespan で生成した接続プールなどが別のイベントループに属してしまい、使えなくなるからである。

`with` ブロックで使うと、Lifespan の startup と shutdown を実行する。
Lifespan のやり取りはイベントループ上のタスクとして実行し、`asyncio.Queue` で `receive` と `send` を仲介する。
`with` を使わない場合、Lifespan は実行されず、最初のリクエストの時点でアプリケーションが確定する。

```python
class TestClient:
    def __init__(self, app: Laminite, *, overrides: Mapping[type, Any] | None = None) -> None: ...
    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str | Sequence[str]] | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        content: bytes | None = None,
    ) -> TestResponse: ...
    def get(self, path: str, **kwargs) -> TestResponse: ...
    # post, put, patch, delete, head も同様

class TestResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    @property
    def text(self) -> str: ...
    def json(self, tp: type[T] = Any) -> T: ...
```

`json=` を渡すと msgspec でエンコードし、`content-type: application/json` を付ける。
`TestClient` は実行中のイベントループの内側では使えない（`asyncio.Runner` の制約）。
async のテスト関数からは、アプリケーションの `__call__` を直接呼ぶ。

## 16. 性能設計とベンチマーク

### 16.1 ホットパスで行う処理

パラメータも依存も持たない GET ルートでは、リクエストごとの処理は次の手順だけになる。

1. `scope["type"]` の比較
2. 静的マップの `dict` 参照二回（パス、メソッド）
3. パイプラインのクロージャの呼び出し
4. ハンドラの呼び出し（引数なしで直接呼ぶ）
5. 戻り値の型による `dict` 参照と、msgspec によるエンコード
6. `send()` 二回

パイプラインは、確定時に生成するクロージャとして実装する。
ハンドラ、デコーダ、パラメータの `TypedDict`、各処理の有無を表す真偽値は、クロージャの自由変数として保持する。
インスタンス属性の参照よりもセルの参照のほうが速いからである。

ルートの処理内容の組み合わせごとに別々の関数をコード生成する方式（`exec` による生成）は、v0.1 では採らない。
デバッグと保守が難しくなる一方で、得られる差はクロージャ内の真偽値の分岐数回分にとどまる見込みである。
ベンチマークでその分岐が無視できない割合を占めると分かった場合に、改めて検討する。

### 16.2 マイクロベンチマーク

フレームワーク自身のオーバーヘッドを測るため、ASGI サーバーを介さずにアプリケーションの `__call__` を直接呼ぶ。
`scope` は事前に作っておき、`receive` と `send` には最小限の関数を渡す。

| ID | シナリオ |
|---|---|
| S1 | `GET /`、引数なし、dict を JSON で返す |
| S2 | `GET /items/{item_id}`、`int` のパスパラメータ |
| S3 | `GET /search?q=foo&limit=10`、二つの query パラメータ |
| S4 | `POST /items`、約 200 バイトの JSON を Struct にデコード |
| S5 | `GET /missing`、404 |

比較対象は Starlette、FastAPI、Litestar である。
同じシナリオを各フレームワークで実装し、同じ手順で計測する。
v0.1 の受け入れ基準は、S1 から S5 のすべてで Laminite の一リクエストあたりの時間が Starlette を下回ることとする。

計測には `pyperf` を使い、ベンチマークのコードと依存は `benchmarks/` と uv の依存グループ `bench` に分離する。

### 16.3 E2E ベンチマーク

Granian 上でアプリケーションを動かし、`oha` で負荷をかけて、スループットとレイテンシ分布（p50、p99）を計測する。
結果は計測環境（CPU、OS、Python、各ライブラリのバージョン）とともに `docs/benchmarks.md` に記録する。

### 16.4 CI での扱い

マイクロベンチマークは CI でも実行し、結果をジョブのサマリーに出力する。
共有ランナーでは計測値のばらつきが大きいため、v0.1 では結果によってジョブを失敗させない。

## 17. 開発基盤

### 17.1 ツール

| 用途 | ツール |
|---|---|
| プロジェクト管理 | uv |
| ビルドバックエンド | uv_build |
| Lint とフォーマット | ruff |
| 型検査 | pyright（strict） |
| テスト | pytest、pytest-cov |
| ベンチマーク | pyperf |

ソースは `src/laminite/` に置く src レイアウトとし、`py.typed` を同梱する。

### 17.2 CI

GitHub Actions で、Python 3.12、3.13、3.14 の行列に対して ruff、pyright、pytest を実行する。
Actions はコミット SHA で固定し、Dependabot で更新する。

### 17.3 リリース

`v*` のタグを push すると、配布物をビルドし、PyPI の Trusted Publishing で公開する。
バージョンは Semantic Versioning に従う。
0.x の間はマイナーバージョンでも互換性のない変更を行いうる。
互換性のない変更は `CHANGELOG.md` に明記する。

### 17.4 テスト方針

- ルーターは、ハンドラを持たない値を登録して単体でテストする。
- アプリケーションの振る舞いは、`TestClient` を使って HTTP の入出力として検証する。
- ASGI プロトコルの細部（チャンク分割されたボディ、`http.disconnect`、Lifespan のメッセージ）は、`receive` と `send` を自前で用意して `__call__` を直接呼んで検証する。
- 確定時の検証は、誤った登録をしたアプリケーションを確定させ、期待した例外が送出されることを確かめる。

## 18. 実装計画

実装は次の順に進める。
各項目は GitHub Issue として登録し、マイルストーン `v0.1.0` に紐づける。

1. プロジェクト基盤（pyproject、ruff、pyright、pytest、CI）
2. ルーター
3. `Request`、`Response`、`HTTPException`
4. アプリケーション本体とディスパッチ（ルート登録、確定、path パラメータ、エラー応答、同期ハンドラと `offload`）
5. テストクライアント（Lifespan なし）
6. query と header のバインド
7. リクエストボディのデコード
8. Lifespan（テストクライアントの `with` 対応を含む）
9. 依存注入
10. ミドルウェア
11. 例外ハンドラ
12. ベンチマーク
13. 利用者向けドキュメント（README、使い方ガイド）
14. リリースパイプラインと v0.1.0 の公開
