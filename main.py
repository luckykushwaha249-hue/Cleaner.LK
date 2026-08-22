"""
main.py
-------
LLB Notes Cleaner - a WhatsApp-style Kivy/KivyMD app.

Workflow:
    User uploads photo of unclear handwritten LLB notes
        -> Gemini extracts raw text (OCR + structure, no summarizing)
        -> Claude verifies/corrects the text against the image using
           LLB context (and optional web search for unclear legal terms)
        -> A clean, readable "handwritten style" image is rendered from
           the verified text
        -> Both original and clean image are shown in a WhatsApp-style
           chat, saved locally, and available on next app launch.

This file intentionally keeps ALL app logic in one place per the
project's "keep it simple" requirement. Sections are clearly commented.
"""

import os
import io
import json
import base64
import threading
import textwrap
from datetime import datetime

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.properties import StringProperty, BooleanProperty, ObjectProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import AsyncImage
from kivy.lang import Builder

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.button import MDIconButton, MDRaisedButton, MDFlatButton
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.dialog import MDDialog
from kivymd.uix.snackbar import Snackbar
from kivymd.uix.card import MDCard
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField

from PIL import Image, ImageDraw, ImageFont, ImageOps

import config

# ---------------------------------------------------------------------------
# Optional Android-only imports (guarded so the app still runs on desktop
# for development/testing).
# ---------------------------------------------------------------------------
try:
    from android.permissions import request_permissions, Permission  # noqa
    from android.storage import app_storage_path  # noqa
    ANDROID = True
except Exception:
    ANDROID = False

try:
    from plyer import camera, filechooser, share
except Exception:
    camera = filechooser = share = None


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------
def get_data_dir():
    """Returns a writable per-app data directory (works on Android + desktop)."""
    if ANDROID:
        base = app_storage_path()
    else:
        base = os.path.join(os.path.expanduser("~"), ".llb_notes_cleaner")
    data_dir = os.path.join(base, config.DATA_DIR_NAME)
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, config.ORIGINAL_IMAGES_DIR), exist_ok=True)
    os.makedirs(os.path.join(data_dir, config.CLEAN_IMAGES_DIR), exist_ok=True)
    return data_dir


class ChatStore:
    """Simple local JSON-backed storage for chat history.

    Each message is a dict:
    {
        "id": str,
        "type": "date" | "user_text" | "user_image" | "ai_status" |
                "ai_image" | "error",
        "timestamp": iso string,
        "text": str (optional),
        "image_path": str (optional, absolute path),
        "verified_text": str (optional, for ai_image - used by Regenerate/Copy),
        "source_image_path": str (optional, original image used to generate this)
    }
    """

    def __init__(self):
        self.data_dir = get_data_dir()
        self.history_path = os.path.join(self.data_dir, config.CHAT_HISTORY_FILE)
        self.messages = self._load()

    def _load(self):
        if os.path.exists(self.history_path):
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def save(self):
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(self.messages, f, ensure_ascii=False, indent=2)

    def add(self, message):
        message.setdefault("id", str(int(datetime.now().timestamp() * 1000)))
        message.setdefault("timestamp", datetime.now().isoformat())
        self.messages.append(message)
        self.save()
        return message

    def remove(self, message_id):
        self.messages = [m for m in self.messages if m.get("id") != message_id]
        self.save()

    def last_date_str(self):
        for m in reversed(self.messages):
            if m.get("type") == "date":
                return m.get("text")
        return None


# ---------------------------------------------------------------------------
# Image preprocessing (basic cleanup before sending to AI)
# ---------------------------------------------------------------------------
def preprocess_image(src_path, dst_path):
    """Resize/enhance the photo a little before AI processing.
    Original file passed in is left untouched by the caller; this writes
    a NEW file at dst_path used only for AI analysis.
    """
    img = Image.open(src_path)
    img = ImageOps.exif_transpose(img)  # correct rotation from EXIF
    img = img.convert("RGB")

    # Downscale very large images
    w, h = img.size
    max_dim = config.MAX_IMAGE_DIMENSION
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # Mild contrast/brightness boost + slight noise reduction
    img = ImageOps.autocontrast(img, cutoff=1)

    img.save(dst_path, quality=92)
    return dst_path


