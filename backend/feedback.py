import os
import json
import logging
from typing import List, Optional
import aiosqlite
from models import Finding, FeedbackEvent, FeedbackStatus

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("FEEDBACK_DB_PATH", ".tmp/feedback.db")

async def init_db() -> None:
    """Initialize the SQLite database with required tables."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    finding_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    comment TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS findings_cache (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS rl_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    finding_id TEXT NOT NULL,
                    action REAL NOT NULL,
                    review_id TEXT,
                    consumed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.commit()
            logger.info(f"Initialized feedback database at {DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize database at {DB_PATH}: {e}")
        raise

async def store_feedback(event: FeedbackEvent) -> None:
    """Store a feedback event in the database."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO feedback (finding_id, status, timestamp, comment) VALUES (?, ?, ?, ?)",
                (event.finding_id, event.status.value, event.timestamp, event.comment)
            )
            await db.commit()
            logger.info(f"Stored feedback for finding_id: {event.finding_id}")
    except Exception as e:
        logger.error(f"Failed to store feedback: {e}")

async def get_feedback(finding_id: str) -> List[FeedbackEvent]:
    """Retrieve feedback events for a specific finding."""
    events = []
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT finding_id, status, timestamp, comment FROM feedback WHERE finding_id = ?",
                (finding_id,)
            ) as cursor:
                async for row in cursor:
                    events.append(FeedbackEvent(
                        finding_id=row[0],
                        status=FeedbackStatus(row[1]),
                        timestamp=row[2],
                        comment=row[3]
                    ))
    except Exception as e:
        logger.error(f"Failed to get feedback for finding_id {finding_id}: {e}")
    return events

async def get_all_feedback() -> List[FeedbackEvent]:
    """Retrieve all feedback events."""
    events = []
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT finding_id, status, timestamp, comment FROM feedback"
            ) as cursor:
                async for row in cursor:
                    events.append(FeedbackEvent(
                        finding_id=row[0],
                        status=FeedbackStatus(row[1]),
                        timestamp=row[2],
                        comment=row[3]
                    ))
    except Exception as e:
        logger.error(f"Failed to get all feedback: {e}")
    return events

async def cache_finding(finding: Finding) -> None:
    """Cache a finding for later lookup."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO findings_cache (id, data) VALUES (?, ?)",
                (finding.id, finding.model_dump_json())
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to cache finding {finding.id}: {e}")

async def get_cached_finding(finding_id: str) -> Optional[Finding]:
    """Retrieve a cached finding by ID."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT data FROM findings_cache WHERE id = ?",
                (finding_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return Finding.model_validate_json(row[0])
    except Exception as e:
        logger.error(f"Failed to retrieve cached finding {finding_id}: {e}")
    return None

async def record_rl_action(finding_id: str, action: float, review_id: Optional[str] = None) -> None:
    """Persist an applied RL action for a finding in a review instance."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO rl_actions (finding_id, action, review_id, consumed) VALUES (?, ?, ?, 0)",
                (finding_id, float(action), review_id)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to record RL action for {finding_id}: {e}")

async def get_and_consume_rl_action(finding_id: str, review_id: Optional[str] = None) -> Optional[float]:
    """
    Atomically retrieves and marks as consumed the earliest unconsumed RL action
    for a finding (or matching review_id if provided).
    Returns None if no matching unconsumed action exists.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if review_id:
                query = "SELECT id, action FROM rl_actions WHERE finding_id = ? AND review_id = ? AND consumed = 0 ORDER BY id ASC LIMIT 1"
                params = (finding_id, review_id)
            else:
                query = "SELECT id, action FROM rl_actions WHERE finding_id = ? AND consumed = 0 ORDER BY id ASC LIMIT 1"
                params = (finding_id,)
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                action_id, action_val = row[0], row[1]

            await db.execute("UPDATE rl_actions SET consumed = 1 WHERE id = ?", (action_id,))
            await db.commit()
            return float(action_val)
    except Exception as e:
        logger.error(f"Failed to retrieve/consume RL action for {finding_id}: {e}")
        return None

async def get_rejection_rate() -> float:
    """Calculate the percentage of findings that have been rejected."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM feedback WHERE status = 'rejected'"
            ) as cursor:
                row = await cursor.fetchone()
                rejected = row[0] if row else 0

            async with db.execute(
                "SELECT COUNT(*) FROM feedback"
            ) as cursor:
                row = await cursor.fetchone()
                total = row[0] if row else 0
                
            if total == 0:
                return 0.0
            return (rejected / total) * 100.0
    except Exception as e:
        logger.error(f"Failed to calculate rejection rate: {e}")
        return 0.0

async def get_feedback_stats() -> dict:
    """
    Aggregate historical feedback events into rule-specific and source-specific statistics.
    Returns:
        {
            "rule_stats": {
                "<rule_id>": {"accepted": int, "rejected": int}
            },
            "source_stats": {
                "<source>": {"accepted": int, "rejected": int}
            }
        }
    """
    stats: dict = {
        "rule_stats": {},
        "source_stats": {}
    }
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT f.status, c.data
                FROM feedback f
                LEFT JOIN findings_cache c ON f.finding_id = c.id
            """) as cursor:
                async for row in cursor:
                    status_str = row[0]
                    cache_json = row[1]
                    if status_str not in ("accepted", "rejected"):
                        continue

                    rule_id = None
                    source_val = None

                    if cache_json:
                        try:
                            finding_dict = json.loads(cache_json)
                            rule_id = finding_dict.get("rule_id")
                            source_val = finding_dict.get("source")
                        except Exception:
                            pass

                    if rule_id:
                        if rule_id not in stats["rule_stats"]:
                            stats["rule_stats"][rule_id] = {"accepted": 0, "rejected": 0}
                        stats["rule_stats"][rule_id][status_str] += 1

                    if source_val:
                        if source_val not in stats["source_stats"]:
                            stats["source_stats"][source_val] = {"accepted": 0, "rejected": 0}
                        stats["source_stats"][source_val][status_str] += 1
    except Exception as e:
        logger.error(f"Failed to calculate feedback stats: {e}")
    return stats
