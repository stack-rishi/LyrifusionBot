from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
import os
import logging
import asyncio
import requests
from pytubefix import Search, YouTube
import yt_dlp
from aiogram import Bot, Dispatcher, types
from bs4 import BeautifulSoup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, USLT, Encoding
from PIL import Image, UnidentifiedImageError
from io import BytesIO
from ddgs import DDGS
import re
import uuid
import glob
import shutil
import json

# Force INFO level logs globally so we can see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)

# Monkey-patch Python 3.9's logging to accept 'once' kwarg (added in Python 3.12)
# yt-dlp uses logging.debug(msg, once=True) internally which crashes on older Python
for _method_name in ('debug', 'info', 'warning', 'error', 'critical'):
    _original = getattr(logging.Logger, _method_name)
    def _make_patched(orig):
        def _patched(self, msg, *args, **kwargs):
            kwargs.pop('once', None)
            return orig(self, msg, *args, **kwargs)
        return _patched
    setattr(logging.Logger, _method_name, _make_patched(_original))

class YTDLLogger:
    """Custom logger to bypass yt-dlp's Python 3.9 'once=True' bug"""
    def debug(self, msg, *args, **kwargs):
        pass
    def warning(self, msg, *args, **kwargs):
        logging.info(f"yt-dlp warn: {msg}")
    def error(self, msg, *args, **kwargs):
        logging.error(f"yt-dlp error: {msg}")
import time
from urllib.parse import quote_plus
import sys

# Auto-install Node.js on Render so yt-dlp can decipher YouTube signatures
# yt-dlp completely fails to decrypt video URLs on latest YouTube without this JS runtime
NODE_DIR = "node_bin"
_NODE_VERSION = "v22.14.0"
_NODE_URL = f"https://nodejs.org/dist/{_NODE_VERSION}/node-{_NODE_VERSION}-linux-x64.tar.xz"
if sys.platform != 'win32':
    import subprocess
    _need_install = not os.path.exists(NODE_DIR)
    if os.path.exists(NODE_DIR):
        _ver = subprocess.run([f"{NODE_DIR}/bin/node", "--version"], capture_output=True, text=True)
        if _ver.returncode == 0 and not _ver.stdout.strip().startswith("v22"):
            import shutil
            shutil.rmtree(NODE_DIR, ignore_errors=True)
            _need_install = True
    if _need_install:
        logging.warning(f"Downloading Node.js {_NODE_VERSION}...")
        os.makedirs(NODE_DIR, exist_ok=True)
        subprocess.run(["wget", "-qO", "node.tar.xz", _NODE_URL])
        subprocess.run(["tar", "xf", "node.tar.xz", "-C", NODE_DIR, "--strip-components=1"])
        if os.path.exists("node.tar.xz"):
            os.remove("node.tar.xz")
        logging.warning("Node.js installed.")

NODE_BIN = None
if sys.platform != 'win32':
    os.environ["PATH"] = f"{os.path.abspath(NODE_DIR)}/bin:" + os.environ.get("PATH", "")
    NODE_BIN = f"{os.path.abspath(NODE_DIR)}/bin/node"

# Auto-setup PO Token server for YouTube Proof-of-Origin bypass
BGUTIL_DIR = "bgutil-ytdlp-pot-provider"
_pot_server_process = None
_pot_status = "skipped (windows)"
if sys.platform != 'win32':
    import subprocess as _sp
    _node_path = os.path.abspath(NODE_DIR) + "/bin"
    _env = os.environ.copy()
    _env["PATH"] = f"{_node_path}:" + _env.get("PATH", "")

    if not os.path.exists(f"{BGUTIL_DIR}/server/build/main.js") or not os.path.exists(f"{BGUTIL_DIR}/.node_version") or open(f"{BGUTIL_DIR}/.node_version").read().strip() != _NODE_VERSION:
        try:
            logging.warning("=== PO TOKEN SERVER SETUP STARTING ===")
            import shutil as _shutil
            if os.path.exists(BGUTIL_DIR):
                _shutil.rmtree(BGUTIL_DIR, ignore_errors=True)

            r = _sp.run(["git", "clone", "--single-branch", "--branch", "1.3.1",
                          "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git", BGUTIL_DIR],
                         capture_output=True, text=True, env=_env)
            logging.warning(f"PO Token: git clone rc={r.returncode}")

            _build_env = _env.copy()
            _build_env["NODE_ENV"] = "development"

            r = _sp.run([f"{_node_path}/npm", "ci", "--include=dev"], cwd=f"{BGUTIL_DIR}/server",
                       capture_output=True, text=True, env=_build_env)
            logging.warning(f"PO Token: npm ci rc={r.returncode}")

            r = _sp.run([f"{_node_path}/npm", "install", "typescript"], cwd=f"{BGUTIL_DIR}/server",
                       capture_output=True, text=True, env=_build_env)
            logging.warning(f"PO Token: npm install typescript rc={r.returncode}")

            r = _sp.run([f"{_node_path}/npx", "--yes", "tsc"], cwd=f"{BGUTIL_DIR}/server",
                       capture_output=True, text=True, env=_build_env)
            logging.warning(f"PO Token: tsc compile rc={r.returncode}")

            if os.path.exists(f"{BGUTIL_DIR}/server/build/main.js"):
                logging.warning("=== PO TOKEN SERVER BUILT OK ===")
                with open(f"{BGUTIL_DIR}/.node_version", "w") as _vf:
                    _vf.write(_NODE_VERSION)
                _pot_status = "built"
            else:
                logging.error("PO Token: build/main.js NOT FOUND after tsc!")
                _pot_status = "build failed"
        except Exception as _build_err:
            logging.error(f"PO Token build exception: {_build_err}")
            _pot_status = f"exception: {_build_err}"
    else:
        logging.warning("PO Token: build/main.js already exists, skipping build")
        _pot_status = "already built"

    # Start the PO Token HTTP server as a background process
    if os.path.exists(f"{BGUTIL_DIR}/server/build/main.js"):
        try:
            _pot_log_path = f"{BGUTIL_DIR}/server/pot_server.log"
            _pot_log = open(_pot_log_path, "w")
            _pot_server_process = _sp.Popen(
                [f"{_node_path}/node", "--experimental-require-module", "build/main.js"],
                cwd=f"{BGUTIL_DIR}/server",
                env=_env,
                stdout=_pot_log, stderr=_pot_log
            )
            import time as _time
            for _attempt in range(15):
                _time.sleep(2)
                if _pot_server_process.poll() is not None:
                    _pot_server_process = None
                    _pot_status = "server crashed"
                    break
                try:
                    _ping = requests.get("http://127.0.0.1:4416/ping", timeout=3)
                    logging.warning(f"=== PO TOKEN SERVER ALIVE (PID:{_pot_server_process.pid}) ping={_ping.status_code} (attempt {_attempt+1}) ===")
                    _pot_status = "RUNNING"
                    break
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"PO Token server start failed: {e}")
            _pot_status = f"start failed: {e}"

logging.warning(f"=== PO TOKEN STATUS: {_pot_status} ===")

BS4_AVAILABLE = True

# Import Rust-dependent DuckDuckGo search
try:
    from ddgs import DDGS
    RUST_SEARCH_AVAILABLE = True
except ImportError:
    RUST_SEARCH_AVAILABLE = False
    logging.warning("DuckDuckGo search not available. Install with: pip install duckduckgo-search")

