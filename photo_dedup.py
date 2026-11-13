#!/usr/bin/env python3
"""
Interactive Photo Deduplication Pipeline (Cross-Platform & Bilingual)
Author: Antigravity

Features:
- Cross-platform support: Windows, macOS, Linux
- Multi-tier detection: Exact MD5, Perceptual Hashing (pHash, dHash, wHash), Color distribution & Burst shots
- Dynamic Sensitivity Slider: Real-time re-clustering without re-scanning
- Bilingual Web Interface: Full Hebrew (RTL) & English (LTR) with one-click instant toggle
- Interactive UI: Visual side-by-side comparison, customizable Keep/Move per photo, group actions
- Safe Execution: Moves duplicates to a dedicated <folder>/Duplicates subfolder with JSON manifest
"""

import os
import sys
import json
import re
import shutil
import hashlib
import threading
import webbrowser
import subprocess
import platform
import io
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import urllib.parse
from PIL import Image
import imagehash
from collections import defaultdict
import numpy as np

# Ensure UTF-8 stdout if supported
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def open_folder_in_os(path: Path):
    """Open folder in the native OS file manager (Windows, macOS, Linux)"""
    system = platform.system()
    path_str = str(path.resolve())
    try:
        if system == 'Windows':
            os.startfile(path_str)
        elif system == 'Darwin':  # macOS
            subprocess.Popen(['open', path_str])
        else:  # Linux / Unix
            subprocess.Popen(['xdg-open', path_str])
    except Exception as e:
        print(f"Failed to open file manager: {e}")

def pick_folder() -> Path:
    """Pick folder via GUI dialog across Windows, macOS, Linux or CLI argument"""
    if len(sys.argv) > 1 and sys.argv[1].strip():
        p = Path(sys.argv[1].strip()).resolve()
        if p.is_dir():
            return p
            
    system = platform.system()
    
    # 1. Try Tkinter (native cross-platform GUI)
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(title="Select Photo Folder / בחר תיקיית תמונות")
        root.destroy()
        if selected:
            return Path(selected).resolve()
    except Exception:
        pass

    # 2. Platform-specific fallbacks if Tkinter is missing
    if system == 'Darwin':
        try:
            cmd = "osascript -e 'POSIX path of (choose folder with prompt \"Select Photo Folder\")'"
            out = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            if out and Path(out).is_dir():
                return Path(out).resolve()
        except Exception:
            pass
    elif system == 'Linux':
        try:
            cmd = "zenity --file-selection --directory --title='Select Photo Folder'"
            out = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            if out and Path(out).is_dir():
                return Path(out).resolve()
        except Exception:
            pass

    # 3. CLI input fallback
    try:
        entered = input("\nEnter images folder path / הכנס נתיב לתיקיית התמונות: ").strip('"\' ')
        if entered and Path(entered).is_dir():
            return Path(entered).resolve()
    except Exception:
        pass

    return None