def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ---------------------------------------------------------------------------
# AI Services: Gemini (OCR/extraction) -> Claude (verification)
# ---------------------------------------------------------------------------
LLB_CONTEXT_PROMPT = """You are helping digitize handwritten whiteboard notes
for an LLB (Bachelor of Law) student, covering Semester 1 to Semester 6.
Topics may include Constitution, Articles, Sections, Acts, Case Law, Tort,
Contract, Criminal Law, Family Law, Constitutional Law, Jurisprudence,
Property Law, Evidence, CPC, CrPC/BNSS, IPC/BNS, Legal Maxims and Latin
legal terms, in English, Hindi, or mixed Hindi-English. Do not assume a
term is present unless it is actually visible in the image."""


def gemini_extract_text(image_path, user_instruction=""):
    """Step 1: Gemini reads the raw image and extracts ALL visible text,
    structure (headings/paragraphs/numbering), without summarizing.

    NOTE: This calls Gemini's REST API directly via `requests` instead of
    the google-generativeai SDK. The SDK pulls in grpcio/protobuf, which
    are very difficult to cross-compile for Android via Buildozer. The
    REST API gives identical results with a much lighter dependency
    footprint (just `requests`, already required elsewhere in this app).
    """
    import requests

    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured. See config.py / README.")

    image_b64 = image_to_base64(image_path)

    prompt = f"""{LLB_CONTEXT_PROMPT}

Task: Extract the COMPLETE visible text from this whiteboard/notebook photo
exactly as written. This is OCR/transcription, NOT summarization.

Rules:
- Do NOT summarize or shorten anything.
- Do NOT remove any visible text.
- Preserve headings, paragraphs, numbering and their order.
- Preserve Hindi text, English text, and mixed Hindi-English text exactly.
- Preserve Article numbers, Section numbers, case names and legal terms
  exactly as written.
- Mark headings clearly (e.g. prefix with "## ").
- If a word/phrase is genuinely illegible, write [unclear: best-guess]
  instead of skipping it.
- Preserve arrows/symbols as text descriptions where relevant (e.g. "->").

User instruction (optional, does not override the accuracy rules above):
{user_instruction or "(none)"}

Return ONLY the transcribed text, preserving structure with line breaks."""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                ]
            }
        ]
    }
    resp = requests.post(url, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response format: {data}") from e


def web_verify_term(term):
    """Optional: verify an unclear legal term using Google Custom Search.
    Returns a short snippet or None if verification is disabled/unavailable.
    """
    if not config.ENABLE_WEB_VERIFICATION:
        return None
    try:
        import requests
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": config.WEB_SEARCH_API_KEY,
                "cx": config.WEB_SEARCH_ENGINE_ID,
                "q": f"{term} Indian law meaning",
                "num": 1,
            },
            timeout=10,
        )
        items = resp.json().get("items", [])
        if items:
            return items[0].get("snippet", "")
    except Exception:
        return None
    return None


def _claude_api_call(system_prompt, tools, messages):
    """Low-level helper: calls Claude's REST API directly via `requests`
    instead of the `anthropic` SDK. The SDK pulls in pydantic (with its
    Rust-based pydantic-core extension) and httpx/httpcore/anyio, which
    are difficult or impossible to cross-compile for Android via
    Buildozer. The REST API gives identical results with only `requests`
    as a dependency, which is already required elsewhere in this app.
    """
    import requests

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": config.CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": config.CLAUDE_MODEL,
            "max_tokens": 4096,
            "system": system_prompt,
            "tools": tools,
            "messages": messages,
        },
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()