# Import Groq for AI
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logging.warning("Groq not available. Install with: pip install groq")

# ================== LOGGING CONFIGURATION ================== #
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

# ================== CONFIGURATION SETUP ================== #
class Config:
    def __init__(self):
        self.TOKEN = self.get_token()
        self.GROQ_API_KEY = self.get_groq_key()
        self.ITUNES_API = "https://itunes.apple.com/search"
        self.DEEZER_API = "https://api.deezer.com/search"

    def get_token(self):
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        if token:
            return token
        try:
            with open('token.txt', 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            logging.error("Missing bot token. Create token.txt file.")
            return None

    def get_groq_key(self):
        key = os.getenv('GROQ_API_KEY')
        if key:
            return key
        try:
            with open('groq_key.txt', 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            return None

from aiogram.client.session.aiohttp import AiohttpSession

config = Config()
if not config.TOKEN:
    exit("Bot token not found. Please create token.txt with your Telegram bot token.")

# Initialize bot with extended timeout for slow Render uploads
session = AiohttpSession(timeout=300.0)
bot = Bot(token=config.TOKEN, session=session)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Initialize Groq client if available
groq_client = None
if config.GROQ_API_KEY and GROQ_AVAILABLE:
    try:
        groq_client = Groq(api_key=config.GROQ_API_KEY)
        logging.info("Groq AI client initialized")
    except Exception as e:
        logging.error(f"Groq client error: {e}")

# ================== BOT STATES ================== #
class MusicStates(StatesGroup):
    waiting_song = State()
    confirm_cover = State()
    waiting_lyrics = State()

# ================== ADVANCED WEB SEARCH WITH RUST ================== #
class AdvancedWebSearch:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        # Initialize DuckDuckGo if available
        self.ddgs = None
        if RUST_SEARCH_AVAILABLE:
            try:
                self.ddgs = DDGS()
                logging.info("DuckDuckGo search initialized (Rust-powered)")
            except Exception as e:
                logging.error(f"DuckDuckGo initialization error: {e}")

    async def search_web(self, query):
        """Advanced web search using Rust-powered DuckDuckGo and fallbacks"""
        results = []
        # Method 1: DuckDuckGo search (Rust-powered, high accuracy)
        if self.ddgs:
            results.extend(await self.search_duckduckgo(query))
        # Method 2: Wikipedia fallback
        results.extend(await self.search_wikipedia(query))
        # Method 3: YouTube metadata
        results.extend(await self.search_youtube_info(query))
        
        # Deduplicate results
        seen = set()
        unique_results = []
        for result in results:
            key = result.get('title', '') + result.get('url', '')
            if key not in seen:
                unique_results.append(result)
                seen.add(key)
        
        return unique_results[:15]

    async def search_duckduckgo(self, query):
        """Search using DuckDuckGo (Rust-powered)"""
        try:
            search_queries = [
                f'"{query}" song details artist album',
                f'{query} movie soundtrack Bollywood',
                f'{query} singer composer music',
                query
            ]
            
            results = []
            for search_query in search_queries[:2]:  # Limit to 2 to avoid rate limits
                try:
                    ddg_results = list(self.ddgs.text(search_query, max_results=5))
                    for result in ddg_results:
                        results.append({
                            'title': result.get('title', ''),
                            'snippet': result.get('body', ''),
                            'url': result.get('href', ''),
                            'source': 'DuckDuckGo'
                        })
                    await asyncio.sleep(0.5)  # Rate limiting
                except Exception as e:
                    logging.error(f"DuckDuckGo search error for '{search_query}': {e}")
                    continue
            
            return results[:8]
        except Exception as e:
            logging.error(f"DuckDuckGo search error: {e}")
            return []

    async def search_wikipedia(self, query):
        """Search Wikipedia API"""
        try:
            search_terms = [query, f"{query} song", f"{query} bollywood", f"{query} movie"]
            results = []
            
            for term in search_terms:
                try:
                    api_url = "https://en.wikipedia.org/w/api.php"
                    params = {
                        'action': 'query',
                        'list': 'search',
                        'srsearch': term,
                        'format': 'json',
                        'srlimit': 3
                    }
                    
                    response = self.session.get(api_url, params=params, timeout=8)
                    if response.status_code == 200:
                        data = response.json()
                        for page in data.get('query', {}).get('search', []):
                            try:
                                summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(page['title'])}"
                                summary_response = self.session.get(summary_url, timeout=5)
                                if summary_response.status_code == 200:
                                    summary_data = summary_response.json()
                                    results.append({
                                        'title': summary_data.get('title', ''),
                                        'snippet': summary_data.get('extract', ''),
                                        'url': summary_data.get('content_urls', {}).get('desktop', {}).get('page', '')
                                    })
                            except:
                                continue
                    
                    if results:
                        break
                except Exception as e:
                    continue
            
            return results[:5]
        except Exception as e:
            logging.error(f"Wikipedia search error: {e}")
            return []

    async def search_youtube_info(self, query):
        """Get YouTube metadata"""
        try:
            s = Search(query)
            results = []
            if s.videos:
                for video in s.videos[:5]:
                    results.append({
                        'title': video.title,
                        'snippet': f"YouTube: {video.title} - {video.author}...",
                        'url': video.watch_url
                    })
            return results
        except Exception as e:
            logging.warning(f"YouTube search skipped (non-critical): {e}")
            return []

    async def search_jiosaavn_web(self, query):
        """Search JioSaavn website directly"""
        try:
            # Search JioSaavn site specifically
            jiosaavn_query = f"site:jiosaavn.com {query}"
            
            results = []
            if self.ddgs:
                try:
                    ddg_results = list(self.ddgs.text(jiosaavn_query, max_results=3))
                    for result in ddg_results:
                        if 'jiosaavn.com' in result.get('href', ''):
                            results.append({
                                'title': result.get('title', ''),
                                'snippet': result.get('body', ''),
                                'url': result.get('href', ''),
                                'source': 'JioSaavn'
                            })
                except Exception as e:
                    logging.error(f"JioSaavn search error: {e}")
            
            return results[:3]
        except Exception as e:
            logging.error(f"JioSaavn web search error: {e}")
            return []

    async def scrape_jiosaavn_page(self, url):
        """Scrape JioSaavn page content"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            response = self.session.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                # Extract text content from HTML
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Remove script and style elements
                for script in soup(["script", "style"]):
                    script.decompose()
                
                # Get text content
                text_content = soup.get_text()
                
                # Clean up whitespace
                lines = (line.strip() for line in text_content.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = ' '.join(chunk for chunk in chunks if chunk)
                
                return text[:4000]  # Limit for Groq processing
                
        except Exception as e:
            logging.error(f"JioSaavn scraping error: {e}")
            return None

# ================== AI METADATA FETCHER ================== #
class AIMetadataFetcher:
    def __init__(self, groq_client, web_search):
        self.groq_client = groq_client
        self.web_search = web_search
        # Enhanced known songs database
        self.known_songs = {
            'humne ghar chhoda hai dil movie': {
                'title': 'Humne Ghar Chhoda Hai',
                'artist': 'Udit Narayan, Anuradha Paudwal',
                'album': 'Dil (1990)',
                'confidence': 0.95
            },
            'humne ghar chhoda hai': {
                'title': 'Humne Ghar Chhoda Hai',
                'artist': 'Udit Narayan, Anuradha Paudwal',
                'album': 'Dil (1990)',
                'confidence': 0.9
            },
            'tujhe dekha to ye jana sanam ddlj': {
                'title': 'Tujhe Dekha To Ye Jana Sanam',
                'artist': 'Lata Mangeshkar, Kumar Sanu',
                'album': 'Dilwale Dulhania Le Jayenge (1995)',
                'confidence': 0.95
            },
            'kal ho naa ho': {
                'title': 'Kal Ho Naa Ho',
                'artist': 'Sonu Nigam',
                'album': 'Kal Ho Naa Ho (2003)',
                'confidence': 0.9
            },
            'mere sapno ki rani ddlj': {
                'title': 'Mere Sapno Ki Rani',
                'artist': 'Lata Mangeshkar, Mukesh',
                'album': 'Aradhana (1969)',
                'confidence': 0.9
            },
            'kuch kuch hota hai': {
                'title': 'Kuch Kuch Hota Hai',
                'artist': 'Udit Narayan, Alka Yagnik',
                'album': 'Kuch Kuch Hota Hai (1998)',
                'confidence': 0.9
            }
        }

    async def search_with_ai(self, query):
        """AI-powered metadata search with JioSaavn priority"""
        try:
            # Step 1: Check known songs database
            query_lower = query.lower().strip()
            if query_lower in self.known_songs:
                logging.info(f"Found in database: {query}")
                return self.known_songs[query_lower]

            # Step 2: Try JioSaavn web scraping first (NEW!)
            jiosaavn_metadata = await self.search_jiosaavn_with_ai(query)
            if jiosaavn_metadata and jiosaavn_metadata.get('confidence', 0) > 0.7:
                logging.info(f"Found via JioSaavn scraping: {jiosaavn_metadata}")
                return jiosaavn_metadata

            # Step 3: Fall back to general web search
            search_results = await self.web_search.search_web(query)
            
            # Step 4: AI analysis if available
            if self.groq_client and search_results:
                ai_metadata = await self.ai_analyze_metadata(query, search_results)
                if ai_metadata and ai_metadata.get('confidence', 0) > 0.5:
                    return ai_metadata

            # Step 5: Enhanced pattern matching
            return await self.enhanced_pattern_matching(query, search_results)

        except Exception as e:
            logging.error(f"AI search error: {e}")
            return self.basic_fallback(query)

    async def ai_analyze_metadata(self, query, search_results):
     """AI analysis of search results"""
     try:
        if not search_results:
            return None

        context = "\n".join([
            f"Title: {result.get('title', '')}\nContent: {result.get('snippet', '')}\n---"
            for result in search_results[:6]
        ])

        prompt = f"""Analyze these search results for the song: "{query}"

Search Results:
{context}

Extract accurate metadata. Rules:
1. For Indian/Bollywood songs, provide actual singer names
2. Album should be "Movie Name (Year)" format for movie songs
3. Use complete, accurate song title
4. Be precise based on evidence
5. IMPORTANT: If query mentions a specific movie name (like "Teree Sang"), prioritize results from that movie
6. Don't confuse similar song titles from different movies/albums

Respond with ONLY this JSON:
{{"title": "Song Title", "artist": "Singer Name(s)", "album": "Album/Movie (Year)", "confidence": 0.0-1.0}}"""

        response = self.groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Extract music metadata. Respond only with valid JSON. Pay attention to specific movie names mentioned in queries."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=150
        )

        ai_response = response.choices[0].message.content.strip()
        
        # Clean response
        if ai_response.startswith('```'):
            ai_response = re.sub(r'```')
        if ai_response.endswith('```'):
            ai_response = ai_response[:-3].strip()

        metadata = json.loads(ai_response)
        
        if all(key in metadata for key in ['title', 'artist', 'album', 'confidence']):
            logging.info(f"AI metadata: {metadata}")
            return metadata

     except Exception as e:
        logging.error(f"AI analysis error: {e}")
        return None


    async def enhanced_pattern_matching(self, query, search_results):
        """Enhanced pattern matching"""
        context_text = " ".join([
            result.get('title', '') + " " + result.get('snippet', '')
            for result in search_results
        ]).lower()

        # Pattern matching
        patterns = [
            (r'(.+?)\s+from\s+(.+?)\s+movie', lambda m: (m.group(1).strip(), m.group(2).strip())),
            (r'(.+?)\s+(.+?)\s+movie', lambda m: (m.group(1).strip(), m.group(2).strip())),
            (r'(.+?)\s+dil\s+movie', lambda m: (m.group(1).strip(), 'Dil')),
        ]

        for pattern, extractor in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                song_name, movie_name = extractor(match)
                # Extract additional info from context
                artist = self.extract_artist_from_context(context_text, song_name)
                year = self.extract_year_from_context(context_text, movie_name)
                
                album = movie_name.title()
                if year:
                    album = f"{album} ({year})"
                
                return {
                    'title': song_name.title(),
                    'artist': artist or 'Various Artists',
                    'album': album,
                    'confidence': 0.7
                }

        return self.basic_fallback(query)

    def extract_artist_from_context(self, context, song_name):
        """Extract artist from context"""
        patterns = [
            rf'{re.escape(song_name.lower())}.*?sung by ([^.,\n]+)',
            rf'{re.escape(song_name.lower())}.*?singer[s]?\s*:?\s*([^.,\n]+)',
            rf'performed by ([^.,\n]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, context, re.IGNORECASE)
            if match:
                return re.sub(r'\s*(and|&)\s*', ', ', match.group(1).strip())
        return None

    def extract_year_from_context(self, context, movie_name):
        """Extract year from context"""
        if not movie_name:
            return None
        
        patterns = [
            rf'{re.escape(movie_name.lower())}.*?(19\d{{2}}|20\d{{2}})',
            rf'(19\d{{2}}|20\d{{2}}).*?{re.escape(movie_name.lower())}',
        ]

        for pattern in patterns:
            match = re.search(pattern, context, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def basic_fallback(self, query):
        """Basic fallback"""
        return {
            'title': query.title(),
            'artist': 'Various Artists',
            'album': 'Unknown Album',
            'confidence': 0.3
        }

    async def search_jiosaavn_with_ai(self, query):
     """Search JioSaavn with AI-powered scraping"""
     try:
        # Step 1: Search JioSaavn website
        jiosaavn_results = await self.web_search.search_jiosaavn_web(query)
        
        if not jiosaavn_results:
            return None
            
        # Step 2: Scrape the most relevant JioSaavn page
        best_result = jiosaavn_results[0]  # ✅ FIXED: Get first result from list
        page_content = await self.web_search.scrape_jiosaavn_page(best_result['url'])
        
        if not page_content:
            return None
            
        # Step 3: Use Groq AI to extract metadata
        return await self.ai_extract_jiosaavn_metadata(query, page_content, best_result)
        
     except Exception as e:
        logging.error(f"JioSaavn AI search error: {e}")
        return None


    async def ai_extract_jiosaavn_metadata(self, query, page_content, result_info):
     """Use Groq AI to extract metadata from JioSaavn page"""
     try:
        prompt = f"""Extract song metadata from this JioSaavn page content for the query: "{query}"

Page Content:
{page_content}

Search Result Title: {result_info.get('title', '')}

Instructions:
1. Find the exact song title, artist name(s), and album name
2. For Bollywood songs, include movie name in album as "Movie Name (Year)"  
3. Extract all featured artists and singers
4. Be precise and accurate based on the page content
5. If multiple songs are found, pick the one most relevant to the query
6. Pay special attention to movie names like "Teree Sang" vs other similar songs

Respond with ONLY this JSON:
{{"title": "Exact Song Title", "artist": "Singer Name(s)", "album": "Album/Movie (Year)", "confidence": 0.0-1.0, "source": "JioSaavn"}}"""

        if not self.groq_client:
            return None
            
        response = self.groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Extract music metadata from JioSaavn page content. Respond only with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=200
        )

        ai_response = response.choices[0].message.content.strip()  # ✅ FIXED: Added [0]
        
        # Clean response
        if ai_response.startswith('```'):
            ai_response = re.sub(r'```(json)?', '', ai_response).strip()
        if ai_response.endswith('```'):
            ai_response = ai_response[:-3].strip()

        metadata = json.loads(ai_response)
        
        if all(key in metadata for key in ['title', 'artist', 'album', 'confidence']):
            logging.info(f"JioSaavn AI metadata: {metadata}")
            return metadata
            
     except Exception as e:
        logging.error(f"JioSaavn AI extraction error: {e}")
        return None

# Initialize components
web_search = AdvancedWebSearch()
ai_fetcher = AIMetadataFetcher(groq_client, web_search)

# ================== METADATA PARSING ================== #
async def clean_and_parse_metadata(original_query, youtube_title=None, youtube_uploader=None):
    """AI-powered metadata parsing"""
    logging.info(f"Parsing metadata for: '{original_query}'")
    
    try:
        ai_metadata = await ai_fetcher.search_with_ai(original_query)
        if ai_metadata:
            logging.info(f"Metadata result: {ai_metadata}")
            return ai_metadata
        
        return {
            'title': original_query.title(),
            'artist': 'Various Artists',
            'album': 'Unknown Album',
            'confidence': 0.2
        }

    except Exception as e:
        logging.error(f"Metadata parsing error: {e}")
        return {
            'title': original_query.title(),
            'artist': 'Various Artists',
            'album': 'Unknown Album',
            'confidence': 0.1
        }

# ================== FILE MANAGEMENT ================== #
def setup_directories():
    """Setup directories"""
    directories = ['downloads', 'processed', 'covers']
    for directory in directories:
        os.makedirs(directory, exist_ok=True)

async def cleanup_user_files(user_data):
    """Cleanup temporary files"""
    files_to_clean = ['file_path', 'cover_path']
    for file_key in files_to_clean:
        if file_key in user_data:
            file_path = user_data[file_key]
            if file_path and os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                except Exception as e:
                    logging.error(f"Cleanup error: {e}")

setup_directories()

# ================== COMMAND HANDLERS ================== #
@dp.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    await cleanup_user_files(user_data)
    await state.clear()
    
    ai_status = "Enabled" if groq_client else "Disabled (Add Groq API key)"
    welcome_text = (
        "🎵 *Welcome to AI-Powered Music Assistant!* 🎵\n\n"
        "Download songs with embedded lyrics and metadata for Samsung Music Player.\n\n"
        "✨ *How it works:*\n"
        "1. Send song name (e.g., 'Humne ghar chhoda hai Dil movie')\n"
        "2. AI extracts accurate metadata\n"
        "3. Confirm cover art\n"
        "4. Send lyrics\n"
        "5. Get perfectly tagged MP3!\n\n"
        " *Use /help if you are confused*\n"
        " *DM : @Ri5h11 For any issues regarding the bot*\n"
        "🚀 *Send a song name to begin!*"
    )
    
    await message.answer(welcome_text, parse_mode="Markdown")
    await state.set_state(MusicStates.waiting_song)

@dp.message(Command("help"))
async def help_command(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    await cleanup_user_files(user_data)
    await state.clear()
    
    help_text = (
        "🆘 *AI Music Assistant Help*\n\n"
        "📋 *Usage:*\n"
        "1. Send song name\n"
        "2. AI searches and extracts metadata\n"
        "3. Confirm cover art\n"
        "4. Send lyrics\n"
        "5. Get tagged MP3!\n\n"
        "💡 *Examples:*\n"
        "- `Humne ghar chhoda hai Dil movie`\n"
        "- `Tujhe dekha to ye jana sanam DDLJ`\n"
        "- `Kal ho naa ho`\n\n"
        "Contact: [@Ri5h11](https://t.me/Ri5h11)"
    )
    
    await message.answer(help_text, parse_mode="Markdown", disable_web_page_preview=True)

@dp.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("ℹ️ No active operation to cancel.")
        return
    
    user_data = await state.get_data()
    await cleanup_user_files(user_data)
    await state.clear()
    await message.answer("❌ Operation cancelled. Use /start to begin again.")

# ================== SONG PROCESSING ================== #
@dp.message(MusicStates.waiting_song)
async def handle_song_request(message: types.Message, state: FSMContext):
    if message.text and message.text.startswith('/'):
        return

    if not message.text:
        await message.answer("❌ Please send a text message with the song name.")
        return

    song_query = message.text.strip()
    if len(song_query) < 3:
        await message.answer("❌ Please enter a longer song name.")
        return

    await state.update_data(song_query=song_query, cover_attempts=0)

    search_methods = []
    if groq_client:
        search_methods.append("AI Analysis")
    search_methods.extend(["Web Search", "Database", "Pattern Matching"])
    
    search_msg = await message.answer(f"🔍 Searching '{song_query}' using {' + '.join(search_methods)}...")

    try:
        # Get AI metadata first
        ai_metadata = await clean_and_parse_metadata(song_query)
        
        # Search for cover art from APIs
        cover_search_query = f"{ai_metadata['title']} {ai_metadata['artist']} {ai_metadata['album']}"
        cover_url, cover_metadata = await search_cover_art(cover_search_query)
        
        if not cover_url:
            cover_url, cover_metadata = await search_cover_art(song_query)

        await bot.delete_message(message.chat.id, search_msg.message_id)
        
        # If API cover found, show it
        if cover_url:
            cover_path = await download_and_process_cover(cover_url)
            if cover_path:
                confidence = ai_metadata.get('confidence', 0)
                confidence_indicator = "High" if confidence >= 0.8 else "Medium" if confidence >= 0.5 else "Low"
                
                with open(cover_path, 'rb') as photo_file:
                    photo_data = photo_file.read()

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Yes", callback_data="cover_yes"),
                     InlineKeyboardButton(text="❌ No", callback_data="cover_no")]
                ])

                await message.answer_photo(
                    photo=BufferedInputFile(photo_data, filename="cover.jpg"),
                    caption=(
                        f"🎨 **Is this the correct cover?**\n\n"
                        f"**Confidence:** {confidence_indicator}\n"
                        f"**Title:** {ai_metadata['title']}\n"
                        f"**Artist:** {ai_metadata['artist']}\n"
                        f"**Album:** {ai_metadata['album']}"
                    ),
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )

                enhanced_query = f"{ai_metadata['title']} {ai_metadata['artist']} song"
                await state.update_data(cover_url=cover_url, cover_path=cover_path, download_query=enhanced_query, **ai_metadata)
                await state.set_state(MusicStates.confirm_cover)
                return

        # NO API COVER FOUND - Download video and use its thumbnail
        await message.answer(f"🎵 No cover art found in databases. Getting YouTube thumbnail...")
        
        # Download the video first to get its thumbnail
        download_query = f"{ai_metadata['title']} {ai_metadata['artist']} song"
        try:
            file_path, thumbnail_url, raw_metadata = await download_audio(download_query)
            
            if thumbnail_url:
                # Process the YouTube thumbnail as cover
                cover_path = await download_and_process_cover(thumbnail_url)
                if cover_path:
                    confidence = ai_metadata.get('confidence', 0)
                    confidence_indicator = "High" if confidence >= 0.8 else "Medium" if confidence >= 0.5 else "Low"
                    
                    with open(cover_path, 'rb') as photo_file:
                        photo_data = photo_file.read()

                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Yes", callback_data="cover_yes"),
                         InlineKeyboardButton(text="❌ No", callback_data="cover_no")]
                    ])

                    await message.answer_photo(
                        photo=BufferedInputFile(photo_data, filename="youtube_cover.jpg"),
                        caption=(
                            f"🎵 **YouTube thumbnail as cover**\n\n"
                            f"**Confidence:** {confidence_indicator}\n"
                            f"**Title:** {ai_metadata['title']}\n"
                            f"**Artist:** {ai_metadata['artist']}\n"
                            f"**Album:** {ai_metadata['album']}\n\n"
                            f"✅ Audio already downloaded!"
                        ),
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )

                    # Store everything in state including the downloaded file
                    await state.update_data(
                        cover_url=thumbnail_url, 
                        cover_path=cover_path, 
                        file_path=file_path,
                        original_thumbnail_url=thumbnail_url,
                        download_query=download_query,
                        **ai_metadata
                    )
                    await state.set_state(MusicStates.confirm_cover)
                    return
            
            # If thumbnail processing failed, proceed without cover
            await message.answer(
                f"⚠️ **No cover available**\n\n"
                f"**Title:** {ai_metadata['title']}\n"
                f"**Artist:** {ai_metadata['artist']}\n"
                f"**Album:** {ai_metadata['album']}\n\n"
                f"✅ Audio downloaded! Please send lyrics now."
            )
            
            await state.update_data(
                file_path=file_path,
                original_thumbnail_url=thumbnail_url,
                **ai_metadata
            )
            
            # Request lyrics directly
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
            ])

            await message.answer(
                f"📝 **Please send lyrics now**\n"
                f"(Paste text or attach text file)",
                parse_mode="Markdown",
                reply_markup=keyboard
            )

            await state.set_state(MusicStates.waiting_lyrics)
            
        except Exception as download_error:
            logging.error(f"Download error: {download_error}")
            await message.answer("❌ Download failed. Try a different song name.")

    except Exception as e:
        logging.error(f"Song request error: {e}")
        try:
            await bot.delete_message(message.chat.id, search_msg.message_id)
        except:
            pass
        await message.answer("❌ Error occurred. Please try again.")

@dp.callback_query(MusicStates.confirm_cover)
async def handle_cover_confirmation(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    user_data = await state.get_data()
    attempts = user_data.get('cover_attempts', 0)

    if callback_query.data == 'cover_yes':
        await callback_query.message.edit_caption(caption="✅ Cover confirmed! Downloading song...")
        await download_and_request_lyrics(callback_query.message, state)
        return

    # User rejected the cover
    attempts += 1
    await state.update_data(cover_attempts=attempts)

    if attempts < 3:
        # Try to find alternative cover
        await callback_query.message.edit_caption(
            caption=f"🔄 Searching for alternative cover (Attempt {attempts}/3)..."
        )
        
        # Search for new cover with different query
        cover_search_query = f"{user_data['title']} {user_data['artist']} album art official"
        cover_url, metadata = await search_cover_art(cover_search_query)

        if not cover_url:
            # Try different search terms
            cover_url, metadata = await get_youtube_cover(user_data['song_query'])

        if cover_url:
            cover_path = await download_and_process_cover(cover_url)
            if cover_path:
                # Clean up old cover file
                old_cover = user_data.get('cover_path')
                if old_cover and os.path.exists(old_cover):
                    try:
                        os.unlink(old_cover)
                    except:
                        pass

                # Show new cover option
                with open(cover_path, 'rb') as photo_file:
                    photo_data = photo_file.read()

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Yes", callback_data="cover_yes"),
                     InlineKeyboardButton(text="❌ No", callback_data="cover_no")]
                ])

                await callback_query.message.answer_photo(
                    photo=BufferedInputFile(photo_data, filename="cover.jpg"),
                    caption=f"🎨 **Is this better?** (Attempt {attempts}/3)\n\n"
                            f"**Title:** {user_data.get('title', 'Unknown')}\n"
                            f"**Artist:** {user_data.get('artist', 'Unknown')}\n",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )

                await state.update_data(cover_url=cover_url, cover_path=cover_path)
                return

        # No alternative cover found
        await callback_query.message.answer("❌ No alternative cover found. Using YouTube thumbnail...")
        await use_youtube_thumbnail(callback_query, state)
        return

    else:
        # Max attempts reached
        await callback_query.message.answer("⏰ **Max attempts reached (3/3).** Using YouTube thumbnail...")
        await use_youtube_thumbnail(callback_query, state)
        return

async def use_youtube_thumbnail(callback_query: types.CallbackQuery, state: FSMContext):
    """Use YouTube thumbnail as cover art and show it to user"""
    user_data = await state.get_data()
    song_query = user_data.get('song_query', '')
    download_query = user_data.get('download_query', song_query)
    
    # Check if we already have the audio downloaded
    existing_file = user_data.get('file_path')
    original_thumbnail = user_data.get('original_thumbnail_url')
    
    if existing_file and original_thumbnail:
        # We already have both file and thumbnail - just show the thumbnail
        logging.info(f"Reusing existing file and thumbnail: {original_thumbnail}")
        thumbnail_url = original_thumbnail
        download_needed = False
    else:
        # Need to download to get thumbnail
        logging.info("Downloading video to get thumbnail...")
        try:
            file_path, thumbnail_url, raw_metadata = await download_audio(download_query)
            await state.update_data(
                file_path=file_path,
                original_thumbnail_url=thumbnail_url
            )
            download_needed = False
        except Exception as e:
            logging.error(f"Download failed in thumbnail function: {e}")
            await callback_query.message.answer("❌ Failed to download video. Please try again.")
            return
    
    if thumbnail_url:
        # Download and process the thumbnail
        cover_path = await download_and_process_cover(thumbnail_url)
        if cover_path:
            # Update state with new cover
            await state.update_data(cover_url=thumbnail_url, cover_path=cover_path)
            
            # Send the thumbnail image to user
            try:
                with open(cover_path, 'rb') as photo_file:
                    photo_data = photo_file.read()
                
                status_text = "✅ Audio already downloaded!" if not download_needed else "⬇️ Downloading audio now..."
                
                await callback_query.message.answer_photo(
                    photo=BufferedInputFile(photo_data, filename="youtube_cover.jpg"),
                    caption=(
                        f"🎵 **Using YouTube thumbnail as cover**\n\n"
                        f"**Song:** {user_data.get('title', song_query)}\n"
                        f"**Artist:** {user_data.get('artist', 'Various Artists')}\n"
                        f"**Album:** {user_data.get('album', 'Unknown Album')}\n\n"
                        f"{status_text}"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Error sending YouTube thumbnail: {e}")
                await callback_query.message.answer("✅ Using YouTube thumbnail as cover.")
            
            # Proceed to lyrics if we have the file, otherwise download first
            if not download_needed:
                # Request lyrics directly
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
                ])

                await callback_query.message.answer(
                    f"📝 **Please send lyrics now**\n"
                    f"(Paste text or attach text file)",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                await state.set_state(MusicStates.waiting_lyrics)
            else:
                await download_and_request_lyrics(callback_query.message, state, skip_cover=True)
            return
        else:
            logging.error(f"Failed to process thumbnail: {thumbnail_url}")
    
    # If no YouTube thumbnail found or processing failed
    logging.error(f"No YouTube thumbnail found for query: {song_query}")
    await callback_query.message.answer("❌ No cover art available. Proceeding without cover.")
    
    # Clear cover data and ensure we have audio file
    await state.update_data(cover_url=None, cover_path=None)
    
    if not user_data.get('file_path'):
        await download_and_request_lyrics(callback_query.message, state, skip_cover=True)
    else:
        # Already have file, go to lyrics
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
        ])

        await callback_query.message.answer(
            f"📝 **Please send lyrics now**\n"
            f"(Paste text or attach text file)",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        await state.set_state(MusicStates.waiting_lyrics)

async def download_and_request_lyrics(message: types.Message, state: FSMContext, skip_cover=False):
    user_data = await state.get_data()
    song_query = user_data['song_query']
    download_query = user_data.get('download_query', song_query)

    # Check if we already have the file downloaded
    existing_file = user_data.get('file_path')
    if existing_file and os.path.exists(existing_file):
        logging.info("Using existing downloaded file")
        file_path = existing_file
        thumbnail_url = user_data.get('original_thumbnail_url')
    else:
        # Download the file
        dl_msg = await message.answer("⬇️ Downloading audio...")
        
        try:
            file_path, thumbnail_url, raw_metadata = await download_audio(download_query)
            await bot.delete_message(message.chat.id, dl_msg.message_id)
        except Exception as e:
            logging.error(f"Download error: {e}")
            await bot.delete_message(message.chat.id, dl_msg.message_id)
            await message.answer("❌ Download failed. Try a different song name.")
            return

    parsed_metadata = {
        'artist': user_data.get('artist', 'Various Artists'),
        'title': user_data.get('title', song_query),
        'album': user_data.get('album', 'Unknown Album')
    }

    # Store everything in state
    await state.update_data(
        file_path=file_path,
        artist=parsed_metadata['artist'],
        title=parsed_metadata['title'],
        album=parsed_metadata['album'],
        original_thumbnail_url=thumbnail_url
    )

    # Only handle cover if skip_cover is False AND no cover is already set
    if not skip_cover and not user_data.get('cover_path') and thumbnail_url:
        cover_path = await download_and_process_cover(thumbnail_url)
        if cover_path:
            await state.update_data(cover_url=thumbnail_url, cover_path=cover_path)
            
            with open(cover_path, 'rb') as photo_file:
                photo_data = photo_file.read()

            await message.answer_photo(
                photo=BufferedInputFile(photo_data, filename="cover.jpg"),
                caption=(
                    f"🎵 **Downloaded successfully!**\n\n"
                    f"**Song:** {parsed_metadata['title']}\n"
                    f"**Artist:** {parsed_metadata['artist']}\n"
                    f"**Album:** {parsed_metadata['album']}"
                ),
                parse_mode="Markdown"
            )

    # Request lyrics
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
    ])

    await message.answer(
        f"📥 **Ready for lyrics!**\n\n"
        f"**Song:** {parsed_metadata['title']}\n"
        f"**Artist:** {parsed_metadata['artist']}\n"
        f"**Album:** {parsed_metadata['album']}\n\n"
        f"📝 **Please send lyrics now**\n"
        f"(Paste text or attach text file)",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

    await state.set_state(MusicStates.waiting_lyrics)

@dp.message(MusicStates.waiting_lyrics)
async def handle_user_lyrics(message: types.Message, state: FSMContext):
    if message.text and message.text.startswith('/cancel'):
        await cancel_command(message, state)
        return

    if message.document and message.document.mime_type == 'text/plain':
        try:
            file = await bot.get_file(message.document.file_id)
            file_content = await bot.download_file(file.file_path)
            lyrics = file_content.read().decode('utf-8').strip()
        except Exception as e:
            logging.error(f"File read error: {e}")
            await message.answer("Failed to read file. Please paste lyrics directly.")
            return
    elif message.text:
        lyrics = message.text.strip()
    else:
        await message.answer("Please send lyrics as text or text file.")
        return

    if len(lyrics) < 20:
        await message.answer("Lyrics too short. Please send complete lyrics.")
        return

    await process_lyrics(message, state, lyrics)

@dp.callback_query(MusicStates.waiting_lyrics)
async def handle_lyrics_cancel(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.data == 'cancel':
        await bot.answer_callback_query(callback_query.id)
        user_data = await state.get_data()
        await cleanup_user_files(user_data)
        await state.clear()
        await callback_query.message.answer("Cancelled. Use /start to begin again.")

async def process_lyrics(message: types.Message, state: FSMContext, lyrics: str):
    user_data = await state.get_data()
    processing_msg = await message.answer("🎵 Processing song...")

    try:
        artist = user_data.get('artist', 'Various Artists')
        album = user_data.get('album', 'Unknown Album')
        title = user_data.get('title', user_data['song_query'])
        cover_path = user_data.get('cover_path')

        final_path = await embed_metadata(
            user_data['file_path'],
            lyrics,
            artist,
            album,
            title,
            cover_path
        )

        # Get file size and duration for audio metadata
        try:
            audio = MP3(final_path)
            duration = int(audio.info.length) if audio.info.length else 0
        except:
            duration = 0

        file_size = os.path.getsize(final_path)
        
        # Send as AUDIO instead of DOCUMENT for direct playback
        if file_size < 50 * 1024 * 1024:  # Less than 50MB
            try:
                # Use cover as thumbnail if available
                thumbnail_data = None
                if cover_path and os.path.exists(cover_path):
                    try:
                        with open(cover_path, 'rb') as thumb_file:
                            thumbnail_data = BufferedInputFile(thumb_file.read(), filename="thumb.jpg")
                    except:
                        thumbnail_data = None

                final_file = FSInputFile(final_path, filename=f"{title}.mp3")
                
                # Send as audio for direct playback in Telegram
                await message.answer_audio(
                    audio=final_file,
                    title=title,
                    performer=artist,
                    duration=duration,
                    thumbnail=thumbnail_data,
                    caption=(
                        f"🎵 **{title}** by **{artist}**\n\n"
                        f"📀 **Album:** {album}\n"
                        f"📝 **Lyrics embedded**\n"
                        f"🏷️ **Metadata added**\n"
                        f"📱 **Samsung Music compatible**\n\n"
                        f"🎧 **Tap to play directly in Telegram!**"
                    ),
                    parse_mode="Markdown"
                )
                
            except Exception as audio_error:
                logging.error(f"Audio send error: {audio_error}")
                # Fallback to document if audio fails
                final_file = FSInputFile(final_path, filename=f"{title}.mp3")
                await message.answer_document(
                    document=final_file,
                    caption=f"🎵 **{title}** by **{artist}** (Download to play)",
                    parse_mode="Markdown"
                )
        else:
            # File too large for audio, send as document
            final_file = FSInputFile(final_path, filename=f"{title}.mp3")
            await message.answer_document(
                document=final_file,
                caption=f"🎵 **{title}** by **{artist}** (File too large for direct playback)",
                parse_mode="Markdown"
            )

        # Cleanup
        await cleanup_user_files(user_data)
        if os.path.exists(final_path):
            try:
                os.unlink(final_path)
            except:
                pass

        await bot.delete_message(message.chat.id, processing_msg.message_id)
        await state.clear()

    except Exception as e:
        logging.error(f"Processing error: {e}")
        await bot.delete_message(message.chat.id, processing_msg.message_id)
        await message.answer("❌ Processing failed. Please try again.")
        await state.clear()

@dp.message()
async def handle_general_message(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state is None:
        if message.text and not message.text.startswith('/'):
            await state.update_data(song_query=message.text.strip())
            await state.set_state(MusicStates.waiting_song)
            await handle_song_request(message, state)
        else:
            await message.answer("Use /start to begin, or /help for assistance!")

# ================== HELPER FUNCTIONS ================== #
async def search_cover_art(query):
    """Search cover art from APIs"""
    try:
        params = {'term': query, 'media': 'music', 'limit': 3}
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        response = requests.get(config.ITUNES_API, params=params, timeout=10, headers=headers)
        if response.status_code == 200:
            data = response.json()
            for result in data.get('results', []):
                cover_url = result['artworkUrl100'].replace('100x100', '600x600')
                return cover_url, {
                    'artist': result['artistName'],
                    'album': result['collectionName'],
                    'title': result['trackName']
                }

        params = {'q': query}
        response = requests.get(config.DEEZER_API, params=params, timeout=10, headers=headers)
        if response.status_code == 200:
            data = response.json()
            for track in data.get('data', [])[:3]:
                cover_url = track['album']['cover_xl']
                return cover_url, {
                    'artist': track['artist']['name'],
                    'album': track['album']['title'],
                    'title': track['title']
                }

    except Exception as e:
        logging.error(f"Cover search error: {e}")
    
    return None, {}

async def get_youtube_cover(query):
    """Get YouTube thumbnail"""
    try:
        s = Search(query)
        if not s.videos:
            return None, {}

        video = s.videos[0]
        thumbnail = video.thumbnail_url
        if thumbnail:
            if 'maxresdefault' not in thumbnail:
                thumbnail = thumbnail.replace('hqdefault', 'maxresdefault')
            return thumbnail, {}

        return None, {}
    except Exception as e:
        logging.warning(f"YouTube cover skipped (non-critical): {e}")
        return None, {}

async def download_and_process_cover(url):
    """Download and process cover art with enhanced error handling"""
    try:
        # Enhanced headers to avoid blocks
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        # Try multiple approaches to download
        response = None
        
        # Method 1: Direct download
        try:
            response = requests.get(url, timeout=15, headers=headers, stream=True)
            logging.info(f"Thumbnail response status: {response.status_code}")
            
            if response.status_code != 200:
                # Method 2: Try without maxresdefault (fallback to lower quality)
                if 'maxresdefault' in url:
                    fallback_url = url.replace('maxresdefault', 'hqdefault')
                    logging.info(f"Trying fallback URL: {fallback_url}")
                    response = requests.get(fallback_url, timeout=15, headers=headers, stream=True)
                
                if response.status_code != 200:
                    logging.error(f"HTTP {response.status_code} for thumbnail: {url}")
                    return None
                    
        except requests.RequestException as e:
            logging.error(f"Network error downloading thumbnail: {e}")
            return None

        # Check content type
        content_type = response.headers.get('content-type', '')
        if not content_type.startswith('image/'):
            logging.error(f"Invalid content type: {content_type} for URL: {url}")
            return None

        # Read image content
        image_content = response.content
        if len(image_content) < 1000:  # Too small to be a valid image
            logging.error(f"Image too small ({len(image_content)} bytes): {url}")
            return None

        # Process image with PIL
        try:
            img = Image.open(BytesIO(image_content))
            
            # Convert to RGB if necessary
            if img.mode in ['RGBA', 'LA', 'P']:
                img = img.convert('RGB')
            
            # Resize if too large
            if img.width > 1000 or img.height > 1000:
                img.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                
            # Ensure minimum size (some music players require this)
            if img.width < 200 or img.height < 200:
                img = img.resize((300, 300), Image.Resampling.LANCZOS)

            # Save with proper directory creation
            os.makedirs('covers', exist_ok=True)
            cover_path = f"covers/{uuid.uuid4().hex}.jpg"
            
            # Save with optimized settings
            img.save(cover_path, format='JPEG', quality=90, optimize=True)
            
            # Verify file was created and has content
            if os.path.exists(cover_path) and os.path.getsize(cover_path) > 1000:
                logging.info(f"Successfully processed thumbnail: {cover_path}")
                return cover_path
            else:
                logging.error(f"File creation failed: {cover_path}")
                return None
                
        except UnidentifiedImageError:
            logging.error(f"Invalid image format from URL: {url}")
            return None
        except Exception as img_error:
            logging.error(f"Image processing error: {img_error}")
            return None

    except Exception as e:
        logging.error(f"Cover processing error for {url}: {e}")
        return None

async def download_audio(query):
    """
    Download audio using yt-dlp.
    Layer 1: YouTube with PO Token + cookies + Node.js (highest quality, correct results)
    Layer 2: SoundCloud fallback (zero IP blocks, but less accurate results)
    """
    import asyncio
    import uuid
    import yt_dlp
    
    safe_query = re.sub(r'[\\/*?:"<>|]', "", query)[:50]
    unique_id = uuid.uuid4().hex[:8]
    thumbnail_url = None

    # Ensure downloads directory exists
    os.makedirs("downloads", exist_ok=True)

    outtmpl = f"downloads/{safe_query}_{unique_id}.%(ext)s"

    def _find_output_file(entry, safe_q, uid):
        """4-method file finder to locate the final MP3 after yt-dlp + FFmpeg processing."""
        # Method 1: yt-dlp's own tracking
        requested = entry.get('requested_downloads', [])
        if requested:
            final_path = requested[0].get('filepath')
            if final_path and os.path.exists(final_path) and os.path.getsize(final_path) > 0:
                logging.info(f"Found via requested_downloads: {final_path}")
                return final_path

        # Method 2: Expected path
        mp3_path = f"downloads/{safe_q}_{uid}.mp3"
        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
            logging.info(f"Found via expected path: {mp3_path}")
            return mp3_path
            
        # Method 3: Glob with unique_id
        for fp in glob.glob(f"downloads/{safe_q}_{uid}.*"):
            if os.path.getsize(fp) > 0:
                logging.info(f"Found via glob: {fp}")
                return fp

        # Method 4: Newest file in downloads/
        all_files = glob.glob("downloads/*.*")
        if all_files:
            newest = max(all_files, key=os.path.getmtime)
            if os.path.getsize(newest) > 0:
                logging.info(f"Found via newest fallback: {newest}")
                return newest
        return None

    def _extract_metadata(entry, fallback_query, source_name):
        """Extract title, artist, thumbnail from yt-dlp info dict."""
        title = entry.get('title', fallback_query)
        artist = entry.get('uploader', 'Unknown Artist')
        thumb = entry.get('thumbnail')
        meta = {'artist': artist, 'title': title, 'album': source_name}
        return title, artist, thumb, meta

    # ======== LAYER 1: YouTube (PO Token + Cookies + Node.js) ========
    try:
        logging.info(f"[Layer 1] Attempting YouTube download for: {query}")

        # Use yt-dlp's native YouTube search to get the most accurate result
        yt_urls = [f"ytsearch1:{query}"]

        yt_opts = {
            'format': 'bestaudio*/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': outtmpl,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 2,
            'source_address': '0.0.0.0',
            'cookiefile': 'cookies.txt',
            'logger': YTDLLogger(),
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
            'extractor_args': {
                'youtube': {
                    'player_client': ['mweb', 'tv'],
                }
            },
            # Enable EJS challenge solver script download from GitHub
            'remote_components': ['ejs:github'],
        }

        # Inject Node.js runtime for JavaScript challenge solving
        if NODE_BIN:
            yt_opts['js_runtimes'] = {'node': {'path': NODE_BIN}}

        # Inject PO Token server if running (use new extractor arg name)
        if _pot_server_process is not None:
            yt_opts.setdefault('extractor_args', {})['youtubepot-bgutilhttp'] = {
                'base_url': ['http://127.0.0.1:4416']
            }

        # Pre-download the EJS challenge solver script if not already done
        try:
            import subprocess
            _ejs_check = subprocess.run(
                ['yt-dlp', '--remote-components', 'ejs:github', '--version'],
                capture_output=True, text=True, timeout=30
            )
            logging.info(f"EJS pre-download: rc={_ejs_check.returncode}")
        except Exception:
            pass

        for yt_url in yt_urls:
            try:
                def run_yt():
                    with yt_dlp.YoutubeDL(yt_opts) as ydl:
                        return ydl.extract_info(yt_url, download=True)

                info = await asyncio.to_thread(run_yt)

                if info and 'entries' in info:
                    entry = (info.get('entries') or [{}])[0] or {}
                else:
                    entry = info or {}

                title, artist, thumb, meta = _extract_metadata(entry, query, 'YouTube')
                if thumb:
                    thumbnail_url = thumb

                logging.info(f"[Layer 1] YouTube extracted: {title}")

                found = _find_output_file(entry, safe_query, unique_id)
                if found:
                    return found, thumbnail_url, meta

            except Exception as yt_err:
                logging.warning(f"[Layer 1] YouTube failed for {yt_url}: {str(yt_err)[:120]}")
                continue

        logging.warning("[Layer 1] All YouTube URLs exhausted. Falling back to SoundCloud...")

    except Exception as layer1_err:
        logging.warning(f"[Layer 1] YouTube layer crashed: {layer1_err}")

    # ======== LAYER 2: SoundCloud Fallback ========
    try:
        logging.info(f"[Layer 2] Attempting SoundCloud download for: {query}")

        # Generate new unique ID for SoundCloud to avoid filename collisions
        unique_id_sc = uuid.uuid4().hex[:8]
        outtmpl_sc = f"downloads/{safe_query}_{unique_id_sc}.%(ext)s"

        sc_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': outtmpl_sc,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
            'source_address': '0.0.0.0',
            'logger': YTDLLogger(),
        }

        def run_sc():
            with yt_dlp.YoutubeDL(sc_opts) as ydl:
                return ydl.extract_info(f"scsearch1:{query}", download=True)

        info = await asyncio.to_thread(run_sc)

        if info and 'entries' in info:
            entry = (info.get('entries') or [{}])[0] or {}
        else:
            entry = info or {}

        title, artist, thumb, meta = _extract_metadata(entry, query, 'SoundCloud')
        if thumb:
            thumbnail_url = thumb

        logging.info(f"[Layer 2] SoundCloud extracted: {title}")

        found = _find_output_file(entry, safe_query, unique_id_sc)
        if found:
            return found, thumbnail_url, meta

        raise Exception("SoundCloud download succeeded but MP3 file not found on disk.")

    except Exception as layer2_err:
        logging.error(f"[Layer 2] SoundCloud also failed: {layer2_err}")
        raise Exception(f"All download layers failed for '{query}'. YouTube and SoundCloud both exhausted.")

async def embed_metadata(file_path, lyrics, artist, album, title, cover_path=None):
    """Embed metadata into MP3"""
    try:
        audio = MP3(file_path, ID3=ID3)
        
        try:
            audio.add_tags()
        except Exception:
            pass

        audio.tags.add(TIT2(encoding=Encoding.UTF16, text=title))
        audio.tags.add(TPE1(encoding=Encoding.UTF16, text=artist))
        audio.tags.add(TALB(encoding=Encoding.UTF16, text=album))
        audio.tags.add(USLT(encoding=Encoding.UTF16, lang='eng', desc='Lyrics', text=lyrics))

        if cover_path and os.path.exists(cover_path):
            try:
                with open(cover_path, 'rb') as f:
                    cover_data = f.read()
                    
                audio.tags.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='Cover',
                    data=cover_data
                ))
            except Exception as e:
                logging.error(f"Cover embedding error: {e}")

        audio.save()

        final_filename = re.sub(r'[\\/*?:"<>|]', "", f"{artist} - {title}.mp3")
        final_path = f"processed/{final_filename}"
        
        os.makedirs('processed', exist_ok=True)
        shutil.move(file_path, final_path)
        
        return final_path

    except Exception as e:
        logging.error(f"Metadata embedding error: {e}")
        raise Exception(f"Failed to embed metadata: {str(e)}")

@dp.errors()
async def error_handler(event, exception):
    logging.error(f"Error: {exception}", exc_info=True)
    
    if hasattr(event, 'message') and event.message:
        try:
            await event.message.answer("Unexpected error. Please try again.")
        except:
            pass
    elif hasattr(event, 'callback_query') and event.callback_query:
        try:
            await event.callback_query.message.answer("Unexpected error. Please try again.")
        except:
            pass
    
    return True

async def main():
    if os.environ.get('RENDER'):
        # Get environment variables
        WEBHOOK_HOST = os.getenv('RENDER_EXTERNAL_URL', 'https://your-app-name.onrender.com')
        WEBHOOK_PATH = f"/webhook/{config.TOKEN}"
        WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
        
        # Set webhook
        await bot.set_webhook(WEBHOOK_URL)
        
        # Create aiohttp application
        app = web.Application()
        
        # Create handler
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
        )
        
        # Register webhook handler
        webhook_requests_handler.register(app, path=WEBHOOK_PATH)
        
        # Setup application
        setup_application(app, dp, bot=bot)
        
        # Add a simple health check route
        async def health_check(request):
            return web.Response(text="Bot is running!")
        
        app.router.add_get('/', health_check)
        
        # Get port from environment (Render provides this)
        port = int(os.environ.get("PORT", 8000))
        
        # Start server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        
        logging.info(f"Bot started on port {port}")
        logging.info(f"Webhook URL: {WEBHOOK_URL}")
        
        # Keep running
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
    else:
        # Local polling mode
        logging.info("Starting bot in local polling mode...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped")
