"""Resonance desktop launcher: start the local server, open it, and offer Open and Quit.

This is the entry point of the packaged Mac app and Windows folder. It starts the
same loopback-only server as ``server.py`` and shows one small window. Closing the
window stops the server; saved data is kept. Nothing here contacts a network.
"""
import argparse
from datetime import datetime
import errno
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import webbrowser

HOST, PORT = '127.0.0.1', 4317
LOG_NAME = 'launcher.log'
MAX_LOG_BYTES = 256 * 1024
ADDRESS_IN_USE = {errno.EADDRINUSE, 10048}
ADDRESS_DENIED = {errno.EACCES, 10013}
TRANSLOCATION_MESSAGE = ('Move Resonance.app to Applications, then open it from there. '
                         'Resonance did not start or change your saved data.')


def log_directory(platform=None, environ=None, home=None):
    """Where the launcher's own log lives. Never inside the data folder or source."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    home = Path(home) if home else Path.home()
    if platform == 'darwin':
        return home / 'Library' / 'Logs' / 'Resonance'
    if platform == 'win32':
        return Path(environ.get('LOCALAPPDATA', str(home / 'AppData' / 'Local'))) / 'Resonance Launcher'
    return Path(environ.get('XDG_STATE_HOME', str(home / '.local' / 'state'))) / 'resonance-launcher'


class Log:
    """A short plain-text event log. It records fixed event words and error types only."""

    def __init__(self, path):
        self.path = Path(path)

    def write(self, event, detail=''):
        # Callers pass fixed words, the local address, or an error type and code. Error
        # messages are never written: they could quote user text. Never log post text,
        # account names, request bodies or credentials.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > MAX_LOG_BYTES:
                self.path.write_text('', encoding='utf-8')
            line = datetime.now().astimezone().isoformat(timespec='seconds') + ' ' + event
            with open(self.path, 'a', encoding='utf-8') as handle:
                handle.write((line + ' ' + str(detail)).rstrip() + '\n')
        except OSError:
            pass  # A log that cannot be written must never stop the app.


def plain_message(error, port, log_path):
    """Explain a failed start without printing the exception's private text."""
    where = f'\n\nDetails are saved in:\n{log_path}'
    codes = {error.errno, getattr(error, 'winerror', None)} if isinstance(error, OSError) else set()
    if codes & ADDRESS_DENIED:
        return (f'The operating system denied access to Resonance’s local port ({port}). '
                'The port may be reserved or restricted. Your saved data was not changed.' + where)
    if codes & ADDRESS_IN_USE:
        return (f'Resonance could not start because its local address ({HOST}:{port}) is already in use. '
                'Use the existing Resonance window if there is one. An older Resonance source launcher '
                'can also leave a background copy running. Close the program using the port if you '
                'recognize it, then open Resonance again. Your saved data was not changed.' + where)
    return ('Resonance could not start. Please close this message '
            'and open Resonance again.' + where)


class Controller:
    """Start, open and stop the local server. The window and browser are injected."""

    def __init__(self, ui, log, data_dir=None, port=PORT, make_server=None, opener=webbrowser.open):
        self.ui, self.log, self.data_dir, self.port = ui, log, data_dir, port
        self.make_server, self.opener = make_server, opener
        self.server = self.thread = None
        self.error_message = ''
        self.stopped = False

    @property
    def url(self):
        return f'http://{HOST}:{self.port}'

    def start(self):
        """Bind first. Return True only when the server owns its address and is serving."""
        try:
            make_server = self.make_server
            if make_server is None:
                import server  # Imported here so a failure becomes a plain message.
                make_server = server.make_server
            # data_dir None lets server.make_server resolve data_directory() after binding.
            self.server = make_server(self.data_dir, self.port)
            # Port 0 is for tests; the server reports the port it was given.
            self.port = self.server.server_address[1]
            self.thread = threading.Thread(target=self.server.serve_forever, name='resonance-server', daemon=True)
            self.thread.start()
        except Exception as error:  # noqa: BLE001 - every failure becomes a plain message.
            if self.server is not None:
                self.server.server_close()
            self.server = None
            code = getattr(error, 'winerror', None) or getattr(error, 'errno', None)
            self.log.write('start-failed', type(error).__name__ + (f' errno={code}' if code else ''))
            self.error_message = plain_message(error, self.port, self.log.path)
            return False
        self.log.write('started', self.url)
        return True

    def open_browser(self):
        try:
            opened = self.opener(self.url)
        except Exception as error:  # noqa: BLE001
            self.log.write('browser-failed', type(error).__name__)
            return False
        if not opened:
            self.log.write('browser-not-opened')
        return bool(opened)

    def shutdown(self):
        """Stop serving and release the address. Idempotent; saved data is untouched."""
        if self.stopped or self.server is None:
            return
        self.stopped = True
        try:
            try:
                self.server.shutdown()
            finally:
                self.server.server_close()
            if self.thread is not None:
                self.thread.join(timeout=5)
        finally:
            self.log.write('stopped')

    def quit(self):
        self.ui.close()

    def run(self):
        """Return the process exit code. The browser opens only after a successful bind."""
        if not self.start():
            self.ui.show_error(self.error_message)
            return 1
        try:
            self.ui.show_running(self.url, self.open_browser, self.quit)
            self.open_browser()
            self.ui.mainloop()
        finally:
            self.shutdown()
        return 0


