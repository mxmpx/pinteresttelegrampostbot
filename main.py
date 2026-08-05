import json
import os
import re
import time
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
import requests

import tkinter as tk
from tkinter import ttk, scrolledtext
from PIL import Image, ImageTk

# === НАСТРОЙКИ СТИЛЯ (AMOLED DARK & CODE EDITOR FONT) ===
BG_AMOLED = "#000000"         # Чистый черный (AMOLED)
PANEL_BG = "#0D0D0D"          # Темно-серый фон панелей
ENTRY_BG = "#1A1A1A"          # Фон полей ввода
TEXT_FG = "#E0E0E0"           # Высококонтрастный текст
ACCENT_GREEN = "#00FF66"      # Зеленый акцент / лог
ACCENT_RED = "#FF3333"        # Красный акцент (стоп)
BORDER_COLOR = "#262626"      # Границы блоков
FONT_CODE = ("Consolas", 10)
FONT_TITLE = ("Consolas", 14, "bold")

CONFIG_FILE = "gui_config.json"
SEEN_PINS_FILE = "seen_pins.json"

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ОБРАБОТКИ ССЫЛОК ===
def clean_telegram_channel(channel_raw):
    """Приводит любые варианты ввода канала к формату @username или -100ID."""
    ch = channel_raw.strip()
    if not ch:
        return ""
    if ch.startswith("-") or ch.isdigit():
        return ch
    ch = re.sub(r'^(https?://)?(www\.)?t\.me/', '', ch, flags=re.IGNORECASE)
    ch = ch.strip("/")
    if not ch.startswith("@"):
        ch = "@" + ch
    return ch

def clean_pinterest_username(pinterest_raw):
    """Извлекает чистый юзернейм из ссылок любого формата."""
    p = pinterest_raw.strip()
    if not p:
        return ""
    p = re.sub(r'^(https?://)?(www\.)?pinterest\.[a-z.]+(/[a-z]{2})?/', '', p, flags=re.IGNORECASE)
    parts = [part for part in p.split('/') if part and part not in ('_pins', '_saved', '_created')]
    return parts[0] if parts else p

class PinterestBotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Pinterest TG Bot")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        self.root.configure(bg=BG_AMOLED)

        self.is_running = False
        self.bot_thread = None
        self.photo_references = []
        self.start_time = None

        self._build_ui()
        self._load_config()

    def _build_ui(self):
        # 1. Заголовок программы
        title_label = tk.Label(
            self.root, 
            text="PINTEREST TG BOT", 
            font=FONT_TITLE, 
            bg=BG_AMOLED, 
            fg=ACCENT_GREEN
        )
        title_label.pack(pady=(12, 8))

        # 2. Блок настроек
        config_frame = tk.Frame(self.root, bg=PANEL_BG, highlightbackground=BORDER_COLOR, highlightthickness=1)
        config_frame.pack(fill="x", padx=15, pady=5)

        fields = [
            ("Bot Token:", "entry_token"),
            ("Канал (Ссылка / @):", "entry_channel"),
            ("Pinterest (Ссылка / Юзер):", "entry_pinterest"),
            ("Интервал (минуты):", "entry_interval")
        ]

        for i, (label_text, attr_name) in enumerate(fields):
            lbl = tk.Label(config_frame, text=label_text, font=FONT_CODE, bg=PANEL_BG, fg=TEXT_FG, anchor="w")
            lbl.grid(row=i, column=0, padx=10, pady=4, sticky="w")
            
            entry = tk.Entry(
                config_frame, 
                font=FONT_CODE, 
                bg=ENTRY_BG, 
                fg=TEXT_FG, 
                insertbackground=TEXT_FG,
                relief="flat", 
                highlightbackground=BORDER_COLOR, 
                highlightthickness=1
            )
            entry.grid(row=i, column=1, padx=10, pady=4, sticky="ew")
            setattr(self, attr_name, entry)

        config_frame.columnconfigure(1, weight=1)

        # 3. Кнопка запуска
        self.btn_toggle = tk.Button(
            self.root,
            text="▶ ЗАПУСТИТЬ БОТА",
            font=("Consolas", 11, "bold"),
            bg=ACCENT_GREEN,
            fg="#000000",
            activebackground="#00CC52",
            activeforeground="#000000",
            relief="flat",
            cursor="hand2",
            command=self._toggle_bot
        )
        self.btn_toggle.pack(fill="x", padx=15, pady=10)

        # 4. Нижняя панель (Галерея и Логи)
        main_split = tk.Frame(self.root, bg=BG_AMOLED)
        main_split.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Слева: Превью загруженных фото
        left_frame = tk.LabelFrame(
            main_split, 
            text=" Загруженные фото ", 
            font=FONT_CODE, 
            bg=PANEL_BG, 
            fg=TEXT_FG,
            highlightbackground=BORDER_COLOR, 
            highlightthickness=1,
            labelanchor="nw"
        )
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        self.canvas_gallery = tk.Canvas(left_frame, bg=PANEL_BG, highlightthickness=0)
        self.scrollbar_gallery = tk.Scrollbar(left_frame, orient="vertical", command=self.canvas_gallery.yview)
        
        self.scroll_inner_frame = tk.Frame(self.canvas_gallery, bg=PANEL_BG)

        # Оптимизация размера Canvas без лагов при ресайзе окна
        self.canvas_window_id = self.canvas_gallery.create_window((0, 0), window=self.scroll_inner_frame, anchor="nw")
        
        def _on_frame_configure(e):
            self.canvas_gallery.configure(scrollregion=self.canvas_gallery.bbox("all"))

        def _on_canvas_configure(e):
            self.canvas_gallery.itemconfig(self.canvas_window_id, width=e.width)

        self.scroll_inner_frame.bind("<Configure>", _on_frame_configure)
        self.canvas_gallery.bind("<Configure>", _on_canvas_configure)

        self.canvas_gallery.configure(yscrollcommand=self.scrollbar_gallery.set)
        self.canvas_gallery.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        self.scrollbar_gallery.pack(side="right", fill="y", pady=5)

        # Справа: Логи
        right_frame = tk.LabelFrame(
            main_split, 
            text=" Логи работы ", 
            font=FONT_CODE, 
            bg=PANEL_BG, 
            fg=TEXT_FG,
            highlightbackground=BORDER_COLOR, 
            highlightthickness=1,
            labelanchor="nw"
        )
        right_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))

        self.log_area = scrolledtext.ScrolledText(
            right_frame,
            font=FONT_CODE,
            bg=BG_AMOLED,
            fg=ACCENT_GREEN,
            insertbackground=TEXT_FG,
            relief="flat",
            state="disabled",
            wrap="word"
        )
        self.log_area.pack(fill="both", expand=True, padx=5, pady=5)

    def log(self, message):
        """Вывод записей в журнал."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {message}\n"

        def _update():
            self.log_area.configure(state="normal")
            self.log_area.insert("end", formatted_msg)
            self.log_area.see("end")
            self.log_area.configure(state="disabled")

        self.root.after(0, _update)

    def add_photo_preview(self, image_bytes, pin_id, title):
        """Отображение фото в галерее."""
        def _update():
            try:
                img = Image.open(BytesIO(image_bytes))
                img.thumbnail((120, 120))
                photo = ImageTk.PhotoImage(img)
                self.photo_references.append(photo)

                item_frame = tk.Frame(self.scroll_inner_frame, bg=ENTRY_BG, highlightbackground=BORDER_COLOR, highlightthickness=1)
                item_frame.pack(fill="x", padx=5, pady=4)

                lbl_img = tk.Label(item_frame, image=photo, bg=ENTRY_BG)
                lbl_img.pack(side="left", padx=5, pady=5)

                lbl_info = tk.Label(
                    item_frame, 
                    text=f"ID: {pin_id}\n{title[:20]}...", 
                    font=("Consolas", 8), 
                    bg=ENTRY_BG, 
                    fg=TEXT_FG, 
                    justify="left"
                )
                lbl_info.pack(side="left", padx=5)
            except Exception as e:
                self.log(f" Ошибка отрисовки превью: {e}")

        self.root.after(0, _update)

    def _save_config(self):
        config = {
            "token": self.entry_token.get().strip(),
            "channel": self.entry_channel.get().strip(),
            "pinterest_user": self.entry_pinterest.get().strip(),
            "interval": self.entry_interval.get().strip()
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.entry_token.insert(0, config.get("token", ""))
                    self.entry_channel.insert(0, config.get("channel", "@pinterestmxmpx"))
                    self.entry_pinterest.insert(0, config.get("pinterest_user", "musamxmpx"))
                    self.entry_interval.insert(0, config.get("interval", "60"))
            except Exception:
                pass
        else:
            self.entry_token.insert(0, "8849798923:AAGMZ9MfrQbj0FwMGd1d08-bUJbnDO8s52Q")
            self.entry_channel.insert(0, "@pinterestmxmpx")
            self.entry_pinterest.insert(0, "musamxmpx")
            self.entry_interval.insert(0, "60")

    def _toggle_bot(self):
        if not self.is_running:
            self._save_config()
            self.is_running = True
            
            self.start_time = datetime.now(timezone.utc)
            
            self.btn_toggle.configure(text="⏹ ОСТАНОВИТЬ БОТА", bg=ACCENT_RED, fg="#FFFFFF")
            self.log(f"🚀 Бот запущен! Ищем пины, созданные после {self.start_time.strftime('%H:%M:%S UTC')}")
            
            self.entry_token.configure(state="disabled")
            self.entry_channel.configure(state="disabled")
            self.entry_pinterest.configure(state="disabled")
            self.entry_interval.configure(state="disabled")

            self.bot_thread = threading.Thread(target=self._bot_loop, daemon=True)
            self.bot_thread.start()
        else:
            self.is_running = False
            self.btn_toggle.configure(text="▶ ЗАПУСТИТЬ БОТА", bg=ACCENT_GREEN, fg="#000000")
            self.log("🛑 Остановка бота...")
            
            self.entry_token.configure(state="normal")
            self.entry_channel.configure(state="normal")
            self.entry_pinterest.configure(state="normal")
            self.entry_interval.configure(state="normal")

    def _bot_loop(self):
        """Основной цикл парсинга."""
        token = self.entry_token.get().strip()
        channel = clean_telegram_channel(self.entry_channel.get())
        pinterest_user = clean_pinterest_username(self.entry_pinterest.get())
        
        try:
            interval_minutes = int(self.entry_interval.get().strip())
            if interval_minutes < 1:
                interval_minutes = 1
        except ValueError:
            interval_minutes = 60

        interval_seconds = interval_minutes * 60
        rss_url = f"https://www.pinterest.com/{pinterest_user}/feed.rss"

        self.log(f" Target Channel: {channel}")
        self.log(f" Target Pinterest User: {pinterest_user}")
        self.log(f" Проверка каждые {interval_minutes} мин.")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

        while self.is_running:
            try:
                self.log("Проверка новых пинов...")
                seen_pins = self._load_seen_pins()
                
                res = requests.get(rss_url, headers=headers, timeout=10)
                if res.status_code == 200:
                    root = ET.fromstring(res.content)
                    pins = []

                    for item in root.findall("./channel/item"):
                        title = item.findtext("title", default="")
                        link = item.findtext("link", default="")
                        pub_date_raw = item.findtext("pubDate", default="")
                        description = item.findtext("description", default="")

                        try:
                            pub_dt = parsedate_to_datetime(pub_date_raw)
                        except Exception:
                            pub_dt = None

                        img_match = re.search(r'src=["\'](https://i\.pinimg\.com/[^"\']+)["\']', description)
                        if img_match:
                            img_url = img_match.group(1)
                            orig_url = re.sub(r'/(236x|474x|736x)/', '/originals/', img_url)
                            pin_id_match = re.search(r'/pin/(\d+)', link)
                            pin_id = pin_id_match.group(1) if pin_id_match else link

                            pins.append({
                                "id": pin_id,
                                "title": title,
                                "link": link,
                                "pub_date": pub_dt,
                                "orig_url": orig_url,
                                "fallback_url": img_url
                            })

                    new_pins = [
                        p for p in pins 
                        if p["id"] not in seen_pins and (p["pub_date"] and self.start_time and p["pub_date"] >= self.start_time)
                    ]

                    if not new_pins:
                        self.log("Новых пинов с момента запуска не найдено.")
                    else:
                        self.log(f"Найдено новых пинов: {len(new_pins)}")

                    for pin in reversed(new_pins):
                        if not self.is_running:
                            break

                        img_bytes = None
                        try:
                            r_img = requests.get(pin["orig_url"], headers=headers, timeout=10)
                            if r_img.status_code == 200:
                                img_bytes = r_img.content
                            else:
                                r_fb = requests.get(pin["fallback_url"], headers=headers, timeout=10)
                                if r_fb.status_code == 200:
                                    img_bytes = r_fb.content
                        except Exception as err:
                            self.log(f" Ошибка скачивания фото {pin['id']}: {err}")

                        if img_bytes:
                            tg_url = f"https://api.telegram.org/bot{token}/sendPhoto"
                            caption = f"📌 {pin['title']}\n\n🔗 [Открыть в Pinterest]({pin['link']})" if pin['title'] else f"🔗 [Открыть в Pinterest]({pin['link']})"
                            
                            payload = {"chat_id": channel, "caption": caption, "parse_mode": "Markdown"}
                            files = {"photo": ("image.jpg", img_bytes, "image/jpeg")}

                            tg_res = requests.post(tg_url, data=payload, files=files, timeout=15)
                            if tg_res.status_code == 200:
                                self.log(f" Успешно отправлен пин {pin['id']}")
                                seen_pins.add(pin["id"])
                                self._save_seen_pins(seen_pins)
                                self.add_photo_preview(img_bytes, pin["id"], pin["title"])
                            else:
                                self.log(f" Ошибка Telegram: {tg_res.text}")

                        time.sleep(3)

                else:
                    self.log(f" Ошибка доступа к RSS: HTTP {res.status_code}")

            except Exception as e:
                self.log(f" Ошибка цикла: {e}")

            for _ in range(interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

    def _load_seen_pins(self):
        if os.path.exists(SEEN_PINS_FILE):
            with open(SEEN_PINS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        return set()

    def _save_seen_pins(self, seen_set):
        with open(SEEN_PINS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_set), f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    root = tk.Tk()
    app = PinterestBotGUI(root)
    root.mainloop()