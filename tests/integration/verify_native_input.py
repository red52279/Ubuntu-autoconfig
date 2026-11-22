#!/usr/bin/env python3
"""Check native IBus Chinese conversion without injecting keyboard events."""
import argparse
import json
from pathlib import Path
import gi

gi.require_version('IBus', '1.0')
from gi.repository import IBus, GLib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', default='libpinyin')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    IBus.init()
    bus = IBus.Bus()
    if not bus.is_connected():
        raise SystemExit('当前会话未连接 IBus。')
    context = bus.create_input_context('ubuntu-autoconfig-native-test')
    context.set_capabilities(IBus.Capabilite.PREEDIT_TEXT | IBus.Capabilite.FOCUS)
    committed, errors = [], []
    context.connect('commit-text', lambda _, text: committed.append(text.get_text()))
    keys = iter('nihao ')
    loop = GLib.MainLoop()
    result = {}

    def finish():
        result.update(framework='ibus', engine=args.engine, input='nihao ',
                      committed=''.join(committed), errors=errors,
                      physical_keyboard_injection=False,
                      ok=''.join(committed) == '你好' and not errors)
        context.focus_out()
        context.destroy()
        loop.quit()
        return False

    def send():
        try:
            key = next(keys)
        except StopIteration:
            GLib.timeout_add(800, finish)
            return False
        try:
            context.process_key_event(ord(key), 0, 0)
            context.process_key_event(ord(key), 0, IBus.ModifierType.RELEASE_MASK)
        except Exception as exc:
            errors.append(str(exc))
            return finish()
        return True

    context.set_engine(args.engine)
    context.focus_in()
    GLib.timeout_add(600, lambda: (GLib.timeout_add(200, send), False)[1])
    GLib.timeout_add(6000, finish)
    loop.run()
    output = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    print(output)
    if args.output:
        args.output.write_text(output)
    raise SystemExit(0 if result.get('ok') else 1)


if __name__ == '__main__':
    main()
