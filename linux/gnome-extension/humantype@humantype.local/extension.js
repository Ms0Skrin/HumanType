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
    <signal name="EnabledChanged">
      <arg type="b" name="on"/>
    </signal>
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
