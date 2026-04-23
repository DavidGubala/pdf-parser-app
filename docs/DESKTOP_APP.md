# Turning PDF Parse into a Desktop Application

Convert the web-based PDF Parse app into a desktop application that users launch from an icon on their desktop. The server (Flask, Docling, SQLite) stays on your infrastructure; users get a lightweight native window on their machine.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Recommended Approach: Tauri (Local-First Hybrid)](#2-recommended-approach-tauri-local-first-hybrid)
3. [Server-Side Changes](#3-server-side-changes)
4. [Distribution & Updates](#4-distribution--updates)

---

## 1. Architecture Overview

The desktop application follows a **Local-First Hybrid Architecture**. While heavy processing (PDF parsing) remains on the server, the application maintains a local SQLite database to ensure the user has instant access to their data even when offline.

```
┌─────────────────────────────┐        HTTPS        ┌──────────────────────────────┐
│     User's Desktop          │  ◄──────────────►   │     Your Server              │
│                             │                      │                              │
│  ┌───────────────────────┐  │                      │  ┌────────────────────────┐  │
│  │      Frontend UI      │  │                      │  │  Flask (app.py)        │  │
│  │   (HTML/CSS/JS)       │  │                      │  │  ├─ Auth & Sessions    │  │
│  └──────────┬────────────┘  │                      │  │  ├─ Docling PDF Parse  │  │
│             │ invoke()      │                      │  │  ├─ SQLite DB          │  │
│  ┌──────────┴────────────┐  │                      │  │  └─ File Storage       │  │
│  │    Tauri Rust Core    │──┼── API / Sync ───────┼──│  └────────────────────────┘  │
│  │  ├─ Local SQLite DB   │  │                      │                              │
│  │  └─ Sync Engine       │  │                      │  Docker / gunicorn           │
│  │                       │  │                      │  Cloudflare Tunnel           │
│  └───────────────────────┘  │                      └──────────────────────────────┘
└─────────────────────────────┘
```

**What the desktop app does:**
- **Local Data Management:** Stores a mirrored copy of the user's POs and schedule in a local SQLite DB.
- **Offline Access:** Allows reading and modifying data without an internet connection.
- **Synchronization:** Background worker that pushes local changes and pulls server updates using timestamps.
- **Server Proxy:** Handles file uploads for PDF processing and authenticates with the Flask server.

**What the desktop app does NOT do:**
- **PDF Processing:** All Docling parsing is still performed on the server to keep the client lightweight.
- **Primary Storage:** The server remains the "Source of Truth".

## 1.1 Data Flow & Synchronization

To achieve a seamless offline experience, the application uses the following patterns:

### Read Operations (Offline-First)
`Frontend` $\rightarrow$ `Tauri Rust` $\rightarrow$ `Local SQLite` $\rightarrow$ `Frontend`
- Data is always read from the local database first. This ensures the UI is instantaneous and works without a connection.

### Write Operations (Sync-Queue)
`Frontend` $\rightarrow$ `Tauri Rust` $\rightarrow$ `Local SQLite` $\rightarrow$ `Sync Queue` $\rightarrow$ `Flask Server`
- Changes are saved locally immediately.
- A background Sync Engine attempts to push these changes to the server. If offline, changes are queued until connectivity is restored.

### PDF Processing (Online-Only)
`Frontend` $\rightarrow$ `Tauri Rust` $\rightarrow$ `Flask Server` $\rightarrow$ `Processing` $\rightarrow$ `Local SQLite`
- PDF parsing requires server-side resources (Docling).
- The file is uploaded to the server; upon successful processing, the server returns the extracted data, which Tauri then saves into the local SQLite DB.

---



## 2. Recommended Approach: Tauri (Local-First Hybrid)

**Tauri** is the best fit for this use case. It allows us to combine a lightweight web frontend with a powerful Rust backend capable of managing a local SQLite database and a synchronization engine. The result is a high-performance application that feels native and works offline while still leveraging server-side power for PDF processing.

### Why Tauri?

- **Tiny installer** — WebView2 is already included in Windows 10/11
- **Native window** — title bar, taskbar icon, system tray support
- **Built-in auto-updater** — push updates without user intervention
- **Security** — Rust backend, no Node.js on the client
- **Production-ready** — used by major apps (Cody, Spacedrive, etc.)

### Prerequisites

On your **development machine** (where you build the installer):

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install Node.js (LTS)
# https://nodejs.org/

# Windows: Install Visual Studio Build Tools (C++ workload)
# macOS: xcode-select --install
# Linux: sudo apt install libwebkit2gtk-4.1-dev build-essential libssl-dev
```

### Project Setup

Create a `desktop/` folder in the repo:

```bash
mkdir desktop && cd desktop
npm create tauri-app@latest . -- --template vanilla
```

### Configuration

Edit `desktop/src-tauri/tauri.conf.json`:

```json
{
  "productName": "PDF Parse",
  "version": "1.0.0",
  "identifier": "com.jarborne.pdf-parse",
  "build": {
    "frontendDist": "../src"
  },
  "app": {
    "title": "PDF Parse – Purchase Order Manager",
    "windows": [
      {
        "url": "index.html",
        "title": "PDF Parse",
        "width": 1280,
        "height": 800,
        "minWidth": 900,
        "minHeight": 600,
        "resizable": true
      }
    ],
    "security": {
      "dangerousRemoteUrlAccess": []
    }
  },
  "bundle": {
    "active": true,
    "targets": ["msi", "nsis"],
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/icon.ico"
    ],
    "windows": {
      "nsis": {
        "installMode": "perUser"
      }
    }
  }
}
```

### Frontend Entry Point

Create `desktop/src/index.html` — a splash/loading screen shown briefly while the WebView navigates to your server:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>PDF Parse</title>
  <style>
    body {
      margin: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      background: #1a1a2e;
      color: #e0e0e0;
      font-family: system-ui, -apple-system, sans-serif;
    }
    .loader {
      text-align: center;
    }
    .spinner {
      width: 40px;
      height: 40px;
      border: 3px solid rgba(255,255,255,0.1);
      border-top-color: #4f8cff;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin: 0 auto 1rem;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="loader">
    <div class="spinner"></div>
    <p>Connecting to server…</p>
  </div>
  <script>
    // The frontend is now bundled locally. 
    // API calls should be made via Tauri's invoke() or fetch() to the server.
    console.log("PDF Parse Desktop initialized");
  </script>
</body>
</html>
```

### Build the Installer

```bash
cd desktop

# Development (opens a window for testing)
npm run tauri dev

# Production build (creates installer in src-tauri/target/release/bundle/)
npm run tauri build
```

Output on Windows:
- `src-tauri/target/release/bundle/nsis/PDF Parse_1.0.0_x64-setup.exe` (~3–5 MB)
- `src-tauri/target/release/bundle/msi/PDF Parse_1.0.0_x64.msi`

### Adding a System Tray Icon (Optional)

In `desktop/src-tauri/src/main.rs`:

```rust
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
};

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&quit])?;

            let _tray = TrayIconBuilder::new()
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

### Folder Structure

```
pdf-parse/
├── app.py                    # Flask server (unchanged)
├── templates/
├── static/
├── desktop/                  # NEW — Tauri desktop wrapper
│   ├── package.json
│   ├── src/
│   │   └── index.html        # Splash screen / loader
│   └── src-tauri/
│       ├── tauri.conf.json   # Window config, server URL
│       ├── src/
│       │   └── main.rs       # Rust entry point
│       ├── icons/            # App icons (.ico, .png)
│       └── target/release/bundle/  # Built installers
├── Dockerfile
├── docker-compose.yml
└── ...
```

---

## 4. Alternative A: Progressive Web App (PWA) — Zero Install

The simplest option — no separate desktop project at all. Add a manifest and service worker to the existing Flask app, and users can "Install" it from their browser. It gets a desktop icon, its own window, and feels like a native app.

### Changes Required (Server-Side Only)

**1. Add `static/manifest.json`:**

```json
{
  "name": "PDF Parse – Purchase Order Manager",
  "short_name": "PDF Parse",
  "description": "Upload and parse purchase orders",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#1a1a2e",
  "theme_color": "#4f8cff",
  "icons": [
    { "src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

**2. Add manifest link to `templates/index.html` and `templates/login.html`:**

```html
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#4f8cff">
```

**3. Add `static/sw.js` (minimal service worker):**

```javascript
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
```

**4. Register the service worker in `static/js/app.js`:**

```javascript
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js');
}
```

### How Users Install It

1. Visit `https://po.yourdomain.com` in Chrome/Edge
2. Click the install icon in the address bar (or browser menu → "Install app")
3. App appears in Start Menu / Desktop with its own icon and window

### Pros & Cons

- **Pro:** Zero build step, zero installer to distribute, automatic updates
- **Pro:** Works on any OS with a modern browser
- **Con:** Users must visit the site first and know to click "Install"
- **Con:** No system tray, limited native OS integration
- **Con:** Some users may not trust or understand the "Install" prompt

---

## 5. Alternative B: PyWebView — Python-Native Wrapper

Since the team already uses Python, **pywebview** is a natural fit. It opens a native window with the system WebView and points it at your server URL. Package it with PyInstaller for a standalone `.exe`.

### Client-Side Project

```bash
mkdir desktop-pywebview && cd desktop-pywebview
python -m venv venv
venv\Scripts\activate
pip install pywebview pyinstaller
```

### `main.py`

```python
import webview
import sys

SERVER_URL = "https://po.yourdomain.com"

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else SERVER_URL

    webview.create_window(
        title="PDF Parse – Purchase Order Manager",
        url=url,
        width=1280,
        height=800,
        min_size=(900, 600),
        text_select=True,
    )
    webview.start()
```

### Build Standalone Executable

```bash
pyinstaller --onefile --windowed --name "PDF Parse" --icon=icon.ico main.py
```

Output: `dist/PDF Parse.exe` (~15–30 MB)

### Pros & Cons

- **Pro:** Very simple — ~15 lines of Python
- **Pro:** Uses system WebView2 (small binary)
- **Pro:** Familiar toolchain (Python + PyInstaller)
- **Con:** PyInstaller `.exe` files sometimes trigger antivirus false positives
- **Con:** No built-in auto-updater (you'd need to build that yourself)
- **Con:** Less polished than Tauri for window management / native features

---

## 6. Alternative C: Electron — Full Chromium Bundle

Electron is the most mature option and powers apps like VS Code, Slack, and Discord. However, it bundles an entire Chromium browser, making the installer **150–200 MB**. For a thin client pointing at a remote server, this is overkill.

### When to Choose Electron

- You need **guaranteed** rendering consistency across all machines
- You plan to add significant **client-side offline features** later
- You want the largest ecosystem of plugins, tutorials, and community support

### Quick Setup

```bash
mkdir desktop-electron && cd desktop-electron
npm init -y
npm install electron electron-builder --save-dev
```

### `main.js`

```javascript
const { app, BrowserWindow } = require('electron');

const SERVER_URL = 'https://po.yourdomain.com';

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'PDF Parse',
    icon: 'icons/icon.png',
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  win.loadURL(SERVER_URL);
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});
```

### Build

```bash
npx electron-builder --win nsis
```

Output: `dist/PDF Parse Setup 1.0.0.exe` (~150 MB)

---

## 3. Server-Side Changes

Regardless of which desktop wrapper you choose, a few server adjustments improve the experience.

### CORS Headers

If the desktop app loads from a local origin (e.g., `tauri://localhost`), you may need CORS headers:

```bash
pip install flask-cors
```

```python
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=[
    "https://po.yourdomain.com",
    "tauri://localhost",    # Tauri desktop app
    "http://localhost:*",   # Local development
])
```

### Content Security Policy

Update your CSP (if you have one) to allow the desktop app origin.

### Session Cookie Settings

Ensure cookies work in the WebView context:

```python
app.config.update(
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_SECURE=True,
)
```

### Health Check Endpoint

Add a simple endpoint for the desktop app to verify connectivity:

```python
@app.route('/api/health')
def health_check():
    return jsonify({"status": "ok", "version": "1.0.0"})
```

---

## 4. Distribution & Updates

### Distributing to Users

| Method | How |
|---|---|
| **Tauri NSIS installer** | Email or host the `.exe` (~3 MB) on your domain |
| **PWA** | Users visit the site and click "Install" — nothing to distribute |
| **PyWebView + PyInstaller** | Email or host the `.exe` (~20 MB) |
| **Electron installer** | Email or host the `.exe` (~150 MB) |

### Auto-Updates

**Tauri** has a built-in updater. Add to `tauri.conf.json`:

```json
{
  "plugins": {
    "updater": {
      "endpoints": [
        "https://po.yourdomain.com/api/desktop-update/{{target}}/{{arch}}/{{current_version}}"
      ],
      "pubkey": "YOUR_PUBLIC_KEY"
    }
  }
}
```

Then add a Flask endpoint that returns update metadata (version, download URL, signature).

**PWA** updates automatically — whenever you deploy a new version of the web app, users get it on next load.

**Electron** uses `electron-updater` with a similar pattern.

**PyWebView** has no built-in updater; you'd need a custom solution or simply re-distribute the `.exe`.

---

## 9. Decision Matrix

### Go with Tauri if:
- You want the most native, polished experience
- You care about installer size (3 MB vs 150 MB)
- You want built-in auto-updates
- You're comfortable with a Rust + Node.js build toolchain

### Go with PWA if:
- You want zero distribution effort
- Your users are comfortable installing from the browser
- You don't need system tray or deep OS integration
- You want the simplest possible implementation (server-side only changes)

### Go with PyWebView if:
- You want to stay 100% in the Python ecosystem
- You need a quick prototype before committing to Tauri
- Your user base is small (manual `.exe` distribution is fine)

### Go with Electron if:
- You may add heavy client-side features later (offline mode, local caching)
- You need guaranteed cross-browser rendering consistency
- Installer size is not a concern

---

## Quick-Start Recommendation

For your use case — **server handles all processing, client is just a window** — the recommended path is:

1. **Start with PWA** (30 minutes of work, immediate results, no installer to manage)
2. **Graduate to Tauri** when you need a distributable installer, system tray icon, or more native OS integration

Both can coexist: the PWA changes benefit all users (including those who just use a browser), and the Tauri app simply opens a window to the same URL.
