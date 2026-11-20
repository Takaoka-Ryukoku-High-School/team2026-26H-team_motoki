import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

import flet as ft


DATA_FILE = Path.home() / ".goods_organizer.json"
ASSET_DIR = Path.home() / ".goods_organizer_images"
AUTH_DB = Path.home() / ".goods_organizer_accounts.sqlite3"
USER_DATA_DIR = Path.home() / ".goods_organizer_users"
PASSWORD_ITERATIONS = 600_000

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


def load_data(data_file=DATA_FILE):
    try:
        value = json.loads(data_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_data()
    if not isinstance(value, dict):
        raise ValueError("保存データの形式が正しくありません")
    return {**default_data(), **value}


def ensure_private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)


def save_data(data, data_file):
    ensure_private_directory(data_file.parent)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=data_file.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(data, temporary_file, ensure_ascii=False, indent=2)
        if os.name == "posix":
            temporary_path.chmod(0o600)
        os.replace(temporary_path, data_file)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def user_storage_paths(email):
    ensure_private_directory(USER_DATA_DIR)
    user_key = hashlib.sha256(email.strip().casefold().encode("utf-8")).hexdigest()
    return USER_DATA_DIR / f"{user_key}.json", USER_DATA_DIR / f"{user_key}_images"


def initialize_auth_store():
    AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(AUTH_DB, timeout=10) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                email TEXT PRIMARY KEY,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                iterations INTEGER NOT NULL
            )
            """
        )
    if os.name == "posix":
        AUTH_DB.chmod(0o600)


def _password_hash(password, salt, iterations=None):
    if iterations is None:
        iterations = PASSWORD_ITERATIONS
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def migrate_legacy_images(data, target_asset_dir):
    source_root = ASSET_DIR.resolve()
    for item in data.get("items", []):
        source_value = item.get("image_path")
        if not source_value:
            continue
        try:
            source = Path(source_value).resolve(strict=True)
            source.relative_to(source_root)
        except (OSError, ValueError):
            continue
        if not source.is_file():
            continue
        ensure_private_directory(target_asset_dir)
        target = target_asset_dir / source.name
        shutil.copyfile(source, target)
        if os.name == "posix":
            target.chmod(0o600)
        item["image_path"] = str(target)


def create_account(email, password):
    email = email.strip().casefold()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("メールアドレスを確認してください")
    if not 12 <= len(password) <= 128:
        raise ValueError("パスワードの長さを確認してください")
    salt = secrets.token_bytes(16)
    password_hash = _password_hash(password, salt)
    connection = sqlite3.connect(AUTH_DB, timeout=10)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM accounts WHERE email = ?", (email,)).fetchone():
            connection.rollback()
            return False
        first_account = not connection.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
        connection.execute(
            "INSERT INTO accounts (email, salt, password_hash, iterations) VALUES (?, ?, ?, ?)",
            (email, salt.hex(), password_hash, PASSWORD_ITERATIONS),
        )
        if first_account and DATA_FILE.exists():
            legacy_data = load_data(DATA_FILE)
            data_file, asset_dir = user_storage_paths(email)
            migrate_legacy_images(legacy_data, asset_dir)
            save_data(legacy_data, data_file)
        connection.commit()
        return True
    finally:
        connection.close()


def authenticate_account(email, password):
    email = email.strip().casefold()
    with sqlite3.connect(AUTH_DB, timeout=10) as connection:
        account = connection.execute(
            "SELECT salt, password_hash, iterations FROM accounts WHERE email = ?",
            (email,),
        ).fetchone()
    if account is None:
        hmac.compare_digest(_password_hash(password, b"goods-organizer-dummy-salt"), "0" * 64)
        return False
    salt, expected_hash, iterations = account
    actual_hash = _password_hash(password, bytes.fromhex(salt), iterations)
    return hmac.compare_digest(actual_hash, expected_hash)


def normalize(value):
    return re.sub(r"[\s　]+", "", (value or "")).casefold()


def item_purchase_status(item):
    return item.get("purchase_status", "purchased" if item.get("owned", True) else "planned")


def google_shopping_url(query):
    params = urllib.parse.urlencode({"tbm": "shop", "q": query})
    return f"https://www.google.com/search?{params}"


def save_picked_image(file, asset_dir):
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
    ensure_private_directory(asset_dir)
    target = asset_dir / f"{digest}{suffix or '.jpg'}"
    if not target.exists():
        target.write_bytes(content)
        if os.name == "posix":
            target.chmod(0o600)
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

    initialize_auth_store()
    data = default_data()
    current_user_email = None
    current_data_file = None
    current_asset_dir = None
    guest_temp_dir = None
    is_guest = False

    active_tab = "collection"
    collection_view = ft.Column(spacing=12)
    search_result_view = ft.Column(spacing=10)
    calendar_view = ft.Column(spacing=12)
    savings_view = ft.Column(spacing=12)
    notification_list = ft.Column(spacing=8)
    snackbar = ft.SnackBar(content=ft.Text(""))
    page.overlay.append(snackbar)

    def notify(message):
        snackbar.content.value = message
        snackbar.open = True
        page.update()

    def make_notification_row(label, detail, color=COLORS["muted"], on_click=None):
        return ft.Container(
            content=ft.Row([
                ft.Container(width=8, height=8, bgcolor=color, border_radius=4),
                ft.Column([
                    ft.Text(label, size=13, weight=ft.FontWeight.W_600),
                    ft.Text(detail, size=11, color=COLORS["muted"]),
                ], expand=True, spacing=2),
            ], vertical_alignment=ft.CrossAxisAlignment.START),
            padding=10,
            bgcolor=COLORS["surface"],
            border=ft.Border.all(1, COLORS["line"]),
            border_radius=8,
            on_click=on_click,
            ink=True,
        )

    def render_notifications():
        notifications = []
        today = date.today()
        for event in sorted(data["events"], key=lambda entry: (entry.get("date", "9999-12-31"), entry.get("time", "23:59"))):
            event_date_raw = (event.get("date") or "").strip()
            event_time_raw = (event.get("time") or "00:00").strip()
            if not event_date_raw:
                continue
            try:
                event_dt = datetime.strptime(f"{event_date_raw} {event_time_raw}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            delta_days = (event_dt.date() - today).days
            if delta_days < 0:
                label = f"予定超過: {event.get('title', '予定')}"
                detail = f"{event_date_raw} {event_time_raw}・予定を確認してください"
                color = COLORS["coral"]
            elif delta_days == 0:
                label = f"本日: {event.get('title', '予定')}"
                detail = f"{event_date_raw} {event_time_raw}・今日が予定日です"
                color = COLORS["gold"]
            elif delta_days <= 3:
                label = f"もうすぐ: {event.get('title', '予定')}"
                detail = f"{event_date_raw} {event_time_raw}・あと{delta_days}日"
                color = COLORS["green"]
            elif delta_days <= 7:
                label = f"予定が近い: {event.get('title', '予定')}"
                detail = f"{event_date_raw} {event_time_raw}・あと{delta_days}日"
                color = COLORS["green_light"]
            else:
                continue
            notifications.append(make_notification_row(label, detail, color=color, on_click=lambda _: show_tab("calendar")))

        for item in data["items"]:
            planned_date_raw = (item.get("planned_purchase_date") or "").strip()
            if item_purchase_status(item) != "planned" or not planned_date_raw:
                continue
            try:
                planned_date = date.fromisoformat(planned_date_raw)
            except ValueError:
                continue
            delta_days = (planned_date - today).days
            if delta_days < 0:
                label = f"購入予定日超過: {item_name(item)}"
                detail = f"{planned_date_raw}・購入予定日を過ぎています"
                color = COLORS["coral"]
            elif delta_days == 0:
                label = f"本日購入予定: {item_name(item)}"
                detail = f"{planned_date_raw}・今日が購入予定日です"
                color = COLORS["gold"]
            elif delta_days <= 7:
                label = f"購入予定日が近い: {item_name(item)}"
                detail = f"{planned_date_raw}・あと{delta_days}日です"
                color = COLORS["green"]
            else:
                continue
            notifications.append(
                make_notification_row(
                    label,
                    detail,
                    color=color,
                    on_click=lambda _: show_tab("collection"),
                )
            )

        for goal in data["goals"]:
            target = max(1, goal.get("target", 0))
            saved = goal.get("saved", 0)
            if saved >= target:
                continue

            deadline_raw = (goal.get("deadline") or "").strip()
            if deadline_raw:
                try:
                    deadline_date = date.fromisoformat(deadline_raw)
                except ValueError:
                    deadline_date = None
                if deadline_date is not None:
                    delta_days = (deadline_date - today).days
                    if delta_days < 0:
                        notifications.append(
                            make_notification_row(
                                f"貯金期限超過: {goal.get('name', '目標')}",
                                f"{deadline_raw}・目標達成が遅れています",
                                color=COLORS["coral"],
                                on_click=lambda _: show_tab("savings"),
                            )
                        )
                        continue
                    if delta_days <= 7:
                        notifications.append(
                            make_notification_row(
                                f"貯金期限が近い: {goal.get('name', '目標')}",
                                f"{deadline_raw}・あと{delta_days}日です",
                                color=COLORS["gold"],
                                on_click=lambda _: show_tab("savings"),
                            )
                        )
                        continue

            percent = min(100, round(saved / target * 100))
            if percent < 50:
                notifications.append(
                    make_notification_row(
                        f"貯金目標: {goal.get('name', '目標')}",
                        f"進捗 {percent}%・あと¥{target - saved:,}です",
                        color=COLORS["gold"],
                        on_click=lambda _, goal_name=goal.get("name", "目標"): show_tab("savings"),
                    )
                )

        planned_items = [item for item in data["items"] if item_purchase_status(item) == "planned"]
        if planned_items and len(notifications) < 3:
            notifications.append(
                make_notification_row(
                    "購入予定のグッズがあります",
                    f"未購入のグッズが{len(planned_items)}件あります",
                    color=COLORS["green"],
                    on_click=lambda _: show_tab("collection"),
                )
            )

        notification_count = len(notifications)
        notification_panel.content.controls[0].controls[1].content.value = str(notification_count) if notification_count else "0"
        notification_panel.content.controls[0].controls[1].bgcolor = COLORS["green"] if notification_count else COLORS["muted"]
        notification_panel.content.controls[1].controls = notifications[:5] if notifications else [
            ft.Container(
                content=ft.Text("新しい通知はありません", color=COLORS["muted"]),
                padding=8,
            )
        ]

    def persist():
        if is_guest:
            return
        if current_data_file is None:
            notify("ログインしてからデータを保存してください")
            return
        try:
            save_data(data, current_data_file)
        except OSError:
            notify("保存できませんでした。ストレージの容量や権限を確認してください")

    def item_name(item):
        return f"{item.get('brand', '')} {item.get('name', '')}".strip()

    def item_image(item):
        if item.get("image_path"):
            path = Path(item["image_path"])
            if path.exists():
                return ft.Image(src=path.read_bytes(), width=72, height=72, fit=ft.BoxFit.COVER, border_radius=6)
        if item.get("image"):
            return ft.Image(src=item["image"], width=72, height=72, fit=ft.BoxFit.COVER, border_radius=6)
        return ft.Container(width=72, height=72, bgcolor=COLORS["green_light"], border_radius=6)

    def delete_item(item_id):
        item = next((entry for entry in data["items"] if entry["id"] == item_id), None)
        if item is None:
            return

        def close(_=None):
            dialog.open = False
            page.update()

        def confirm(_):
            data["items"] = [entry for entry in data["items"] if entry["id"] != item_id]
            persist()
            close()
            render_collection()
            render_calendar()

        dialog = ft.AlertDialog(
            title=ft.Text("グッズを削除"),
            content=ft.Text(f"「{item_name(item)}」を削除しますか？この操作は取り消せません。"),
            actions=[
                ft.TextButton("キャンセル", on_click=close),
                ft.Button("削除", on_click=confirm),
            ],
        )
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

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
                            *(
                                [
                                    ft.TextButton(
                                        "商品ページ",
                                        icon=ft.Icons.OPEN_IN_NEW,
                                        url=item["url"],
                                    )
                                ]
                                if item.get("url")
                                else []
                            ),
                            ft.TextButton(
                                "削除",
                                icon=ft.Icons.DELETE_OUTLINE,
                                on_click=lambda _, item_id=item["id"]: delete_item(item_id),
                            ),
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
        render_notifications()
        page.update()

    def add_manual(item=None, *args):
        item = item if isinstance(item, dict) else {}
        name_field = ft.TextField(label="商品名（必須）", value=item.get("name", ""), autofocus=True)
        brand_field = ft.TextField(label="ブランド・作品名", value=item.get("brand", ""))
        barcode_field = ft.TextField(label="バーコード", value=item.get("barcode", ""), keyboard_type=ft.KeyboardType.NUMBER)
        price_field = ft.TextField(label="購入金額", value=str(item.get("price", "")), keyboard_type=ft.KeyboardType.NUMBER)
        memo_field = ft.TextField(label="メモ", value=item.get("memo", ""), multiline=True, min_lines=2)
        product_url_field = ft.TextField(
            label="商品ページURL（必須）",
            value=item.get("url") or "",
            hint_text="商品の販売ページURLを貼り付け",
            keyboard_type=ft.KeyboardType.URL,
        )
        quantity_field = ft.TextField(
            label="購入個数（購入済みの場合必須）",
            value=str(item.get("quantity", "")),
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        purchase_status_field = ft.RadioGroup(
            content=ft.Column([
                ft.Radio(value="purchased", label="購入済み"),
                ft.Radio(value="planned", label="購入予定"),
            ]),
            value=item_purchase_status(item) if item.get("id") else None,
        )
        planned_purchase_date_field = ft.TextField(
            label="購入予定日（任意）",
            value=item.get("planned_purchase_date", ""),
            hint_text="YYYY-MM-DD",
        )
        search_name = ft.TextField(label="インターネット検索", hint_text="商品名・作品名", expand=True)
        search_barcode = ft.TextField(label="バーコード検索", hint_text="バーコード数字", expand=True, keyboard_type=ft.KeyboardType.NUMBER)
        search_status = ft.Text(size=12, color=COLORS["muted"])
        search_candidates = ft.Column(spacing=6)
        selected_image = item.get("image", "")
        selected_image_path = item.get("image_path", "")
        selected_image_hash = item.get("image_hash", "")
        image_preview = ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def render_registration_image():
            if selected_image_path and Path(selected_image_path).exists():
                preview = ft.Image(
                    src=Path(selected_image_path).read_bytes(),
                    width=72,
                    height=72,
                    fit=ft.BoxFit.COVER,
                    border_radius=6,
                )
            elif selected_image:
                preview = ft.Image(
                    src=selected_image,
                    width=72,
                    height=72,
                    fit=ft.BoxFit.COVER,
                    border_radius=6,
                )
            else:
                preview = ft.Container(
                    content=ft.Text("未添付", size=11, color=COLORS["muted"]),
                    width=72,
                    height=72,
                    bgcolor=COLORS["green_light"],
                    border_radius=6,
                    alignment=ft.Alignment.CENTER,
                )
            image_preview.controls = [
                preview,
                ft.Text(
                    "画像を添付済み" if selected_image_path or selected_image else "画像は任意です",
                    size=12,
                    color=COLORS["muted"],
                ),
            ]

        def close(_=None):
            dialog.open = False
            page.update()

        def update_purchase_fields(_=None):
            quantity_field.visible = purchase_status_field.value == "purchased"
            planned_purchase_date_field.visible = purchase_status_field.value == "planned"
            page.update()

        purchase_status_field.on_change = update_purchase_fields
        quantity_field.visible = purchase_status_field.value == "purchased"
        planned_purchase_date_field.visible = purchase_status_field.value == "planned"

        def submit(_):
            name = name_field.value.strip()
            if not name:
                notify("商品名を入力してください")
                return
            if not price_field.value.strip():
                notify("購入金額を入力してください")
                return
            try:
                price = int(price_field.value)
            except ValueError:
                notify("購入金額は数字で入力してください")
                return
            if price < 0:
                notify("購入金額は0以上で入力してください")
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
            planned_purchase_date = planned_purchase_date_field.value.strip()
            if planned_purchase_date:
                try:
                    date.fromisoformat(planned_purchase_date)
                except ValueError:
                    notify("購入予定日は YYYY-MM-DD 形式で入力してください")
                    return
            product_url = product_url_field.value.strip()
            if not product_url:
                notify("商品ページURLを入力してください")
                return
            parsed_product_url = urllib.parse.urlsplit(product_url)
            if parsed_product_url.scheme not in ("http", "https") or not parsed_product_url.netloc:
                notify("商品ページURLは http:// または https:// から始まるURLを入力してください")
                return
            record = {
                "id": item.get("id") or str(__import__("uuid").uuid4()),
                "name": name,
                "brand": brand_field.value.strip(),
                "barcode": re.sub(r"\D", "", barcode_field.value),
                "price": price,
                "memo": memo_field.value.strip(),
                "url": product_url,
                "purchase_status": purchase_status,
                "owned": purchase_status == "purchased",
                "quantity": quantity,
                "planned_purchase_date": planned_purchase_date,
                "image": selected_image,
                "image_path": selected_image_path,
                "image_hash": selected_image_hash,
            }
            existing = next((entry for entry in data["items"] if entry["id"] == record["id"]), None)
            if existing:
                existing.update(record)
            else:
                data["items"].insert(0, record)
            persist()
            close()
            render_collection()
            render_calendar()

        def apply_candidate(candidate):
            nonlocal selected_image, selected_image_path, selected_image_hash
            name_field.value = candidate.get("name", "")
            brand_field.value = candidate.get("brand", "")
            barcode_field.value = candidate.get("barcode", "")
            product_url_field.value = candidate.get("url") or ""
            selected_image = candidate.get("image") or ""
            selected_image_path = candidate.get("image_path", "")
            selected_image_hash = candidate.get("image_hash", "")
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
            asset_dir = current_asset_dir
            if asset_dir is None:
                notify("画像を保存するにはログインしてください")
                return
            try:
                files = await image_picker.pick_files(
                    file_type=ft.FilePickerFileType.IMAGE,
                    allow_multiple=False,
                    with_data=True,
                    cancel_upload_on_window_blur=False,
                )
            except RuntimeError as error:
                notify(f"画像を選択できませんでした: {error}")
                return
            if not files:
                return
            nonlocal selected_image, selected_image_path, selected_image_hash
            try:
                target, digest = save_picked_image(files[0], asset_dir)
            except (OSError, ValueError) as error:
                notify(f"画像を読み込めませんでした: {error}")
                return
            selected_image = ""
            selected_image_path = str(target)
            selected_image_hash = digest
            render_registration_image()
            search_status.value = "選択した画像はグッズと一緒に保存されます"
            page.update()

        render_registration_image()
        dialog = ft.AlertDialog(
            title=ft.Text("グッズを編集" if item.get("id") else "グッズを登録"),
            content=ft.Container(
                content=ft.Column([
                    ft.Text("インターネットから検索", weight=ft.FontWeight.W_600, color=COLORS["green"]),
                    ft.Row([search_name, ft.Button("商品名", icon=ft.Icons.SEARCH, on_click=search_name_online)], spacing=8),
                    ft.Row([search_barcode, ft.Button("バーコード", icon=ft.Icons.QR_CODE_SCANNER, on_click=search_barcode_online)], spacing=8),
                    ft.Text("画像を添付", weight=ft.FontWeight.W_600, color=COLORS["green"]),
                    ft.Row([
                        ft.Button("端末のフォルダから画像を選択", icon=ft.Icons.PHOTO_CAMERA, on_click=pick_registration_image),
                        image_preview,
                    ], spacing=8),
                    ft.Text("選択した画像ファイルはアプリ内に保存されます。", size=11, color=COLORS["muted"]),
                    search_status,
                    search_candidates,
                    ft.Divider(height=1),
                    ft.Text(
                        "必須: 商品名・商品ページURL・購入状況・購入金額。購入済みの場合は個数も必須です。画像・作品名/ブランド・バーコード・メモは任意です。",
                        size=12,
                        color=COLORS["muted"],
                    ),
                    name_field,
                    brand_field,
                    barcode_field,
                    price_field,
                    memo_field,
                    product_url_field,
                    ft.Text(
                        "商品ページURLを登録すると、所持品や検索結果から商品ページを開けます。画像は端末から選択できます。",
                        size=11,
                        color=COLORS["muted"],
                    ),
                    ft.Text("購入状況", weight=ft.FontWeight.W_600),
                    purchase_status_field,
                    planned_purchase_date_field,
                    quantity_field,
                ], tight=True, scroll=ft.ScrollMode.ADAPTIVE),
                width=520,
                height=560,
            ),
            actions=[ft.TextButton("キャンセル", on_click=close), ft.Button("保存" if item.get("id") else "登録", on_click=submit)],
        )
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    async def find_by_image(_):
        asset_dir = current_asset_dir
        if asset_dir is None:
            notify("画像を検索するにはログインしてください")
            return
        try:
            files = await image_picker.pick_files(
                file_type=ft.FilePickerFileType.IMAGE,
                allow_multiple=False,
                with_data=True,
                cancel_upload_on_window_blur=False,
            )
        except RuntimeError as error:
            notify(f"画像を選択できませんでした: {error}")
            return
        if not files:
            return
        uploaded = files[0]
        try:
            target, digest = save_picked_image(uploaded, asset_dir)
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
                ft.Image(src=target.read_bytes(), height=220, fit=ft.BoxFit.CONTAIN),
                ft.Button("この画像で登録", on_click=lambda _: add_manual({"name": "", "image_path": str(target), "image_hash": digest})),
            ]
        page.update()

    image_picker = ft.FilePicker()
    page.services.append(image_picker)

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
                            "このボタンでアプリ内の商品登録に進みます。必要に応じて端末の画像ファイルを選択してください。",
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
                            *(
                                [
                                    ft.TextButton(
                                        "商品ページを開く",
                                        icon=ft.Icons.OPEN_IN_NEW,
                                        url=item["url"],
                                    )
                                ]
                                if item.get("url")
                                else []
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
        render_notifications()

    def render_calendar():
        rows = []
        schedule_entries = [
            (event["date"], event["time"], "event", event)
            for event in data["events"]
        ]
        for item in data["items"]:
            planned_date = (item.get("planned_purchase_date") or "").strip()
            if item_purchase_status(item) != "planned" or not planned_date:
                continue
            try:
                date.fromisoformat(planned_date)
            except ValueError:
                continue
            schedule_entries.append((planned_date, "12:00", "purchase", item))

        for entry_date, entry_time, entry_type, entry in sorted(schedule_entries, key=lambda scheduled: (scheduled[0], scheduled[1])):
            if entry_type == "purchase":
                rows.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Column([
                                ft.Text(entry_date, color=COLORS["green"], weight=ft.FontWeight.W_600),
                                ft.Text("購入予定", size=12, color=COLORS["muted"]),
                            ], width=85),
                            ft.Column([
                                ft.Text(f"購入予定: {item_name(entry)}"),
                                ft.Text("変更は所持品の編集から行えます", size=12, color=COLORS["muted"]),
                            ], expand=True),
                            ft.IconButton(
                                ft.Icons.EDIT_OUTLINED,
                                tooltip="グッズ情報から購入予定日を変更",
                                on_click=lambda _, value=entry: add_manual(value),
                            ),
                        ]),
                        padding=12,
                        bgcolor=COLORS["surface"],
                        border=ft.Border.all(1, COLORS["line"]),
                        border_radius=8,
                    )
                )
                continue

            event = entry
            rows.append(
                ft.Container(
                    content=ft.Row([
                        ft.Column([ft.Text(event["date"], color=COLORS["green"], weight=ft.FontWeight.W_600), ft.Text(event["time"], size=12, color=COLORS["muted"])], width=85),
                        ft.Column([ft.Text(event["title"]), ft.Text(event.get("memo", ""), size=12, color=COLORS["muted"])], expand=True),
                        ft.IconButton(
                            ft.Icons.EDIT_OUTLINED,
                            tooltip="予定を編集",
                            on_click=lambda _, value=event: add_event(value),
                        ),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=COLORS["coral"], tooltip="削除", on_click=lambda _, event_id=event["id"]: delete_event(event_id)),
                    ]),
                    padding=12,
                    bgcolor=COLORS["surface"],
                    border=ft.Border.all(1, COLORS["line"]),
                    border_radius=8,
                )
            )
        calendar_view.controls = rows or [ft.Text("予定はまだありません", color=COLORS["muted"])]
        render_notifications()
        page.update()

    def add_event(event=None):
        title = ft.TextField(label="予定名", value=event.get("title", "") if event else "", autofocus=True)
        event_date = ft.TextField(label="日付", value=event.get("date", date.today().isoformat()) if event else date.today().isoformat(), hint_text="YYYY-MM-DD")
        event_time = ft.TextField(label="時刻", value=event.get("time", "12:00") if event else "12:00", hint_text="HH:MM")
        memo = ft.TextField(label="メモ", value=event.get("memo", "") if event else "", multiline=True, min_lines=2)

        def close(_=None):
            dialog.open = False
            page.update()

        def submit(_):
            title_value = title.value.strip()
            date_value = event_date.value.strip()
            time_value = event_time.value.strip()
            try:
                date.fromisoformat(date_value)
                datetime.strptime(time_value, "%H:%M")
            except ValueError:
                notify("日付と時刻の形式を確認してください")
                return
            if not title_value:
                notify("予定名を入力してください")
                return
            record = {
                "id": event["id"] if event else str(__import__("uuid").uuid4()),
                "title": title_value,
                "date": date_value,
                "time": time_value,
                "memo": memo.value.strip(),
            }
            if event:
                event.update(record)
            else:
                data["events"].append(record)
            persist()
            close()
            render_calendar()
            render_notifications()

        dialog = ft.AlertDialog(title=ft.Text("予定を編集" if event else "予定を追加"), content=ft.Container(ft.Column([title, event_date, event_time, memo]), width=420), actions=[ft.TextButton("キャンセル", on_click=close), ft.Button("保存" if event else "追加", on_click=submit)])
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    def delete_goal(goal_id):
        data["goals"] = [goal for goal in data["goals"] if goal["id"] != goal_id]
        persist()
        render_savings()
        render_notifications()

    def render_savings():
        rows = []
        for goal in data["goals"]:
            target = max(1, goal["target"])
            saved = goal["saved"]
            percent = min(100, round(saved / target * 100))
            deadline = (goal.get("deadline") or "").strip()
            due_text = f"期限: {deadline}" if deadline else "期限: 未設定"
            deadline_color = COLORS["muted"]
            if deadline:
                try:
                    due_date = date.fromisoformat(deadline)
                    if due_date < date.today():
                        deadline_color = COLORS["coral"]
                    elif (due_date - date.today()).days <= 7:
                        deadline_color = COLORS["gold"]
                except ValueError:
                    due_text = f"期限: {deadline}（形式不備）"
                    deadline_color = COLORS["coral"]
            rows.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text(goal["name"], weight=ft.FontWeight.W_600, expand=True),
                            ft.IconButton(
                                ft.Icons.EDIT_OUTLINED,
                                tooltip="貯金目標を編集",
                                on_click=lambda _, value=goal: add_savings(value),
                            ),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=COLORS["coral"], tooltip="削除", on_click=lambda _, goal_id=goal["id"]: delete_goal(goal_id)),
                        ]),
                        ft.ProgressBar(value=saved / target, color=COLORS["green"], bgcolor=COLORS["green_light"]),
                        ft.Row([ft.Text(f"¥{saved:,}", weight=ft.FontWeight.W_600), ft.Text(f"目標 ¥{target:,} ・ {percent}%", color=COLORS["muted"])], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        ft.Text(due_text, size=12, color=deadline_color),
                    ], spacing=8),
                    padding=16,
                    bgcolor=COLORS["surface"],
                    border=ft.Border.all(1, COLORS["line"]),
                    border_radius=8,
                )
            )
        savings_view.controls = rows or [ft.Text("貯金目標はまだありません", color=COLORS["muted"])]
        render_notifications()
        page.update()

    def add_savings(goal=None):
        name = ft.TextField(label="目標名", value=goal.get("name", "") if goal else "", autofocus=True)
        target = ft.TextField(label="目標額", value=str(goal.get("target", "")) if goal else "", keyboard_type=ft.KeyboardType.NUMBER)
        saved = ft.TextField(label="現在の貯蓄額", value=str(goal.get("saved", "")) if goal else "0", keyboard_type=ft.KeyboardType.NUMBER)
        deadline = ft.TextField(label="達成予定日", value=str(goal.get("deadline", "")) if goal else "", hint_text="YYYY-MM-DD")

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
            deadline_value = deadline.value.strip()
            if deadline_value:
                try:
                    date.fromisoformat(deadline_value)
                except ValueError:
                    notify("達成予定日は YYYY-MM-DD 形式で入力してください")
                    return
            record = {"id": goal["id"] if goal else str(__import__("uuid").uuid4()), "name": name.value.strip(), "target": target_value, "saved": saved_value, "deadline": deadline_value}
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
            render_notifications()

        dialog = ft.AlertDialog(title=ft.Text("貯金目標"), content=ft.Container(ft.Column([name, target, saved, deadline]), width=420), actions=[ft.TextButton("キャンセル", on_click=close), ft.Button("保存", on_click=submit)])
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
            "safety": safety_layout,
        }[tab]
        render_notifications()
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

    safety_layout = ft.Column([
        ft.Text("安心・データについて", size=24, weight=ft.FontWeight.W_600),
        card("AI・センサー利用の倫理配慮", [
            ft.Text(
                "AIによる自動判定や、カメラ・位置情報などのセンサーによる自動取得は行いません。"
                "画像は利用者が選択します。人物が写った画像を使う場合は、本人の許可を得てください。"
                "バーコードの商品候補は、登録前に内容を確認できます。",
                size=13,
                color=COLORS["muted"],
            ),
        ]),
        card("データの扱い・安全性", [
            ft.Text(
                "メールアドレスをログインIDとして登録し、パスワードはハッシュ化して保存します。"
                "グッズ・予定・貯金目標と画像はアカウントごとに分けて、アプリ実行端末に保存します。"
                "データ自体は暗号化していません。メール確認とパスワード再設定には対応していません。"
                "バーコード照合では番号をOpen Food Factsへ送信し、"
                "Google ショッピングを開くと入力した検索語がGoogleへ送信されます。"
                "個人情報をメモや検索語に入力しないでください。",
                size=13,
                color=COLORS["muted"],
            ),
        ]),
        card("利用者が安心して使うために", [
            ft.Text(
                "グッズ・予定・貯金目標は画面から個別に確認・編集・削除できます。"
                "画像検索は、選んだ画像と登録画像の一致を端末内で確認します。"
                "アカウント利用時の画像ファイルは端末のファイル管理からも確認できます。"
                "ゲスト利用のデータは一時的にメモリと一時領域だけに保存され、ページ切断時に破棄されます。",
                size=13,
                color=COLORS["muted"],
            ),
        ]),
    ], spacing=14)

    nav_bar = ft.Container(
        content=ft.Row([nav("search", "グッズ検索・選択", ft.Icons.SEARCH), nav("collection", "所持品", ft.Icons.INVENTORY_2_OUTLINED), nav("calendar", "予定", ft.Icons.CALENDAR_MONTH_OUTLINED), nav("savings", "貯金", ft.Icons.SAVINGS_OUTLINED), nav("safety", "安心・データ", ft.Icons.SHIELD_OUTLINED)], wrap=True),
        bgcolor=COLORS["surface"],
        padding=8,
        border=ft.Border.all(1, COLORS["line"]),
        border_radius=8,
    )

    notification_panel = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Text("通知", size=16, weight=ft.FontWeight.W_600, color=COLORS["ink"]),
                ft.Container(
                    content=ft.Text("", size=11, weight=ft.FontWeight.W_600, color="white"),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                    bgcolor=COLORS["green"],
                    border_radius=12,
                ),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            notification_list,
        ], spacing=8),
        padding=14,
        bgcolor=COLORS["surface"],
        border=ft.Border.all(1, COLORS["line"]),
        border_radius=8,
    )

    auth_mode = "login"
    auth_title = ft.Text("ログイン", size=20, weight=ft.FontWeight.W_600)
    auth_status = ft.Text("", size=12, color=COLORS["coral"])
    email_field = ft.TextField(
        label="メールアドレス",
        keyboard_type=ft.KeyboardType.EMAIL,
        autofocus=True,
    )
    password_field = ft.TextField(
        label="パスワード",
        password=True,
        can_reveal_password=True,
    )
    password_confirm_field = ft.TextField(
        label="パスワード（確認）",
        password=True,
        can_reveal_password=True,
        visible=False,
    )
    auth_submit_button = ft.Button("ログイン")
    auth_mode_button = ft.TextButton("アカウントを新規作成")
    guest_button = ft.OutlinedButton("アカウントなしでゲスト利用")
    logout_button = ft.TextButton("ログアウト", icon=ft.Icons.LOGOUT)
    auth_shell = ft.Column([
        ft.Text("コレクト | グッズ管理", size=18, weight=ft.FontWeight.W_700, color=COLORS["green"]),
        card("アカウント", [
            auth_title,
            email_field,
            password_field,
            password_confirm_field,
            auth_submit_button,
            auth_mode_button,
            guest_button,
            auth_status,
            ft.Text(
                "メールアドレスはログインIDとして使います。確認メール・パスワード再設定はありません。"
                "最初に登録したアカウントには、以前のローカルデータがあれば移行されます。"
                "公開リンクを共有する前に、所有者が最初に登録してください。",
                size=11,
                color=COLORS["muted"],
            ),
            ft.Text(
                "ゲスト利用のデータはこのページを開いている間だけ保持され、ログアウトまたはページ切断時に破棄されます。",
                size=11,
                color=COLORS["muted"],
            ),
        ]),
    ], width=480, spacing=16)

    guest_notice = ft.Text(
        "ゲスト利用中です。このページを離れるとデータは破棄されます。",
        size=12,
        color=COLORS["coral"],
        visible=False,
    )
    shell = ft.Column([
        ft.Row([
            ft.Text("コレクト", size=15, weight=ft.FontWeight.W_700, color=COLORS["green"]),
            ft.Text("グッズ管理", size=12, color=COLORS["muted"], expand=True),
            logout_button,
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        guest_notice,
        nav_bar,
        notification_panel,
        content_area,
    ], width=760, spacing=16, visible=False)

    def set_auth_mode(mode):
        nonlocal auth_mode
        auth_mode = mode
        is_registering = mode == "register"
        auth_title.value = "アカウント作成" if is_registering else "ログイン"
        auth_submit_button.text = "登録してログイン" if is_registering else "ログイン"
        auth_mode_button.text = "ログインに戻る" if is_registering else "アカウントを新規作成"
        password_confirm_field.visible = is_registering
        auth_status.value = ""
        page.update()

    def open_account(email):
        nonlocal current_user_email, current_data_file, current_asset_dir
        nonlocal guest_temp_dir, is_guest
        if guest_temp_dir is not None:
            guest_temp_dir.cleanup()
            guest_temp_dir = None
        data_file, asset_dir = user_storage_paths(email)
        account_data = load_data(data_file)
        ensure_private_directory(asset_dir)
        data.clear()
        data.update(account_data)
        current_user_email = email
        current_data_file = data_file
        current_asset_dir = asset_dir
        is_guest = False
        guest_notice.visible = False
        logout_button.text = "ログアウト"
        activate_app()

    def activate_app():
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
        auth_shell.visible = False
        shell.visible = True
        render_collection()
        render_calendar()
        render_savings()
        render_notifications()
        show_tab("search")

    def open_guest(_):
        nonlocal current_user_email, current_data_file, current_asset_dir
        nonlocal guest_temp_dir, is_guest
        data.clear()
        data.update(default_data())
        guest_temp_dir = tempfile.TemporaryDirectory(prefix="goods-organizer-guest-")
        current_user_email = None
        current_data_file = None
        current_asset_dir = Path(guest_temp_dir.name) / "images"
        ensure_private_directory(current_asset_dir)
        is_guest = True
        guest_notice.visible = True
        logout_button.text = "アカウントでログイン"
        auth_shell.visible = False
        shell.visible = True
        activate_app()

    def submit_auth(_):
        email = email_field.value.strip().casefold()
        password = password_field.value
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            auth_status.value = "メールアドレスを正しく入力してください"
            page.update()
            return
        if not password or len(password) > 128:
            auth_status.value = "パスワードを入力してください（128文字以内）"
            page.update()
            return

        try:
            if auth_mode == "register":
                if len(password) < 12:
                    auth_status.value = "パスワードは12文字以上で設定してください"
                    page.update()
                    return
                if password != password_confirm_field.value:
                    auth_status.value = "確認用パスワードが一致しません"
                    page.update()
                    return
                if not create_account(email, password):
                    auth_status.value = "このメールアドレスはすでに登録されています"
                    page.update()
                    return
            elif not authenticate_account(email, password):
                auth_status.value = "メールアドレスかパスワードが違います"
                page.update()
                return
            open_account(email)
            password_field.value = ""
            password_confirm_field.value = ""
        except (OSError, sqlite3.Error, ValueError):
            logging.exception("Account operation failed")
            auth_status.value = "アカウントを処理できませんでした。管理者にお問い合わせください"
            page.update()

    def logout(_):
        nonlocal auth_mode, current_user_email, current_data_file, current_asset_dir
        nonlocal guest_temp_dir, is_guest
        data.clear()
        data.update(default_data())
        if guest_temp_dir is not None:
            guest_temp_dir.cleanup()
            guest_temp_dir = None
        current_user_email = None
        current_data_file = None
        current_asset_dir = None
        is_guest = False
        guest_notice.visible = False
        logout_button.text = "ログアウト"
        password_field.value = ""
        password_confirm_field.value = ""
        shell.visible = False
        auth_shell.visible = True
        set_auth_mode("login")
        page.update()

    def cleanup_guest_session(_):
        nonlocal guest_temp_dir, current_asset_dir, is_guest
        if guest_temp_dir is not None:
            guest_temp_dir.cleanup()
            guest_temp_dir = None
        current_asset_dir = None
        is_guest = False

    auth_submit_button.on_click = submit_auth
    auth_mode_button.on_click = lambda _: set_auth_mode(
        "register" if auth_mode == "login" else "login"
    )
    logout_button.on_click = logout
    guest_button.on_click = open_guest
    page.on_disconnect = cleanup_guest_session

    def resize(event):
        width = page.width or 760
        shell.width = min(760, max(280, width - 32))
        auth_shell.width = min(480, max(280, width - 32))
        page.update()

    page.on_resize = resize
    page.add(auth_shell, shell)
    open_guest(None)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    ft.run(main, host="0.0.0.0", view=ft.AppView.WEB_BROWSER, port=port)
