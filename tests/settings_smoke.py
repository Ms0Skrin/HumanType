import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent, Qt
app = QApplication([])
from humantype import config
config.SETTINGS_FILE = Path(tempfile.mkdtemp()) / "s.json"
from humantype.config import Settings, Hotkey
from humantype.hotkeys import MOD_CONTROL, MOD_ALT
from humantype.settings_window import SettingsWindow, hotkey_text, key_name
s = Settings.load()
print(hotkey_text(s.fix_word), "|", hotkey_text(s.fix_line), "|", hotkey_text(s.history_popup))
print(key_name(0x25), key_name(0x2E), key_name(0xC0), key_name(0x75))
applied = []
w = SettingsWindow(s, lambda n: (applied.append(n), [])[1], lambda: None)
w.show()
b = w.hk_word
b.click()
ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier, 0x25, ord("K"), 0)
b.keyPressEvent(ev)
assert b.hotkey == Hotkey(MOD_CONTROL | MOD_ALT, ord("K")), b.hotkey
print("captured:", b.text())
w.limit.setValue(300); w.save_images.setChecked(False)
w._save()
n = applied[0]
assert n.fix_word.vk == ord("K") and n.history_limit == 300 and not n.save_images
n.save(); r = Settings.load()
assert r == n, (r, n)
# duplicate check
w2 = SettingsWindow(s, lambda n: [], lambda: None); w2.hk_line.hotkey = w2.hk_word.hotkey
print("OK")