class TkUi:
    """The small launcher window. Tk is loaded only when this is used."""

    def __init__(self):
        import tkinter
        self.tk = tkinter
        self.root = tkinter.Tk()
        self.root.withdraw()

    def show_error(self, message):
        from tkinter import messagebox
        messagebox.showerror('Resonance', message, parent=self.root)
        self.root.destroy()

    def show_running(self, url, open_callback, quit_callback):
        tk, root = self.tk, self.root
        root.title('Resonance')
        root.resizable(False, False)
        root.protocol('WM_DELETE_WINDOW', quit_callback)
        try:
            root.createcommand('::tk::mac::Quit', quit_callback)
        except tk.TclError:
            pass
        frame = tk.Frame(root, padx=28, pady=22)
        frame.pack()
        tk.Label(frame, text='Resonance running', font=('TkDefaultFont', 16, 'bold')).pack()
        tk.Label(frame, text=url + '\nOnly on this computer. Closing this window stops Resonance;\n'
                 'everything you saved is kept.', justify='center').pack(pady=(8, 16))
        buttons = tk.Frame(frame)
        buttons.pack()
        tk.Button(buttons, text='Open Resonance', command=open_callback, width=16).pack(side='left', padx=6)
        tk.Button(buttons, text='Quit', command=quit_callback, width=10).pack(side='left', padx=6)
        root.deiconify()

    def mainloop(self):
        self.root.mainloop()

    def close(self):
        self.root.quit()


def keep_streams_usable():
    # A windowed Windows app has no stdout or stderr; libraries that write to them must not crash.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))


def is_translocated(executable=None, platform=None, frozen=None):
    if platform is None:
        platform = sys.platform
    if frozen is None:
        frozen = getattr(sys, 'frozen', False)
    return (platform == 'darwin' and bool(frozen) and
            'AppTranslocation' in Path(sys.executable if executable is None else executable).parts)


def show_move_instruction():
    # A Finder launch may have no visible stderr. Show the move instruction without loading Tk.
    import ctypes

    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    create = cf.CFStringCreateWithCString
    create.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    create.restype = ctypes.c_void_p
    release = cf.CFRelease
    release.argtypes = [ctypes.c_void_p]
    release.restype = None
    alert = cf.CFUserNotificationDisplayAlert
    alert.argtypes = [ctypes.c_double, ctypes.c_ulong] + [ctypes.c_void_p] * 9
    alert.restype = ctypes.c_int32
    title = create(None, b'Resonance', 0x08000100)  # kCFStringEncodingUTF8
    message = create(None, TRANSLOCATION_MESSAGE.encode('utf-8'), 0x08000100)
    try:
        alert(0, 0, None, None, None, title, message, None, None, None, None)
    finally:
        for value in (message, title):
            if value:
                release(value)


