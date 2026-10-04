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
- **Clean CLI**: Easy-to-use commands (`fetch`, `submit`, `linear`, `run`, `loop`, `heartbeat`).

---

## 🛠️ Setup

1. **Activate Python Virtual Environment**:
   ```pwsh
   .\venv\Scripts\activate
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

---

## ⚙️ Options & Arguments

| Flag | Description | Default |
|---|---|---|
| `action` | `run`, `fetch`, `submit`, `linear`, `loop`, `heartbeat` | `run` |
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
| `--interval` | Loop wait time in minutes | `10` |
| `--refresh` | Force re-scrape cards before submitting | `False` |
| `--check-pro` | Attempt to scrape pro decks as well | `False` |
