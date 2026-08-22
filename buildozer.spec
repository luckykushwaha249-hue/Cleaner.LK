[app]

title = LLB Notes Cleaner
package.name = llbnotescleaner
package.domain = org.llbstudent

source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,json

version = 1.0.0

requirements = python3==3.11.9,hostpython3==3.11.9,cython==0.29.33,kivy==2.3.0,pillow,requests,plyer,certifi,chardet,idna,urllib3

orientation = portrait
fullscreen = 0

# icon.filename left unset on purpose - assets/icon.png does not exist yet.
# Buildozer will use a default icon. Add your own PNG and uncomment below.
# icon.filename = %(source.dir)s/assets/icon.png

# ---------------------------------------------------------------------------
# Android specifics
# ---------------------------------------------------------------------------
android.permissions = INTERNET,CAMERA,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_IMAGES

android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.allow_backup = True


# Needed for modern Android (13+) scoped storage / media access
android.add_permissions = READ_MEDIA_IMAGES, READ_MEDIA_VIDEO

# Uncomment and set if you use a custom presplash/icon
# presplash.filename = %(source.dir)s/assets/presplash.png

[buildozer]
log_level = 2
warn_on_root = 1
