"""
main.py - LLB Notes Cleaner (SIMPLE VERSION, plain Kivy only, no KivyMD)

Flow: upload a photo -> Gemini extracts text -> Claude verifies text ->
clean image is drawn from the verified text -> shown in a chat list.
"""

import os
import sys
import json
import base64
import threading
import textwrap
import traceback
from datetime import datetime

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.image import AsyncImage
from kivy.lang import Builder

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    PIL_OK = True
except Exception:
    PIL_OK = False

import config

try:
    from android.permissions import request_permissions, Permission
    from android.storage import app_storage_path
    ANDROID = True
except Exception:
    ANDROID = False

try:
    from plyer import filechooser
except Exception:
    filechooser = None


def log(msg):
    try:
        print("[LLBNotesCleaner] " + str(msg))
        sys.stdout.flush()
    except Exception:
        pass


def data_dir():
    base = app_storage_path() if ANDROID else os.path.join(os.path.expanduser("~"), ".llb_notes_cleaner")
    d = os.path.join(base, "llb_data")
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, "orig"), exist_ok=True)
    os.makedirs(os.path.join(d, "clean"), exist_ok=True)
    return d


def history_path():
    return os.path.join(data_dir(), "history.json")


def load_history():
    p = history_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_history(items):
    with open(history_path(), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


LLB_CONTEXT = ("You are transcribing handwritten LLB (law) student notes, "
               "Semester 1-6, possibly in English, Hindi, or mixed. "
               "Do not summarize. Preserve everything exactly.")


def image_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def gemini_extract(image_path, instruction=""):
    import requests
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    prompt = (f"{LLB_CONTEXT}\nExtract ALL visible text exactly as written, "
              f"no summarizing, preserve headings/numbering/Hindi/English. "
              f"Extra instruction: {instruction or 'none'}")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}")
    payload = {"contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": image_b64(image_path)}},
    ]}]}
    r = requests.post(url, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


def claude_verify(image_path, gemini_text):
    import requests
    if not config.CLAUDE_API_KEY:
        raise RuntimeError("CLAUDE_API_KEY not set")
    system = (f"{LLB_CONTEXT} Compare the OCR text below against the image, "
              "fix mistakes, fill in unclear words using context. Never "
              "summarize, never drop text, never change Article/Section "
              "numbers or case names unless correcting an obvious OCR error. "
              "Return ONLY the corrected full text.")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": config.CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": config.CLAUDE_MODEL,
            "max_tokens": 4096,
            "system": system,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64(image_path)}},
                    {"type": "text", "text": f"OCR text:\n\n{gemini_text}\n\nReturn the corrected full text."},
                ],
            }],
        },
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()


def render_clean_image(text, out_path):
    if not PIL_OK:
        raise RuntimeError("Pillow not available")
    width, margin, line_h, font_size = 1000, 50, 34, 24
    try:
        font = ImageFont.truetype(config.HANDWRITING_FONT_PATH, font_size)
    except Exception:
        font = ImageFont.load_default()
    lines = []
    for raw in text.splitlines():
        wrapped = textwrap.wrap(raw, width=60) or [""]
        lines.extend(wrapped)
    height = margin * 2 + line_h * (len(lines) + 2)
    img = Image.new("RGB", (width, max(height, 600)), (253, 253, 250))
    draw = ImageDraw.Draw(img)
    y = margin
    for line in lines:
        draw.text((margin, y), line, font=font, fill=(30, 30, 30))
        y += line_h
    img.save(out_path, quality=92)


def preprocess(src, dst):
    img = Image.open(src)
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > 1600:
        scale = 1600 / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    img.save(dst, quality=92)


KV = """
BoxLayout:
    orientation: "vertical"

    Label:
        text: "LLB Notes Cleaner"
        size_hint_y: None
        height: "48dp"
        bold: True
        font_size: "20sp"

    ScrollView:
        id: scroll
        BoxLayout:
            id: chat_box
            orientation: "vertical"
            size_hint_y: None
            height: self.minimum_height
            padding: "8dp"
            spacing: "8dp"

    BoxLayout:
        size_hint_y: None
        height: "50dp"
        padding: "4dp"
        spacing: "4dp"

        Button:
            text: "+"
            size_hint_x: None
            width: "50dp"
            on_release: app.pick_image()

        TextInput:
            id: msg_input
            hint_text: "Instruction (optional)..."
            multiline: False

        Button:
            text: "Send"
            size_hint_x: None
            width: "80dp"
            on_release: app.on_send()
"""


