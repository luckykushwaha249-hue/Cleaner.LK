# LLB Notes Cleaner

A WhatsApp-style Android app (Python + Kivy/KivyMD) that turns blurry
handwritten LLB whiteboard notes into a clean, readable image, using
**Google Gemini** (OCR/extraction) followed by **Claude** (verification +
correction using LLB context, with optional web lookup for unclear legal
terms).

## Workflow

```
Blurry board photo
      │
      ▼
Gemini  → extracts ALL visible text (headings, paragraphs, Hindi/English,
           Article/Section numbers, case names) — no summarizing
      │
      ▼
Claude  → compares the extraction against the original photo, fixes OCR
           mistakes, fills in unclear words using LLB context, and can
           call a web-verification tool for unclear legal terms/case names
      │
      ▼
Clean image renderer → draws the verified text as a clean, readable
                        "handwritten notes" style image
      │
      ▼
Shown in a WhatsApp-style chat (original + clean image both saved locally)
```

Both AIs are instructed never to summarize, never to drop visible content,
and never to change Article/Section numbers, case names or legal
terminology except to correct an OCR mistake confirmed by the image.

## 1. Project structure

```
LLBNotesCleaner/
├── main.py                        # Full Kivy/KivyMD app (UI + AI pipeline)
├── config.py                      # API key / settings loader (no keys hard-coded)
├── buildozer.spec                 # Android build configuration
├── README.md
└── .github/workflows/main.yml     # CI: builds the APK automatically
```

## 2. Requirements (desktop testing)

- Python 3.10+
- A Devanagari-capable TTF font if you want Hindi text rendered correctly
  in the clean image (see step 5 below)

Install dependencies:

```bash
pip install kivy==2.3.0 kivymd==1.2.0 pillow requests plyer \
            google-generativeai anthropic
```

## 3. API key setup

**Never commit real API keys.** Two supported ways to provide them:

**Option A — environment variables (recommended for desktop testing)**

```bash
export GEMINI_API_KEY="your_gemini_key"
export CLAUDE_API_KEY="your_claude_key"
```

**Option B — local `secrets.json` (for personal test APK builds only)**

Create a file `secrets.json` in the project root (already meant to be
git-ignored — see step 8 on security):

```json
{
  "GEMINI_API_KEY": "your_gemini_key",
  "CLAUDE_API_KEY": "your_claude_key"
}
```

Optional (only if you want live web verification of unclear legal terms
via Google Custom Search):

```json
{
  "WEB_SEARCH_API_KEY": "your_google_cse_key",
  "WEB_SEARCH_ENGINE_ID": "your_cse_id"
}
```

If web verification keys are not set, that feature is simply skipped —
the app still works fully using Gemini + Claude + the image + LLB context.

## 4. Running on desktop (for development)

```bash
python main.py
```

Camera/gallery pickers use `plyer`, which falls back gracefully to a
desktop file dialog on Windows/Linux/Mac for quick testing.

## 5. Hindi font (recommended)

For correct Hindi rendering in the generated clean image, download a
Devanagari-capable font (for example **Noto Sans Devanagari**) and place
it here:

```
assets/fonts/NotoSansDevanagari-Regular.ttf
```

If this file is missing, the app still runs and falls back to a default
font, but Hindi characters may not render correctly in the final image.

## 6. Building the APK locally with Buildozer

```bash
pip install buildozer cython==0.29.36
buildozer android debug
```

The APK will be produced under `bin/`. First builds take a while since
Buildozer downloads the Android SDK/NDK.

## 7. Building automatically with GitHub Actions

1. Push this project to a GitHub repository.
2. In the repo, go to **Settings → Secrets and variables → Actions** and
   add secrets `GEMINI_API_KEY` and `CLAUDE_API_KEY` (optional but
   recommended so the CI-built APK works immediately for testing).
3. Go to the **Actions** tab → select **Build Android APK** → **Run
   workflow** (or just push to `main`, which triggers it automatically).
4. Once the run finishes, download the `llb-notes-cleaner-apk` artifact —
   it contains the built `.apk` file.

## 8. Security notes (important)

- API keys are **never** hard-coded in `main.py` or `config.py`. They are
  loaded from environment variables or an optional local `secrets.json`
  (add `secrets.json` to `.gitignore` — do not commit it).
- Shipping real API keys inside an installed APK is inherently insecure
  for a public release: anyone can decompile the APK and extract the
  keys. This project is structured so `config.py` is the **single place**
  that resolves keys, which makes it easy to later swap in a backend
  proxy:
  - Stand up a small backend (e.g. FastAPI/Node) that holds the real
    Gemini/Claude keys.
  - Expose two endpoints, e.g. `/extract` and `/verify`.
  - Change `gemini_extract_text()` / `claude_verify_text()` in `main.py`
    to call your backend instead of the AI SDKs directly.
  - Remove the keys from the APK entirely.
- For personal use / private testing (installing the APK on your own
  phone only), using GitHub Actions secrets to bake in your own keys, as
  set up above, is a reasonable and simple approach.

## 9. Features implemented

- WhatsApp-style chat UI (top bar, scrollable chat, bottom message box)
- `+` button → Camera / Gallery / File / Text instruction
- Processing status bubbles: Reading Image → Gemini analyzing → Claude
  verifying → Checking legal terms → Creating clean image → Done ✓
- Three-dot menu on every generated clean image: Share, Download, Save to
  Gallery, Regenerate (reuses stored verified text, no re-OCR), Copy
  Extracted Text, Delete
- Local JSON-based chat history (original + clean images persist across
  app restarts)
- Automatic WhatsApp-style date separators for each new calendar day
- Basic image preprocessing (auto-rotate via EXIF, resize, contrast) before
  sending to the AI, while the original photo is kept unmodified in chat
- Error handling with a Retry option if the API/internet call fails

## 10. Notes / limitations to be aware of

- Model names in `config.py` (`GEMINI_MODEL`, `CLAUDE_MODEL`) should be
  reviewed/updated to whichever current models your API keys have access
  to, since provider model names change over time.
- The "clean image" is generated by rendering the verified text onto a
  plain canvas with a readable font (not a generative handwriting AI
  image model), which keeps the app simple, fast, and fully text-faithful
  to the original notes — this matches the requirement to never turn
  notes into an "artistic interpretation."
- Android runtime permission prompts (camera/storage) are requested on
  first launch; if a user denies them, camera/gallery features will show
  a simple error message instead of crashing.
