"""HumanType for Linux (GNOME on Wayland).

The keyboards are read through evdev and grabbed; every event goes on to the system through a
virtual twin made with uinput, so a word can be held back, erased and typed again in the other
layout. A small GNOME Shell extension (linux/gnome-extension) reports and switches the layout,
tells which app has the focus and what kind of field it is, and shows the notes.
"""
