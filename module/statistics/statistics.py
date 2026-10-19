"""
Daily income statistics for Cube and Gem (and optionally Oil/Coin).

Design principles:
- "Gross gain", not net change: every positive delta between consecutive
  snapshots (within or across day boundary) is added; negative deltas are
  discarded. This prevents unreasonable negative numbers when the player
  manually spends resources or when scripts consume them.
- Persist in a dedicated file, not in the main config JSON, so the
  config file does not grow unboundedly.
- Snapshots are retained for 30 days; daily aggregates are kept forever
  (a few KB per year).

File layout (config/<instance>_stats.json):
{
    "snapshots": [ {"ts": "<ISO>", "group": "<str>", "value": <int>}, ... ],
    "daily":      { "<YYYY-MM-DD>": {"Cube": <int>, "Gem": <int>}, ... },
    "last_update": "<ISO>"
}
"""
import copy
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from deploy.atomic import atomic_read_bytes, atomic_write
from module.logger import logger
from module.config.utils import filepath_config

# Snapshots older than this many days are pruned.
# Daily aggregates are kept forever.
SNAPSHOT_RETENTION_DAYS = 30

# Groups tracked by DailyIncome. Oil/Coin are optional; Cube/Gem are core.
TRACKED_GROUPS = ('Cube', 'Gem', 'Oil', 'Coin')

# All calculations use Asia/Taipei (UTC+8) as day boundary.
_TZ_TPEI = timezone(timedelta(hours=8))


def _now_iso() -> str:
    """Current time in UTC+8, ISO-8601 without tz suffix."""
    return datetime.now(_TZ_TPEI).strftime('%Y-%m-%dT%H:%M:%S')


def _today_key() -> str:
    """YYYY-MM-DD key for today (UTC+8)."""
    return datetime.now(_TZ_TPEI).strftime('%Y-%m-%d')


def _key_to_dt(key: str) -> datetime:
    """Convert 'YYYY-MM-DD' key to a UTC+8 datetime (midnight)."""
    return datetime.strptime(key, '%Y-%m-%d')


def _filepath_stats(config_name: str) -> str:
    """Path to the dedicated stats file for an instance."""
    return filepath_config(f'{config_name}_stats')


