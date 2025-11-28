"""Seed anti-scalper (risk / ban) mock data.

Usage (from project root or backend/ directory):

    python backend/scripts/seed_anti_scalper.py --commit

Optional args:
    --low N        Number of low-risk users (default 3)
    --medium N     Number of medium-risk users (default 2)
    --high N       Number of high-risk users (default 2)
    --days N       Number of past days to generate risk logs (default 7)
    --reset        Delete existing risk_log / user_ban records for the selected users before inserting
    --dry-run      Show planned actions without committing (default if --commit not supplied)
    --verbose      Print per-record details

What it does:
  1. Picks users by ascending user_id for each risk band (reuses pool if not enough users).
  2. Generates RiskLog rows per user for the past N days (random score in band).
  3. Creates UserBan rows:
       - First high-risk user: active ban in future (duration 7 days, type=all)
       - Second high-risk user (if exists): expired historical ban (already deactivated)
  4. Prints summary; requires --commit to persist.

Safety:
  - Does NOT create new users; if user table empty nothing is written.
  - Dry-run by default; must specify --commit to persist.

Future extension placeholders:
  - compute_dynamic_score() can be replaced by real scoring logic.
  - risk_level mapping kept simple; adapt to config thresholds later.
"""
from __future__ import annotations

import asyncio
import argparse
import random
import sys
import os
from datetime import datetime, timedelta
from typing import List, Sequence

# Ensure parent directory (backend) is on sys.path so that 'app' package is importable
_SCRIPT_DIR = os.path.dirname(__file__)
_BACKEND_DIR = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import AsyncSessionLocal
from app.models.user import User
from app.models.risk_log import RiskLog
from app.models.user_ban import UserBan

# ------------------------------
# Configuration helpers
# ------------------------------

LOW_RANGE = (10, 25)
MEDIUM_RANGE = (40, 55)
HIGH_RANGE = (80, 95)


def risk_level_for(score: int) -> str:
    if score < 30:
        return "low"
    if score < 70:
        return "medium"
    return "high"


def compute_mock_score(base_range: Sequence[int]) -> int:
    return random.randint(base_range[0], base_range[1])

# ------------------------------
# Core seeding logic
# ------------------------------

async def fetch_user_ids(session: AsyncSession) -> List[int]:
    result = await session.execute(select(User.user_id).order_by(User.user_id))
    return [r[0] for r in result.all()]


async def reset_existing(session: AsyncSession, user_ids: Sequence[int]) -> None:
    if not user_ids:
        return
    await session.execute(delete(RiskLog).where(RiskLog.user_id.in_(user_ids)))
    await session.execute(delete(UserBan).where(UserBan.user_id.in_(user_ids)))


async def seed(session: AsyncSession, *, low: int, medium: int, high: int, days: int, reset: bool, verbose: bool) -> dict:
    all_user_ids = await fetch_user_ids(session)
    if not all_user_ids:
        return {"status": "no-users", "message": "User table empty; nothing to seed"}

    # Simple allocation: slice sequentially; if insufficient users reuse from start
    def allocate(count: int, offset: int) -> List[int]:
        if count <= 0:
            return []
        pool = all_user_ids[offset: offset + count]
        if len(pool) < count:
            # reuse from beginning to reach desired count (avoid duplicates via set then list)
            needed = count - len(pool)
            pool.extend(all_user_ids[:needed])
        return pool

    low_users = allocate(low, 0)
    medium_users = allocate(medium, len(low_users))
    high_users = allocate(high, len(low_users) + len(medium_users))

    target_user_ids = list({*low_users, *medium_users, *high_users})

    if reset:
        await reset_existing(session, target_user_ids)

    now = datetime.utcnow()
    risk_logs: List[RiskLog] = []

    def gen_logs(user_list: Sequence[int], score_range: Sequence[int]):
        for uid in user_list:
            for d in range(days):
                ts = now - timedelta(days=(days - 1 - d))  # oldest first
                score = compute_mock_score(score_range)
                risk_logs.append(
                    RiskLog(
                        user_id=uid,
                        risk_score=score,
                        risk_level=risk_level_for(score),
                        alert_time=ts,
                    )
                )
                if verbose:
                    print(f"RiskLog -> user={uid} {ts.date()} score={score}")

    gen_logs(low_users, LOW_RANGE)
    gen_logs(medium_users, MEDIUM_RANGE)
    gen_logs(high_users, HIGH_RANGE)

    for rl in risk_logs:
        session.add(rl)

    bans: List[UserBan] = []
    if high_users:
        # Active ban for first high risk user
        active_user = high_users[0]
        ban = UserBan(user_id=active_user, ban_type="all", reason="mock high risk active ban")
        ban.apply_duration(7)
        bans.append(ban)
        if verbose:
            print(f"Active Ban -> user={active_user} until={ban.ban_until}")
        if len(high_users) > 1:
            # Expired historic ban for second high user
            expired_user = high_users[1]
            past_ban = UserBan(user_id=expired_user, ban_type="all", reason="historical ban (expired)")
            past_ban.apply_duration(1)
            past_ban.ban_until = now - timedelta(days=1)
            past_ban.deactivate("auto-expired")
            bans.append(past_ban)
            if verbose:
                print(f"Expired Ban -> user={expired_user} unban_time={past_ban.unban_time}")

    for b in bans:
        session.add(b)

    return {
        "status": "ok",
        "low_users": low_users,
        "medium_users": medium_users,
        "high_users": high_users,
        "risk_log_count": len(risk_logs),
        "ban_count": len(bans),
    }

# ------------------------------
# CLI / entrypoint
# ------------------------------

async def async_main(args: argparse.Namespace):
    async with AsyncSessionLocal() as session:
        summary = await seed(
            session,
            low=args.low,
            medium=args.medium,
            high=args.high,
            days=args.days,
            reset=args.reset,
            verbose=args.verbose,
        )
        if summary.get("status") == "no-users":
            print("[WARN]", summary["message"])
            return
        print("Summary (pending commit):")
        print(summary)
        if args.commit:
            await session.commit()
            print("[COMMIT] Changes persisted.")
        else:
            await session.rollback()
            print("[DRY-RUN] Rolled back; use --commit to persist.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Seed anti-scalper mock data (risk logs & bans). Dry-run by default.")
    p.add_argument("--low", type=int, default=3, help="Number of low-risk users")
    p.add_argument("--medium", type=int, default=2, help="Number of medium-risk users")
    p.add_argument("--high", type=int, default=2, help="Number of high-risk users")
    p.add_argument("--days", type=int, default=7, help="Number of days of historical logs")
    p.add_argument("--reset", action="store_true", help="Delete existing risk_log/user_ban rows for selected users first")
    p.add_argument("--commit", action="store_true", help="Actually commit the inserts")
    p.add_argument("--dry-run", action="store_true", help="Force dry-run (override --commit if both given)")
    p.add_argument("--verbose", action="store_true", help="Print per record details")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    # If user explicitly sets --dry-run, override commit flag.
    if args.dry_run:
        args.commit = False
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
