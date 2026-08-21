[app]

title = LLB Notes Cleaner
package.name = llbnotescleaner
package.domain = org.llbstudent

source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,json

version = 1.0.0

requirements = python3,kivy==2.3.0,kivymd==1.2.0,pillow,requests,plyer,google-generativeai,anthropic,certifi,chardet,idna,urllib3

orientation = portrait
fullscreen = 0

# icon.filename left unset on purpose - the assets/icon.png referenced
# earlier does not exist in this project yet. Buildozer will use a
# default icon. Add your own PNG and uncomment the line below if desired.
# icon.filename = %(source.dir)s/assets/icon.png

# ---------------------------------------------------------------------------
# Android specifics
# ---------------------------------------------------------------------------
android.permissions = INTERNET,CAMERA,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_IMAGES

android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True

# IMPORTANT FIX: the stock python-for-android "master"/release recipe for
# SDL2_ttf's bundled harfbuzz fails to compile with newer NDK clang
# versions (hb-static.cc / hb-ft.cc -Werror issues). Using the "develop"
# branch of python-for-android pulls in the fixed recipe. See:
# https://github.com/orgs/kivy/discussions/28
p4a.branch = develop

# Needed for modern Android (13+) scoped storage / media access
android.add_permissions = READ_MEDIA_IMAGES, READ_MEDIA_VIDEO

# Uncomment and set if you use a custom presplash/icon
# presplash.filename = %(source.dir)s/assets/presplash.png

[buildozer]
log_level = 2
warn_on_root = 1

# If the harfbuzz build still fails after switching to p4a develop branch,
# a fallback is to force-disable the -Werror flags that are turning normal
# compiler warnings into hard build failures. This is set as an
# environment variable (CFLAGS/CXXFLAGS) in .github/workflows/main.yml
# rather than here, since buildozer.spec cannot set env vars directly.