class DedupScanner:
    def __init__(self, folder: Path):
        self.folder = folder.resolve()
        self.files = sorted([
            f for f in self.folder.iterdir() 
            if f.is_file() and f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']
        ])
        self.images_data = []
        self.images_info = {}
        self.pairwise_matrix = []

    def scan(self):
        print(f"\n🔍 Scanning {len(self.files)} images in / סורק {len(self.files)} תמונות בתיקייה:\n   {self.folder}...")
        
        md5_map = defaultdict(list)
        
        for idx, f in enumerate(self.files):
            try:
                content = f.read_bytes()
                md5 = hashlib.md5(content).hexdigest()
                md5_map[md5].append(f.name)
                
                with Image.open(f) as img:
                    w, h = img.size
                    rgb = img.convert('RGB')
                    ph = imagehash.phash(rgb)
                    dh = imagehash.dhash(rgb)
                    wh = imagehash.whash(rgb)
                    # Color distribution vector (16x16 thumbnail)
                    color_vec = np.array(rgb.resize((16, 16))).mean(axis=(0, 1))
                    
                item = {
                    'name': f.name,
                    'path': f,
                    'size': len(content),
                    'dim': (w, h),
                    'md5': md5,
                    'phash': ph,
                    'dhash': dh,
                    'whash': wh,
                    'color_vec': color_vec
                }
                self.images_info[f.name] = item
                self.images_data.append(item)
            except Exception as e:
                print(f"Error reading {f.name}: {e}")
                
            if (idx + 1) % 50 == 0 or idx == len(self.files) - 1:
                print(f"  Processed {idx + 1}/{len(self.files)} images...")

        # Precompute candidate distances
        n = len(self.images_data)
        print(f"  Computing visual similarities across {n} images...")
        self.pairwise_matrix = []
        for i in range(n):
            d1 = self.images_data[i]
            for j in range(i + 1, n):
                d2 = self.images_data[j]
                
                is_md5 = (d1['md5'] == d2['md5'])
                dp = int(d1['phash'] - d2['phash'])
                dd = int(d1['dhash'] - d2['dhash'])
                dw = int(d1['whash'] - d2['whash'])
                color_diff = float(np.abs(d1['color_vec'] - d2['color_vec']).max())
                
                # Keep candidates within maximum conceivable threshold
                if is_md5 or (dp <= 20 and color_diff <= 45.0):
                    self.pairwise_matrix.append((
                        d1['name'], d2['name'], is_md5, dp, dd, dw, color_diff
                    ))
                    
        print(f"✅ Scanning completed. {len(self.pairwise_matrix)} candidate pairs indexed.")

    def cluster(self, sensitivity: int = 3):
        """
        Cluster images based on sensitivity level:
        1: Strict (Exact & near-identical only, dp <= 2)
        2: High (Very close duplicates & minor edits, dp <= 4)
        3: Balanced (Recommended: standard duplicates & close bursts, dp <= 8)
        4: Wide (Loose burst shots & similar composition, dp <= 12)
        5: Maximum (Broad visual similarity, dp <= 16)
        """
        thresholds = {
            1: {'dp': 2,  'dd': 4,  'color': 15.0},
            2: {'dp': 4,  'dd': 6,  'color': 20.0},
            3: {'dp': 8,  'dd': 10, 'color': 25.0},
            4: {'dp': 12, 'dd': 14, 'color': 32.0},
            5: {'dp': 16, 'dd': 18, 'color': 40.0}
        }
        cfg = thresholds.get(sensitivity, thresholds[3])
        
        parent = {d['name']: d['name'] for d in self.images_data}
        
        def find(i):
            if parent[i] == i:
                return i
            parent[i] = find(parent[i])
            return parent[i]
            
        def union(i, j):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        pair_match_types = {}
        
        for n1, n2, is_md5, dp, dd, dw, color_diff in self.pairwise_matrix:
            if is_md5:
                union(n1, n2)
                pair_match_types[(min(n1, n2), max(n1, n2))] = 'exact'
                continue
                
            if color_diff > cfg['color']:
                continue
                
            if dp == 0 and dd == 0:
                union(n1, n2)
                pair_match_types[(min(n1, n2), max(n1, n2))] = 'visual_identical'
            elif dp <= cfg['dp'] and dd <= cfg['dd']:
                union(n1, n2)
                mtype = 'near_duplicate' if dp <= 4 else 'burst'
                pair_match_types[(min(n1, n2), max(n1, n2))] = mtype

        clusters = defaultdict(list)
        for d in self.images_data:
            clusters[find(d['name'])].append(d['name'])
            
        raw_groups = [members for root, members in clusters.items() if len(members) > 1]
        groups = []
        
        for g_idx, grp in enumerate(raw_groups, 1):
            # Sort by resolution and file size (largest = default keeper)
            grp_sorted = sorted(grp, key=lambda x: (
                self.images_info[x]['dim'][0] * self.images_info[x]['dim'][1], 
                self.images_info[x]['size']
            ), reverse=True)
            
            has_burst = False
            has_exact = False
            has_visual = False
            
            for i in range(len(grp_sorted)):
                for j in range(i + 1, len(grp_sorted)):
                    k = (min(grp_sorted[i], grp_sorted[j]), max(grp_sorted[i], grp_sorted[j]))
                    t = pair_match_types.get(k, '')
                    if t == 'exact': has_exact = True
                    elif t == 'burst': has_burst = True
                    elif t in ['visual_identical', 'near_duplicate']: has_visual = True

            if has_burst:
                cat_he = "צילומי רצף / סצנה דומה"
                cat_en = "Burst Shots / Similar Scene"
                badge = "badge-burst"
            elif has_visual:
                cat_he = "כפילות ויזואלית (גודל/דחיסה שונה)"
                cat_en = "Visual Duplicate (Different Size/Compression)"
                badge = "badge-visual"
            else:
                cat_he = "כפילות בינארית זהה (100%)"
                cat_en = "Exact Binary Duplicate (100%)"
                badge = "badge-exact"
                
            groups.append({
                'id': g_idx,
                'category_he': cat_he,
                'category_en': cat_en,
                'badge': badge,
                'keeper': grp_sorted[0],
                'members': [
                    {
                        'name': m,
                        'w': self.images_info[m]['dim'][0],
                        'h': self.images_info[m]['dim'][1],
                        'size_kb': round(self.images_info[m]['size'] / 1024, 1),
                        'is_default_keeper': (m == grp_sorted[0])
                    } for m in grp_sorted
                ]
            })

        return groups

