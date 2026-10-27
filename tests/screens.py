import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
out = Path(sys.argv[1])
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
app = QApplication([]); app.setStyle("Fusion")
from humantype import config
config.SETTINGS_FILE = Path(tempfile.mkdtemp()) / "s.json"
from humantype.history import History
from humantype.config import Settings
from humantype.settings_window import SettingsWindow
from humantype.popup import ClipPopup
d = Path(tempfile.mkdtemp()); h = History(d / "h.db", d)
for t in ["love.men.i3550@gmail.com", "Накидай вариков на тему что можно с тобой навайбкодить", "git commit -m \"HumanType 0.2: новый дизайн\""]:
    h.add_text(t); time.sleep(0.01)
img = QImage(320, 180, QImage.Format.Format_RGB32); img.fill(QColor("#38BDF8")); h.add_image(img)
h.toggle_pin(h.items()[3].id)
from humantype.autoswitch import AutoSwitcher
from humantype.detector import Detector
sw = AutoSwitcher(Detector(Path(__file__).resolve().parent.parent / "assets" / "dict"), d / "exc.txt")
sw.detector.exceptions = {"ghbdtn", "vfvf"}
w = SettingsWindow(Settings(), lambda s: [], lambda: None, h, sw); w.resize(820, 900); w.show(); app.processEvents()
for i, name in enumerate(["hotkeys", "autoswitch", "history", "behaviour", "about"]):
    w.nav.setCurrentRow(i); app.processEvents(); w.grab().save(str(out / f"settings_{name}.png"))
p = ClipPopup(h, print); p.open_at_cursor(None); app.processEvents(); time.sleep(0.2); p.setWindowOpacity(1); app.processEvents()
p.grab().save(str(out / "popup.png"))
from humantype import app as appmod
inst = appmod.HumanTypeApp.__new__(appmod.HumanTypeApp)
inst.history = h; inst.fixer = None; inst.popup = p; inst.settings = Settings()
m = inst._menu(); inst._update_autoswitch_action(); inst._update_header(); m.popup(app.primaryScreen().geometry().center()); app.processEvents()
m.grab().save(str(out / "menu.png"))
print("ok")
