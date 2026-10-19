# 🌌 Vayom Drive

> **Turn Telegram into an infinite, zero-cost, zero-knowledge personal cloud storage with the sleek duality of Google Photos & Google Drive.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![React: 18](https://img.shields.io/badge/Frontend-React%2018%20+%20Vite-61DAFB.svg?logo=react&logoColor=black)](https://vitejs.dev)
[![Storage: Telegram | S3 | NAS](https://img.shields.io/badge/Storage-Telegram%20%7C%20S3%20%7C%20NAS-0088cc.svg?logo=telegram&logoColor=white)](https://telegram.org)

---

## 💡 What is Vayom Drive?

Every major cloud provider charges you recurring monthly fees to store your own files, while scanning your photos and locking your data behind walled gardens.

**Vayom Drive transforms Telegram's free, distributed cloud infrastructure into your own private, unlimited personal drive.** 

Instead of dealing with clunky Telegram bots or scrolling through endless chat channels to find your media, Vayom Drive wraps Telegram’s bottomless storage in a polished **Google Photos-style media timeline** and a **Google Drive-style folder hierarchy**. 

Your files are encrypted on your device before they touch the internet. And for power users, Vayom Drive bridges seamlessly with your **home NAS (Synology, TrueNAS, Unraid)** and **S3 / Cloudflare R2 / MinIO** buckets under one unified dashboard.

---
✨ Features

☁️ Storage Without the Storage Bill

Telegram-backed storage: Store files remotely using Telegram as a backend, without a recurring storage subscription from Vayom Drive itself.

Multiple storage adapters: Bring together Telegram, local disks, NAS, S3-compatible storage (including Cloudflare R2 and AWS S3), and Nextcloud.

Large-file support: Automatically split files into manageable parts and reconstruct them when needed, subject to backend limits. Because files don't get smaller just because a platform says so.

Watch-folder cataloging: Automatically catalog changes in supported local folders without manually importing every file.

🛡️ Smarter Transfers, Fewer Headaches

Adaptive anti-flood protection: Upload queues, rate limiting, and automatic pauses help Vayom Drive respect Telegram API limits and reduce flood-limit errors. No magic ban-proof button exists, unfortunately.

Folder drag and drop: Import complete directory trees instead of uploading files one lonely document at a time.

Built-in ZIP packaging: Package folders into .zip archives directly in the app when a single archive makes more sense.

🔐 Privacy Is Not an Afterthought

Client-side encryption: Encrypt files using AES-256-CTR with keys derived from your master passphrase before uploading them.

Private file contents: Properly implemented client-side encryption keeps the storage provider from reading the plaintext contents of encrypted files without the relevant key.

Security depends on correct key derivation, IV/nonce management, and implementation. AES-CTR alone does not provide tamper detection.

🎞️ One Library, Two Ways to Browse

Photos Mode: Browse media chronologically with capture dates and available EXIF camera metadata. Find that picture from three years ago without scrolling through a thousand filenames.

Drive Mode: Navigate folders, manage directories, and perform bulk file operations through a familiar file-tree interface.

Video streaming and seeking: Use HTTP Range requests for supported media and storage backends to seek through videos without downloading the entire file first. Playback quality and responsiveness depend on the browser, format, network, and backend.

Media organization: Keep photos, videos, documents, and other files accessible from one interface rather than scattering them across unrelated tools.

🎧 A Little More Than File Storage

Audio player: Play FLAC, MP3, and WAV files through a floating player while continuing to browse your library.

PDF viewer: Open multi-page PDF documents in-app, with presentation zoom for more comfortable reading.
## 📖 User Guide

### 1. Connecting Your Telegram Account
1. Launch Vayom Drive and open `http://localhost:5173`.
2. Enter your phone number (including country code, e.g., `+1234567890`).
3. Enter the official verification code received inside your Telegram app.
4. If you have Telegram Two-Step Verification (cloud password) enabled, enter it securely when prompted.
5. Vayom Drive establishes a secure local MTProto session. Your credentials and session strings live strictly on your host machine.

### 2. Dual-Mode Interface: Photos vs. Drive
- **Photos Mode**: Click the **Photos** toggle in the top bar to view your entire media library arranged in a responsive, infinite-scroll chronological timeline. Filter assets by camera model, lens, video codec, or resolution. Click any image or video to open the fullscreen lightbox.
- **Drive Mode**: Click the **Drive** toggle to switch into a traditional file manager. Create folders, organize project documents, navigate with breadcrumb paths, and view storage metrics.

### 3. Uploading Files & Directories
- **Single or Multiple Files**: Click **Upload** or drag files anywhere onto the window.
- **Uploading Folders**: Click the **Folder** button or drag an entire folder into the browser. Vayom automatically detects the folder and prompts you:
  - **Upload as Folder**: Automatically creates a matching Album and enqueues all nested files with direct album associations.
  - **Compress to .ZIP Archive**: Packages the folder tree into a single compressed `.zip` archive on your device before uploading.
- **Choosing Destination**: Use the storage pills in the header to direct your upload to **Telegram**, **NAS**, or **S3**.

### 4. Media Streaming & Audio Studio
- **4K Video Playback**: Click any video to stream instantly. Scrubbing to any point in the timeline seeks natively via HTTP Range requests without downloading the entire file.
- **Floating Audio Player**: Click any audio track (MP3, FLAC, WAV). A floating player opens at the bottom of the screen, allowing you to browse, search, and manage your vault while music plays in the background.
- **Document Viewing**: Click any PDF to open the embedded reader with page navigation, zoom controls, and print/download shortcuts.

### 5. Managing Your Zero-Knowledge Vault
- In **Settings**, configure your vault encryption passphrase.
- When enabled, files are encrypted on your device prior to upload using AES-256-CTR with PBKDF2 key derivation.
- Lock your vault at any time to restrict access to encrypted assets. Locked files return HTTP 423 until unlocked with your master key.

### 6. Adding Local NAS & S3 Storage
- Open **Settings** to add an S3-compatible provider (Cloudflare R2, MinIO, AWS S3, Wasabi).
- In the **Watch Folders** tab, point Vayom Drive at local hard drive paths or mounted network shares (`/mnt/nas/media`). The background watcher monitors for new files, generates thumbnails, extracts EXIF/video dimensions, and catalogs them automatically.

---

## 🚀 Self-Deployment Guide

### Prerequisites
- **Python 3.11+**
- **Node.js 18+** & `npm`
- Free Telegram API ID & Hash from [my.telegram.org](https://my.telegram.org)

---

### Option A: The One-Command Launch (Fastest)

Clone the repository and launch the automated runner:

```bash
git clone https://github.com/indieAYUSH/Vayom-Tele-Drive.git
cd Vayom-Tele-Drive

# Make executable and launch
chmod +x run.sh
./run.sh
```

`run.sh` sets up your Python virtual environment, installs backend and frontend dependencies, starts the FastAPI engine on port 8000, and launches the Vite frontend at `http://localhost:5173`.

---

### Option B: Step-by-Step Manual Deployment

#### 1. Configure Environment Variables
Create a `.env` file in the project root:

```bash
cat <<EOF > .env
PORT=8000
DEFAULT_STORAGE_BACKEND=telegram

# Telegram Cloud Backend
TELEGRAM_API_ID=your_telegram_api_id
TELEGRAM_API_HASH=your_telegram_api_hash
TELEGRAM_PHONE=+1234567890

# Optional: S3 / Cloudflare R2 / MinIO Backend
S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID=your_access_key
S3_SECRET_ACCESS_KEY=your_secret_key
S3_BUCKET_NAME=your_bucket
S3_REGION=auto

# Storage Directories
DATA_DIR=./data
UPLOAD_TEMP_DIR=./data/temp
THUMBNAIL_DIR=./data/thumbnails
EOF
```

#### 2. Start the Backend API Engine
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run FastAPI engine
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

#### 3. Start the Frontend Workstation
In a second terminal window:
```bash
cd frontend
npm install
npm run dev
```

Open **`http://localhost:5173`** in your browser to start using Vayom Drive.

---

## 🛡️ Security & Privacy Architecture

- **Local-First Master Catalog**: File metadata, hashes, and albums are stored in a local SQLite database that never leaves your machine.
- **Client-Side Cryptography**: Zero-knowledge encryption ensures that storage backends (including Telegram and S3) store only ciphertext.
- **No Third-Party Telemetry**: Zero analytics, zero tracker scripts, and zero phone-home calls.
- **Verified Stability**: Over 170 automated unit and integration tests covering multi-part uploads, range streaming, and anti-flood protection.
