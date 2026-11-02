import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import flet as ft


DATA_FILE = Path.home() / ".goods_organizer.json"
ASSET_DIR = Path.home() / ".goods_organizer_images"

COLORS = {
    "ink": "#26312D",
    "muted": "#747D78",
    "paper": "#F4F1E8",
    "surface": "#FFFDF7",
    "green": "#306653",
    "green_light": "#E1EBE4",
    "coral": "#C85F45",
    "gold": "#E3B34C",
    "line": "#DDDCD2",
}


def default_data():
    return {"items": [], "events": [], "goals": []}


def load_data():
    try:
        value = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default_data()
    except (OSError, json.JSONDecodeError):
        return default_data()


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize(value):
    return re.sub(r"[\s　]+", "", (value or "")).casefold()


def item_purchase_status(item):
    return item.get("purchase_status", "purchased" if item.get("owned", True) else "planned")


def google_shopping_url(query):
    params = urllib.parse.urlencode({"tbm": "shop", "q": query})
    return f"https://www.google.com/search?{params}"


def save_picked_image(file):
    content = file.bytes
    if content is None:
        if not file.path:
            raise ValueError("画像データを読み込めませんでした")
        source = Path(file.path)
        content = source.read_bytes()
        suffix = source.suffix
    else:
        suffix = Path(file.name).suffix

    digest = hashlib.sha256(content).hexdigest()
    target = ASSET_DIR / f"{digest}{suffix or '.jpg'}"
    if not target.exists():
        target.write_bytes(content)
    return target, digest


