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
