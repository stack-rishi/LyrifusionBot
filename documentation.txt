# LyriFusion Bot - Architecture & Deployment Documentation

## 🚀 The Goal
To build and deploy a highly resilient Telegram music bot capable of directly extracting high-quality audio from YouTube while bypassing aggressive anti-bot protections, IP blocks, and protocol restrictions.

## 🛑 The Initial Problem
Initially deployed on Render, the bot was constantly hitting walls:
1. **IP Bans:** YouTube aggressively blocks shared datacenter IPs (like Render) with `HTTP 403 Forbidden` or "Sign in to confirm you're not a bot" errors.
2. **SABR Streaming:** YouTube forced SABR (Server ABR) streaming on the default `web` client, which blocks direct URL downloads and causes extraction failures.
3. **Signature Decryption:** YouTube frequently updates its JavaScript `n` challenge algorithms, breaking standard `yt-dlp` extractions.
4. **PO Tokens:** YouTube now requires Proof-of-Origin (PO) tokens to verify that requests are coming from legitimate browsers/devices.

## 🏆 The Winning Solution Stack
To conquer YouTube's defenses, we migrated to **AWS EC2** and built a multi-layered bypass architecture inside `bot.py`.

### 1. Infrastructure: AWS EC2 (Amazon Linux 2023)
*   Moved away from Render's webhook model to a dedicated AWS EC2 VPS.
*   **Why?** EC2 provides a better IP reputation.
*   **Mode:** Switched from Aiohttp Webhooks to `aiogram`'s Long Polling mode (`dp.start_polling`), running 24/7 inside a `tmux` session.

### 2. Environment: Python 3.11 Upgrade
*   Amazon Linux 2023 ships with Python 3.9. 
*   **The Bug:** `yt-dlp` calls `logging.debug(msg, once=True)`, but the `once` keyword argument was only introduced in Python 3.12. This caused fatal crashes during downloads.
*   **The Fix:** Upgraded the system to Python 3.11 via `dnf` and added a custom logging monkey-patch in `bot.py` to strip the `once` argument, preventing crashes.

### 3. JavaScript Challenge Solving: Node.js + EJS
*   **Node.js Auto-Installer:** `bot.py` is programmed to automatically download and extract a portable Node.js v22 runtime (`node_bin`) if it's missing on the host system.
*   **EJS Solver:** Added `remote_components: ['ejs:github']` to the `yt-dlp` config. This forces `yt-dlp` to download an external, constantly-updated JavaScript challenge solver from GitHub, which executes via the Node.js runtime to decrypt YouTube signatures.

### 4. Proof-of-Origin (PO) Token Server
*   **bgutil-ytdlp-pot-provider:** `bot.py` automatically clones this repository, compiles it via TypeScript (`tsc`), and boots a local HTTP server on `127.0.0.1:4416`.
*   **yt-dlp Integration:** Configured `extractor_args: {'youtubepot-bgutilhttp': {'base_url': ['http://127.0.0.1:4416']}}` to route extraction requests through the token server, proving to YouTube that the request is legitimate.

### 5. Client Spoofing (`mweb` & `tv`)
*   The default `web` client was blocked by SABR streaming protocols.
*   The `ios` client bypassed SABR, but it **does not support cookies**, causing instant bot detection on datacenter IPs.
*   **The Fix:** Switched `player_client` to `['mweb', 'tv']`. Mobile Web (`mweb`) bypasses SABR streaming restrictions *and* perfectly supports cookie authentication.

### 6. Authentication: `cookies.txt`
*   Injected a Netscape-format `cookies.txt` file (exported from a real browser) into the `yt-dlp` configuration.
*   This authenticates the bot's requests as a real Google user, drastically reducing the chances of IP bans.

### 7. Networking Fix: IPv4 Forcing
*   **The Bug:** Downloads from SoundCloud and some YouTube CDNs were silently hanging/freezing indefinitely.
*   **The Cause:** AWS EC2 `t2.micro` instances often have misconfigured or unroutable IPv6 addresses. `yt-dlp` tries IPv6 first and gets blackholed.
*   **The Fix:** Added `'source_address': '0.0.0.0'` to the configuration, forcing all traffic over reliable IPv4.

### 8. Layer 2 Fallback: SoundCloud
*   If YouTube completely fails, the bot catches the error and automatically falls back to `scsearch1:`, extracting the audio from SoundCloud as a safety net.

### 9. AI-Enhanced Search Queries (`ytsearch1:`)
*   **The Bug:** When users entered vague search queries (e.g., "Tere bin teree sang Full song"), standard web searches (like DuckDuckGo) returned completely unrelated videos, causing the bot to download the wrong song entirely.
*   **The Fix:** We implemented a two-step enhancement:
    1. The AI (Groq + JioSaavn) cleans the user's raw text and extracts the perfect metadata (e.g., `Title: Tere Bin, Artist: Raza Hassan, Sumedha Karmahe, Bappi Lahiri`).
    2. We build an enhanced query string (`f"{title} {artist} song"`) and pass it directly into `yt-dlp`'s native YouTube search (`ytsearch1:`), bypassing third-party search engines completely. This ensures YouTube's own algorithm serves the exact correct video.

---

## 🛠️ Deployment Instructions for AWS EC2 (Amazon Linux 2023)

If you ever need to rebuild the server from scratch, follow these exact steps:

### 1. Install System Dependencies
```bash
sudo dnf update -y
sudo dnf install python3.11 python3.11-pip nodejs npm git tmux tar xz -y
```

### 2. Install FFmpeg (Manual Static Build)
Amazon Linux doesn't have FFmpeg in its default repos.
```bash
wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar xvf ffmpeg-release-amd64-static.tar.xz
sudo mv ffmpeg-*-static/ffmpeg /usr/local/bin/
sudo mv ffmpeg-*-static/ffprobe /usr/local/bin/
```

### 3. Clone Repository & Setup Virtual Environment
```bash
git clone https://github.com/stack-rishi/LyrifusionBot.git
cd LyrifusionBot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Configure Secrets
Ensure `token.txt` (Telegram Bot Token) and `cookies.txt` (YouTube Cookies) are placed in the root directory.

### 5. Start the Bot (24/7 Background Session)
```bash
tmux new -s bot
python3.11 bot.py
```
*To safely detach from the tmux session without killing the bot: Press `Ctrl+B`, release, then press `D`.*

---
*Documented on: April 24, 2026*
*Success achieved after conquering SABR, Python 3.9 bugs, IPv6 blackholes, and JS Signature Encryption.*
