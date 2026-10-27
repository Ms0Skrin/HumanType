// HumanType helper for GNOME Shell.
//
// The humantype service reads the keyboard and does the switching; only the shell knows the
// layout really in force, the app in focus and what kind of text field it is, so it asks here
// over D-Bus (org.humantype.Shell). The text around the caret never leaves the shell: the service
// may only ask whether it ends with a word it expects.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import IBus from 'gi://IBus';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as Keyboard from 'resource:///org/gnome/shell/ui/status/keyboard.js';
import * as KeyboardManager from 'resource:///org/gnome/shell/misc/keyboardManager.js';

const SHELL_IFACE = `
<node>
  <interface name="org.humantype.Shell">
    <method name="GetState">
      <arg type="s" direction="in" name="expect"/>
      <arg type="b" direction="in" name="ignoreCase"/>
      <arg type="a{sv}" direction="out" name="state"/>
    </method>
    <method name="SetLayout">
      <arg type="s" direction="in" name="lang"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="ShowNote">
      <arg type="s" direction="in" name="kind"/>
      <arg type="s" direction="in" name="text"/>
    </method>
    <method name="ShowHistory">
      <arg type="a(ussb)" direction="in" name="items"/>
    </method>
    <signal name="LayoutChanged">
      <arg type="s" name="lang"/>
    </signal>
    <signal name="FocusChanged"/>
  </interface>
</node>`;

const DAEMON_IFACE = `
<node>
  <interface name="org.humantype.Daemon">
    <method name="SetEnabled">
      <arg type="b" direction="in" name="on"/>
    </method>
    <property name="Enabled" type="b" access="read"/>
    <property name="Version" type="s" access="read"/>
    <signal name="EnabledChanged">
      <arg type="b" name="on"/>
    </signal>
    <method name="ClipboardAdd">
      <arg type="s" direction="in" name="text"/>
      <arg type="s" direction="in" name="png"/>
    </method>
    <method name="HistoryGet">
      <arg type="u" direction="in" name="id"/>
      <arg type="s" direction="out" name="kind"/>
      <arg type="s" direction="out" name="text"/>
      <arg type="s" direction="out" name="image"/>
    </method>
    <method name="HistoryPin">
      <arg type="u" direction="in" name="id"/>
    </method>
    <method name="HistoryDelete">
      <arg type="u" direction="in" name="id"/>
    </method>
    <method name="HistoryDone">
      <arg type="u" direction="in" name="id"/>
    </method>
  </interface>
</node>`;
const DaemonProxy = Gio.DBusProxy.makeProxyWrapper(DAEMON_IFACE);

const WORD_EDGE = ' \t\n([{«"\'—–-/';
const XKB_TO_LANG = {us: 'en', ru: 'ru'};
const LANG_TO_XKB = {en: 'us', ru: 'ru'};

function purposeName(value) {
    for (const [name, v] of Object.entries(IBus.InputPurpose)) {
        if (v === value)
            return name.toLowerCase().replace('free_form', 'normal');
    }
    return 'normal';
}

// Password managers mark what they copy so that history tools leave it out.
const PRIVATE_MIME = 'x-kde-passwordManagerHint';
const TEXT_MIMES = ['text/plain;charset=utf-8', 'UTF8_STRING', 'text/plain', 'STRING', 'TEXT'];
const KEYCODE_P = 33;  // the P key whatever the layout (evdev 25 + 8)
const ROW = 'border-radius: 10px; padding: 8px 10px;';
const ROW_SELECTED = `${ROW} background-color: rgba(56, 189, 248, 0.22);`;