def claude_verify_text(image_path, gemini_text):
    """Step 2: Claude cross-checks Gemini's extracted text against the
    original image, corrects OCR mistakes using LLB context, and may call
    a web-verification tool for unclear legal terms/case names.
    """
    if not config.CLAUDE_API_KEY:
        raise RuntimeError("CLAUDE_API_KEY is not configured. See config.py / README.")

    image_b64 = image_to_base64(image_path)

    tools = [
        {
            "name": "verify_legal_term",
            "description": (
                "Look up a legal term, Article/Section number or case name "
                "online ONLY when it is genuinely unclear in the image, to "
                "confirm the exact correct spelling/number. Do not use this "
                "to add unrelated information."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "The unclear term/phrase to verify"}
                },
                "required": ["term"],
            },
        }
    ]

    system_prompt = f"""{LLB_CONTEXT_PROMPT}

You are the SECOND verification step after Gemini OCR. You will receive the
original photo and Gemini's extracted text. Your job:

1. Compare Gemini's text against the actual image.
2. Correct OCR mistakes (wrong words, garbled Hindi/English, misread numbers).
3. Recover missing or unclear words using the image + LLB context.
4. If a legal term, Article number, Section number or case name is still
   unclear after looking closely, you may call the verify_legal_term tool
   to confirm it. Only use it for genuinely unclear items - not for things
   already clear in the image.

STRICT RULES (must follow exactly):
- Never intentionally remove visible text.
- Never summarize the notes.
- Never add unrelated information.
- Never change Article numbers, Section numbers, case names, or legal
  terminology unless correcting an obvious OCR error confirmed by the image.
- Preserve Hindi and English text exactly as accurately as possible.
- Preserve numbering, order, headings and paragraph structure.
- Do not ask the user questions - resolve ambiguity yourself using the
  image, LLB context, and (if needed) the verify_legal_term tool.

Return ONLY the final corrected/verified full text (no commentary, no
explanations, no markdown code fences)."""

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                },
                {
                    "type": "text",
                    "text": f"Gemini's extracted text:\n\n{gemini_text}\n\n"
                    "Please verify this against the image and return the "
                    "final corrected full text.",
                },
            ],
        }
    ]

    # Allow a couple of tool-use round trips for web verification
    for _ in range(4):
        response = _claude_api_call(system_prompt, tools, messages)
        content_blocks = response.get("content", [])
        stop_reason = response.get("stop_reason")

        if stop_reason == "tool_use":
            tool_results = []
            for block in content_blocks:
                if block.get("type") == "tool_use" and block.get("name") == "verify_legal_term":
                    term = block.get("input", {}).get("term", "")
                    snippet = web_verify_term(term) or "No verification available."
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.get("id"),
                            "content": snippet,
                        }
                    )
            messages.append({"role": "assistant", "content": content_blocks})
            messages.append({"role": "user", "content": tool_results})
            continue

        # Final answer
        final_text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        return final_text.strip()

    # Fallback if it never stopped tool-calling
    return gemini_text


# ---------------------------------------------------------------------------
# Clean image renderer (draws the verified text as a clean notes image)
# ---------------------------------------------------------------------------
def render_clean_image(verified_text, out_path):
    """Render the verified text onto a clean white 'notes style' image.
    Uses a Hindi+English capable font if available (see config.py),
    otherwise falls back to PIL's default font.
    """
    width = 1240  # ~A4 at 150dpi, portrait
    margin = 60
    line_height = 40
    font_size = 28
    heading_font_size = 34

    font_path = config.HANDWRITING_FONT_PATH
    try:
        body_font = ImageFont.truetype(font_path, font_size)
        heading_font = ImageFont.truetype(font_path, heading_font_size)
    except Exception:
        body_font = ImageFont.load_default()
        heading_font = ImageFont.load_default()

    # Wrap lines to fit width
    wrap_chars = 62
    rendered_lines = []  # (text, is_heading)
    for raw_line in verified_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            rendered_lines.append(("", False))
            continue
        is_heading = line.startswith("## ")
        text = line[3:] if is_heading else line
        wrapped = textwrap.wrap(text, width=wrap_chars) or [""]
        for w_line in wrapped:
            rendered_lines.append((w_line, is_heading))

    height = margin * 2 + line_height * (len(rendered_lines) + 2)
    img = Image.new("RGB", (width, max(height, 800)), color=(253, 253, 250))
    draw = ImageDraw.Draw(img)

    y = margin
    for text, is_heading in rendered_lines:
        font = heading_font if is_heading else body_font
        color = (20, 30, 90) if is_heading else (35, 35, 35)
        draw.text((margin, y), text, font=font, fill=color)
        y += line_height if not is_heading else int(line_height * 1.3)

    img.save(out_path, quality=95)
    return out_path


