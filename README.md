# Visual Photo Deduplicator

A lightweight, cross-platform tool to find and organize duplicate photos, resized images, and burst shots using perceptual visual hashing.

## Features

- **Exact & Perceptual Hashing**: Detects identical files (MD5) as well as resized, compressed, or burst shots (pHash, dHash, wHash).
- **Interactive Web Interface**: Visual side-by-side gallery to review each duplicate group with preview thumbnails.
- **Dynamic Sensitivity Slider**: Adjust detection threshold on the fly without re-scanning files.
- **Bilingual Interface**: Seamless toggle between Hebrew (RTL) and English (LTR).
- **Non-Destructive**: Selected duplicates are safely moved to a Duplicates/ folder with an audit manifest.
- **Cross-Platform**: Runs on Windows, macOS, and Linux.

## Installation

`ash
pip install -r requirements.txt
`

## Usage

### Run on Windows
Double-click un_windows.bat or run:
`ash
python photo_dedup.py C:\path\to\your\photos
`

### Run on macOS / Linux
`ash
chmod +x run_unix.sh
./run_unix.sh /path/to/your/photos
`

If no path is specified, a native folder selection dialog will open automatically.

## License
MIT License