// The clipboard history at the caret: type to search, arrows and Enter to paste, Ctrl+P pins,
// Shift+Delete removes, Esc or a click elsewhere closes.
const HistoryPopup = GObject.registerClass(
class HistoryPopup extends St.Widget {
    _init(items, actions) {
        // A see-through layer over the whole screen: a click outside the card closes the list.
        super._init({reactive: true, x: 0, y: 0, width: global.stage.width, height: global.stage.height});
        this._items = items;
        this._actions = actions;  // {pick(item), pin(item), remove(item), closed(picked)}
        this._shown = [];
        this._rows = [];
        this._selected = 0;
        this._closed = false;
        this._card = new St.BoxLayout({
            vertical: true,
            reactive: true,
            style: 'background-color: rgba(22, 26, 34, 0.97); border-radius: 16px; padding: 12px;' +
                'border: 1px solid rgba(56, 189, 248, 0.35); spacing: 8px; width: 460px;',
        });
        this.add_child(this._card);
        this._entry = new St.Entry({hint_text: 'Поиск в истории буфера', can_focus: true, style: 'padding: 6px 10px;'});
        this._card.add_child(this._entry);
        this._list = new St.BoxLayout({vertical: true, style: 'spacing: 2px;'});
        this._scroll = new St.ScrollView({
            style: 'max-height: 420px;',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true,
        });
        this._scroll.set_child(this._list);
        this._card.add_child(this._scroll);
        this._card.add_child(new St.Label({
            text: 'Enter — вставить · Ctrl+P — закрепить · Shift+Delete — удалить · Esc — закрыть',
            style: 'font-size: 11px; color: rgba(242, 244, 248, 0.55);',
        }));
        this._entry.clutter_text.connect('text-changed', () => {
            this._selected = 0;
            this._fill();
        });
        this._entry.clutter_text.connect('key-press-event', (_actor, event) => this._key(event));
        this.connect('button-press-event', (_actor, event) => {
            const [x, y] = event.get_coords();
            const [cx, cy] = this._card.get_transformed_position();
            const [cw, ch] = this._card.get_transformed_size();
            if (x < cx || y < cy || x > cx + cw || y > cy + ch)
                this.close(false);
            return Clutter.EVENT_PROPAGATE;
        });
        this._fill();
    }

    open(x, y) {
        Main.layoutManager.uiGroup.add_child(this);
        this._grab = Main.pushModal(this, {actionMode: Shell.ActionMode.POPUP});
        if ((this._grab.get_seat_state() & Clutter.GrabState.KEYBOARD) === 0) {
            this.close(false);
            return;
        }
        const monitor = Main.layoutManager.currentMonitor;
        const [, width] = this._card.get_preferred_width(-1);
        const [, height] = this._card.get_preferred_height(width);
        x = Math.max(monitor.x + 8, Math.min(x, monitor.x + monitor.width - width - 8));
        y = Math.max(monitor.y + 8, Math.min(y, monitor.y + monitor.height - height - 8));
        this._card.set_position(Math.round(x), Math.round(y));
        this._entry.grab_key_focus();
    }

    close(picked) {
        if (this._closed)
            return;
        this._closed = true;
        if (this._grab)
            Main.popModal(this._grab);
        this.destroy();
        this._actions.closed(picked);
    }

    _fill() {
        this._list.destroy_all_children();
        this._rows = [];
        const q = this._entry.get_text().trim().toLowerCase();
        this._shown = this._items.filter(item => !q || item.text.toLowerCase().includes(q) ||
            (item.kind === 'image' && 'картинка изображение image'.includes(q)));
        if (this._shown.length === 0) {
            this._list.add_child(new St.Label({
                text: this._items.length ? 'Ничего не нашлось' : 'История пуста: скопируйте что-нибудь',
                style: 'padding: 12px; color: rgba(242, 244, 248, 0.6);',
            }));
            return;
        }
        this._selected = Math.min(this._selected, this._shown.length - 1);
        this._shown.forEach((item, i) => {
            const row = new St.Button({x_expand: true, can_focus: false, style: i === this._selected ? ROW_SELECTED : ROW});
            const box = new St.BoxLayout({x_expand: true, style: 'spacing: 10px;'});
            if (item.kind === 'image' && item.image)
                box.add_child(new St.Icon({gicon: Gio.FileIcon.new(Gio.File.new_for_path(item.image)), icon_size: 48}));
            box.add_child(new St.Label({
                text: (item.pinned ? '📌 ' : '') + (item.kind === 'image' ? `Картинка ${item.text}` : item.text),
                x_expand: true,
                y_align: Clutter.ActorAlign.CENTER,
                style: 'color: #f2f4f8; font-size: 13px;',
            }));
            row.set_child(box);
            row.connect('clicked', () => {
                this._selected = i;
                this._pick();
            });
            this._list.add_child(row);
            this._rows.push(row);
        });
    }

    _select(i) {
        if (this._rows.length === 0)
            return;
        this._rows[this._selected].style = ROW;
        this._selected = (i + this._rows.length) % this._rows.length;
        this._rows[this._selected].style = ROW_SELECTED;
        this._scrollTo(this._rows[this._selected]);
    }

    _scrollTo(row) {
        // Keep the selected row in view; the adjustment's place moved between shell versions.
        const adjustment = this._scroll.vadjustment ?? this._scroll.vscroll?.adjustment;
        if (!adjustment)
            return;
        const box = row.get_allocation_box();
        if (box.y1 < adjustment.value)
            adjustment.value = box.y1;
        else if (box.y2 > adjustment.value + adjustment.page_size)
            adjustment.value = box.y2 - adjustment.page_size;
    }

    _key(event) {
        const sym = event.get_key_symbol();
        const state = event.get_state();
        const item = this._shown[this._selected];
        if (sym === Clutter.KEY_Escape) {
            this.close(false);
        } else if (sym === Clutter.KEY_Down) {
            this._select(this._selected + 1);
        } else if (sym === Clutter.KEY_Up) {
            this._select(this._selected - 1);
        } else if (sym === Clutter.KEY_Return || sym === Clutter.KEY_KP_Enter) {
            this._pick();
        } else if (item && sym === Clutter.KEY_Delete && (state & Clutter.ModifierType.SHIFT_MASK)) {
            this._actions.remove(item);
            this._items = this._items.filter(x => x !== item);
            this._fill();
        } else if (item && event.get_key_code() === KEYCODE_P && (state & Clutter.ModifierType.CONTROL_MASK)) {
            this._actions.pin(item);
            item.pinned = !item.pinned;
            this._items = [...this._items.filter(x => x.pinned), ...this._items.filter(x => !x.pinned)];
            this._fill();
        } else {
            return Clutter.EVENT_PROPAGATE;
        }
        return Clutter.EVENT_STOP;
    }

    _pick() {
        const item = this._shown[this._selected];
        if (!item)
            return;
        this.close(true);
        this._actions.pick(item);
    }
});