class DedupRequestHandler(SimpleHTTPRequestHandler):
    scanner = None
    server_instance = None
    
    def log_message(self, format, *args):
        pass # Silence verbose console request logs

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path in ['/', '/index.html']:
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            html = self.get_ui_html()
            self.wfile.write(html.encode('utf-8'))
            return
            
        elif path == '/api/recluster':
            query = urllib.parse.parse_qs(parsed.query)
            sensitivity = int(query.get('sensitivity', ['3'])[0])
            sensitivity = max(1, min(5, sensitivity))
            new_groups = self.scanner.cluster(sensitivity)
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(new_groups, ensure_ascii=False).encode('utf-8'))
            return

        elif path.startswith('/thumbnail/'):
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
                        self.send_header('Cache-Control', 'max-age=86400')
                        self.end_headers()
                        self.wfile.write(data)
                        return
                except Exception:
                    pass
            self.send_error(404)
            return

        elif path.startswith('/full/'):
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
        if parsed.path == '/api/move':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            data = json.loads(body)
            to_move = data.get('to_move', [])
            
            target_dir = self.scanner.folder / "Duplicates"
            target_dir.mkdir(parents=True, exist_ok=True)
            
            moved = []
            for fname in to_move:
                src = self.scanner.folder / fname
                dst = target_dir / fname
                if src.exists():
                    shutil.move(str(src), str(dst))
                    moved.append(fname)
                    
            manifest_file = self.scanner.folder / "duplicates_manifest.json"
            manifest = {
                'moved_count': len(moved),
                'target_directory': str(target_dir),
                'files': moved
            }
            manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            res = json.dumps({'success': True, 'moved_count': len(moved), 'target': str(target_dir)})
            self.wfile.write(res.encode('utf-8'))
            return

        elif parsed.path == '/api/open_folder':
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

    def get_ui_html(self):
        folder_str = str(self.scanner.folder)
        initial_groups = self.scanner.cluster(sensitivity=3)
        groups_json = json.dumps(initial_groups, ensure_ascii=False)
        total_photos = len(self.scanner.files)
        
        return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Photo Deduplication Manager</title>
    <style>
        :root {{
            --bg: #090d16;
            --surface: #131b2e;
            --surface-card: #1a243d;
            --surface-hover: #223052;
            --border: #2a3b61;
            --primary: #38bdf8;
            --primary-hover: #0ea5e9;
            --accent-green: #22c55e;
            --accent-orange: #f97316;
            --accent-red: #ef4444;
            --accent-yellow: #eab308;
            --text: #f8fafc;
            --text-muted: #94a3b8;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            padding: 30px 20px 140px 20px;
            transition: direction 0.2s ease;
        }}
        .container {{
            max-width: 1450px;
            margin: 0 auto;
        }}
        header {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 24px 30px;
            margin-bottom: 24px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.4);
            position: relative;
        }}
        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            flex-wrap: wrap;
            gap: 12px;
        }}
        h1 {{
            font-size: 26px;
            color: var(--primary);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .lang-switch {{
            display: flex;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            overflow: hidden;
        }}
        .lang-btn {{
            background: transparent;
            color: var(--text-muted);
            border: none;
            padding: 6px 14px;
            font-size: 13px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.15s;
        }}
        .lang-btn.active {{
            background: var(--primary);
            color: #000;
        }}
        .path-label {{
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 16px;
            word-break: break-all;
        }}
        .path-label code {{
            background: #060911;
            padding: 4px 8px;
            border-radius: 6px;
            color: #e2e8f0;
            border: 1px solid var(--border);
        }}
        .controls-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            background: rgba(0,0,0,0.2);
            padding: 16px 20px;
            border-radius: 12px;
            border: 1px solid var(--border);
        }}
        .stats-row {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .stat-pill {{
            background: var(--bg);
            border: 1px solid var(--border);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .stat-pill strong {{
            color: var(--primary);
            font-size: 15px;
        }}
        .slider-wrap {{
            display: flex;
            align-items: center;
            gap: 14px;
            background: var(--bg);
            padding: 8px 16px;
            border-radius: 10px;
            border: 1px solid var(--border);
        }}
        .slider-label {{
            font-size: 13px;
            font-weight: 600;
            white-space: nowrap;
        }}
        .slider-input {{
            width: 140px;
            cursor: pointer;
            accent-color: var(--primary);
        }}
        .slider-val {{
            font-size: 12px;
            color: var(--primary);
            font-weight: 700;
            min-width: 110px;
        }}
        .group-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 16px;
            margin-bottom: 24px;
            overflow: hidden;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }}
        .group-header {{
            background: #17223b;
            padding: 14px 22px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            flex-wrap: wrap;
            gap: 12px;
        }}
        .group-title-wrap {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .group-title {{
            font-size: 16px;
            font-weight: 700;
        }}
        .badge {{
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge-exact {{ background: rgba(239,68,68,0.2); color: #f87171; border: 1px solid #ef4444; }}
        .badge-visual {{ background: rgba(234,179,8,0.2); color: #facc15; border: 1px solid #eab308; }}
        .badge-burst {{ background: rgba(56,189,248,0.2); color: #38bdf8; border: 1px solid #38bdf8; }}
        .group-actions {{
            display: flex;
            gap: 8px;
        }}
        .btn-sm {{
            background: var(--surface-hover);
            color: var(--text);
            border: 1px solid var(--border);
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .btn-sm:hover {{
            border-color: var(--primary);
            background: #2b3b61;
        }}
        .photos-grid {{
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            padding: 20px;
        }}
        .photo-card {{
            background: var(--surface-card);
            border: 2px solid var(--border);
            border-radius: 12px;
            width: 230px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: all 0.2s;
            cursor: pointer;
            user-select: none;
            position: relative;
        }}
        .photo-card:hover {{
            transform: translateY(-3px);
        }}
        .photo-card.is-keeper {{
            border-color: var(--accent-green);
            background: #0f241a;
        }}
        .photo-card.is-move {{
            border-color: var(--accent-orange);
            background: #261711;
        }}
        .img-box {{
            width: 230px;
            height: 220px;
            background: #060a14;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            position: relative;
        }}
        .img-box img {{
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }}
        .zoom-btn {{
            position: absolute;
            top: 8px;
            left: 8px;
            background: rgba(0,0,0,0.65);
            color: #fff;
            border: none;
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 11px;
            cursor: pointer;
            opacity: 0;
            transition: opacity 0.2s;
            text-decoration: none;
        }}
        [dir="ltr"] .zoom-btn {{
            left: auto;
            right: 8px;
        }}
        .img-box:hover .zoom-btn {{
            opacity: 1;
        }}
        .card-details {{
            padding: 12px;
            font-size: 12px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .action-tag {{
            font-weight: 700;
            font-size: 11px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .is-keeper .action-tag {{ color: var(--accent-green); }}
        .is-move .action-tag {{ color: var(--accent-orange); }}
        .file-title {{
            color: #f1f5f9;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            font-size: 13px;
        }}
        .file-meta {{
            display: flex;
            justify-content: space-between;
            color: var(--text-muted);
            font-size: 11px;
        }}
        .toggle-btn {{
            margin-top: 6px;
            padding: 6px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            border: 1px solid var(--border);
            cursor: pointer;
            text-align: center;
            transition: all 0.15s;
        }}
        .is-keeper .toggle-btn {{
            background: rgba(34,197,94,0.15);
            color: var(--accent-green);
            border-color: var(--accent-green);
        }}
        .is-move .toggle-btn {{
            background: rgba(249,115,22,0.15);
            color: var(--accent-orange);
            border-color: var(--accent-orange);
        }}
        .floating-bar {{
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: #111827;
            border: 1px solid var(--border);
            padding: 14px 28px;
            border-radius: 20px;
            display: flex;
            align-items: center;
            gap: 24px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.7);
            z-index: 1000;
            backdrop-filter: blur(10px);
        }}
        .selection-count {{
            font-size: 15px;
            color: var(--text);
        }}
        .selection-count strong {{
            color: var(--accent-orange);
            font-size: 18px;
        }}
        .btn-confirm {{
            background: var(--accent-green);
            color: #000;
            font-weight: 700;
            border: none;
            padding: 12px 24px;
            border-radius: 12px;
            font-size: 15px;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .btn-confirm:hover {{
            background: #16a34a;
            transform: scale(1.03);
        }}
        .btn-confirm:disabled {{
            background: #475569;
            color: #94a3b8;
            cursor: not-allowed;
            transform: none;
        }}
        .modal-overlay {{
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.8);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 2000;
        }}
        .modal {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 20px;
            max-width: 500px;
            width: 90%;
            padding: 30px;
            text-align: center;
            box-shadow: 0 20px 50px rgba(0,0,0,0.8);
        }}
        .modal h2 {{
            color: var(--accent-green);
            margin-bottom: 12px;
            font-size: 22px;
        }}
        .modal p {{
            color: var(--text-muted);
            margin-bottom: 24px;
            line-height: 1.5;
        }}
        .modal-actions {{
            display: flex;
            gap: 12px;
            justify-content: center;
        }}
        .btn-primary {{
            background: var(--primary);
            color: #000;
            font-weight: 700;
            border: none;
            padding: 10px 20px;
            border-radius: 10px;
            cursor: pointer;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-top">
                <h1 id="t-title">⚡ ניהול כפילויות תמונות חכם</h1>
                <div class="lang-switch">
                    <button class="lang-btn active" id="btn-lang-he" onclick="setLanguage('he')">עברית</button>
                    <button class="lang-btn" id="btn-lang-en" onclick="setLanguage('en')">English</button>
                </div>
            </div>
            <div class="path-label">
                <span id="t-active-folder">תיקייה פעילה:</span> <code>{folder_str}</code>
            </div>
            
            <div class="controls-row">
                <div class="stats-row">
                    <div class="stat-pill"><span id="t-total-photos">סה"כ תמונות:</span> <strong>{total_photos}</strong></div>
                    <div class="stat-pill"><span id="t-total-groups">קבוצות כפילות:</span> <strong id="lbl-total-groups">0</strong></div>
                </div>

                <div class="slider-wrap">
                    <span class="slider-label" id="t-sensitivity">רגישות זיהוי:</span>
                    <input type="range" class="slider-input" id="sensitivity-slider" min="1" max="5" value="3" oninput="onSensitivityChange(this.value)">
                    <span class="slider-val" id="sensitivity-desc">3 (מאוזן - מומלץ)</span>
                </div>
            </div>
        </header>

        <div id="groups-container"></div>
    </div>

    <div class="floating-bar">
        <div class="selection-count">
            <span id="t-marked-count">נבחרו להעברה:</span> <strong id="move-count">0</strong> <span id="t-photos-word">תמונות</span>
        </div>
        <button class="btn-confirm" id="btn-submit" onclick="executeMove()">
            <span id="t-confirm-btn">🚀 אשר והעבר תמונות לתיקיית Duplicates</span>
        </button>
    </div>

    <!-- Success Modal -->
    <div class="modal-overlay" id="success-modal">
        <div class="modal">
            <h2 id="t-modal-title">🎉 ההעברה הושלמה בהצלחה!</h2>
            <p id="success-message">התמונות הנבחרות הועברו בבטחה לתיקיית Duplicates.</p>
            <div class="modal-actions">
                <button class="btn-primary" onclick="openDuplicatesFolder()" id="t-btn-open-folder">📂 פתח תיקייה בסייר הקבצים</button>
                <button class="btn-sm" onclick="closeAndExit()" id="t-btn-exit">סיום ויציאה</button>
            </div>
        </div>
    </div>

    <script>
        const i18n = {{
            he: {{
                title: "⚡ ניהול כפילויות תמונות חכם",
                activeFolder: "תיקייה פעילה:",
                totalPhotos: "סה\"כ תמונות:",
                totalGroups: "קבוצות כפילות:",
                sensitivity: "רגישות זיהוי:",
                sensLevels: {{
                    1: "1 (מחמיר ביותר - זהות כמעט מוחלטת)",
                    2: "2 (גבוהה - כפילויות קרובות)",
                    3: "3 (מאוזן - מומלץ)",
                    4: "4 (רחב - כולל צילומי רצף)",
                    5: "5 (מקסימלי - דמיון כללי)"
                }},
                markedCount: "נבחרו להעברה:",
                photosWord: "תמונות",
                confirmBtn: "🚀 אשר והעבר תמונות לתיקיית Duplicates",
                keepAll: "🛡️ שמור את כולן",
                resetDefault: "⭐ שחזר ברירת מחדל",
                statusKeeper: "⭐ נשמר במקום",
                statusMove: "📦 יועבר ל-Duplicates",
                btnMove: "העבר ל-Duplicates ⬅️",
                btnKeep: "שמור תמונה זו ⬅️",
                viewFull: "🔍 פתח מקור",
                modalTitle: "🎉 ההעברה הושלמה בהצלחה!",
                modalSuccessPrefix: "הועברו בהצלחה ",
                modalSuccessSuffix: " תמונות לתיקייה: ",
                btnOpenFolder: "📂 פתח תיקייה בסייר הקבצים",
                btnExit: "סיום ויציאה",
                confirmPrompt: "האם להעביר את התמונות שנבחרו לתיקיית Duplicates?",
                movingMsg: "⏳ מעביר תמונות...",
                groupText: "קבוצה",
                photosCountText: "תמונות",
                noDuplicatesFound: "✨ לא נמצאו כפילויות ברמת רגישות זו!"
            }},
            en: {{
                title: "⚡ Smart Photo Deduplication Manager",
                activeFolder: "Active Folder:",
                totalPhotos: "Total Photos:",
                totalGroups: "Duplicate Groups:",
                sensitivity: "Detection Sensitivity:",
                sensLevels: {{
                    1: "1 (Strict - Near-Exact Only)",
                    2: "2 (High - Close Duplicates)",
                    3: "3 (Balanced - Recommended)",
                    4: "4 (Wide - Including Bursts)",
                    5: "5 (Maximum - General Similarity)"
                }},
                markedCount: "Selected for move:",
                photosWord: "photos",
                confirmBtn: "🚀 Confirm & Move Photos to Duplicates",
                keepAll: "🛡️ Keep All",
                resetDefault: "⭐ Reset Default",
                statusKeeper: "⭐ Keep in Place",
                statusMove: "📦 Move to Duplicates",
                btnMove: "Move to Duplicates ➡️",
                btnKeep: "Keep this Photo ➡️",
                viewFull: "🔍 View Full",
                modalTitle: "🎉 Move Completed Successfully!",
                modalSuccessPrefix: "Successfully moved ",
                modalSuccessSuffix: " photos to: ",
                btnOpenFolder: "📂 Open Folder in File Manager",
                btnExit: "Done & Exit",
                confirmPrompt: "Are you sure you want to move the selected photos to the Duplicates folder?",
                movingMsg: "⏳ Moving photos...",
                groupText: "Group",
                photosCountText: "photos",
                noDuplicatesFound: "✨ No duplicates found at this sensitivity level!"
            }}
        }};

        let currentLang = localStorage.getItem('dedup_lang') || 'he';
        let groups = {groups_json};
        const fileStates = {{}};

        function setLanguage(lang) {{
            currentLang = lang;
            localStorage.setItem('dedup_lang', lang);
            document.documentElement.setAttribute('lang', lang);
            document.documentElement.setAttribute('dir', lang === 'he' ? 'rtl' : 'ltr');
            document.body.setAttribute('dir', lang === 'he' ? 'rtl' : 'ltr');
            
            document.getElementById('btn-lang-he').classList.toggle('active', lang === 'he');
            document.getElementById('btn-lang-en').classList.toggle('active', lang === 'en');
            
            const t = i18n[lang];
            document.getElementById('t-title').innerText = t.title;
            document.getElementById('t-active-folder').innerText = t.activeFolder;
            document.getElementById('t-total-photos').innerText = t.totalPhotos;
            document.getElementById('t-total-groups').innerText = t.totalGroups;
            document.getElementById('t-sensitivity').innerText = t.sensitivity;
            document.getElementById('t-marked-count').innerText = t.markedCount;
            document.getElementById('t-photos-word').innerText = t.photosWord;
            document.getElementById('t-confirm-btn').innerText = t.confirmBtn;
            document.getElementById('t-modal-title').innerText = t.modalTitle;
            document.getElementById('t-btn-open-folder').innerText = t.btnOpenFolder;
            document.getElementById('t-btn-exit').innerText = t.btnExit;

            const sliderVal = document.getElementById('sensitivity-slider').value;
            document.getElementById('sensitivity-desc').innerText = t.sensLevels[sliderVal];

            render();
        }}

        function initStates() {{
            groups.forEach(g => {{
                g.members.forEach(m => {{
                    if (fileStates[m.name] === undefined) {{
                        fileStates[m.name] = m.is_default_keeper ? 'keep' : 'move';
                    }}
                }});
            }});
        }}

        initStates();

        function render() {{
            const t = i18n[currentLang];
            const container = document.getElementById('groups-container');
            container.innerHTML = '';

            document.getElementById('lbl-total-groups').innerText = groups.length;

            if (groups.length === 0) {{
                container.innerHTML = `<div style="text-align:center; padding: 60px; color: var(--text-muted); font-size: 18px;">${{t.noDuplicatesFound}}</div>`;
                document.getElementById('move-count').innerText = '0';
                document.getElementById('btn-submit').disabled = true;
                return;
            }}

            let totalToMove = 0;

            groups.forEach(g => {{
                const groupEl = document.createElement('div');
                groupEl.className = 'group-card';

                const category = (currentLang === 'he') ? g.category_he : g.category_en;

                const header = document.createElement('div');
                header.className = 'group-header';
                header.innerHTML = `
                    <div class="group-title-wrap">
                        <span class="group-title">${{t.groupText}} #${{g.id}} (${{g.members.length}} ${{t.photosCountText}})</span>
                        <span class="badge ${{g.badge}}">${{category}}</span>
                    </div>
                    <div class="group-actions">
                        <button class="btn-sm" onclick="setGroupState(${{g.id}}, 'keep')">${{t.keepAll}}</button>
                        <button class="btn-sm" onclick="resetGroupDefault(${{g.id}})">${{t.resetDefault}}</button>
                    </div>
                `;
                groupEl.appendChild(header);

                const grid = document.createElement('div');
                grid.className = 'photos-grid';

                g.members.forEach(m => {{
                    const state = fileStates[m.name] || (m.is_default_keeper ? 'keep' : 'move');
                    if (state === 'move') totalToMove++;

                    const isKeeper = (state === 'keep');
                    const card = document.createElement('div');
                    card.className = `photo-card ${{isKeeper ? 'is-keeper' : 'is-move'}}`;
                    card.onclick = (e) => {{
                        if (e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A') {{
                            togglePhoto(m.name);
                        }}
                    }};

                    card.innerHTML = `
                        <div class="img-box">
                            <img src="/thumbnail/${{encodeURIComponent(m.name)}}" alt="${{m.name}}" loading="lazy" />
                            <a class="zoom-btn" href="/full/${{encodeURIComponent(m.name)}}" target="_blank" onclick="event.stopPropagation()">${{t.viewFull}}</a>
                        </div>
                        <div class="card-details">
                            <div class="action-tag">
                                ${{isKeeper ? t.statusKeeper : t.statusMove}}
                            </div>
                            <div class="file-title" title="${{m.name}}">${{m.name}}</div>
                            <div class="file-meta">
                                <span>📐 ${{m.w}}x${{m.h}}</span>
                                <span>💾 ${{m.size_kb}} KB</span>
                            </div>
                            <button class="toggle-btn" onclick="togglePhoto('${{m.name}}'); event.stopPropagation();">
                                ${{isKeeper ? t.btnMove : t.btnKeep}}
                            </button>
                        </div>
                    `;
                    grid.appendChild(card);
                }});

                groupEl.appendChild(grid);
                container.appendChild(groupEl);
            }});

            document.getElementById('move-count').innerText = totalToMove;
            document.getElementById('btn-submit').disabled = (totalToMove === 0);
        }}

        function togglePhoto(name) {{
            fileStates[name] = fileStates[name] === 'keep' ? 'move' : 'keep';
            render();
        }}

        function setGroupState(groupId, targetState) {{
            const g = groups.find(x => x.id === groupId);
            if (g) {{
                g.members.forEach(m => {{
                    fileStates[m.name] = targetState;
                }});
                render();
            }}
        }}

        function resetGroupDefault(groupId) {{
            const g = groups.find(x => x.id === groupId);
            if (g) {{
                g.members.forEach(m => {{
                    fileStates[m.name] = m.is_default_keeper ? 'keep' : 'move';
                }});
                render();
            }}
        }}

        let debounceTimer = null;
        function onSensitivityChange(val) {{
            const t = i18n[currentLang];
            document.getElementById('sensitivity-desc').innerText = t.sensLevels[val];
            
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(async () => {{
                try {{
                    const res = await fetch(`/api/recluster?sensitivity=${{val}}`);
                    groups = await res.json();
                    initStates();
                    render();
                }} catch (err) {{
                    console.error('Reclustering error:', err);
                }}
            }}, 150);
        }}

        async function executeMove() {{
            const t = i18n[currentLang];
            const toMove = Object.keys(fileStates).filter(name => fileStates[name] === 'move');
            if (toMove.length === 0) return;

            if (!confirm(`${{t.confirmPrompt}} (${{toMove.length}} ${{t.photosWord}})`)) {{
                return;
            }}

            const btn = document.getElementById('btn-submit');
            btn.disabled = true;
            btn.innerText = t.movingMsg;

            try {{
                const res = await fetch('/api/move', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ to_move: toMove }})
                }});
                const data = await res.json();
                if (data.success) {{
                    document.getElementById('success-message').innerText = `${{t.modalSuccessPrefix}}${{data.moved_count}}${{t.modalSuccessSuffix}}${{data.target}}`;
                    document.getElementById('success-modal').style.display = 'flex';
                }} else {{
                    alert('Error executing move');
                }}
            }} catch (err) {{
                alert('Communication error: ' + err);
            }} finally {{
                btn.disabled = false;
                btn.innerText = t.confirmBtn;
            }}
        }}

        function openDuplicatesFolder() {{
            fetch('/api/open_folder', {{ method: 'POST' }});
        }}

        function closeAndExit() {{
            fetch('/api/shutdown', {{ method: 'POST' }});
            window.close();
            document.body.innerHTML = '<div style="color:white;text-align:center;padding:100px;font-family:sans-serif;"><h1>Process finished. You may close this tab.</h1></div>';
        }}

        // Run initial translation setup
        setLanguage(currentLang);
    </script>
</body>
</html>
"""

def main():
    folder = pick_folder()
    if not folder:
        print("❌ No folder selected. Exiting. / לא נבחרה תיקייה. יציאה.")
        sys.exit(1)
        
    scanner = DedupScanner(folder)
    scanner.scan()
    initial_groups = scanner.cluster(sensitivity=3)
    
    if not initial_groups:
        print("\n✨ No duplicate photos found in this folder! / לא נמצאו כפילויות בתיקייה זו.")
        input("Press Enter to exit / לחץ Enter לסיום...")
        sys.exit(0)
        
    port = 8765
    DedupRequestHandler.scanner = scanner
    
    # Try preferred port, fallback to any free port if occupied
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(('127.0.0.1', p), DedupRequestHandler)
            port = p
            break
        except OSError:
            continue
            
    if not server:
        print("❌ Could not bind local HTTP port.")
        sys.exit(1)
        
    DedupRequestHandler.server_instance = server
    
    url = f"http://127.0.0.1:{port}"
    print(f"\n🌐 Opening interactive dashboard in browser / פותח ממשק ניהול בדפדפן:")
    print(f"   {url}")
    print("\n💡 Adjust the sensitivity slider, review photos, and click 'Confirm & Move'.")
    print("   To stop manually: Press Ctrl+C in terminal.")
    
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
        print("Pipeline exited successfully.")

if __name__ == '__main__':
    main()
