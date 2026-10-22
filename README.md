<div align="center">

# 🕰️ KRONOS 🕰️

### *Your Telegram account, always awake.*

![PYTHON](https://img.shields.io/badge/PYTHON-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![DOCKER](https://img.shields.io/badge/DOCKER-READY-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![AI](https://img.shields.io/badge/AI-GROQ%20%7C%20OPENROUTER-F55036?style=for-the-badge)
![STATUS](https://img.shields.io/badge/STATUS-ACTIVE-2ea44f?style=for-the-badge)
![LICENSE](https://img.shields.io/badge/LICENSE-MIT-blue?style=for-the-badge)

</div>

---

## 🇬🇧 English

**Kronos** is a Telegram userbot that keeps your profile alive with a live clock, answers your chats with AI while you're away, and guards your inbox from abuse — all running from your own account.

### ✨ What it does

| ✦ | Feature |
|---|---|
| 🕐 | Live bold clock in your last name (12h / 24h, any timezone) |
| 🎭 | Rotating bio that changes every few minutes |
| 🤖 | AI replies in your name — only while **you're away** (smart presence) |
| 🛡️ | Auto-moderation: warnings → block, for text, images, videos and edits |
| 🧠 | Per-person memory: facts, summaries, mood and time-gap awareness |
| 🎙️ | Voice notes understood (speech-to-text) |
| 🔎 | Optional web search for fresh answers |
| 🎭 | Replies with **your own** stickers |
| 👑 | VIP list — never warned, never blocked |
| 🎛️ | Owner-only commands + optional button control bot |
| 🔁 | Multi-model fallbacks (Groq / OpenRouter / any LiteLLM model) |

All of it is configured once in `.env` — no code edits needed.

### 📋 Requirements

| What | Where to get it |
|---|---|
| Python **3.10+** (or Docker) | [python.org](https://www.python.org) |
| `API_ID` + `API_HASH` | [my.telegram.org](https://my.telegram.org) |
| `SESSION_STRING` | run `python generate_session.py` |
| `OWNER_ID` | your numeric Telegram ID (e.g. via @userinfobot) |
| `GROQ_API_KEY` *(free tier works)* | [console.groq.com](https://console.groq.com) |
| `OPENROUTER_API_KEY` *(optional)* | [openrouter.ai](https://openrouter.ai) |
| `BRAVE_API_KEY` *(optional, web search)* | [brave.com/search/api](https://brave.com/search/api) |
| `CONTROL_BOT_TOKEN` *(optional, buttons)* | [@BotFather](https://t.me/BotFather) |

### 🚀 Quick Start

```bash
# 1. Get the project & install
git clone https://github.com/mohsenAITaie/Kronos.git
cd Kronos
pip install -r requirements.txt

# 2. Generate your session (run locally, keep it secret)
python generate_session.py

# 3. Configure
cp .env.example .env
cp persona.example.json persona.json
nano .env          # fill API_ID, API_HASH, SESSION_STRING, OWNER_ID, GROQ_API_KEY
nano persona.json  # describe yourself — the AI speaks as you

# 4. Run
python main.py
```

### 🐳 Docker

```bash
docker build -t kronos .
docker run -d --name kronos --restart unless-stopped \
  --env-file .env -v "$(pwd)/data:/app/data" kronos
```

> 📌 On Railway, add a **Volume mounted at `/app/data`** — otherwise memory, warnings and bans are erased on every deploy.

### 📱 Termux (advanced)

```bash
pkg update -y && pkg upgrade -y
pkg install python git ffmpeg libjpeg-turbo -y
```

Then follow steps 1–4 above. Keep it alive with `tmux new -s kronos` → `python main.py` → `Ctrl+B` then `D`.
Some packages (`av`, `Pillow`) can be hard to build on Termux — a VPS or Docker is the smoother route.

### 🎛️ Owner commands

Send these from your own account:

| Command | Does |
|---|---|
| `.ai on\|off` | Turn AI replies on/off (protection stays on) |
| `.wake` / `.presence` | Wake the AI now / see presence state |
| `.vip` | Manage VIP list |
| `.bans` `.warns` `.unwarn` `.who` | Review moderation |
| `.memory` `.forget <id>` | Inspect or erase a person's memory |
| `.model` `.models` | Switch / check AI models |
| `.draft on\|off` | Draft mode (nothing is sent automatically) |
| `.stats` `.health` `.time` `.bio` | Status & info |

### 🔒 Security — read this

- `SESSION_STRING` = full access to your account. **Never** commit it, post it, or screenshot it.
- `.env`, `*.session` and `data/` are already in `.gitignore`. Keep it that way.
- `persona.json` is personal. Publish `persona.example.json` only, never your real one.
- Userbots can break Telegram's rules if misused. Use responsibly and at your own risk.

---

<div align="center">

## 🌐 Connect

[![YouTube](https://img.shields.io/badge/YOUTUBE-SUBSCRIBE-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://youtube.com/@m7jn)
[![Instagram](https://img.shields.io/badge/INSTAGRAM-FOLLOW-E4405F?style=for-the-badge&logo=instagram&logoColor=white)](https://www.instagram.com/z0_28)
[![TikTok](https://img.shields.io/badge/TIKTOK-FOLLOW-000000?style=for-the-badge&logo=tiktok&logoColor=white)](https://www.tiktok.com/@zt_tb)
[![Telegram](https://img.shields.io/badge/TELEGRAM-@K0__MG-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/K0_MG)

</div>

---

<div dir="rtl">

## 🇮🇶 العربية

**Kronos** بوت تليجرام يشتغل من حسابك: يحوّل بروفايلك لواجهة حية بساعة وبايو متغيّر، يرد على الدردشات بالذكاء الاصطناعي وانت مو موجود، ويحمي الخاص من الإساءة.

### ✨ شنو يسوي؟

| ✦ | الميزة |
|---|---|
| 🕐 | ساعة حية بأرقام عريضة داخل اسمك الأخير (12 أو 24 ساعة، أي توقيت) |
| 🎭 | بايو يتغير تلقائيًا كل كم دقيقة |
| 🤖 | ردود AI باسمك — فقط **وانت غايب** (حضور ذكي) |
| 🛡️ | حماية تلقائية: تحذير ثم حظر، للنص والصور والفيديو والتعديلات |
| 🧠 | ذاكرة لكل شخص: معلومات وملخصات ومزاجه والفترة من آخر حجي |
| 🎙️ | يفهم الرسائل الصوتية (تحويل صوت لنص) |
| 🔎 | بحث ويب اختياري للأجوبة الحديثة |
| 🎭 | يرد بـ**ستيكراتك** انت |
| 👑 | قائمة VIP — ما ينحذرون وما ينحظرون أبدًا |
| 🎛️ | أوامر للمالك فقط + بوت تحكم بأزرار (اختياري) |
| 🔁 | عدة موديلات احتياطية (Groq / OpenRouter / أي موديل LiteLLM) |

كل شي يتضبط مرة وحدة من ملف `.env` — بدون ما تعدّل الكود.

### 📋 المتطلبات

| الشي | من وين تجيبه |
|---|---|
| بايثون **3.10+** (أو Docker) | [python.org](https://www.python.org) |
| `API_ID` و `API_HASH` | [my.telegram.org](https://my.telegram.org) |
| `SESSION_STRING` | شغّل `python generate_session.py` |
| `OWNER_ID` | ايديك الرقمي بتليجرام (مثلًا من @userinfobot) |
| `GROQ_API_KEY` *(المجاني يكفي)* | [console.groq.com](https://console.groq.com) |
| `OPENROUTER_API_KEY` *(اختياري)* | [openrouter.ai](https://openrouter.ai) |
| `BRAVE_API_KEY` *(اختياري — بحث ويب)* | [brave.com/search/api](https://brave.com/search/api) |
| `CONTROL_BOT_TOKEN` *(اختياري — أزرار)* | [@BotFather](https://t.me/BotFather) |

### 🚀 التشغيل السريع

```bash
# 1. نزّل المشروع وثبّت المكتبات
git clone https://github.com/mohsenAITaie/Kronos.git
cd Kronos
pip install -r requirements.txt

# 2. سوّي الجلسة (شغّلها عندك، وخليها سر)
python generate_session.py

# 3. اضبط الإعدادات
cp .env.example .env
cp persona.example.json persona.json
nano .env          # عبّي API_ID و API_HASH و SESSION_STRING و OWNER_ID و GROQ_API_KEY
nano persona.json  # عرّف نفسك — الـAI يحجي كأنه انت

# 4. شغّله
python main.py
```

### 🐳 دوكر

```bash
docker build -t kronos .
docker run -d --name kronos --restart unless-stopped \
  --env-file .env -v "$(pwd)/data:/app/data" kronos
```

> 📌 على Railway ضيف **Volume على المسار `/app/data`** — وإلا الذاكرة والتحذيرات والحظر تنمسح مع كل نشر.

### 📱 ترمكس (متقدم)

```bash
pkg update -y && pkg upgrade -y
pkg install python git ffmpeg libjpeg-turbo -y
```

بعدها كمّل الخطوات 1–4. وخليه شغال بالخلفية: `tmux new -s kronos` ← `python main.py` ← `Ctrl+B` ثم `D`.
بعض المكتبات (`av` و `Pillow`) أحيانًا تتعب بالتثبيت على ترمكس — السيرفر أو دوكر أسهل.

### 🎛️ أوامر المالك

ترسلها من حسابك أنت:

| الأمر | شنو يسوي |
|---|---|
| `.ai on\|off` | تشغيل/إيقاف ردود الـAI (الحماية تبقى شغالة) |
| `.wake` / `.presence` | صحّي الـAI فورًا / حالة حضورك |
| `.vip` | إدارة قائمة المميزين |
| `.bans` `.warns` `.unwarn` `.who` | مراجعة الحماية |
| `.memory` `.forget <id>` | عرض أو مسح ذاكرة شخص |
| `.model` `.models` | تغيير/فحص الموديلات |
| `.draft on\|off` | وضع المسودة (ما يرسل شي تلقائيًا) |
| `.stats` `.health` `.time` `.bio` | حالة ومعلومات |

### 🔒 الأمان — اقرأها

- `SESSION_STRING` يعني تحكم كامل بحسابك. **لا** ترفعه ولا تنشره ولا تصوّره.
- `.env` و `*.session` و `data/` موجودة بـ `.gitignore`. لا تشيلها.
- `persona.json` شخصي. انشر `persona.example.json` فقط، مو ملفك الحقيقي.
- اليوزربوت ممكن يخالف قوانين تليجرام إذا انستخدم غلط. استخدمه بمسؤوليتك.

</div>

---

<div align="center">

## 🌐 Connect

[![YouTube](https://img.shields.io/badge/YOUTUBE-SUBSCRIBE-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://youtube.com/@m7jn)
[![Instagram](https://img.shields.io/badge/INSTAGRAM-FOLLOW-E4405F?style=for-the-badge&logo=instagram&logoColor=white)](https://www.instagram.com/z0_28)
[![TikTok](https://img.shields.io/badge/TIKTOK-FOLLOW-000000?style=for-the-badge&logo=tiktok&logoColor=white)](https://www.tiktok.com/@zt_tb)
[![Telegram](https://img.shields.io/badge/TELEGRAM-@K0__MG-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/K0_MG)

**📩 Support / التواصل:** Telegram [@K0_MG](https://t.me/K0_MG)

</div>