# ---------------------------------------------------------------------------
# UI: KV layout string
# ---------------------------------------------------------------------------
KV = """
#:import dp kivy.metrics.dp

<UserTextBubble>:
    orientation: "vertical"
    size_hint_y: None
    height: self.minimum_height
    padding: dp(10)
    md_bg_color: 0.85, 0.93, 0.83, 1
    radius: [14, 14, 2, 14]
    size_hint_x: 0.75
    pos_hint: {"right": 1}
    MDLabel:
        id: lbl
        text: root.text
        size_hint_y: None
        height: self.texture_size[1]
        halign: "left"

<AiStatusBubble>:
    orientation: "vertical"
    size_hint_y: None
    height: self.minimum_height
    padding: dp(10)
    md_bg_color: 0.93, 0.93, 0.93, 1
    radius: [14, 14, 14, 2]
    size_hint_x: 0.75
    MDLabel:
        id: lbl
        text: root.text
        size_hint_y: None
        height: self.texture_size[1]

<DateSeparatorLabel>:
    size_hint_y: None
    height: dp(30)
    halign: "center"
    theme_text_color: "Secondary"

<ImageBubble>:
    orientation: "vertical"
    size_hint_y: None
    height: self.minimum_height
    padding: dp(6)
    md_bg_color: 0.95, 0.95, 0.95, 1
    radius: [14, 14, 14, 2]
    size_hint_x: 0.8

    BoxLayout:
        size_hint_y: None
        height: dp(340)
        AsyncImage:
            id: img
            source: root.image_source
            allow_stretch: True
            keep_ratio: True

    BoxLayout:
        size_hint_y: None
        height: dp(36)
        MDLabel:
            id: caption
            text: root.caption
            theme_text_color: "Secondary"
            font_style: "Caption"
        MDIconButton:
            icon: "dots-vertical"
            disabled: not root.show_menu
            opacity: 1 if root.show_menu else 0
            on_release: root.open_menu(self)

MDBoxLayout:
    orientation: "vertical"

    MDTopAppBar:
        title: "LLB Notes Cleaner"
        elevation: 4
        left_action_items: []
        right_action_items: [["dots-vertical", lambda x: app.open_top_menu(x)]]

    MDScrollView:
        id: scroll
        MDBoxLayout:
            id: chat_box
            orientation: "vertical"
            size_hint_y: None
            height: self.minimum_height
            padding: dp(10)
            spacing: dp(8)

    MDBoxLayout:
        size_hint_y: None
        height: dp(56)
        padding: dp(6)
        spacing: dp(6)
        md_bg_color: 1, 1, 1, 1

        MDIconButton:
            icon: "plus-circle"
            on_release: app.open_plus_menu(self)

        MDTextField:
            id: message_input
            hint_text: "Type an instruction (optional)... e.g. Isko clear karo"
            mode: "round"
            multiline: False

        MDIconButton:
            icon: "send"
            on_release: app.on_send_pressed()
"""


class UserTextBubble(MDCard):
    text = StringProperty("")


class AiStatusBubble(MDCard):
    text = StringProperty("")


class DateSeparatorLabel(MDLabel):
    pass