const HumanTypeToggle = GObject.registerClass(
class HumanTypeToggle extends QuickSettings.QuickToggle {
    _init(onClick, gicon) {
        super._init({
            title: 'HumanType',
            subtitle: 'Автопереключение',
            gicon,
            toggleMode: false,
        });
        this.connect('clicked', onClick);
    }
});

const HumanTypeIndicator = GObject.registerClass(
class HumanTypeIndicator extends QuickSettings.SystemIndicator {
    _init(onClick, gicon) {
        super._init();
        // The icon in the top bar: bright while switching is on, dim while off or not running.
        this.icon = this._addIndicator();
        this.icon.gicon = gicon;
        this.icon.visible = true;
        this.toggle = new HumanTypeToggle(onClick, gicon);
        this.quickSettingsItems.push(this.toggle);
    }

    showOn(on) {
        this.icon.opacity = on ? 255 : 90;
    }
});

export default class HumanTypeExtension extends Extension {
    enable() {
        this._keyboardManager = KeyboardManager.getKeyboardManager();
        this._sources = Keyboard.getInputSourceManager();
        this._seat = global.stage.context.get_backend().get_default_seat();
        this._lang = this._currentLang();

        this._dbus = Gio.DBusExportedObject.wrapJSObject(SHELL_IFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/humantype/Shell');
        this._nameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session, 'org.humantype.Shell', Gio.BusNameOwnerFlags.REPLACE, null, null);

        this._signals = [
            [global.backend, global.backend.connect('keymap-layout-group-changed', () => this._layoutMaybeChanged())],
            [global.backend, global.backend.connect('keymap-changed', () => this._layoutMaybeChanged())],
            [global.display, global.display.connect('notify::focus-window', () => this._dbus.emit_signal('FocusChanged', null))],
        ];
        const selection = global.display.get_selection();
        this._signals.push([selection, selection.connect('owner-changed', (_sel, type) => {
            if (type === Meta.SelectionType.SELECTION_CLIPBOARD)
                this._clipboardChanged();
        })]);
        this._history = null;
        this._sources.connectObject('current-source-changed', () => this._layoutMaybeChanged(), this);

        const gicon = Gio.FileIcon.new(this.dir.get_child('icons').get_child('humantype.svg'));
        this._indicator = new HumanTypeIndicator(() => this._toggleClicked(), gicon);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
        this._daemon = null;
        this._watchId = Gio.bus_watch_name(Gio.BusType.SESSION, 'org.humantype.Daemon',
            Gio.BusNameWatcherFlags.NONE, () => this._daemonAppeared(), () => this._daemonVanished());
        this._daemonVanished();
    }

