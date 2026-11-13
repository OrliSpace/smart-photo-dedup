#!/usr/bin/env python3
import hashlib
from pathlib import Path
from PIL import Image
import imagehash
from collections import defaultdict
import numpy as np

class DedupScanner:
    def __init__(self, folder: Path):
        self.folder = folder.resolve()
        self.files = []
        self.images_data = []
        self.images_info = {}
        self.pairwise_matrix = []

    def scan(self):
        # Reset state for fresh scan / rescan
        self.files = sorted([
            f for f in self.folder.iterdir() 
            if f.is_file() and f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']
        ])
        self.images_data = []
        self.images_info = {}
        self.pairwise_matrix = []

        print(f"\n[Scanner] Scanning {len(self.files)} images in: {self.folder}...")
        
        for idx, f in enumerate(self.files):
            try:
                content = f.read_bytes()
                md5 = hashlib.md5(content).hexdigest()
                
                with Image.open(f) as img:
                    w, h = img.size
                    rgb = img.convert('RGB')
                    ph = imagehash.phash(rgb)
                    dh = imagehash.dhash(rgb)
                    wh = imagehash.whash(rgb)
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
                print(f"[Scanner] Error reading {f.name}: {e}")
                
            if (idx + 1) % 50 == 0 or idx == len(self.files) - 1:
                print(f"[Scanner] Processed {idx + 1}/{len(self.files)} images...")

        n = len(self.images_data)
        print(f"[Scanner] Computing visual similarities across {n} images...")
        for i in range(n):
            d1 = self.images_data[i]
            for j in range(i + 1, n):
                d2 = self.images_data[j]
                
                is_md5 = (d1['md5'] == d2['md5'])
                dp = int(d1['phash'] - d2['phash'])
                dd = int(d1['dhash'] - d2['dhash'])
                dw = int(d1['whash'] - d2['whash'])
                color_diff = float(np.abs(d1['color_vec'] - d2['color_vec']).max())
                
                if is_md5 or (dp <= 16 and color_diff <= 45.0):
                    self.pairwise_matrix.append((
                        d1['name'], d2['name'], is_md5, dp, dd, dw, color_diff
                    ))
                    
        print(f"[Scanner] Completed. {len(self.pairwise_matrix)} candidate pairs indexed.\n")

    def cluster(self, mode: str = 'crop'):
        thresholds = {
            'crop': {'dp': 6,  'dd': 6,  'color': 20.0},
            'burst': {'dp': 14, 'dd': 16, 'color': 35.0}
        }
        cfg = thresholds.get(mode, thresholds['crop'])
        
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
                mtype = 'exact_or_cropped' if mode == 'crop' else 'burst'
                pair_match_types[(min(n1, n2), max(n1, n2))] = mtype

        clusters = defaultdict(list)
        for d in self.images_data:
            clusters[find(d['name'])].append(d['name'])
            
        raw_groups = [members for root, members in clusters.items() if len(members) > 1]
        groups = []
        
        for g_idx, grp in enumerate(raw_groups, 1):
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
                    elif t in ['visual_identical', 'exact_or_cropped']: has_visual = True

            if mode == 'burst' or has_burst:
                category = "Burst Shots / Similar Scene"
                badge = "badge-burst"
            elif has_visual:
                category = "Identical / Cropped / Resized Images"
                badge = "badge-visual"
            else:
                category = "Exact Binary Duplicate (100% Match)"
                badge = "badge-exact"
                
            groups.append({
                'id': g_idx,
                'category': category,
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