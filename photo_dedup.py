#!/usr/bin/env python3
import os
import sys
import json
import shutil
import threading
import webbrowser
import subprocess
import platform
import io
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import urllib.parse
from PIL import Image
from core import DedupScanner

def open_folder_in_os(path: Path):
    system = platform.system()
    path_str = str(path.resolve())
    try:
        if system == 'Windows':
            os.startfile(path_str)
        elif system == 'Darwin':
            subprocess.Popen(['open', path_str])
        else:
            subprocess.Popen(['xdg-open', path_str])
    except Exception as e:
        print(f"[Backend] Failed to open file manager: {e}")

def open_native_folder_dialog() -> str:
    system = platform.system()
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(title="Select Photo Folder")
        root.destroy()
        if selected:
            return str(Path(selected).resolve())
    except Exception:
        pass

    if system == 'Darwin':
        try:
            cmd = "osascript -e 'POSIX path of (choose folder with prompt \"Select Photo Folder\")'"
            out = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            if out and Path(out).is_dir():
                return str(Path(out).resolve())
        except Exception:
            pass
    elif system == 'Linux':
        try:
            cmd = "zenity --file-selection --directory --title='Select Photo Folder'"
            out = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            if out and Path(out).is_dir():
                return str(Path(out).resolve())
        except Exception:
            pass

    return ""

class DedupRequestHandler(SimpleHTTPRequestHandler):
    scanner = None
    active_mode = 'crop'
    server_instance = None
    
    def log_message(self, format, *args):
        pass

    def _send_html_file(self, filename: str):
        html_path = Path(__file__).parent / 'templates' / filename
        if html_path.exists():
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html_path.read_bytes())
        else:
            self.send_error(404, f"Template {filename} not found.")

    def _send_json(self, payload, status: int = 200):
        self.send_response(status)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path in ['/', '/setup', '/setup.html']:
            self._send_html_file('setup.html')
            return

        elif path in ['/dashboard', '/dashboard.html']:
            if not self.scanner:
                self.send_response(302)
                self.send_header('Location', '/')
                self.end_headers()
                return
            self._send_html_file('dashboard.html')
            return

        elif path == '/api/state':
            if not self.scanner:
                self._send_json({'has_folder': False})
                return
            self._send_json({
                'has_folder': True,
                'folder': str(self.scanner.folder),
                'mode': self.active_mode,
                'total_photos': len(self.scanner.files),
                'groups': self.scanner.cluster(mode=self.active_mode)
            })
            return

        elif path.startswith('/thumbnail/'):
            if not self.scanner:
                self.send_error(404)
                return
            filename = urllib.parse.unquote(path[len('/thumbnail/'):])
            file_path = self.scanner.folder / filename
            if file_path.exists():
                try:
                    with Image.open(file_path) as img:
                        img.thumbnail((320, 320))
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=80)
                        data = buf.getvalue()
                        self.send_response(200)
                        self.send_header('Content-type', 'image/jpeg')
                        self.send_header('Cache-Control', 'no-cache')
                        self.end_headers()
                        self.wfile.write(data)
                        return
                except Exception:
                    pass
            self.send_error(404)
            return

        elif path.startswith('/full/'):
            if not self.scanner:
                self.send_error(404)
                return
            filename = urllib.parse.unquote(path[len('/full/'):])
            file_path = self.scanner.folder / filename
            if file_path.exists():
                self.send_response(200)
                self.send_header('Content-type', 'image/jpeg')
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
                return
            self.send_error(404)
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else '{}'
        data = json.loads(body)

        if parsed.path == '/api/browse':
            selected = open_native_folder_dialog()
            self._send_json({'success': bool(selected), 'folder': selected})
            return

        elif parsed.path == '/api/load_folder':
            raw_path = data.get('folder', '').strip('"\' ')
            mode = data.get('mode', 'crop')
            if not raw_path:
                self._send_json({'success': False, 'error': 'Folder path is empty.'}, 400)
                return

            folder_path = Path(raw_path).resolve()
            if not folder_path.exists() or not folder_path.is_dir():
                self._send_json({'success': False, 'error': f'Invalid directory: {folder_path}'}, 400)
                return

            scanner = DedupScanner(folder_path)
            scanner.scan()
            DedupRequestHandler.scanner = scanner
            DedupRequestHandler.active_mode = mode

            self._send_json({'success': True})
            return

        elif parsed.path == '/api/recluster':
            if not self.scanner:
                self._send_json([])
                return
            mode = data.get('mode', 'crop')
            DedupRequestHandler.active_mode = mode
            new_groups = self.scanner.cluster(mode)
            self._send_json(new_groups)
            return

        elif parsed.path == '/api/move':
            if not self.scanner:
                self._send_json({'success': False, 'error': 'No active folder loaded.'}, 400)
                return

            to_move = data.get('to_move', [])
            mode = data.get('mode', self.active_mode)
            
            target_dir = self.scanner.folder / "Duplicates"
            target_dir.mkdir(parents=True, exist_ok=True)
            
            moved = []
            failed = []
            
            for fname in to_move:
                src = self.scanner.folder / fname
                dst = target_dir / fname
                try:
                    if src.exists():
                        shutil.move(str(src), str(dst))
                        moved.append(fname)
                    else:
                        failed.append((fname, "File not found"))
                except Exception as e:
                    failed.append((fname, str(e)))
                    
            manifest_file = self.scanner.folder / "duplicates_manifest.json"
            manifest = {
                'moved_count': len(moved),
                'failed_count': len(failed),
                'target_directory': str(target_dir),
                'files': moved,
                'failures': failed
            }
            manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')

            self.scanner.scan()
            updated_groups = self.scanner.cluster(mode=mode)
            
            self._send_json({
                'success': True,
                'moved_count': len(moved),
                'failed_count': len(failed),
                'target': str(target_dir),
                'total_photos': len(self.scanner.files),
                'groups': updated_groups
            })
            return

        elif parsed.path == '/api/open_folder':
            if self.scanner:
                folder_to_open = self.scanner.folder / "Duplicates"
                if not folder_to_open.exists():
                    folder_to_open = self.scanner.folder
                open_folder_in_os(folder_to_open)
            self.send_response(200)
            self.end_headers()
            return

        elif parsed.path == '/api/shutdown':
            self.send_response(200)
            self.end_headers()
            threading.Thread(target=self.server_instance.shutdown).start()
            return

        self.send_error(404)

def main():
    port = 8765
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(('127.0.0.1', p), DedupRequestHandler)
            port = p
            break
        except OSError:
            continue
            
    if not server:
        sys.exit(1)
        
    DedupRequestHandler.server_instance = server
    url = f"http://127.0.0.1:{port}"
    
    print(f"Opening setup screen in browser: {url}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()