def self_test(data_dir, port, log):
    """Check headless Tcl, the page and the assistant prompt with disposable data."""
    try:
        import tkinter

        tcl_patch = tkinter.Tcl().call('info', 'patchlevel')
    except Exception:  # Toolkit import and native initialization can fail in several ways.
        return {'ok': False, 'problem': 'tcl'}
    if not isinstance(tcl_patch, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', tcl_patch):
        return {'ok': False, 'problem': 'tcl'}

    from urllib.request import ProxyHandler, build_opener
    from zoneinfo import ZoneInfo
    import assistant_kit
    import server

    frozen = bool(getattr(sys, 'frozen', False))
    with tempfile.TemporaryDirectory(prefix='resonance-assistant-smoke-') as temporary:
        destination = Path(temporary).resolve() / 'kit'
        try:
            kit = assistant_kit.ensure(server.ROOT, destination=destination, data_directory=data_dir)
        except assistant_kit.KitError:
            return {'ok': False, 'problem': 'assistant-kit'}

        def make_server(directory, address_port):
            app = server.make_server(directory, address_port)
            app.assistant_kit_destination = destination
            return app

        controller = Controller(None, log, data_dir=data_dir, port=port, make_server=make_server)
        if not controller.start():
            return {'ok': False, 'problem': 'start'}
        try:
            client = build_opener(ProxyHandler({}))
            with client.open(controller.url + '/', timeout=5) as response:
                page = response.status == 200 and b'<html' in response.read(4096).lower()
            with client.open(controller.url + '/api/assistant', timeout=5) as response:
                assistant = json.load(response) == {
                    'prompt': f'Read ASSISTANT.md in {kit} and run my Resonance check.'}
            ZoneInfo('America/New_York')
            return {'ok': bool(page and assistant), 'assistant_kit': assistant,
                    'version': (server.ROOT / 'VERSION').read_text(encoding='utf-8').strip(),
                    'seed': (server.ROOT / 'examples' / 'practice.seed.json').is_file(),
                    'frozen': frozen, 'tcl': True, 'tcl_patch': tcl_patch}
        except (OSError, ValueError, KeyError):
            return {'ok': False, 'problem': 'check'}
        finally:
            controller.shutdown()


def write_report(path, result):
    """Publish a complete JSON report even if the executable has no stdout."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(result, file)
            file.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    keep_streams_usable()
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--self-test', action='store_true',
                        help='Check Tcl and the page once without a window or browser, and exit')
    parser.add_argument('--data-dir', type=Path, help='Only for --self-test: a synthetic data folder')
    parser.add_argument('--port', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--log-dir', type=Path, help='Only for --self-test: a synthetic log folder')
    parser.add_argument('--self-test-report', type=Path, help='Only for --self-test: save the JSON result here')
    # Finder and Windows can supply launch arguments that have no meaning here.
    args, _ = parser.parse_known_args(argv)
    if not args.self_test and any(value is not None for value in
                                 (args.data_dir, args.port, args.log_dir, args.self_test_report)):
        parser.error('--data-dir, --port, --log-dir and --self-test-report are only for --self-test.')
    if args.self_test:
        if args.data_dir is None:
            parser.error('--self-test needs --data-dir with a temporary synthetic folder.')
        if args.port is not None and not 0 <= args.port <= 65535:
            parser.error('--port must be between 0 and 65535.')
    if is_translocated():
        print(TRANSLOCATION_MESSAGE, file=sys.stderr)
        if not args.self_test:
            try:
                show_move_instruction()
            except (OSError, AttributeError):
                pass  # stderr still carries the instruction for a terminal launch.
        return 1
    if args.self_test:
        import server

        try:
            data_dir = server.safe_data_directory(args.data_dir)
            log_dir = server.safe_data_directory(args.log_dir) if args.log_dir is not None else data_dir
            report = (server.safe_data_directory(args.self_test_report.parent) / args.self_test_report.name
                      if args.self_test_report is not None else None)
        except (server.Invalid, OSError, RuntimeError):
            print('Choose local synthetic folders for the Resonance self-test.', file=sys.stderr)
            return 1
        log = Log(log_dir / LOG_NAME)
        result = self_test(data_dir, PORT if args.port is None else args.port, log)
        if report is not None:
            try:
                write_report(report, result)
            except OSError:
                print('Resonance could not save its self-test report.', file=sys.stderr)
                return 1
        print(json.dumps(result))
        return 0 if result['ok'] else 1
    log = Log(log_directory() / LOG_NAME)
    try:
        return Controller(TkUi(), log).run()
    except Exception as error:  # noqa: BLE001 - no traceback for a normal user.
        log.write('crashed', type(error).__name__)
        try:
            from tkinter import messagebox
            messagebox.showerror('Resonance', 'Resonance could not open its window. '
                                 f'\n\nDetails are saved in:\n{log.path}')
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