    disable() {
        Gio.bus_unwatch_name(this._watchId);
        this._daemon = null;
        this._indicator.quickSettingsItems.forEach(item => item.destroy());
        this._indicator.destroy();
        this._indicator = null;
        for (const [obj, id] of this._signals)
            obj.disconnect(id);
        this._signals = [];
        this._sources.disconnectObject(this);
        Gio.bus_unown_name(this._nameId);
        this._dbus.unexport();
        this._dbus = null;
        this._note?.destroy();
        this._note = null;
        this._history?.close(false);
        this._history = null;
        if (this._noteTimeout) {
            GLib.source_remove(this._noteTimeout);
            this._noteTimeout = 0;
        }
    }

    // --- the layout really in force (Alt+Shift through xkb may leave the shell's own idea behind) ---

    _currentLang() {
        const keymap = this._seat.get_keymap();
        const name = keymap.get_current_display_name?.();
        const group = this._keyboardManager._current?.group ?? [];
        const info = group.find(g => g.displayName === name) ??
            group.find(g => g.shortName === keymap.get_current_short_name?.());
        const layout = info ? info.layout : this._sources.currentSource?.xkbId?.split('+')[0];
        return XKB_TO_LANG[layout] ?? '';
    }

    _layoutMaybeChanged() {
        const lang = this._currentLang();
        if (lang !== this._lang) {
            this._lang = lang;
            this._dbus?.emit_signal('LayoutChanged', new GLib.Variant('(s)', [lang]));
        }
    }

    // --- D-Bus methods ---

    GetState(expect, ignoreCase) {
        const win = global.display.focus_window;
        const app = win ? Shell.WindowTracker.get_default().get_window_app(win) : null;
        const im = Main.inputMethod;
        const state = {
            layout: new GLib.Variant('s', this._currentLang()),
            app: new GLib.Variant('s', (app?.get_id() ?? '').replace(/\.desktop$/, '')),
            wm_class: new GLib.Variant('s', win?.get_wm_class() ?? ''),
            purpose: new GLib.Variant('s', im.currentFocus ? purposeName(im._purpose) : 'normal'),
            private: new GLib.Variant('b', Boolean(im.currentFocus &&
                (im._hints & (IBus.InputHints.PRIVATE | IBus.InputHints.HIDDEN_TEXT)))),
            fullscreen: new GLib.Variant('b', win?.is_fullscreen() ?? false),
        };
        const [text, cursor, anchor] = im.getSurroundingText();
        if (im.currentFocus && text !== null && cursor === anchor) {
            // Offsets are in characters (mutter converts the protocol's bytes).
            let before = Array.from(text).slice(0, cursor).join('');
            let want = expect;
            if (ignoreCase) {
                before = before.toLowerCase();
                want = want.toLowerCase();
            }
            const matches = before.endsWith(want);
            const prev = matches ? before.slice(0, before.length - want.length).slice(-1) : '';
            state.text_known = new GLib.Variant('b', true);
            state.matches = new GLib.Variant('b', matches);
            state.at_edge = new GLib.Variant('b', matches && (prev === '' || WORD_EDGE.includes(prev)));
            // How much of the expected text is there after all: the app may have changed the text
            // without a key (a chat clears its field after sending). A length, never the text.
            const b = Array.from(before), w = Array.from(want);
            let common = 0;
            while (common < b.length && common < w.length && b[b.length - 1 - common] === w[w.length - 1 - common])
                common++;
            const prevCommon = b[b.length - 1 - common] ?? '';
            state.common = new GLib.Variant('u', common);
            state.common_edge = new GLib.Variant('b', prevCommon === '' || WORD_EDGE.includes(prevCommon));
        }
        return state;
    }