class LLBApp(App):
    def build(self):
        self.title = "LLB Notes Cleaner"
        if ANDROID:
            try:
                request_permissions([
                    Permission.CAMERA, Permission.READ_EXTERNAL_STORAGE,
                    Permission.WRITE_EXTERNAL_STORAGE, Permission.INTERNET,
                ])
            except Exception:
                pass
        self.history = load_history()
        self.pending_image = None
        root = Builder.load_string(KV)
        self.root_widget = root
        Clock.schedule_once(lambda dt: self.render_history(), 0.2)
        return root

    def render_history(self):
        root = self.root_widget
        box = root.ids.chat_box
        box.clear_widgets()
        for item in self.history:
            self.add_item_widget(box, item)
        Clock.schedule_once(lambda dt: setattr(root.ids.scroll, "scroll_y", 0), 0.05)

    def add_item_widget(self, box, item):
        t = item.get("type")
        if t == "text":
            box.add_widget(Label(text=item.get("text", ""), size_hint_y=None, height="40dp"))
        elif t == "status":
            box.add_widget(Label(text=item.get("text", ""), size_hint_y=None, height="30dp", color=(0.6, 0.6, 0.6, 1)))
        elif t in ("orig_image", "clean_image"):
            wrap = BoxLayout(orientation="vertical", size_hint_y=None, height="340dp")
            wrap.add_widget(AsyncImage(source=item.get("path", "")))
            if t == "clean_image":
                row = BoxLayout(size_hint_y=None, height="40dp")
                row.add_widget(Button(text="Copy Text", on_release=lambda x, it=item: self.copy_text(it)))
                row.add_widget(Button(text="Regenerate", on_release=lambda x, it=item: self.regenerate(it)))
                row.add_widget(Button(text="Delete", on_release=lambda x, it=item: self.delete_item(it)))
                wrap.add_widget(row)
                wrap.height = "380dp"
            box.add_widget(wrap)

    def pick_image(self):
        if not filechooser:
            self.status("File picker not available")
            return
        try:
            filechooser.open_file(on_selection=self.on_image_selected, filters=["*.jpg", "*.jpeg", "*.png"])
        except Exception as e:
            self.status(f"Picker error: {e}")

    def on_image_selected(self, selection):
        if not selection:
            return
        img_path = selection[0]
        instruction = self.root_widget.ids.msg_input.text.strip()
        self.root_widget.ids.msg_input.text = ""
        d = data_dir()
        ts = int(datetime.now().timestamp() * 1000)
        dest = os.path.join(d, "orig", f"orig_{ts}.jpg")
        try:
            Image.open(img_path).convert("RGB").save(dest, quality=92)
        except Exception as e:
            self.status(f"Could not read image: {e}")
            return
        self.history.append({"type": "orig_image", "path": dest})
        save_history(self.history)
        self.render_history()
        if instruction:
            self.history.append({"type": "text", "text": instruction})
            save_history(self.history)
            self.render_history()
        threading.Thread(target=self.run_pipeline, args=(dest, instruction), daemon=True).start()

    def on_send(self):
        text = self.root_widget.ids.msg_input.text.strip()
        self.root_widget.ids.msg_input.text = ""
        if text:
            self.history.append({"type": "text", "text": text})
            save_history(self.history)
            self.render_history()
        else:
            self.status("Type something, or tap + to attach an image.")

    def status(self, text):
        self.history.append({"type": "status", "text": text})
        save_history(self.history)
        Clock.schedule_once(lambda dt: self.render_history())

    def run_pipeline(self, orig_path, instruction):
        try:
            Clock.schedule_once(lambda dt: self.status("Reading image..."))
            d = data_dir()
            prep = os.path.join(d, "prep.jpg")
            preprocess(orig_path, prep)

            Clock.schedule_once(lambda dt: self.status("Gemini analyzing..."))
            gtext = gemini_extract(prep, instruction)

            Clock.schedule_once(lambda dt: self.status("Claude verifying..."))
            vtext = claude_verify(prep, gtext)

            Clock.schedule_once(lambda dt: self.status("Creating clean image..."))
            ts = int(datetime.now().timestamp() * 1000)
            clean_path = os.path.join(d, "clean", f"clean_{ts}.jpg")
            render_clean_image(vtext, clean_path)

            def done(dt):
                self.status("Done")
                self.history.append({"type": "clean_image", "path": clean_path, "text": vtext, "source": orig_path})
                save_history(self.history)
                self.render_history()

            Clock.schedule_once(done)
        except Exception as e:
            err = str(e)
            Clock.schedule_once(lambda dt: self.status(f"Error: {err}"))

    def copy_text(self, item):
        try:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(item.get("text", ""))
            self.status("Copied.")
        except Exception as e:
            self.status(f"Copy failed: {e}")

    def regenerate(self, item):
        text = item.get("text", "")
        if not text:
            self.status("No text stored.")
            return

        def work():
            d = data_dir()
            ts = int(datetime.now().timestamp() * 1000)
            new_path = os.path.join(d, "clean", f"clean_{ts}.jpg")
            render_clean_image(text, new_path)

            def done(dt):
                self.history.append({"type": "clean_image", "path": new_path, "text": text, "source": item.get("source", "")})
                save_history(self.history)
                self.render_history()

            Clock.schedule_once(done)

        threading.Thread(target=work, daemon=True).start()

    def delete_item(self, item):
        try:
            self.history.remove(item)
        except ValueError:
            pass
        save_history(self.history)
        self.render_history()


if __name__ == "__main__":
    try:
        LLBApp().run()
    except Exception:
        log("CRASH:\n" + traceback.format_exc())
        raise
