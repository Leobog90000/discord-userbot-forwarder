# Discord User-Bot Channel Forwarder

🇬🇧 English | [🇷🇺 Русский](README_RU.md)

A **user-bot** (self-bot) built on [`discord.py-self`](https://github.com/dolfies/discord.py-self) that automatically forwards messages from chosen Discord channels to other channels — across different servers.

> ⚠️ **Disclaimer / educational purpose only.**
> This project is published **solely to demonstrate how such a bot works**. Automating a regular user account (a "self-bot" / "user-bot") **violates the [Discord Terms of Service](https://discord.com/terms)** and can lead to the account being **limited or permanently banned**. You use this code entirely at your own risk. The author is not responsible for any consequences. Use a separate, disposable account — never your main one.

---

## Table of contents

- [How it works](#how-it-works)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Getting the user token](#getting-the-user-token)
- [Configuration](#configuration)
- [Running](#running)
- [Usage / commands](#usage--commands)
- [Project files](#project-files)
- [Security notes](#security-notes)
- [License](#license)

---

## How it works

The bot logs in as a regular Discord **user account** (not an official bot application) using that account's token.

- It listens to the **source channels** you configure.
- Every new message from **any user except the bot's administrators** is re-sent to the paired **target channel**.
- Only the content is forwarded — **no author name, no source channel, no source server** is shown. Attachments are re-uploaded; links stay as plain links so Discord builds the preview itself.
- Messages from administrators are treated **only as commands** and are never forwarded.

**Important:** the account used by the bot must be a **member of every server involved** — both the server(s) it forwards *from* and the server(s) it forwards *to*. For example, to forward from *Server 1 / Channel A* to *Server 2 / Channel B*, the account must be on both Server 1 and Server 2, and must be able to read Channel A and send messages in Channel B.

## Features

- 🔁 Live forwarding between any number of channel pairs (source → target)
- 🖼️ Attachments re-uploaded (images/GIFs always; other files up to 20 MB, larger ones sent as a link)
- 👮 Admin system — base owners in code + admins added by command (persisted across restarts)
- 🧭 Manage channel pairs at runtime with commands (persisted across restarts)
- 🕰️ `!syncrange` — forward history for a date range, with a delay and a message limit as safeguards

## Requirements

- Python **3.9+**
- A Discord account (preferably a **second / spare** one) that is a member of all relevant servers
- Packages from `requirements.txt`: `discord.py-self`, `python-dotenv`

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>

# 2. (Optional) create a virtual environment
python -m venv venv
# Windows:  venv\Scripts\activate
# Linux/macOS:  source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env file from the example
cp .env.example .env        # Windows: copy .env.example .env
```

## Getting the user token

> Use a **second account** whenever possible. It can be a brand-new or an old one — it doesn't matter.
> **Never share your token.** Anyone who has it has full access to the account.

1. Open Discord **in a browser** (<https://discord.com/app>) and log in to the account the bot will use.
2. Press **F12** to open Developer Tools and go to the **Network** tab.
3. In the **Filter** field type `/api`.
4. Reload the page with **Ctrl+R** or **F5** and wait until the list of requests fills up.
5. Find an entry named **`science`**. There may be several — you might have to check each one to find the one containing the token.
6. Open the **Headers** tab of that entry.
7. Scroll the list of headers until you find **`authorization`**. The value after the colon is your personal **token**.
8. Copy it and put it into `.env`, replacing `your_user_token`:

```env
USER_TOKEN=your_user_token
```

## Configuration

### 1. Administrators (`OWNER_IDS`)

Open the bot file (`botEN.py` or `botRU.py`) and replace the placeholder IDs in `OWNER_IDS` with real **user IDs**:

```python
OWNER_IDS = {
    111111111111111111,  # the bot account itself
    222222222222222222,  # your main account
}
```

Administrators can run commands; their own messages are **never** forwarded. Base owners can't be removed by command. Enable *Developer Mode* in Discord (Settings → Advanced) and right-click a user → **Copy User ID**.

### 2. Channel pairs (`config.json`) — optional

Copy `config.example.json` to `config.json` and fill in channel IDs (right-click a channel → **Copy Channel ID**):

```json
{
  "source_servers": [
    {
      "channels": [
        { "source_id": "111111111111111111", "target_id": "222222222222222222" }
      ]
    }
  ]
}
```

You can skip this file entirely and add pairs later using `!serverres add` instead.

## Running

Run **one** of the two versions:

```bash
python botEN.py     # English messages and commands help
python botRU.py     # Russian messages and commands help
```

When the bot starts you should see `Logged in as ...`. Then, from an administrator account, send `!helpres` in any channel the bot can see.

## Usage / commands

Prefix: `!` — commands only work for administrators.

| Command | Description |
|---|---|
| `!helpres` | Show help |
| `!aboutres` | Bot status and active pairs |
| `!adminres list` | List administrators |
| `!adminres add <user_id>` | Add an administrator |
| `!adminres remove <user_id>` | Remove an administrator (not base owners) |
| `!serverres list` | List forwarding pairs |
| `!serverres add <source_id> <target_id>` | Add a pair |
| `!serverres remove <source_id>` | Remove a pair |
| `!syncrange <sources> <start> [end] [limit]` | Forward history for a period |

**`!syncrange` details**

- `<sources>` — one channel ID, several IDs separated by commas (no spaces), or `all`
- Dates — `YYYY-MM-DD` or `YYYY-MM-DD HH:MM` (UTC). If `end` is omitted, "now" is used
- `limit` — max messages **per channel** (default 500)

```text
!syncrange 111111111111111111 2025-01-01 2025-01-05
!syncrange 111111111111111111,222222222222222222 2025-01-01 2025-01-05
!syncrange all 2025-01-01 2025-01-05 200
```

**Example of a full setup**

1. Put the account into Server 1 and Server 2.
2. Run `!serverres add <channel-A-id> <channel-B-id>`.
3. Anyone (except admins) who writes in channel A will now have their message appear in channel B.

## Project files

| File | Purpose |
|---|---|
| `botEN.py` | Bot — English version |
| `botRU.py` | Bot — Russian version |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template for the token file |
| `config.example.json` | Template for initial channel pairs |
| `admins_extra.json` | *(auto-created)* admins added via command |
| `mappings_extra.json` | *(auto-created)* pairs added via command |

## Security notes

- **Never commit `.env` or your token.** The included `.gitignore` already excludes `.env`, `config.json` and the auto-generated JSON files. If a token ever leaks, change the account password immediately — that invalidates the token.
- Don't use your main account. Automated user accounts are against Discord's rules and may be banned.
- Keep `SYNC_DELAY_SECONDS` reasonable to avoid rate limits.

## License

MIT — see [LICENSE](LICENSE).
