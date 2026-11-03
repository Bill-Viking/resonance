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
import sys
import threading
import webbrowser

HOST, PORT = '127.0.0.1', 4317
LOG_NAME = 'launcher.log'
MAX_LOG_BYTES = 256 * 1024
ADDRESS_IN_USE = {errno.EADDRINUSE, errno.EACCES, 10048, 10013}  # Windows socket codes included.


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
    """One calm sentence for the person; the technical detail stays in the log."""
    where = f'\n\nDetails are saved in:\n{log_path}'
    if isinstance(error, OSError) and error.errno in ADDRESS_IN_USE:
        return (f'Resonance could not start because its local address ({HOST}:{port}) is already in use. '
                'If Resonance is already open, use that window. Otherwise close the other program that is '
                'using it and open Resonance again. Your saved data was not changed.' + where)
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
        except Exception as error:  # noqa: BLE001 - every failure becomes a plain message.
            self.server = None
            code = getattr(error, 'errno', None)
            self.log.write('start-failed', type(error).__name__ + (f' errno={code}' if code else ''))
            self.error_message = plain_message(error, self.port, self.log.path)
            return False
        self.thread = threading.Thread(target=self.server.serve_forever, name='resonance-server', daemon=True)
        self.thread.start()
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
            self.server.shutdown()
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


def self_test(data_dir, port, log):
    """Headless check of a packaged build: serve once from a synthetic folder, request the page."""
    from urllib.request import ProxyHandler, build_opener
    from zoneinfo import ZoneInfo
    import server

    class Silent:
        def show_error(self, message):
            pass

    controller = Controller(Silent(), log, data_dir=data_dir, port=port)
    if not controller.start():
        return {'ok': False, 'problem': 'start'}
    try:
        with build_opener(ProxyHandler({})).open(controller.url + '/', timeout=5) as response:
            page = response.status == 200 and b'<html' in response.read(4096).lower()
        ZoneInfo('America/New_York')
        return {'ok': bool(page), 'version': (server.ROOT / 'VERSION').read_text(encoding='utf-8').strip(),
                'seed': (server.ROOT / 'examples' / 'practice.seed.json').is_file(),
                'frozen': bool(getattr(sys, 'frozen', False))}
    finally:
        controller.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true',
                        help='Start once without a window or browser, check the page, and exit')
    parser.add_argument('--data-dir', type=Path, help='Only for --self-test: a synthetic data folder')
    parser.add_argument('--port', type=int, default=PORT, help=argparse.SUPPRESS)
    # Finder may pass a process-serial argument on older macOS releases; it means nothing here.
    argv = [item for item in (sys.argv[1:] if argv is None else argv) if not item.startswith('-psn_')]
    args = parser.parse_args(argv)
    keep_streams_usable()
    log = Log(log_directory() / LOG_NAME)
    if args.self_test:
        if args.data_dir is None:
            parser.error('--self-test needs --data-dir with a temporary synthetic folder.')
        result = self_test(args.data_dir, args.port, log)
        print(json.dumps(result))
        return 0 if result['ok'] else 1
    if args.data_dir is not None or args.port != PORT:
        parser.error('--data-dir and --port are only for --self-test.')
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