class DailyIncome:
    """
    Gross daily acquisition tracker.

    Thread-safe enough for the current usage pattern: LogRes.snapshot()
    is called from the main script flow, and the webui reads via
    get_daily()/aggregate() which reload the file each call.
    """

    _lock = threading.Lock()

    def __init__(self, config_name: str):
        self.config_name = config_name
        self.filepath = _filepath_stats(config_name)
        self._data: dict = self._load()

    # ------------------------------------------------------------------ #
    # File I/O
    # ------------------------------------------------------------------ #
    def _load(self) -> dict:
        if not os.path.exists(self.filepath):
            return {'snapshots': [], 'daily': {}, 'last_update': None}
        content = atomic_read_bytes(self.filepath)
        if not content:
            return {'snapshots': [], 'daily': {}, 'last_update': None}
        try:
            import json
            return json.loads(content.decode('utf-8'))
        except Exception as e:
            logger.warning(f'DailyIncome: failed to parse {self.filepath}: {e}')
            return {'snapshots': [], 'daily': {}, 'last_update': None}

    def _save(self) -> None:
        import json
        try:
            text = json.dumps(
                self._data, indent=2, ensure_ascii=False, sort_keys=False
            )
            atomic_write(self.filepath, text.encode('utf-8'))
        except Exception as e:
            logger.warning(f'DailyIncome: failed to write {self.filepath}: {e}')

    # ------------------------------------------------------------------ #
    # Snapshot ingestion
    # ------------------------------------------------------------------ #
    def snapshot(self, group: str, value: int) -> None:
        """
        Record a new OCR / known value for `group` at "now".

        Args:
            group: e.g. 'Cube', 'Gem'.
            value: integer value. If not integer, silently skip.
        """
        if group not in TRACKED_GROUPS:
            return
        try:
            value = int(value)
        except (TypeError, ValueError):
            return

        with DailyIncome._lock:
            self._data['snapshots'].append({
                'ts': _now_iso(),
                'group': group,
                'value': value,
            })
            today = _today_key()
            self._recompute_daily(today)
            self._prune_snapshots()
            self._data['last_update'] = _now_iso()
            self._save()

    # ------------------------------------------------------------------ #
    # Core algorithm: positive-delta sum
    # ------------------------------------------------------------------ #
    def _recompute_daily(self, date_key: str) -> None:
        """
        Recompute daily gross gains for `date_key` (YYYY-MM-DD).

        Gross gain = sum of max(0, curr - prev) for every consecutive
        pair of snapshots of the same group, where `prev` may be the
        last snapshot of the **previous day** (so that an overnight
        jump counts as acquisition on the current day).
        """
        groups = set()
        for s in self._data['snapshots']:
            if s['ts'].startswith(date_key):
                groups.add(s['group'])

        # Also look at groups that had their first-ever snapshot on this
        # date — handled naturally because we just scan all snapshots.

        # Determine the previous day's key
        prev_day_dt = _key_to_dt(date_key) - timedelta(days=1)
        prev_key = prev_day_dt.strftime('%Y-%m-%d')

        for group in groups:
            # Sort all snapshots of this group by ts
            all_sorted = sorted(
                [s for s in self._data['snapshots'] if s['group'] == group],
                key=lambda x: x['ts'],
            )
            # Filter to snapshots <= date_key
            # Find index of first snapshot of date_key
            # Take everything from that point onwards but also include
            # the last snapshot of prev_key as baseline
            # Actually simpler: walk ALL sorted snapshots chronologically,
            # but only ADD positive deltas whose curr.ts is on date_key.
            last_value: Optional[int] = None
            gross = 0
            for snap in all_sorted:
                if last_value is not None:
                    delta = snap['value'] - last_value
                    if delta > 0 and snap['ts'].startswith(date_key):
                        gross += delta
                last_value = snap['value']
            self._data['daily'].setdefault(date_key, {})[group] = gross

    def _prune_snapshots(self) -> None:
        """Remove snapshots older than SNAPSHOT_RETENTION_DAYS."""
        if not self._data['snapshots']:
            return
        cutoff = (
            datetime.now(_TZ_TPEI) -
            timedelta(days=SNAPSHOT_RETENTION_DAYS)
        ).strftime('%Y-%m-%dT%H:%M:%S')
        before = len(self._data['snapshots'])
        self._data['snapshots'] = [
            s for s in self._data['snapshots'] if s['ts'] >= cutoff
        ]
        after = len(self._data['snapshots'])
        if before != after:
            logger.info(
                f'DailyIncome: pruned {before - after} snapshots '
                f'(kept {after})'
            )

    # ------------------------------------------------------------------ #
    # Read API (for GUI)
    # ------------------------------------------------------------------ #
    def get_daily(self, date_key: str, group: str) -> int:
        """Return gross gain for group on date_key, or 0 if unknown."""
        return int(self._data.get('daily', {}).get(date_key, {}).get(group, 0))

    def get_today(self, group: str) -> int:
        return self.get_daily(_today_key(), group)

    def aggregate(self, period: str, group: str) -> int:
        """
        Sum of daily gross gains over a range.

        Args:
            period: 'week' (Monday→now), 'month' (1st→now),
                    'year' (Jan 1→now), or 'day' (today only).
            group: resource group.
        Returns:
            integer sum.
        """
        if period == 'day':
            return self.get_today(group)
        today_dt = _key_to_dt(_today_key())

        if period == 'week':
            # ISO weekday: Mon=1..Sun=7
            start_dt = today_dt - timedelta(days=today_dt.weekday())
        elif period == 'month':
            start_dt = today_dt.replace(day=1)
        elif period == 'year':
            start_dt = today_dt.replace(month=1, day=1)
        else:
            raise ValueError(f'Unknown period: {period}')

        total = 0
        cur = start_dt
        while cur <= today_dt:
            total += self.get_daily(cur.strftime('%Y-%m-%d'), group)
            cur += timedelta(days=1)
        return total

    def last_snapshot(self, group: str) -> Optional[dict]:
        """Most recent snapshot for group, or None."""
        cands = [s for s in self._data['snapshots'] if s['group'] == group]
        if not cands:
            return None
        return max(cands, key=lambda x: x['ts'])

    def get_recent_days(self, days: int = 30, groups=None) -> list:
        """
        Return a list of dicts for the most recent N days, newest first.
        Each dict: {'date': 'YYYY-MM-DD', 'Cube': int, 'Gem': int, ...}.
        """
        if groups is None:
            groups = ('Cube', 'Gem')
        today_dt = _key_to_dt(_today_key())
        result = []
        for i in range(days):
            d = (today_dt - timedelta(days=i)).strftime('%Y-%m-%d')
            row = {'date': d}
            for g in groups:
                row[g] = self.get_daily(d, g)
            result.append(row)
        return result
