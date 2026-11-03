# whoop-sync

Download your own WHOOP data through the official [WHOOP Developer API v2](https://developer.whoop.com/api/)
and keep a one-row-per-day CSV with recovery, HRV, sleep, strain, calories and workouts.

- Standard library only, no runtime dependencies.
- OAuth 2.0 with automatic refresh-token rotation. Tokens are stored locally with `0600` permissions.
- Days are assigned the way you live them: a WHOOP cycle that starts when you fall asleep before
  midnight counts for the next day, and every record uses its own timezone offset.
- Re-running a sync updates the days it downloads and never duplicates rows.

## 1. Create a WHOOP app

1. Go to the [WHOOP Developer Dashboard](https://developer-dashboard.whoop.com/apps/create) and create an app.
   You may be asked to create a team first.
2. Set the **Redirect URI** to `http://127.0.0.1:3000/callback`.
3. Select these **scopes**:
   - `read:recovery`, `read:sleep`, `read:workout`, `read:cycles`
   - `read:profile`, `read:body_measurement`
   - `offline`, which lets the tool renew access without asking you to log in again
4. Put the credentials in a `.env` file. Real environment variables also work.

   ```
   WHOOP_CLIENT_ID=...
   WHOOP_CLIENT_SECRET=...
   WHOOP_REDIRECT_URI=http://127.0.0.1:3000/callback
   ```

## 2. Install

```bash
uv tool install git+https://github.com/daredled/whoop-sync
# or run it without installing
uvx --from git+https://github.com/daredled/whoop-sync whoop-sync --help
```

## 3. Authorize (once)

```bash
whoop-sync auth
```

1. Open the printed URL and approve access. WHOOP redirects to `127.0.0.1:3000`, where the command is
   waiting for the reply.
2. If your browser cannot reach that address (for example a Windows browser with the tool running in WSL),
   press Ctrl+C and use `whoop-sync auth --manual`. Paste the URL from the browser's address bar, even if
   the page shows an error.

Tokens are saved to `~/.config/whoop-sync/tokens.json`. Change the location with `--tokens-file`.

## 4. Sync

```bash
whoop-sync sync --days 30 --csv whoop_daily.csv --raw-dir whoop_raw/
```

| Option | Default | Meaning |
|---|---|---|
| `--days` | 14 | How many days back to download |
| `--csv` | `whoop_daily.csv` | Daily CSV to create or update |
| `--raw-dir` | none | Also save the raw API responses as JSON |
| `--env-file` | `./.env` | Credentials file (global option, goes before the command) |
| `--tokens-file` | `~/.config/whoop-sync/tokens.json` | Token storage (global option) |

### CSV columns

| Column | Source |
|---|---|
| `date` | Local calendar date |
| `recovery`, `hrv_ms`, `resting_hr`, `spo2`, `skin_temp_c`, `calibrating` | Recovery of that day's cycle |
| `sleep_h` | Hours asleep (light + deep + REM), naps excluded |
| `in_bed_h`, `awake_h`, `light_h`, `deep_h`, `rem_h`, `sleep_cycles`, `disturbances` | Sleep stages, summed over the non-nap sleeps ending that day |
| `sleep_performance`, `sleep_efficiency`, `sleep_consistency`, `respiratory_rate` | Longest sleep ending that day |
| `sleep_need_h`, `sleep_debt_h` | WHOOP's sleep need for the longest sleep (baseline + debt + strain + naps) and its debt part |
| `nap_h` | Hours asleep in naps ending that day |
| `day_strain`, `day_kcal`, `avg_hr`, `max_hr`, `steps` | Physiological cycle (`steps` = the cycle's step count) |
| `workout_kcal`, `workout_min`, `workout_max_hr`, `workouts` | Workouts started that day, e.g. `basketball 90min 420kcal strain 8.5` |
| `zone0_min` … `zone5_min` | Minutes in each heart rate zone, summed over that day's workouts |
| `cycle_complete` | `no` while the day is still in progress |

## Use as a library

```python
from pathlib import Path
from whoop_sync import Credentials, WhoopClient, summarize_days

client = WhoopClient(Credentials.from_env(Path(".env")))
data = client.fetch_all("2026-09-01T00:00:00.000Z", "2026-09-28T00:00:00.000Z")
rows = summarize_days(data)  # {"2026-09-01": {...}, ...}
```

## Development

```bash
uv sync
uv run pytest
```

## License

MIT. This project is not affiliated with or endorsed by WHOOP.
