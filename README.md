# speakeasy

Local-first text-to-speech CLI for macOS. Reads text sentence by sentence in a full-screen terminal UI with playback controls, session persistence, and optional AI rewriting via Ollama.

---

## Requirements

- macOS (Apple Silicon recommended)
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- [Piper TTS](https://github.com/rhasspy/piper)
- [Ollama](https://ollama.com) (optional — only needed for `--rewrite`)

---

## Installation

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone and install

```bash
git clone <repo-url> speakeasy
cd speakeasy
uv sync
```

### 3. Install Piper TTS

```bash
pip install piper-tts
```

### 4. Download a voice model

```bash
mkdir -p ~/.local/share/piper
cd ~/.local/share/piper
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

### 5. Install Ollama (optional — for `--rewrite`)

```bash
brew install ollama
ollama serve &
ollama pull llama3:8b
```

---

## Commands

### `start` — begin a new session

```bash
speakeasy start --text "Some text to read aloud"
speakeasy start --file article.txt
cat article.txt | speakeasy start
```

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--text` | `-t` | — | Text to speak (inline) |
| `--file` | `-f` | — | Path to a text file |
| `--speed` | `-s` | `1.0` | Speed multiplier — `1.5` is 50% faster, `0.8` is slower |
| `--voice` | `-v` | auto | Path to a Piper `.onnx` model file |
| `--rewrite` | `-r` | off | Rewrite with Ollama before speaking (starts paused) |
| `--debug` | `-d` | off | Debug mode: print and play each sentence sequentially, no UI |

**Examples:**

```bash
# Read a file at 1.4× speed
speakeasy start --file chapter.txt --speed 1.4

# Pipe text in with custom voice
cat notes.txt | speakeasy start --voice ~/.local/share/piper/en_US-ryan-medium.onnx

# Rewrite for clarity before reading (starts paused so you can review)
speakeasy start --file notes.txt --rewrite
```

> Text with quotes: use `--file` or pipe to avoid shell quoting issues.

---

### `list` — show saved sessions

```bash
speakeasy list
```

Displays session ID, title (first 8 words), a progress bar, and creation date.

---

### `resume` — continue a previous session

```bash
speakeasy resume <id>
speakeasy resume <id> --speed 1.5
```

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--speed` | `-s` | session default | Override playback speed for this run only |

---

### `delete` — remove sessions

```bash
speakeasy delete <id>
speakeasy delete <id1> <id2> <id3>
```

---

### `cache` — inspect or clear the audio cache

```bash
speakeasy cache            # list cached files with sizes and total
speakeasy cache --clear    # delete all cached audio
```

Audio is cached by content hash, so identical sentences reuse files across sessions.

---

## Keybindings

| Key | Action |
|-----|--------|
| `space` | Pause / resume |
| `k` | Next sentence |
| `j` | Previous sentence |
| `q` | Quit (saves progress) |
| `Ctrl-C` | Quit (saves progress) |

Sentences not yet generated show a spinner (`⠋`) while being synthesised in the background.

---

## Data locations

| Path | Contents |
|------|----------|
| `~/.speakeasy/sessions.db` | SQLite sessions database |
| `~/.speakeasy/cache/` | Cached WAV audio (SHA-256 keyed) |
| `~/.local/share/piper/` | Piper voice models |

---

## Architecture

```
speakeasy/
├── main.py       CLI entry point and commands
├── session.py    SQLite session persistence
├── rewrite.py    Ollama streaming rewrite
├── tts.py        Piper TTS subprocess wrapper
├── player.py     Playback engine + background generation threads
├── ui.py         Rich full-screen terminal UI + key capture
├── splitter.py   NLTK sentence splitting with paragraph preservation
├── cache.py      SHA-256 keyed WAV file cache
└── constants.py  Shared constants (PARAGRAPH_BREAK sentinel)
```

---

## Troubleshooting

**"Piper binary not found"**
Run `pip install piper-tts` and ensure `piper` is on your PATH.

**"Piper voice model not found"**
Download the `.onnx` and `.onnx.json` files to `~/.local/share/piper/`.

**Rewrite fails or times out**
Start Ollama with `ollama serve` and pull a model with `ollama pull llama3:8b`.
The app will show the specific error and fall back to the original text.
