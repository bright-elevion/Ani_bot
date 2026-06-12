
import asyncio
import json
import re
import os

async def get_available_formats(url):
    """Get available video formats using yt-dlp, including resolution and file size."""
    try:
        command = [
            "yt-dlp",
            "-j",
            "--no-warnings",
            "--impersonate", "chrome",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            url
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            print(f"yt-dlp error: {stderr.decode()}")
            return []
        
        info = json.loads(stdout.decode())
        formats = []
        for f in info.get('formats', []):
            format_id = f.get('format_id')
            resolution = f.get('format_note') or f.get('resolution') or (f'{f.get("height")}p' if f.get("height") else "Unknown")
            ext = f.get('ext')
            filesize = f.get('filesize') or f.get('filesize_approx')
            
            if f.get('vcodec') != 'none':
                formats.append({
                    'format_id': format_id,
                    'resolution': resolution,
                    'ext': ext,
                    'filesize': filesize
                })
        
        unique_formats = {}
        for f in formats:
            key = f['resolution']
            if key not in unique_formats or (f['filesize'] or 0) > (unique_formats[key]['filesize'] or 0):
                unique_formats[key] = f
        
        sorted_formats = sorted(unique_formats.values(), key=lambda x: int(re.search(r'\d+', str(x['resolution'])).group()) if re.search(r'\d+', str(x['resolution'])) else 0, reverse=True)
        return sorted_formats
    except Exception as e:
        print(f"Error getting formats: {e}")
        return []

async def main():
    # Using a known working ok.ru link if possible, or just checking logic with a mock
    url = "https://ok.ru/video/6371552070240" 
    print(f"Testing URL: {url}")
    formats = await get_available_formats(url)
    if formats:
        for f in formats:
            size_mb = f['filesize'] / (1024*1024) if f['filesize'] else 0
            print(f"ID: {f['format_id']} | Res: {f['resolution']} | Ext: {f['ext']} | Size: {size_mb:.2f} MB")
    else:
        print("No formats found or error occurred.")

if __name__ == "__main__":
    asyncio.run(main())
