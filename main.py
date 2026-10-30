import hashlib
import json
import re
import shutil
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


def fetch_web_products(query):
    params = urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": 6,
        "gsrnamespace": 0,
        "prop": "pageimages|info",
        "piprop": "thumbnail",
        "pithumbsize": 180,
        "inprop": "url",
    })
    request = urllib.request.Request(
        f"https://ja.wikipedia.org/w/api.php?{params}",
        headers={"User-Agent": "GoodsOrganizer/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.load(response)
    except (OSError, TimeoutError, json.JSONDecodeError):
        return []
    products = []
    for page in (payload.get("query", {}).get("pages", {}) or {}).values():
        thumbnail = page.get("thumbnail") or {}
        products.append({
            "name": page.get("title", "名称未登録"),
            "brand": "Wikipedia",
            "image": thumbnail.get("source", ""),
            "image_path": "",
            "barcode": "",
            "url": page.get("fullurl", ""),
        })
    return products


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
            status = "所持中" if item.get("owned", True) else "検討中"
            rows.append(
                ft.Container(
                    content=ft.Row(
                        [
                            item_image(item),
                            ft.Column(
                                [
                                    ft.Text(item_name(item), size=15, weight=ft.FontWeight.W_600),
                                    ft.Text(
                                        f"{item.get('barcode') or 'バーコード未登録'} ・ {status} ・ ¥{item.get('price', 0):,}",
                                        size=12,
                                        color=COLORS["muted"],
                                    ),
                                    ft.Text(item.get("memo", ""), size=12, color=COLORS["muted"]),
                                ],
                                expand=True,
                                spacing=3,
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

    async def add_manual(item=None, *args):
        item = item if isinstance(item, dict) else {}
        name_field = ft.TextField(label="商品名", value=item.get("name", ""), autofocus=True)
        brand_field = ft.TextField(label="ブランド・作品名", value=item.get("brand", ""))
        barcode_field = ft.TextField(label="バーコード", value=item.get("barcode", ""), keyboard_type=ft.KeyboardType.NUMBER)
        price_field = ft.TextField(label="購入金額", value=str(item.get("price", "")), keyboard_type=ft.KeyboardType.NUMBER)
        memo_field = ft.TextField(label="メモ", value=item.get("memo", ""), multiline=True, min_lines=2)
        owned_field = ft.Checkbox(label="所持中", value=item.get("owned", True))
        search_name = ft.TextField(label="インターネット検索", hint_text="商品名・作品名", expand=True)
        search_barcode = ft.TextField(label="バーコード検索", hint_text="バーコード数字", expand=True, keyboard_type=ft.KeyboardType.NUMBER)
        search_status = ft.Text(size=12, color=COLORS["muted"])
        search_candidates = ft.Column(spacing=6)
        selected_image = item.get("image", "")

        def close(_=None):
            dialog.open = False
            page.update()

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
            record = {
                "id": item.get("id") or str(__import__("uuid").uuid4()),
                "name": name,
                "brand": brand_field.value.strip(),
                "barcode": re.sub(r"\D", "", barcode_field.value),
                "price": price,
                "memo": memo_field.value.strip(),
                "owned": owned_field.value,
                "image": selected_image,
                "image_path": item.get("image_path", ""),
                "image_hash": item.get("image_hash", ""),
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
            nonlocal selected_image
            name_field.value = candidate.get("name", "")
            brand_field.value = candidate.get("brand", "")
            barcode_field.value = candidate.get("barcode", "")
            selected_image = candidate.get("image", "")
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
            search_status.value = "商品名を検索中..."
            page.update()
            candidates = fetch_web_products(query)
            if not candidates:
                search_status.value = "候補が見つかりませんでした"
                search_candidates.controls = []
            else:
                search_status.value = f"{len(candidates)} 件の候補"
                render_candidates(candidates)
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
            files = await image_picker.pick_files(file_type=ft.FilePickerFileType.IMAGE, allow_multiple=False)
            if not files:
                return
            source = Path(files[0].path)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            target = ASSET_DIR / f"{digest}{source.suffix or '.jpg'}"
            if not target.exists():
                shutil.copy2(source, target)
            nonlocal selected_image
            selected_image = str(target)
            search_status.value = "画像を選択しました。Google Lensで画像を検索できます"
            lens_url = f"https://lens.google.com/uploadbyurl?url={urllib.parse.quote(target.as_uri())}"
            search_candidates.controls = [
                ft.Row([
                    ft.Image(src=str(target), width=72, height=72, fit=ft.BoxFit.COVER, border_radius=6),
                    ft.TextButton("画像をネット検索", icon=ft.Icons.OPEN_IN_NEW, url=lens_url),
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
                    ft.Row([ft.Button("画像を選ぶ", icon=ft.Icons.PHOTO_CAMERA, on_click=pick_registration_image), ft.Text("画像を保存してネット検索", expand=True, size=12, color=COLORS["muted"])], spacing=8),
                    search_status,
                    search_candidates,
                    ft.Divider(height=1),
                    name_field,
                    brand_field,
                    barcode_field,
                    price_field,
                    memo_field,
                    owned_field,
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
        )
        if not files:
            return
        uploaded = files[0]
        source = Path(uploaded.path)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        matches = [item for item in data["items"] if item.get("image_hash") == digest]
        if matches:
            search_result_view.controls = [
                ft.Text("同じ画像のグッズが見つかりました", weight=ft.FontWeight.W_600),
                ft.Column([ft.Row([item_image(item), ft.Text(item_name(item), expand=True)]) for item in matches]),
            ]
        else:
            target = ASSET_DIR / f"{digest}{source.suffix or '.jpg'}"
            if not target.exists():
                shutil.copy2(source, target)
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
        query = normalize(text_search.value)
        matches = [item for item in data["items"] if query in normalize(item_name(item))]
        if matches:
            search_result_view.controls = [ft.Text(f"所持庫に {len(matches)} 件あります", weight=ft.FontWeight.W_600), *[ft.Row([item_image(item), ft.Text(item_name(item), expand=True)]) for item in matches]]
        elif query:
            encoded = urllib.parse.quote(text_search.value)
            search_result_view.controls = [ft.Text("所持庫にはありません"), ft.TextButton("Google で商品を検索", icon=ft.Icons.OPEN_IN_NEW, url=f"https://www.google.com/search?q={encoded}")]
        else:
            search_result_view.controls = [ft.Text("検索語を入力してください")]
        page.update()

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
        ft.Text("検索", size=24, weight=ft.FontWeight.W_600),
        card("商品名・所持情報", [ft.Row([text_search, ft.Button("検索", icon=ft.Icons.SEARCH, on_click=text_lookup)], spacing=8)]),
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
        content=ft.Row([nav("collection", "所持品", ft.Icons.INVENTORY_2_OUTLINED), nav("search", "検索", ft.Icons.SEARCH), nav("calendar", "予定", ft.Icons.CALENDAR_MONTH_OUTLINED), nav("savings", "貯金", ft.Icons.SAVINGS_OUTLINED)], wrap=True),
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
    show_tab("collection")


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.WEB_BROWSER, port=8000)