    async SetLayoutAsync([lang], invocation) {
        try {
            invocation.return_value(new GLib.Variant('(b)', [await this._setLayout(lang)]));
        } catch (e) {
            logError(e, 'HumanType: SetLayout');
            invocation.return_value(new GLib.Variant('(b)', [false]));
        }
    }

    async _setLayout(lang) {
        if (this._currentLang() === lang)
            return true;
        const xkb = LANG_TO_XKB[lang];
        const source = Object.values(this._sources.inputSources)
            .find(s => s.type === 'xkb' && s.xkbId.split('+')[0] === xkb);
        if (!source)
            return false;
        const km = this._keyboardManager;
        if (km._current?.id === source.xkbId && km._ourKeymapDescription) {
            // The shell believes it is there already (the layout was changed through xkb options):
            // set the group directly, as the shell itself does.
            const info = km._layoutInfos[source.xkbId];
            await global.backend.set_keymap_async(km._ourKeymapDescription, info.groupIndex, null);
        } else {
            source.activate(true);
        }
        // The keymap is applied in mutter's input thread; wait until it is in force so that the
        // keys pressed next are read in the new layout.
        const until = GLib.get_monotonic_time() + 400 * 1000;
        while (this._currentLang() !== lang && GLib.get_monotonic_time() < until) {
            await new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 5, () => {
                resolve();
                return GLib.SOURCE_REMOVE;
            }));
        }
        this._layoutMaybeChanged();
        return this._currentLang() === lang;
    }

    ShowNote(kind, text) {
        this._note?.destroy();
        if (this._noteTimeout)
            GLib.source_remove(this._noteTimeout);
        const note = new St.Label({
            text,
            style: 'background-color: rgba(30, 32, 38, 0.94); color: #f2f4f8; border-radius: 9px;' +
                'padding: 6px 12px; font-size: 13px; border: 1px solid rgba(56, 189, 248, 0.45);',
            opacity: 0,
        });
        Main.layoutManager.uiGroup.add_child(note);
        const rect = Main.inputMethod.currentFocus ? Main.inputMethod._cursorRect : null;
        let [x, y] = rect ? [rect.x, rect.y + rect.height + 6] : global.get_pointer();
        if (!rect)
            [x, y] = [x + 16, y + 20];
        const monitor = Main.layoutManager.currentMonitor;
        const [, natWidth] = note.get_preferred_width(-1);
        const [, natHeight] = note.get_preferred_height(-1);
        x = Math.min(x, monitor.x + monitor.width - natWidth - 8);
        y = Math.min(y, monitor.y + monitor.height - natHeight - 8);
        note.set_position(Math.round(x), Math.round(y));
        note.ease({opacity: 255, duration: 120, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this._note = note;
        const ms = kind === 'fix' ? 1100 : 1600;
        this._noteTimeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._noteTimeout = 0;
            note.ease({
                opacity: 0, duration: 260, mode: Clutter.AnimationMode.EASE_OUT_QUAD,
                onComplete: () => {
                    note.destroy();
                    if (this._note === note)
                        this._note = null;
                },
            });
            return GLib.SOURCE_REMOVE;
        });
    }

    // --- the clipboard history: the service keeps it, the shell sees the clipboard and shows the list ---

    _clipboardChanged() {
        if (!this._daemon)
            return;
        const clipboard = St.Clipboard.get_default();
        const types = clipboard.get_mimetypes(St.ClipboardType.CLIPBOARD) ?? [];
        if (types.includes(PRIVATE_MIME))
            return;
        if (types.some(t => TEXT_MIMES.includes(t))) {
            clipboard.get_text(St.ClipboardType.CLIPBOARD, (_c, text) => {
                if (text && text.trim())
                    this._daemon?.ClipboardAddRemote(text, '', (_r, error) => error && logError(error, 'HumanType: ClipboardAdd'));
            });
        } else if (types.includes('image/png') && clipboard.get_content) {
            clipboard.get_content(St.ClipboardType.CLIPBOARD, 'image/png', (_c, bytes) => {
                if (!bytes || bytes.get_size() === 0)
                    return;
                // The picture goes to the service as a file of the user's own: the bus is no place for megabytes.
                const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), `humantype-clip-${Date.now()}.png`]);
                const file = Gio.File.new_for_path(path);
                file.replace_contents_bytes_async(bytes, null, false, Gio.FileCreateFlags.PRIVATE, null, (f, result) => {
                    try {
                        f.replace_contents_finish(result);
                    } catch (e) {
                        logError(e, 'HumanType: clipboard picture');
                        return;
                    }
                    this._daemon?.ClipboardAddRemote('', path, (_r, error) => error && logError(error, 'HumanType: ClipboardAdd'));
                });
            });
        }
    }

    ShowHistory(rows) {
        this._history?.close(false);
        const items = rows.map(([id, kind, text, image, pinned]) => ({id, kind, text, image, pinned}));
        const rect = Main.inputMethod.currentFocus ? Main.inputMethod._cursorRect : null;
        let [x, y] = rect ? [rect.x, rect.y + rect.height + 6] : global.get_pointer();
        if (!rect)
            [x, y] = [x + 12, y + 16];
        const popup = new HistoryPopup(items, {
            pick: item => this._paste(item),
            pin: item => this._daemon?.HistoryPinRemote(item.id, () => {}),
            remove: item => this._daemon?.HistoryDeleteRemote(item.id, () => {}),
            closed: picked => {
                if (this._history === popup)
                    this._history = null;
                if (!picked)
                    this._daemon?.HistoryDoneRemote(0, () => {});
            },
        });
        this._history = popup;
        popup.open(x, y);
    }

    _paste(item) {
        const done = id => this._daemon?.HistoryDoneRemote(id, () => {});
        if (!this._daemon) {
            return;
        }
        this._daemon.HistoryGetRemote(item.id, (result, error) => {
            if (error) {
                logError(error, 'HumanType: HistoryGet');
                done(0);
                return;
            }
            const [kind, text, image] = result;
            const clipboard = St.Clipboard.get_default();
            try {
                if (kind === 'text') {
                    clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
                } else if (kind === 'image' && image && clipboard.set_content) {
                    const [, contents] = GLib.file_get_contents(image);
                    clipboard.set_content(St.ClipboardType.CLIPBOARD, 'image/png', new GLib.Bytes(contents));
                } else {
                    done(0);
                    return;
                }
            } catch (e) {
                logError(e, 'HumanType: set clipboard');
                done(0);
                return;
            }
            done(item.id);  // the service presses Ctrl+V
        });
    }

    // --- the quick settings toggle ---

    _daemonAppeared() {
        this._daemon = new DaemonProxy(Gio.DBus.session, 'org.humantype.Daemon', '/org/humantype/Daemon',
            (proxy, error) => {
                if (error) {
                    logError(error, 'HumanType: daemon proxy');
                    return;
                }
                proxy.connectSignal('EnabledChanged', (_p, _sender, [on]) => this._showEnabled(on));
                this._showEnabled(proxy.Enabled);
                if (proxy.Version)
                    this._indicator.toggle.title = `HumanType ${proxy.Version}`;
            });
    }

    _daemonVanished() {
        this._daemon = null;
        if (!this._indicator)
            return;
        this._indicator.toggle.checked = false;
        this._indicator.toggle.subtitle = 'Служба не запущена';
        this._indicator.showOn(false);
    }

    _showEnabled(on) {
        this._indicator.toggle.checked = Boolean(on);
        this._indicator.toggle.subtitle = on ? 'Автопереключение' : 'Выключено';
        this._indicator.showOn(Boolean(on));
    }

    _toggleClicked() {
        if (!this._daemon) {
            this.ShowNote('toggle', 'Служба HumanType не запущена');
            return;
        }
        this._daemon.SetEnabledRemote(!this._indicator.toggle.checked, (_r, error) => {
            if (error)
                logError(error, 'HumanType: SetEnabled');
        });
    }
}
