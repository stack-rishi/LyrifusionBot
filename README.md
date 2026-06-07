<div align="center">

<img src="https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python" />
<img src="https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram" />
<img src="https://img.shields.io/badge/Powered%20By-yt--dlp-red?style=for-the-badge" />
<img src="https://img.shields.io/badge/AI-Groq-orange?style=for-the-badge" />

# 🎵 LyriFusion Bot

**The most robust Telegram music bot that actually works — even on datacenter IPs.**

*Sends AI-tagged MP3 files with cover art, artist metadata, and embedded lyrics directly in Telegram.*

[![Try the Bot](https://img.shields.io/badge/Try%20the%20Bot-Telegram-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/LyriFusionBot)

</div>

---

## ✨ Features

- 🎵 **High-quality MP3 downloads** (192kbps) from YouTube & SoundCloud
- 🤖 **AI Metadata Extraction** — Groq AI + JioSaavn identify the exact song, artist & album from any vague query
- 🖼️ **Smart Cover Art** — Searches multiple music databases, falls back to YouTube thumbnail
- 📝 **Lyrics Embedding** — Paste lyrics and they get embedded directly into the MP3 ID3 tags
- 🛡️ **Multi-layer YouTube bypass** — PO Token server, mweb client spoofing, EJS JS challenge solver, cookies
- 🔄 **Automatic SoundCloud fallback** — If YouTube fails, SoundCloud takes over seamlessly

---

## 🏗️ Architecture

The bot uses a **9-layer anti-bot bypass stack** to reliably download from YouTube even on VPS/datacenter IPs:

| Layer | What it does |
|---|---|
| **mweb + tv client** | Bypasses SABR streaming (which 403s the web client) |
| **cookies.txt** | Authenticates as a real Google user |
| **PO Token server** | Generates Proof-of-Origin tokens via bgutil |
| **EJS challenge solver** | Decrypts YouTube JS signature challenges via Node.js |
| **IPv4 forcing** | Prevents IPv6 blackhole hangs on EC2 |
| **AI-enhanced queries** | Groq AI cleans user input → `ytsearch1:` gets exact correct video |
| **SoundCloud fallback** | Safety net if all YouTube layers fail |

---

## 🚀 Quick Start

### Requirements
- Python 3.11+
- FFmpeg
- Node.js (auto-downloaded if missing)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- A Groq API key (free at [console.groq.com](https://console.groq.com))
- YouTube `cookies.txt` (Netscape format, exported from your browser)

### Installation

```bash
git clone https://github.com/stack-rishi/LyrifusionBot.git
cd LyrifusionBot
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

Copy the example env file and fill in your secrets:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
# Your Telegram bot token from @BotFather
TELEGRAM_BOT_TOKEN=your_token_here

# Your Groq API key from https://console.groq.com
GROQ_API_KEY=your_groq_key_here

# Path to YouTube cookies file (optional)
COOKIES_FILE=cookies.txt
```

Export your YouTube cookies to `cookies.txt` in Netscape format using the [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) Chrome extension while logged into your Google account.

> ⚠️ **Important:** Cookies expire over time. If YouTube downloads start failing, re-export `cookies.txt`.
>
> 💡 **Legacy support:** If you prefer, you can still use `token.txt` and `groq_key.txt` files instead of `.env` — the bot checks both.

### Run

#### Option A: Manual Setup (Local/VPS)
```bash
python3.11 bot.py
```

For 24/7 deployment on a VPS:
```bash
tmux new -s bot
python3.11 bot.py
# Detach: Ctrl+B then D
```

#### Option B: Docker Setup (Recommended)
If you have Docker and Docker Compose installed, you can build and run the entire stack (including Python, Node.js, and FFmpeg) in one command:
```bash
docker compose up --build -d
```

To view logs:
```bash
docker compose logs -f
```

---

## ☁️ Deploying on AWS EC2

See [DOCUMENTATION.md](DOCUMENTATION.md) for a full AWS EC2 (Amazon Linux 2023) setup guide including FFmpeg installation, Python 3.11 upgrade, and tmux persistence.

---

## 📦 Project Structure

```
.
├── bot.py              # Main bot — all logic lives here
├── Dockerfile          # Container build definition
├── docker-compose.yml  # Docker service composition
├── LICENSE             # MIT License
├── .env.example        # Template for environment variables (safe to share)
├── .env                # YOUR secrets — never committed (gitignored)
├── requirements.txt    # Python dependencies
├── .gitignore          # Prevents secrets from being committed
├── README.md           # This file
├── DOCUMENTATION.md    # Full technical architecture & deployment guide
└── documentation.txt   # Plain-text copy of documentation
```

> 🔒 `.env`, `token.txt`, `groq_key.txt`, and `cookies.txt` are **gitignored** and never pushed to GitHub.

---

## 📋 How It Works

1. **User sends a song name** → Groq AI + JioSaavn identify exact title, artist, album
2. **Bot shows cover art** → searched from music databases or YouTube thumbnail
3. **User confirms cover** → bot builds AI-enhanced search query and downloads via yt-dlp
4. **User pastes lyrics** → embedded into MP3 ID3 tags via mutagen
5. **Bot sends tagged MP3** → with cover art, title, artist, album, and lyrics embedded

---

## 🔧 Environment Variables

| Variable | Description | Required |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather | ✅ Yes |
| `GROQ_API_KEY` | Groq AI API key from console.groq.com | ✅ Yes |
| `COOKIES_FILE` | Path to YouTube cookies file (Netscape format) | Optional |

---

## ⚠️ Disclaimer

This bot is for **personal and educational use only**. Downloading copyrighted content may violate YouTube's Terms of Service and applicable copyright laws in your country. Use responsibly.

---

<div align="center">

Made with ❤️ by [@stack-rishi](https://github.com/stack-rishi)

Contact on Telegram: [@Ri5h11](https://t.me/Ri5h11)

</div>