class ImageBubble(MDCard):
    image_source = StringProperty("")
    caption = StringProperty("")
    show_menu = BooleanProperty(False)
    message_id = StringProperty("")
    is_clean_image = BooleanProperty(False)
    verified_text = StringProperty("")
    source_image_path = StringProperty("")

    def open_menu(self, button):
        app = MDApp.get_running_app()
        app.open_image_menu(self, button)


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------
class LLBNotesCleanerApp(MDApp):
    def build(self):
        self.title = config.APP_NAME
        self.theme_cls.primary_palette = "Teal"
        self.theme_cls.theme_style = "Light"

        if ANDROID:
            try:
                request_permissions(
                    [
                        Permission.CAMERA,
                        Permission.READ_EXTERNAL_STORAGE,
                        Permission.WRITE_EXTERNAL_STORAGE,
                        Permission.INTERNET,
                    ]
                )
            except Exception:
                pass

        self.store = ChatStore()
        self.root_widget = Builder.load_string(KV)
        self._pending_image_path = None
        self._plus_menu = None
        self._top_menu = None
        self._current_image_menu = None

        Clock.schedule_once(lambda dt: self.render_history(), 0.2)
        if not config.keys_configured():
            Clock.schedule_once(
                lambda dt: Snackbar(
                    text="Gemini/Claude API keys not configured. See README."
                ).open(),
                1,
            )
        return self.root_widget

    # -- history rendering ---------------------------------------------
    def render_history(self):
        chat_box = self.root_widget.ids.chat_box
        chat_box.clear_widgets()
        for msg in self.store.messages:
            self._add_widget_for_message(msg, scroll=False)
        self._scroll_to_bottom()

    def _maybe_insert_date_separator(self):
        today_str = datetime.now().strftime("%d %B %Y")
        if self.store.last_date_str() != today_str:
            msg = self.store.add({"type": "date", "text": today_str})
            self._add_widget_for_message(msg)

    def _add_widget_for_message(self, msg, scroll=True):
        chat_box = self.root_widget.ids.chat_box
        mtype = msg.get("type")

        if mtype == "date":
            lbl = DateSeparatorLabel(text=f"──── {msg['text']} ────")
            chat_box.add_widget(lbl)
        elif mtype == "user_text":
            chat_box.add_widget(UserTextBubble(text=msg.get("text", "")))
        elif mtype in ("ai_status", "error"):
            chat_box.add_widget(AiStatusBubble(text=msg.get("text", "")))
        elif mtype in ("user_image", "ai_image"):
            bubble = ImageBubble(
                image_source=msg.get("image_path", ""),
                caption="Original photo" if mtype == "user_image" else "Clean notes",
                show_menu=(mtype == "ai_image"),
                message_id=msg.get("id", ""),
                is_clean_image=(mtype == "ai_image"),
                verified_text=msg.get("verified_text", ""),
                source_image_path=msg.get("source_image_path", ""),
            )
            chat_box.add_widget(bubble)

        if scroll:
            self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        def _do(dt):
            self.root_widget.ids.scroll.scroll_y = 0

        Clock.schedule_once(_do, 0.05)

    # -- plus menu (camera / gallery / file / text) ----------------------
    def open_plus_menu(self, caller):
        items = [
            {"text": "Camera", "on_release": lambda: self._menu_pick("camera")},
            {"text": "Gallery", "on_release": lambda: self._menu_pick("gallery")},
            {"text": "File / PDF", "on_release": lambda: self._menu_pick("file")},
            {"text": "Text instruction only", "on_release": lambda: self._menu_pick("text")},
        ]
        menu_items = [
            {
                "text": it["text"],
                "on_release": (lambda x=it: self._on_plus_item(x)),
            }
            for it in items
        ]
        self._plus_menu = MDDropdownMenu(caller=caller, items=menu_items, width_mult=4)
        self._plus_menu.open()

    def _on_plus_item(self, item):
        if self._plus_menu:
            self._plus_menu.dismiss()
        item["on_release"]()

    def _menu_pick(self, kind):
        if kind == "camera":
            self.capture_from_camera()
        elif kind == "gallery":
            self.pick_from_gallery()
        elif kind == "file":
            self.pick_from_gallery(file_types=["*.jpg", "*.jpeg", "*.png", "*.pdf"])
        elif kind == "text":
            Snackbar(text="Type your instruction below and tap send.").open()

    def capture_from_camera(self):
        if not camera:
            Snackbar(text="Camera not available on this platform.").open()
            return
        tmp_path = os.path.join(get_data_dir(), "camera_capture.jpg")
        try:
            camera.take_picture(filename=tmp_path, on_complete=lambda p: self._on_image_picked(p))
        except Exception as e:
            Snackbar(text=f"Camera error: {e}").open()

    def pick_from_gallery(self, file_types=None):
        if not filechooser:
            Snackbar(text="File picker not available on this platform.").open()
            return
        try:
            filechooser.open_file(
                on_selection=lambda sel: self._on_image_picked(sel[0]) if sel else None,
                filters=file_types or ["*.jpg", "*.jpeg", "*.png"],
            )
        except Exception as e:
            Snackbar(text=f"Gallery error: {e}").open()

    def _on_image_picked(self, path):
        if not path:
            return
        self._pending_image_path = path
        Snackbar(text="Image selected. Add an instruction (optional) and tap send.").open()

    # -- send button -------------------------------------------------------
    def on_send_pressed(self):
        text_field = self.root_widget.ids.message_input
        instruction = text_field.text.strip()
        text_field.text = ""

        self._maybe_insert_date_separator()

        if self._pending_image_path:
            self._handle_new_image(self._pending_image_path, instruction)
            self._pending_image_path = None
        elif instruction:
            msg = self.store.add({"type": "user_text", "text": instruction})
            self._add_widget_for_message(msg)
        else:
            Snackbar(text="Attach an image using + or type an instruction.").open()

    # -- main processing pipeline ------------------------------------------
    def _handle_new_image(self, picked_path, instruction):
        data_dir = get_data_dir()
        ts = int(datetime.now().timestamp() * 1000)
        original_dest = os.path.join(
            data_dir, config.ORIGINAL_IMAGES_DIR, f"orig_{ts}.jpg"
        )
        try:
            Image.open(picked_path).convert("RGB").save(original_dest, quality=95)
        except Exception as e:
            Snackbar(text=f"Could not read image: {e}").open()
            return

        msg = self.store.add({"type": "user_image", "image_path": original_dest})
        self._add_widget_for_message(msg)

        if instruction:
            tmsg = self.store.add({"type": "user_text", "text": instruction})
            self._add_widget_for_message(tmsg)

        self._run_pipeline_async(original_dest, instruction)

    def _status(self, text):
        msg = self.store.add({"type": "ai_status", "text": text})
        Clock.schedule_once(lambda dt: self._add_widget_for_message(msg))
        return msg

    def _run_pipeline_async(self, original_path, instruction):
        thread = threading.Thread(
            target=self._pipeline_worker, args=(original_path, instruction), daemon=True
        )
        thread.start()

    def _pipeline_worker(self, original_path, instruction):
        data_dir = get_data_dir()
        try:
            Clock.schedule_once(lambda dt: self._status("📖 Reading image..."))

            prep_path = os.path.join(data_dir, "tmp_preprocessed.jpg")
            preprocess_image(original_path, prep_path)

            Clock.schedule_once(lambda dt: self._status("🔎 Gemini analyzing handwriting..."))
            gemini_text = gemini_extract_text(prep_path, instruction)

            Clock.schedule_once(lambda dt: self._status("🧠 Claude verifying text..."))
            verified_text = claude_verify_text(prep_path, gemini_text)

            Clock.schedule_once(lambda dt: self._status("⚖️ Checking unclear legal terms..."))
            # (Web verification already happened inside claude_verify_text via tool calls)

            Clock.schedule_once(lambda dt: self._status("🖊️ Creating clean image..."))
            ts = int(datetime.now().timestamp() * 1000)
            clean_path = os.path.join(data_dir, config.CLEAN_IMAGES_DIR, f"clean_{ts}.jpg")
            render_clean_image(verified_text, clean_path)

            def _finish(dt):
                self._status("Done ✓")
                msg = self.store.add(
                    {
                        "type": "ai_image",
                        "image_path": clean_path,
                        "verified_text": verified_text,
                        "source_image_path": original_path,
                    }
                )
                self._add_widget_for_message(msg)

            Clock.schedule_once(_finish)

        except Exception as e:
            err = str(e)

            def _fail(dt):
                emsg = self.store.add(
                    {"type": "error", "text": f"⚠️ Something went wrong: {err}"}
                )
                self._add_widget_for_message(emsg)
                Snackbar(text="Processing failed. Tap Retry to try again.").open()
                self._add_retry_button(original_path, instruction)

            Clock.schedule_once(_fail)

    def _add_retry_button(self, original_path, instruction):
        chat_box = self.root_widget.ids.chat_box
        btn = MDRaisedButton(
            text="Retry",
            size_hint=(None, None),
            size=(dp(120), dp(40)),
            on_release=lambda x: self._run_pipeline_async(original_path, instruction),
        )
        chat_box.add_widget(btn)
        self._scroll_to_bottom()

    # -- three-dot menu on a generated clean image --------------------------
    def open_image_menu(self, bubble, caller):
        items = [
            {"text": "Share", "action": lambda: self.share_image(bubble)},
            {"text": "Download", "action": lambda: self.download_image(bubble)},
            {"text": "Save to Gallery", "action": lambda: self.save_to_gallery(bubble)},
            {"text": "Regenerate", "action": lambda: self.regenerate_image(bubble)},
            {"text": "Copy Extracted Text", "action": lambda: self.copy_text(bubble)},
            {"text": "Delete", "action": lambda: self.delete_image(bubble)},
        ]
        menu_items = [
            {"text": it["text"], "on_release": (lambda x=it: self._on_image_menu_item(x))}
            for it in items
        ]
        self._current_image_menu = MDDropdownMenu(caller=caller, items=menu_items, width_mult=4)
        self._current_image_menu.open()

    def _on_image_menu_item(self, item):
        if self._current_image_menu:
            self._current_image_menu.dismiss()
        item["action"]()

    def share_image(self, bubble):
        if share:
            try:
                share.share(filepath=bubble.image_source)
                return
            except Exception:
                pass
        Snackbar(text=f"Image saved at: {bubble.image_source}").open()

    def download_image(self, bubble):
        # On Android, "Download" == copy into the public Downloads folder.
        try:
            if ANDROID:
                from android.storage import primary_external_storage_path
                downloads = os.path.join(primary_external_storage_path(), "Download")
                os.makedirs(downloads, exist_ok=True)
                dest = os.path.join(downloads, os.path.basename(bubble.image_source))
                Image.open(bubble.image_source).save(dest)
                Snackbar(text=f"Saved to Downloads: {os.path.basename(dest)}").open()
            else:
                Snackbar(text=f"Image already at: {bubble.image_source}").open()
        except Exception as e:
            Snackbar(text=f"Download failed: {e}").open()

    def save_to_gallery(self, bubble):
        try:
            if ANDROID:
                from android.storage import primary_external_storage_path
                gallery_dir = os.path.join(
                    primary_external_storage_path(), "Pictures", "LLBNotesCleaner"
                )
                os.makedirs(gallery_dir, exist_ok=True)
                dest = os.path.join(gallery_dir, os.path.basename(bubble.image_source))
                Image.open(bubble.image_source).save(dest)
                Snackbar(text="Saved to Gallery.").open()
            else:
                Snackbar(text=f"Image already at: {bubble.image_source}").open()
        except Exception as e:
            Snackbar(text=f"Save failed: {e}").open()

    def regenerate_image(self, bubble):
        """Re-render a new clean image from the ALREADY verified text,
        without re-running Gemini/Claude OCR again."""
        if not bubble.verified_text:
            Snackbar(text="No verified text stored for this image.").open()
            return
        data_dir = get_data_dir()
        ts = int(datetime.now().timestamp() * 1000)
        new_path = os.path.join(data_dir, config.CLEAN_IMAGES_DIR, f"clean_{ts}.jpg")

        def _work():
            render_clean_image(bubble.verified_text, new_path)

            def _done(dt):
                msg = self.store.add(
                    {
                        "type": "ai_image",
                        "image_path": new_path,
                        "verified_text": bubble.verified_text,
                        "source_image_path": bubble.source_image_path,
                    }
                )
                self._add_widget_for_message(msg)
                Snackbar(text="Regenerated a new clean version.").open()

            Clock.schedule_once(_done)

        threading.Thread(target=_work, daemon=True).start()

    def copy_text(self, bubble):
        try:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(bubble.verified_text or "")
            Snackbar(text="Text copied to clipboard.").open()
        except Exception as e:
            Snackbar(text=f"Copy failed: {e}").open()

    def delete_image(self, bubble):
        self.store.remove(bubble.message_id)
        self.render_history()
        Snackbar(text="Deleted.").open()

    # -- top-right app menu --------------------------------------------------
    def open_top_menu(self, caller):
        items = [
            {"text": "Clear all chat history", "action": self.confirm_clear_history},
            {"text": "About", "action": self.show_about},
        ]
        menu_items = [
            {"text": it["text"], "on_release": (lambda x=it: self._on_top_menu_item(x))}
            for it in items
        ]
        self._top_menu = MDDropdownMenu(caller=caller, items=menu_items, width_mult=4)
        self._top_menu.open()

    def _on_top_menu_item(self, item):
        if self._top_menu:
            self._top_menu.dismiss()
        item["action"]()

    def confirm_clear_history(self):
        def _do_clear(*a):
            self.store.messages = []
            self.store.save()
            self.render_history()
            dialog.dismiss()

        dialog = MDDialog(
            title="Clear all chat history?",
            text="This will delete all local chat messages and images.",
            buttons=[
                MDFlatButton(text="Cancel", on_release=lambda x: dialog.dismiss()),
                MDFlatButton(text="Clear", on_release=_do_clear),
            ],
        )
        dialog.open()

    def show_about(self):
        Snackbar(
            text="LLB Notes Cleaner - Gemini + Claude powered notes cleanup."
        ).open()


if __name__ == "__main__":
    LLBNotesCleanerApp().run()
