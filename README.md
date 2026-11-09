# Parroto Bulk Cards Learner

Bulk card reviewer & portfolio booster for [Parroto](https://parroto.app).

## 🚀 Features

- **Automated Preview Card Collection**: Scrapes preview cards across all accessible decks via Next.js endpoints and stores a simple array of card IDs (`["id1", "id2", ...]`) in `data/cards.json`.
- **Auto-Resolving Build ID**: Automatically extracts and falls back to active Next.js `buildId` if Parroto redeploys.
- **🔄 Auto Token Renewal (Never Expires!)**: Automatically requests fresh access tokens from Google Identity Platform using `REFRESH_TOKEN` and `API_KEY`. If an access token expires mid-loop (after 1 hour), it seamlessly renews and keeps running indefinitely without manual intervention.
- **Two Submission Modes**:
  - **Simultaneous Mode (`submit`)**: Submits requests in parallel batches (controlled by `--concurrency`).
  - **Linear Mode (`linear`)**: Submits cards one-by-one with configurable pacing (`--delay`) and **automatic rate limit backoff (`--cooldown-429`)** if HTTP 429 Too Many Requests is returned.
- **Smart Cooldown Handling**: If a card is still on cooldown within the 10-minute AGAIN window, it logs the remaining time and continues other cards.
- **💓 Background Online Heartbeat**: Automatically tracks online learning time and status by sending periodic heartbeat requests (`POST /api/learning-time/heartbeat`) every 5 minutes (configurable) with `--heartbeat`. Handles session lifecycle (`/start` -> `/heartbeat` -> `/end`) and auto token renewal.
- **Loop Mode**: Run continuously every 10 minutes (or custom interval) to automatically farm diamonds and review stats.
- **Deck/Group Loop**: Load cards organized by deck and group, discover authenticated group IDs, reset progress before the first round and every subsequent round, and review with your chosen rating.
- **Clean CLI**: Easy-to-use commands (`fetch`, `submit`, `linear`, `run`, `loop`, `group-loop`, `heartbeat`).

---

## 🛠️ Setup

1. **Create the environment, activate it, and install dependencies**:
   ```pwsh
   python -m venv venv
   .\venv\Scripts\activate
   python -m pip install -r requirements.txt
   ```
   *(or run directly with `.\venv\Scripts\python.exe`)*

2. **Configure `.env`**:
   Add your Firebase credentials to `.env` (refer to `.env.example`):
   ```env
   # Recommended: Refresh Token & API Key (Never expires!)
   REFRESH_TOKEN="your_refresh_token_here"
   API_KEY="your_api_key_here"

   # Optional (no longer needed if REFRESH_TOKEN is set):
   BEARER_TOKEN=""

   # General options
   BUILD_ID="bWYuKDgCvminqE7L8hyo1"
   CONCURRENCY=10
   RATING="again"
   LOOP_INTERVAL_MINUTES=10
   LINEAR_DELAY=0.5
   COOLDOWN_429=10.0
   DECK_ID=""
   ```

---

## 💻 Usage

### 1. Run Linearly (One by one with 429 Cooldown)
```bash
python main.py linear
```
Or with custom pacing and cooldown:
```bash
python main.py linear --delay 0.5 --cooldown-429 10.0
```
*When Parroto responds with `[ERROR 429] Too many requests`, the program automatically pauses for 10 seconds and retries that card up to 3 times before moving forward.*

### 2. Run Simultaneously (Parallel Batch)
```bash
python main.py submit --concurrency 5
```

### 3. Fetch Cards Only
```bash
python main.py fetch
```
Scrapes all accessible decks and saves card IDs to `data/cards.json`.

### 4. Continuous Loop Mode (Runs for hours/days without expiring)
- **Loop with simultaneous submits**:
  ```bash
  python main.py loop --interval 10
  ```
- **Loop with linear submits**:
  ```bash
  python main.py loop --linear --interval 10
  ```
- **Loop with no interval between rounds (back-to-back)**:
  ```bash
  python main.py loop --linear --interval 0
  ```
  *(Note: See Cooldown considerations below when running without intervals)*

### 5. Running with Online Heartbeat
To keep your account online and accumulate learning time while running any command, add `--heartbeat`:
```bash
# Run with heartbeat (every 5 minutes in background)
python main.py run --heartbeat

# Continuous loop with linear submit + background heartbeat
python main.py loop --linear --heartbeat

# Custom heartbeat interval (e.g. ping every 3 minutes)
python main.py loop --linear --heartbeat --heartbeat-interval 3
```

### 6. Standalone Heartbeat (Maintain Online Status Only)
If you just want to keep your account online and accumulate learning time without reviewing cards:
```bash
python main.py heartbeat
```

### 7. Keep Windows Awake
Add `--keep-awake` to any command to prevent automatic Windows sleep for the entire run, including waits between loop rounds:
```bash
python main.py loop --linear --heartbeat --keep-awake
```
The display can still turn off. The previous sleep state is restored when the command completes, fails, or exits with Ctrl+C. This does not override manual Sleep or laptop lid-close behavior. On other platforms, the flag prints a notice and the command continues normally.

### 8. Deck/Group Reset and Review Loop

Copy `data/group/decks.example.json` to `data/group/decks.json` and replace the example IDs with real deck, group, and card IDs:

```json
[
  {
    "deckId": "your-deck-id",
    "groups": [
      {
        "groupId": "your-group-id",
        "cards": ["card-id-1", "card-id-2"]
      }
    ]
  }
]
```

The default source is every `data/group/*.json` file except `*.example.json`. Each file contains an array of decks; deck IDs must be unique across files. Each selected deck needs non-empty groups and card IDs, with no duplicate groups or cards within that deck. Real group data files are ignored by Git; the example is shared.

```bash
# Repeat for a specific deck, using a higher rating
python main.py group-loop --deckId your-deck-id --rating easy

# Use DECK_ID from .env, or all local decks if it is blank
python main.py group-loop --rating good --linear

# Choose another data source and stop after one completed round
python main.py group-loop --groups-file data/group/decks.json --rating easy --rounds 1

# Optional pacing, heartbeat, and sleep prevention
python main.py group-loop --rating easy --linear --delay 0.5 --interval 1 --heartbeat --keep-awake
```

Deck selection precedence is `--deckId` (alias `--deck-id`), then `DECK_ID` in `.env` (the key `deckId` is also accepted), then all decks in the source. An explicit deck must exist in the local data; it never falls back to other decks. Missing or invalid data logs an error and exits before any API call.

Each round:

1. Send authenticated `GET /api/learning-vocabulary/groups-progress/{deckId}` for every selected deck and remember its group IDs.
2. Verify that all local group IDs are present in the response, then send authenticated `DELETE /api/learning-vocabulary/group/{deckId}/{groupId}` for **every returned group**, including groups without local cards. This resets the selected decks' group progress.
3. Once every reset succeeds, submit the local cards using `--rating` or `RATING` from `.env` and the existing simultaneous or linear submission mode.
4. After a fully successful round, repeat from step 1. By default this command waits **0 minutes** between rounds; `--interval` overrides that. The ordinary `loop` command retains its existing interval setting.

The rating default remains `again`; pass `--rating good`/`easy` or set `RATING` to use a higher rating. A failed reset, failed/delayed submission, or unexpected response stops the loop with an error instead of starting another reset. Group requests retry a 401 once after token refresh and retry a 429 up to three times using `--cooldown-429`. Ctrl+C stops the command; there is no extra reset on shutdown. `--rounds N` stops after N successful rounds and leaves the final round's progress intact.

Until a real response sample is available, the progress parser supports a group-object array directly, under `groups`, under `data`, or under `data.groups`. Group IDs may be `groupId`, `group_id`, `id`, or `_id`. Other shapes stop before any reset. The example does not provide runnable real IDs, and example-only directories stop with a missing-data error.

---

## ⚙️ Options & Arguments

| Flag | Description | Default |
|---|---|---|
| `action` | `run`, `fetch`, `submit`, `linear`, `loop`, `group-loop`, `heartbeat` | `run` |
| `--keep-awake` | Prevent automatic Windows sleep until the command exits | `False` |
| `--heartbeat` | Send online heartbeat ping every 5 minutes in background | `False` |
| `--heartbeat-interval` | Interval in minutes between heartbeat pings | `5` (or `HEARTBEAT_INTERVAL_MINUTES`) |
| `--refresh-token` | Override Firebase refresh token | Read from `.env` |
| `--api-key` | Override Firebase API key | Read from `.env` |
| `--token` | Override static Bearer token | Read from `.env` |
| `--linear` | Run submissions sequentially with 429 backoff | `False` |
| `--delay` | Delay in seconds between requests in linear mode | `0.5` |
| `--cooldown-429` | Seconds to pause when hitting HTTP 429 | `10.0` |
| `--concurrency` | Number of simultaneous requests (simultaneous mode) | `10` |
| `--build-id` | Override Next.js build ID | Read from `.env` or auto |
| `--rating` | Rating level (`again`, `hard`, `good`, `easy`) | `again` |
| `--cards-file` | Target JSON file path | `data/cards.json` |
| `--deckId`, `--deck-id` | Select a deck for `group-loop` | `DECK_ID`/`deckId` in `.env`, otherwise all local decks |
| `--groups-file` | JSON file or directory for `group-loop` | `data/group/` (excluding examples) |
| `--rounds` | Successful rounds before stopping `group-loop` | Unlimited |
| `--interval` | Loop wait time in minutes | `10` for `loop`, `0` for `group-loop` |
| `--refresh` | Force re-scrape cards before submitting | `False` |
| `--check-pro` | Attempt to scrape pro decks as well | `False` |

Python creates `__pycache__/` and `.pyc` files automatically when modules run. Ignoring these files does not affect setup or execution after cloning; only the `.py` source files and installed dependencies are needed.

Run the offline test suite with `python -m unittest discover -s tests`. API requests in these tests are mocked; they do not reset or submit real learning progress.