def fetch_barcode(code):
    url = f"https://world.openfoodfacts.org/api/v2/product/{urllib.parse.quote(code)}.json"
    request = urllib.request.Request(url, headers={"User-Agent": "GoodsOrganizer/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.load(response)
    except (OSError, TimeoutError, json.JSONDecodeError):
        return None
    if payload.get("status") != 1:
        return None
    product = payload.get("product") or {}
    return {
        "name": product.get("product_name") or product.get("generic_name") or "名称未登録の商品",
        "brand": product.get("brands") or "",
        "image": product.get("image_front_small_url") or "",
        "image_path": "",
        "barcode": code,
        "url": product.get("url", ""),
    }


def card(title, controls, **kwargs):
    return ft.Container(
        content=ft.Column(
            [ft.Text(title, size=18, weight=ft.FontWeight.W_600, color=COLORS["ink"]), *controls],
            spacing=12,
        ),
        padding=20,
        bgcolor=COLORS["surface"],
        border=ft.Border.all(1, COLORS["line"]),
        border_radius=8,
        **kwargs,
    )


def main(page: ft.Page):
    page.title = "コレクト | グッズ管理"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = COLORS["paper"]
    page.padding = ft.Padding.all(16)
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.scroll = ft.ScrollMode.ADAPTIVE

    data = load_data()
    ASSET_DIR.mkdir(parents=True, exist_ok=True)

    active_tab = "collection"
    collection_view = ft.Column(spacing=12)
    search_result_view = ft.Column(spacing=10)
    calendar_view = ft.Column(spacing=12)
    savings_view = ft.Column(spacing=12)
    snackbar = ft.SnackBar(content=ft.Text(""))
    page.overlay.append(snackbar)

    def notify(message):
        snackbar.content.value = message
        snackbar.open = True
        page.update()

    def persist():
        try:
            save_data(data)
        except OSError:
            notify("保存できませんでした。ストレージの容量や権限を確認してください")

    def item_name(item):
        return f"{item.get('brand', '')} {item.get('name', '')}".strip()

    def item_image(item):
        if item.get("image_path"):
            path = Path(item["image_path"])
            if path.exists():
                return ft.Image(src=str(path), width=72, height=72, fit=ft.BoxFit.COVER, border_radius=6)
        if item.get("image"):
            return ft.Image(src=item["image"], width=72, height=72, fit=ft.BoxFit.COVER, border_radius=6)
        return ft.Container(width=72, height=72, bgcolor=COLORS["green_light"], border_radius=6)

    def delete_item(item_id):
        data["items"] = [item for item in data["items"] if item["id"] != item_id]
        persist()
        render_collection()

    def render_collection(query=""):
        query_key = normalize(query)
        rows = []
        for item in data["items"]:
            if query_key and query_key not in normalize(item_name(item)):
                continue
            status = item_purchase_status(item)
            purchase_summary = (
                f"購入済み ・ {item.get('quantity', 1)}個"
                if status == "purchased"
                else "購入予定"
            )
            rows.append(
                ft.Container(
                    content=ft.Row(
                        [
                            item_image(item),
                            ft.Column(
                                [
                                    ft.Text(item_name(item), size=15, weight=ft.FontWeight.W_600),
                                    ft.Text(
                                        f"{item.get('barcode') or 'バーコード未登録'} ・ {purchase_summary} ・ ¥{item.get('price', 0):,}",
                                        size=12,
                                        color=COLORS["muted"],
                                    ),
                                    ft.Text(item.get("memo", ""), size=12, color=COLORS["muted"]),
                                ],
                                expand=True,
                                spacing=3,
                            ),
                            ft.IconButton(
                                ft.Icons.EDIT_OUTLINED,
                                tooltip="登録内容を編集",
                                on_click=lambda _, value=item: add_manual(value),
                            ),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=COLORS["coral"], tooltip="削除", on_click=lambda event, item_id=item["id"]: delete_item(item_id)),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=12,
                    bgcolor=COLORS["surface"],
                    border=ft.Border.all(1, COLORS["line"]),
                    border_radius=8,
                )
            )
        collection_view.controls = rows or [ft.Text("該当するグッズはありません", color=COLORS["muted"], text_align=ft.TextAlign.CENTER)]
        page.update()

    def add_manual(item=None, *args):
        item = item if isinstance(item, dict) else {}
        name_field = ft.TextField(label="商品名", value=item.get("name", ""), autofocus=True)
        brand_field = ft.TextField(label="ブランド・作品名", value=item.get("brand", ""))
        barcode_field = ft.TextField(label="バーコード", value=item.get("barcode", ""), keyboard_type=ft.KeyboardType.NUMBER)
        price_field = ft.TextField(label="購入金額", value=str(item.get("price", "")), keyboard_type=ft.KeyboardType.NUMBER)
        memo_field = ft.TextField(label="メモ", value=item.get("memo", ""), multiline=True, min_lines=2)
        quantity_field = ft.TextField(
            label="購入個数",
            value=str(item.get("quantity", 1)),
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        item_image_url = item.get("image", "")
        image_url_field = ft.TextField(
            label="商品画像URL（画像アドレスを貼り付け）",
            value=item_image_url if isinstance(item_image_url, str) and item_image_url.startswith(("http://", "https://")) else "",
            hint_text="商品ページではなく画像そのもののURL",
        )
        purchase_status_field = ft.RadioGroup(
            content=ft.Column([
                ft.Radio(value="purchased", label="購入済み"),
                ft.Radio(value="planned", label="購入予定"),
            ]),
            value=item_purchase_status(item),
        )
        search_name = ft.TextField(label="インターネット検索", hint_text="商品名・作品名", expand=True)
        search_barcode = ft.TextField(label="バーコード検索", hint_text="バーコード数字", expand=True, keyboard_type=ft.KeyboardType.NUMBER)
        search_status = ft.Text(size=12, color=COLORS["muted"])
        search_candidates = ft.Column(spacing=6)
        selected_image = item.get("image", "")
        selected_image_path = item.get("image_path", "")
        selected_image_hash = item.get("image_hash", "")

        def close(_=None):
            dialog.open = False
            page.update()

        def update_purchase_fields(_=None):
            quantity_field.visible = purchase_status_field.value == "purchased"
            page.update()

        purchase_status_field.on_change = update_purchase_fields
        quantity_field.visible = purchase_status_field.value == "purchased"

        def submit(_):
            name = name_field.value.strip()
            if not name:
                notify("商品名を入力してください")
                return
            try:
                price = max(0, int(price_field.value or 0))
            except ValueError:
                notify("購入金額は数字で入力してください")
                return
            try:
                quantity = int(quantity_field.value or 0)
            except ValueError:
                quantity = 0
            purchase_status = purchase_status_field.value
            if purchase_status not in ("purchased", "planned"):
                notify("購入状況を選択してください")
                return
            if purchase_status == "purchased" and quantity < 1:
                notify("購入個数は1以上で入力してください")
                return
            image_url = image_url_field.value.strip()
            if image_url:
                parsed_image_url = urllib.parse.urlsplit(image_url)
                if parsed_image_url.scheme not in ("http", "https") or not parsed_image_url.netloc:
                    notify("画像URLは http:// または https:// から始まるURLを入力してください")
                    return
            record = {
                "id": item.get("id") or str(__import__("uuid").uuid4()),
                "name": name,
                "brand": brand_field.value.strip(),
                "barcode": re.sub(r"\D", "", barcode_field.value),
                "price": price,
                "memo": memo_field.value.strip(),
                "purchase_status": purchase_status,
                "owned": purchase_status == "purchased",
                "quantity": quantity,
                "image": image_url or selected_image,
                "image_path": "" if image_url else selected_image_path,
                "image_hash": "" if image_url else selected_image_hash,
            }
            existing = next((entry for entry in data["items"] if entry["id"] == record["id"]), None)
            if existing:
                existing.update(record)
            else:
                data["items"].insert(0, record)
            persist()
            close()
            render_collection()

        def apply_candidate(candidate):
            nonlocal selected_image, selected_image_path, selected_image_hash
            name_field.value = candidate.get("name", "")
            brand_field.value = candidate.get("brand", "")
            barcode_field.value = candidate.get("barcode", "")
            selected_image = candidate.get("image") or ""
            selected_image_path = candidate.get("image_path", "")
            selected_image_hash = candidate.get("image_hash", "")
            image_url_field.value = selected_image if selected_image.startswith(("http://", "https://")) else ""
            search_status.value = "候補を入力欄に反映しました"
            page.update()

        def render_candidates(candidates):
            rows = []
            for candidate in candidates:
                rows.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Image(src=candidate["image"], width=52, height=52, fit=ft.BoxFit.COVER, border_radius=6)
                            if candidate.get("image") else ft.Container(width=52, height=52, bgcolor=COLORS["green_light"], border_radius=6),
                            ft.Column([
                                ft.Text(candidate.get("name", ""), size=13, weight=ft.FontWeight.W_600),
                                ft.Text(candidate.get("brand", ""), size=11, color=COLORS["muted"]),
                            ], expand=True, spacing=2),
                            ft.IconButton(ft.Icons.CHECK, tooltip="この候補を使う", on_click=lambda _, value=candidate: apply_candidate(value)),
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        padding=8,
                        bgcolor=COLORS["surface"],
                        border=ft.Border.all(1, COLORS["line"]),
                        border_radius=6,
                    )
                )
            search_candidates.controls = rows
            page.update()

        def search_name_online(_):
            query = search_name.value.strip()
            if not query:
                search_status.value = "商品名を入力してください"
                page.update()
                return
            search_status.value = "Google ショッピングでグッズを検索できます"
            search_candidates.controls = [
                ft.TextButton(
                    "Google ショッピングでグッズを検索",
                    icon=ft.Icons.OPEN_IN_NEW,
                    url=google_shopping_url(query),
                ),
                ft.Button(
                    "この商品名で選択して登録",
                    icon=ft.Icons.ADD,
                    on_click=lambda _: select_search_name(query),
                ),
                ft.Text(
                    "Googleの商品情報は自動取得されません。商品名を登録欄に反映して登録できます。",
                    size=11,
                    color=COLORS["muted"],
                ),
            ]
            page.update()

        def select_search_name(query):
            name_field.value = query
            search_status.value = "商品名を登録欄に反映しました"
            page.update()

        def search_barcode_online(_):
            code = re.sub(r"\D", "", search_barcode.value)
            if not re.fullmatch(r"\d{8,14}", code):
                search_status.value = "バーコードは8〜14桁の数字で入力してください"
                page.update()
                return
            search_status.value = "バーコードを照合中..."
            page.update()
            candidate = fetch_barcode(code)
            if candidate:
                search_status.value = "候補が見つかりました"
                render_candidates([candidate])
            else:
                search_status.value = "公開データに候補がありません"
                search_candidates.controls = []
            page.update()

        async def pick_registration_image(_):
            files = await image_picker.pick_files(
                file_type=ft.FilePickerFileType.IMAGE,
                allow_multiple=False,
                with_data=True,
            )
            if not files:
                return
            nonlocal selected_image, selected_image_path, selected_image_hash
            try:
                target, digest = save_picked_image(files[0])
            except (OSError, ValueError) as error:
                notify(f"画像を読み込めませんでした: {error}")
                return
            selected_image = ""
            selected_image_path = str(target)
            selected_image_hash = digest
            image_url_field.value = ""
            search_status.value = "画像を選択しました。この画像もグッズと一緒に登録されます"
            search_candidates.controls = [
                ft.Row([
                    ft.Image(src=str(target), width=72, height=72, fit=ft.BoxFit.COVER, border_radius=6),
                    ft.Text("登録する画像", color=COLORS["muted"]),
                ]),
            ]
            page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("グッズを登録"),
            content=ft.Container(
                content=ft.Column([
                    ft.Text("インターネットから検索", weight=ft.FontWeight.W_600, color=COLORS["green"]),
                    ft.Row([search_name, ft.Button("商品名", icon=ft.Icons.SEARCH, on_click=search_name_online)], spacing=8),
                    ft.Row([search_barcode, ft.Button("バーコード", icon=ft.Icons.QR_CODE_SCANNER, on_click=search_barcode_online)], spacing=8),
                    ft.Row([ft.Button("画像を選んで登録", icon=ft.Icons.PHOTO_CAMERA, on_click=pick_registration_image), ft.Text("選んだ画像はグッズと一緒に保存されます", expand=True, size=12, color=COLORS["muted"])], spacing=8),
                    search_status,
                    search_candidates,
                    ft.Divider(height=1),
                    name_field,
                    brand_field,
                    barcode_field,
                    price_field,
                    memo_field,
                    image_url_field,
                    ft.Text(
                        "ショップの「画像アドレスをコピー」から貼り付けるか、画像ファイルを選択してください。",
                        size=11,
                        color=COLORS["muted"],
                    ),
                    ft.Text("購入状況", weight=ft.FontWeight.W_600),
                    purchase_status_field,
                    quantity_field,
                ], tight=True, scroll=ft.ScrollMode.ADAPTIVE),
                width=520,
                height=560,
            ),
            actions=[ft.TextButton("キャンセル", on_click=close), ft.Button("登録", on_click=submit)],
        )
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    async def find_by_image(_):
        files = await image_picker.pick_files(
            file_type=ft.FilePickerFileType.IMAGE,
            allow_multiple=False,
            with_data=True,
        )
        if not files:
            return
        uploaded = files[0]
        try:
            target, digest = save_picked_image(uploaded)
        except (OSError, ValueError) as error:
            notify(f"画像を読み込めませんでした: {error}")
            return
        matches = [item for item in data["items"] if item.get("image_hash") == digest]
        if matches:
            search_result_view.controls = [
                ft.Text("同じ画像のグッズが見つかりました", weight=ft.FontWeight.W_600),
                ft.Column([ft.Row([item_image(item), ft.Text(item_name(item), expand=True)]) for item in matches]),
            ]
        else:
            search_result_view.controls = [
                ft.Text("一致する所持グッズはありません。画像を新しい所持情報として登録できます。"),
                ft.Image(src=str(target), height=220, fit=ft.BoxFit.CONTAIN),
                ft.Button("この画像で登録", on_click=lambda _: add_manual({"name": "", "image_path": str(target), "image_hash": digest})),
            ]
        page.update()

    image_picker = ft.FilePicker()
    page.overlay.append(image_picker)

    def lookup_barcode(_):
        code = barcode_search.value.strip()
        if not re.fullmatch(r"\d{8,14}", code):
            notify("バーコードは8〜14桁の数字で入力してください")
            return
        result = fetch_barcode(code)
        if not result:
            search_result_view.controls = [
                ft.Text("公開データベースでは見つかりませんでした。手動登録できます。"),
                ft.Button("手動で登録", on_click=lambda _: add_manual({"barcode": code})),
            ]
        else:
            search_result_view.controls = [
                ft.Text("候補が見つかりました", weight=ft.FontWeight.W_600),
                ft.Row([ft.Image(src=result["image"], width=100, height=100, fit=ft.BoxFit.COVER) if result["image"] else ft.Container(), ft.Column([ft.Text(result["name"], weight=ft.FontWeight.W_600), ft.Text(result["brand"]), ft.Text(code, color=COLORS["muted"])], expand=True)]),
                ft.Button("所持グッズとして登録", on_click=lambda _: add_manual(result)),
            ]
        page.update()

    barcode_search = ft.TextField(label="バーコード", hint_text="例: 490...", expand=True, keyboard_type=ft.KeyboardType.NUMBER)
    text_search = ft.TextField(label="商品名・作品名で検索", expand=True)

    def text_lookup(_):
        search_term = text_search.value.strip()
        query = normalize(search_term)
        matches = [item for item in data["items"] if query in normalize(item_name(item))]
        if not query:
            search_result_view.controls = [
                ft.Text("商品名を入力して検索してください", color=COLORS["muted"]),
                *[
                    product_selection_card(item)
                    for item in data["items"]
                ],
                ft.Button(
                    "新しいグッズを登録",
                    icon=ft.Icons.ADD,
                    on_click=lambda _: add_manual(),
                ),
            ]
            page.update()
            return
        search_result_view.controls = [
            ft.Text(
                f"所持庫に {len(matches)} 件あります" if matches else "所持庫にはありません",
                weight=ft.FontWeight.W_600,
            ),
            *[product_selection_card(item) for item in matches],
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            f"「{search_term}」を登録",
                            size=17,
                            weight=ft.FontWeight.W_600,
                            color=COLORS["ink"],
                        ),
                        ft.Text(
                            "このボタンでアプリ内の商品登録に進みます。画像を選ぶか、商品画像URLを貼り付けてください。",
                            size=12,
                            color=COLORS["muted"],
                        ),
                        ft.Button(
                            "この商品を選択して画像付きで登録",
                            icon=ft.Icons.ADD_PHOTO_ALTERNATE,
                            on_click=lambda _, value=search_term: add_manual({"name": value}),
                        ),
                    ],
                    spacing=10,
                ),
                padding=16,
                bgcolor=COLORS["green_light"],
                border_radius=8,
            ),
            ft.TextButton(
                "Google ショッピングで商品を確認（外部サイト）",
                icon=ft.Icons.OPEN_IN_NEW,
                url=google_shopping_url(search_term),
            ),
            ft.Text(
                "外部サイトで商品を確認した後は、この画面で商品名を検索し直して上のボタンから登録してください。外部サイトの選択内容は自動では取り込まれません。",
                size=11,
                color=COLORS["muted"],
            ),
        ]
        page.update()

    def product_selection_card(item):
        status = item_purchase_status(item)
        purchase_summary = (
            f"購入済み ・ {item.get('quantity', 1)}個"
            if status == "purchased"
            else "購入予定"
        )
        return ft.Container(
            content=ft.Row(
                [
                    item_image(item),
                    ft.Column(
                        [
                            ft.Text(item_name(item), weight=ft.FontWeight.W_600),
                            ft.Text(
                                f"{purchase_summary} ・ ¥{item.get('price', 0):,}",
                                size=12,
                                color=COLORS["muted"],
                            ),
                            ft.Text(
                                "このグッズを選択",
                                size=12,
                                color=COLORS["green"],
                            ),
                        ],
                        expand=True,
                        spacing=4,
                    ),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, color=COLORS["green"]),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=12,
            bgcolor=COLORS["surface"],
            border=ft.Border.all(1, COLORS["line"]),
            border_radius=8,
            ink=True,
            tooltip="タップしてこのグッズを選択",
            on_click=lambda _, value=item: add_manual(value),
        )

    def delete_event(event_id):
        data["events"] = [event for event in data["events"] if event["id"] != event_id]
        persist()
        render_calendar()

    def render_calendar():
        rows = []
        for event in sorted(data["events"], key=lambda entry: (entry["date"], entry["time"])):
            rows.append(
                ft.Container(
                    content=ft.Row([
                        ft.Column([ft.Text(event["date"], color=COLORS["green"], weight=ft.FontWeight.W_600), ft.Text(event["time"], size=12, color=COLORS["muted"])], width=85),
                        ft.Column([ft.Text(event["title"]), ft.Text(event.get("memo", ""), size=12, color=COLORS["muted"])], expand=True),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=COLORS["coral"], tooltip="削除", on_click=lambda _, event_id=event["id"]: delete_event(event_id)),
                    ]),
                    padding=12,
                    bgcolor=COLORS["surface"],
                    border=ft.Border.all(1, COLORS["line"]),
                    border_radius=8,
                )
            )
        calendar_view.controls = rows or [ft.Text("予定はまだありません", color=COLORS["muted"])]
        page.update()

    def add_event(_):
        title = ft.TextField(label="予定名", autofocus=True)
        event_date = ft.TextField(label="日付", value=date.today().isoformat(), hint_text="YYYY-MM-DD")
        event_time = ft.TextField(label="時刻", value="12:00", hint_text="HH:MM")
        memo = ft.TextField(label="メモ", multiline=True, min_lines=2)

        def close(_=None):
            dialog.open = False
            page.update()

        def submit(_):
            try:
                date.fromisoformat(event_date.value)
                int(event_time.value[:2]), int(event_time.value[3:])
            except (ValueError, IndexError):
                notify("日付と時刻の形式を確認してください")
                return
            data["events"].append({"id": str(__import__("uuid").uuid4()), "title": title.value.strip(), "date": event_date.value, "time": event_time.value, "memo": memo.value.strip()})
            persist()
            close()
            render_calendar()

        dialog = ft.AlertDialog(title=ft.Text("予定を追加"), content=ft.Container(ft.Column([title, event_date, event_time, memo]), width=420), actions=[ft.TextButton("キャンセル", on_click=close), ft.Button("追加", on_click=submit)])
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    def delete_goal(goal_id):
        data["goals"] = [goal for goal in data["goals"] if goal["id"] != goal_id]
        persist()
        render_savings()

    def render_savings():
        rows = []
        for goal in data["goals"]:
            target = max(1, goal["target"])
            saved = goal["saved"]
            percent = min(100, round(saved / target * 100))
            rows.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row([ft.Text(goal["name"], weight=ft.FontWeight.W_600, expand=True), ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=COLORS["coral"], tooltip="削除", on_click=lambda _, goal_id=goal["id"]: delete_goal(goal_id))]),
                        ft.ProgressBar(value=saved / target, color=COLORS["green"], bgcolor=COLORS["green_light"]),
                        ft.Row([ft.Text(f"¥{saved:,}", weight=ft.FontWeight.W_600), ft.Text(f"目標 ¥{target:,} ・ {percent}%", color=COLORS["muted"])], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ], spacing=8),
                    padding=16,
                    bgcolor=COLORS["surface"],
                    border=ft.Border.all(1, COLORS["line"]),
                    border_radius=8,
                )
            )
        savings_view.controls = rows or [ft.Text("貯金目標はまだありません", color=COLORS["muted"])]
        page.update()

    def add_savings(goal=None):
        name = ft.TextField(label="目標名", value=goal.get("name", "") if goal else "", autofocus=True)
        target = ft.TextField(label="目標額", value=str(goal.get("target", "")) if goal else "", keyboard_type=ft.KeyboardType.NUMBER)
        saved = ft.TextField(label="現在の貯蓄額", value=str(goal.get("saved", "")) if goal else "0", keyboard_type=ft.KeyboardType.NUMBER)

        def close(_=None):
            dialog.open = False
            page.update()

        def submit(_):
            try:
                target_value = int(target.value)
                saved_value = int(saved.value)
            except ValueError:
                notify("金額は数字で入力してください")
                return
            if target_value <= 0 or saved_value < 0:
                notify("目標額と貯蓄額を確認してください")
                return
            record = {"id": goal["id"] if goal else str(__import__("uuid").uuid4()), "name": name.value.strip(), "target": target_value, "saved": saved_value}
            if not record["name"]:
                notify("目標名を入力してください")
                return
            if goal:
                goal.update(record)
            else:
                data["goals"].append(record)
            persist()
            close()
            render_savings()

        dialog = ft.AlertDialog(title=ft.Text("貯金目標"), content=ft.Container(ft.Column([name, target, saved]), width=420), actions=[ft.TextButton("キャンセル", on_click=close), ft.Button("保存", on_click=submit)])
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    def save_event(_):
        pass

    nav_buttons = {}
    content_area = ft.Container(expand=True)

    def show_tab(tab):
        nonlocal active_tab
        active_tab = tab
        for key, button in nav_buttons.items():
            selected = key == tab
            button.style = ft.ButtonStyle(
                color="white" if selected else COLORS["muted"],
                bgcolor=COLORS["green"] if selected else "transparent",
                shape=ft.RoundedRectangleBorder(radius=6),
            )
        content_area.content = {
            "collection": collection_layout,
            "search": search_layout,
            "calendar": calendar_layout,
            "savings": savings_layout,
        }[tab]
        page.update()

    def nav(name, label, icon):
        button = ft.TextButton(
            label,
            icon=icon,
            style=ft.ButtonStyle(
                color=COLORS["muted"],
                bgcolor="transparent",
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=lambda _: show_tab(name),
        )
        nav_buttons[name] = button
        return button

    collection_layout = ft.Column([
        ft.Row([ft.Text("所持コレクション", size=24, weight=ft.FontWeight.W_600, expand=True), ft.Button("グッズを追加", icon=ft.Icons.ADD, on_click=lambda event: add_manual())], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.TextField(prefix_icon=ft.Icons.SEARCH, hint_text="所持品を絞り込み", on_change=lambda event: render_collection(event.control.value)),
        collection_view,
    ], spacing=14)

    search_layout = ft.Column([
        ft.Text("グッズ検索・選択", size=24, weight=ft.FontWeight.W_600),
        card("グッズを検索して選択・登録", [
            ft.Row([text_search, ft.Button("検索", icon=ft.Icons.SEARCH, on_click=text_lookup)], spacing=8),
            ft.Text("商品名・作品名を入力すると、所持品から選ぶか新しいグッズとして登録できます。", size=12, color=COLORS["muted"]),
        ]),
        card("バーコード", [ft.Row([barcode_search, ft.Button("照合", icon=ft.Icons.QR_CODE_SCANNER, on_click=lookup_barcode)], spacing=8), ft.Text("公開商品データベースで検索します", size=12, color=COLORS["muted"])]),
        card("画像検索", [ft.Row([ft.Button("画像を選ぶ", icon=ft.Icons.PHOTO_CAMERA, on_click=find_by_image), ft.Text("アップロード画像と所持画像の完全一致を検索", expand=True, color=COLORS["muted"])])]),
        search_result_view,
    ], spacing=14)

    calendar_layout = ft.Column([
        ft.Row([ft.Text("カレンダー・予定", size=24, weight=ft.FontWeight.W_600, expand=True), ft.Button("予定を追加", icon=ft.Icons.ADD, on_click=add_event)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        calendar_view,
    ], spacing=14)

    savings_layout = ft.Column([
        ft.Row([ft.Text("貯金目標", size=24, weight=ft.FontWeight.W_600, expand=True), ft.Button("目標を追加", icon=ft.Icons.ADD, on_click=lambda _: add_savings())], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        savings_view,
    ], spacing=14)

    nav_bar = ft.Container(
        content=ft.Row([nav("search", "グッズ検索・選択", ft.Icons.SEARCH), nav("collection", "所持品", ft.Icons.INVENTORY_2_OUTLINED), nav("calendar", "予定", ft.Icons.CALENDAR_MONTH_OUTLINED), nav("savings", "貯金", ft.Icons.SAVINGS_OUTLINED)], wrap=True),
        bgcolor=COLORS["surface"],
        padding=8,
        border=ft.Border.all(1, COLORS["line"]),
        border_radius=8,
    )

    shell = ft.Column([
        ft.Row([ft.Text("コレクト", size=15, weight=ft.FontWeight.W_700, color=COLORS["green"]), ft.Text("グッズ管理", size=12, color=COLORS["muted"])], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        nav_bar,
        content_area,
    ], width=760, spacing=16)

    def resize(event):
        width = page.width or 760
        shell.width = min(760, max(280, width - 32))
        page.update()

    page.on_resize = resize
    page.add(shell)
    render_collection()
    render_calendar()
    render_savings()
    search_result_view.controls = [
        ft.Text(
            "商品名・作品名を入力して検索するか、登録済みグッズを選択してください。",
            color=COLORS["muted"],
        ),
        *[product_selection_card(item) for item in data["items"]],
        ft.Button(
            "新しいグッズを登録",
            icon=ft.Icons.ADD,
            on_click=lambda _: add_manual(),
        ),
    ]
    show_tab("search")


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.WEB_BROWSER, port=8000)
