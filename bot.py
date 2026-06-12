
import os
import asyncio
import subprocess
import sys
import re
import requests
from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import json

# --- CONFIGURATION ---
def get_env(name, default=None):
    val = os.getenv(name, default)
    if val:
        return val.strip().replace('"', '').replace("'", '')
    return default

API_ID_STR = get_env("API_ID", "36762662")
API_HASH = get_env("API_HASH", "1d0ffd66d332c0638d3da242a63ad19a")
BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", "8829867859:AAEgdGVxAdeRODgQluTjDtrH_KOVwVWi1e8")

if not BOT_TOKEN:
    print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN is missing!")
    sys.exit(1)

API_ID = int(API_ID_STR)

app = Client(
    "anime_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

async def extract_video_urls(page_url):
    """Extract embedded video URLs from a myanime.live page, prioritizing specific domains."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Referer": "https://myanime.live/"
    }
    try:
        response = requests.get(page_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        video_urls = []
        
        # Look for iframes (Dailymotion, OK.ru, etc.)
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                # Prioritize OK.ru and Dailymotion as per user's logic
                if 'ok.ru' in src or 'dailymotion.com' in src:
                    video_urls.insert(0, src)
                else:
                    video_urls.append(src)
        
        # Look for direct video tags
        for video in soup.find_all('video'):
            src = video.get('src')
            if src:
                video_urls.append(src)
                
        return video_urls
    except Exception as e:
        print(f"Extraction error: {e}")
        return []

async def get_available_formats(url):
    """Get available video formats using yt-dlp, including resolution and file size."""
    try:
        # Using -j to get JSON output of all metadata including formats
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
            # For OK.ru and similar, we want formats that are combined or at least have video
            # yt-dlp usually provides combined formats for OK.ru
            format_id = f.get('format_id')
            # Resolution can be in 'format_note', 'resolution', or 'height'
            resolution = f.get('format_note') or f.get('resolution') or f'{f.get("height")}p' if f.get("height") else "Unknown"
            ext = f.get('ext')
            filesize = f.get('filesize') or f.get('filesize_approx')
            
            # We want to show formats that are likely to be what the user wants
            # Filter out manifest-only or audio-only if possible, but for OK.ru they are usually combined
            if f.get('vcodec') != 'none':
                formats.append({
                    'format_id': format_id,
                    'resolution': resolution,
                    'ext': ext,
                    'filesize': filesize
                })
        
        # Deduplicate and sort
        unique_formats = {}
        for f in formats:
            # Key by resolution to avoid duplicates of same quality
            key = f['resolution']
            if key not in unique_formats or (f['filesize'] or 0) > (unique_formats[key]['filesize'] or 0):
                unique_formats[key] = f
        
        sorted_formats = sorted(unique_formats.values(), key=lambda x: int(re.search(r'\d+', str(x['resolution'])).group()) if re.search(r'\d+', str(x['resolution'])) else 0, reverse=True)
        return sorted_formats
    except Exception as e:
        print(f"Error getting formats: {e}")
        return []

async def download_and_send(client, chat_id, url, format_id=None, status_msg=None):
    if not status_msg:
        status_msg = await client.send_message(chat_id, f"🔍 **Analyzing:** {url}")
    else:
        await status_msg.edit_text(f"🔍 **Analyzing:** {url}")
    
    file_prefix = f"video_{chat_id}_{os.urandom(4).hex()}"
    output_template = f"{file_prefix}_%(title)s.%(ext)s"
    
    common_args = [
        "yt-dlp",
        "-o", output_template,
        "--ignore-errors",
        "--no-warnings",
        "--impersonate", "chrome",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "--socket-timeout", "60",
    ]
    
    if format_id:
        common_args.extend(["-f", format_id])
    else:
        common_args.extend(["-f", "bestvideo+bestaudio/best"])

    try:
        await status_msg.edit_text("⏳ **Downloading...**")
        download_command = common_args + [url]
        process = await asyncio.create_subprocess_exec(
            *download_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        possible_files = [f for f in os.listdir('.') if f.startswith(file_prefix)]
        if not possible_files:
            raise Exception(f"Download failed. yt-dlp output: {stderr.decode()[:100]}")
        
        filename_to_upload = possible_files[0]

        # 3. Upload
        await status_msg.edit_text("✅ **Download complete!**\n📤 Uploading to Telegram...")
        
        async def progress(current, total):
            try:
                percentage = current * 100 / total
                # Update every 20% to avoid flood limits
                if int(percentage) % 20 == 0:
                    await status_msg.edit_text(f"📤 **Uploading:** {percentage:.1f}%")
            except: pass

        await client.send_video(
            chat_id=chat_id,
            video=filename_to_upload,
            caption=f"🎬 **{os.path.basename(filename_to_upload)}**",
            progress=progress
        )
        
        await status_msg.delete()
        if os.path.exists(filename_to_upload):
            os.remove(filename_to_upload)

    except Exception as e:
        print(f"Error: {e}")
        await status_msg.edit_text(f"❌ **Error:** {str(e)}")
        # Clean up
        for f in os.listdir('.'):
            if f.startswith(file_prefix):
                try: os.remove(f)
                except: pass

@app.on_message(filters.command(["start", "help"]))
async def send_welcome(client: Client, message: Message):
    await message.reply_text(
        "✨ **Anime Downloader Bot** ✨\n\n"
        "Send a link from myanime.live or a direct video link (OK.ru, etc).\n\n"
        "**Features:**\n"
        "- Quality selection with file sizes\n"
        "- myanime.live auto-extraction\n"
        "- Batch ranges: `https://myanime.live/show-episode-{1-5}/`"
    )

@app.on_message(filters.text & ~filters.command(["start", "help"]))
async def handle_message(client: Client, message: Message):
    text = message.text.strip()
    
    # Handle batch ranges
    range_match = re.search(r"\{(\d+)-(\d+)\}", text)
    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2))
        if end - start > 10:
            await message.reply_text("⚠️ Please limit range to 10 episodes at a time.")
            return
        await message.reply_text(f"🚀 **Batch processing {end - start + 1} episodes...**")
        for i in range(start, end + 1):
            url = text.replace(range_match.group(0), str(i))
            # For batch, we use default best quality to avoid multiple prompts
            await download_and_send(client, message.chat.id, url)
            await asyncio.sleep(2)
        return

    # Handle single link
    status_msg = await message.reply_text("⏳ **Processing link...**")
    
    final_url = text
    if "myanime.live" in text:
        embedded_urls = await extract_video_urls(text)
        if embedded_urls:
            final_url = embedded_urls[0]
        else:
            # If extraction fails, try the original URL directly
            pass

    formats = await get_available_formats(final_url)
    if formats:
        buttons = []
        for f in formats:
            res = f['resolution']
            ext = f['ext']
            size = f['filesize']
            size_str = f"({size // (1024*1024)}MB)" if size else ""
            
            button_text = f"{res} - {ext} {size_str}".strip()
            callback_data = f"dl|{f['format_id']}|{final_url}"
            
            # Telegram callback_data limit is 64 bytes. 
            # If URL is too long, we might need to store it or shorten it.
            if len(callback_data) > 64:
                # Fallback: just use format_id and we'll have to handle the URL differently
                # For now, let's try to truncate or use a shorter prefix
                callback_data = f"dl|{f['format_id']}|short" 
                # In a real bot, you'd use a database or cache to map 'short' to the actual URL
                # For this implementation, we'll try to keep it simple.
            
            buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        if buttons:
            await status_msg.edit_text(
                f"✅ **Formats found for:**\n`{final_url[:50]}...`\n\nSelect quality:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            await download_and_send(client, message.chat.id, final_url, status_msg=status_msg)
    else:
        await download_and_send(client, message.chat.id, final_url, status_msg=status_msg)

@app.on_callback_query(filters.regex(r"^dl\|"))
async def callback_handler(client: Client, query):
    data = query.data.split("|")
    format_id = data[1]
    url = data[2]
    
    if url == "short":
        # If we had to shorten it, we look at the message text or original message
        # This is a bit hacky but works for a stateless demo
        await query.message.edit_text("❌ URL was too long for callback. Please try a shorter link or contact admin.")
        return

    await download_and_send(client, query.message.chat.id, url, format_id=format_id, status_msg=query.message)
    await query.answer()

if __name__ == "__main__":
    print("Bot is starting...")
    app.run()
