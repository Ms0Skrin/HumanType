import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from humantype.layout import convert, LANG_RU, LANG_EN
assert convert("Yfrblfq dfhbrjd")[0] == "Накидай вариков", convert("Yfrblfq dfhbrjd")
assert convert("ghbdtn? rfr ltkf")[0] == "привет, как дела"
assert convert("Руддщ")== ("Hello", LANG_EN)
assert convert("ntcn")[1] == LANG_RU
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
app = QApplication([])
from humantype.history import History
d = Path(tempfile.mkdtemp())
h = History(d / "h.db", d, limit=3)
h.add_text("один"); h.add_text("два"); h.add_text("один")
img = QImage(30, 20, QImage.Format.Format_RGB32); img.fill(QColor("red")); h.add_image(img)
items = h.items()
assert [i.text for i in items] == ["30×20", "один", "два"], [i.text for i in items]
h.toggle_pin(items[2].id); h.add_text("три"); h.add_text("четыре")
texts = [i.text for i in h.items()]
assert texts[0] == "два" and len(texts) == 4, texts
assert [i.kind for i in h.items("картинка")] == ["image"]
h.clear(); assert [i.text for i in h.items()] == ["два"]
# real clipboard round trip
cb = app.clipboard(); h2 = History(d / "h2.db", d)
cb.setText("из буфера"); app.processEvents()
cb.setImage(img); app.processEvents()
print([ (i.kind, i.text) for i in h2.items()])
print("OK")
