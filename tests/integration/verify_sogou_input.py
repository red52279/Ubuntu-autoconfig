#!/usr/bin/env python3
"""Verify Sogou commits Chinese through an isolated GTK3 input context."""
import argparse
import json
import os
from pathlib import Path
import subprocess

# Test GTK3/XWayland without sending synthetic keyboard events to other apps.
os.environ['GDK_BACKEND'] = 'x11'
os.environ['GTK_IM_MODULE'] = 'fcitx'
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    window = Gtk.Window(title='搜狗中文转换验证')
    window.set_default_size(380, 60)
    window.add(Gtk.Label(label='检查搜狗引擎，无需输入键盘。'))
    window.show_all()
    context = Gtk.IMMulticontext()
    context.set_context_id('fcitx')
    context.set_client_window(window.get_window())
    committed, filtered, errors = [], [], []
    context.connect('commit', lambda _, text: committed.append(text))
    keys = iter('nihao ')
    report = {}

    def finish():
        report.update(input='nihao ', committed=''.join(committed),
                      platform='GTK3/XWayland', physical_keyboard_injection=False,
                      filtered_keys=filtered, errors=errors,
                      ok=''.join(committed) == '你好' and not errors)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if args.output:
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        context.focus_out()
        window.destroy()
        return False

    def send():
        try:
            key = next(keys)
        except StopIteration:
            GLib.timeout_add(1200, finish)
            return False
        try:
            success, entries = Gdk.Keymap.get_for_display(window.get_display()).get_entries_for_keyval(ord(key))
            if not success:
                raise RuntimeError('无法查找测试字符的 X11 键码。')
            wrapper = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            event = wrapper.key
            event.window = window.get_window()
            event.send_event = True
            event.time = Gdk.CURRENT_TIME
            event.state = Gdk.ModifierType(0)
            event.keyval = ord(key)
            event.hardware_keycode = entries[0].keycode
            event.group = 0
            filtered.append(bool(context.filter_keypress(event)))
            wrapper_release = Gdk.Event.new(Gdk.EventType.KEY_RELEASE)
            released = wrapper_release.key
            released.window = event.window
            released.keyval = event.keyval
            released.hardware_keycode = event.hardware_keycode
            released.state = event.state
            context.filter_keypress(released)
        except Exception as exc:
            errors.append(str(exc))
            return finish()
        return True

    def begin():
        context.focus_in()
        subprocess.run(['/usr/bin/fcitx-remote', '-s', 'sogoupinyin'], check=False)
        subprocess.run(['/usr/bin/fcitx-remote', '-o'], check=False)
        GLib.timeout_add(400, send)
        return False

    window.connect('destroy', Gtk.main_quit)
    GLib.timeout_add(800, begin)
    GLib.timeout_add(8500, finish)
    Gtk.main()
    raise SystemExit(0 if report.get('ok') else 1)


if __name__ == '__main__':
    main()
