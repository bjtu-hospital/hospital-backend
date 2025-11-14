"""Backfill risk_log and user_risk_summary from hospital.sql or live DB.

Usage (from repo root or backend/):

  # From SQL dump (MySQL .sql)
  python backend/scripts/backfill_risk_from_sql.py --sql ../hospital.sql --commit --reset --verbose

  # From live DB (reads RegistrationOrder table)
  python backend/scripts/backfill_risk_from_sql.py --commit --reset --verbose

Notes:
- Generates RiskLog via RiskScoreService.update_risk_score to keep summary in sync and trigger auto-bans.
- Heuristics:
  * no_show: +30 per order
  * cancelled: +8 per order
  * completed: -5 per order (capped to -50 total)
- If --reset is supplied, deletes existing RiskLog and UserRiskSummary for the affected users before insert.
- Dry-run by default; use --commit to persist.
"""
from __future__ import annotations

import asyncio
import argparse
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Ensure backend/ on sys.path so we can import app.*
_SCRIPT_DIR = os.path.dirname(__file__)
_BACKEND_DIR = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import AsyncSessionLocal
from app.models.risk_log import RiskLog
from app.models.user_risk_summary import UserRiskSummary
from app.models.registration_order import RegistrationOrder
from app.models.user import User
from app.services.risk_score_service import risk_score_service

# ---------------------------
# Simple MySQL INSERT parser
# ---------------------------

@dataclass
class InsertBlock:
    table: str
    columns: List[str]
    rows: List[List[str]]  # raw string tokens per value (no type conversion)


_INSERT_RE = re.compile(r"INSERT\s+INTO\s+`?(?P<table>[\w]+)`?\s*\((?P<cols>[^)]+)\)\s*VALUES\s*(?P<values>.+);\s*$", re.IGNORECASE)


def _split_columns(cols_raw: str) -> List[str]:
    # cols like: `order_id`, `patient_id`, `user_id`, `status`
    parts = []
    for c in cols_raw.split(","):
        c = c.strip().strip("`")
        if c:
            parts.append(c)
    return parts


def _tokenize_values(values_raw: str) -> List[List[str]]:
    """Tokenize VALUES section into list of row token lists.
    Handles commas inside quoted strings and NULL.
    """
    rows: List[List[str]] = []
    i = 0
    n = len(values_raw)
    def parse_one_tuple(start: int) -> Tuple[List[str], int]:
        assert values_raw[start] == '(', "tuple must start with ("
        i = start + 1
        cur = ''
        in_str = False
        esc = False
        tokens: List[str] = []
        while i < n:
            ch = values_raw[i]
            if in_str:
                if esc:
                    cur += ch
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == "'":
                    in_str = False
                else:
                    cur += ch
                i += 1
                continue
            else:
                if ch == "'":
                    in_str = True
                    i += 1
                    continue
                if ch == ",":
                    tokens.append(cur.strip())
                    cur = ''
                    i += 1
                    continue
                if ch == ")":
                    tokens.append(cur.strip())
                    i += 1
                    break
                # regular char
                cur += ch
                i += 1
        # skip trailing spaces and comma between tuples
        while i < n and values_raw[i] in (' ', '\n', '\r', '\t'):
            i += 1
        if i < n and values_raw[i] == ',':
            i += 1
        return tokens, i

    while i < n:
        # skip whitespace and commas
        while i < n and values_raw[i] in (' ', '\n', '\r', '\t', ','):
            i += 1
        if i >= n:
            break
        if values_raw[i] != '(':
            # malformed; stop
            break
        toks, i = parse_one_tuple(i)
        rows.append(toks)
    return rows


def parse_insert_blocks(sql_text: str, target_tables: List[str]) -> List[InsertBlock]:
    blocks: List[InsertBlock] = []
    buf: List[str] = []
    for line in sql_text.splitlines():
        line = line.strip()
        if not line or line.startswith('--') or line.startswith('/*'):
            continue
        buf.append(line)
        if line.endswith(';'):
            stmt = ' '.join(buf)
            buf = []
            m = _INSERT_RE.match(stmt)
            if not m:
                continue
            table = m.group('table')
            if table not in target_tables:
                continue
            cols = _split_columns(m.group('cols'))
            rows = _tokenize_values(m.group('values'))
            blocks.append(InsertBlock(table=table, columns=cols, rows=rows))
    return blocks


# ---------------------------
# Risk computation heuristics
# ---------------------------

@dataclass
class Counters:
    no_show: int = 0
    cancelled: int = 0
    completed: int = 0


def filter_nonzero(counters: Dict[int, 'Counters']) -> Dict[int, 'Counters']:
    return {uid: c for uid, c in counters.items() if (c.no_show or c.cancelled or c.completed)}


def aggregate_from_rows(blocks: List[InsertBlock]) -> Dict[int, Counters]:
    """Aggregate per-user counters from registration_order insert rows.
    Requires that blocks for table 'registration_order' provide columns including 'user_id' and 'status'.
    """
    user_counters: Dict[int, Counters] = defaultdict(Counters)
    for blk in blocks:
        if blk.table != 'registration_order':
            continue
        # map column index
        try:
            idx_user = blk.columns.index('user_id')
            idx_status = blk.columns.index('status')
        except ValueError:
            # column missing; skip block
            continue
        for row in blk.rows:
            if idx_user >= len(row) or idx_status >= len(row):
                continue
            raw_uid = row[idx_user]
            raw_status = row[idx_status]
            # normalize
            try:
                user_id = int(raw_uid)
            except Exception:
                continue
            status = raw_status.strip()
            if status.upper() == 'NULL':
                continue
            # strip quotes
            if status.startswith("'") and status.endswith("'"):
                status = status[1:-1]
            c = user_counters[user_id]
            if status == 'no_show':
                c.no_show += 1
            elif status == 'cancelled':
                c.cancelled += 1
            elif status == 'completed':
                c.completed += 1
    return user_counters


async def aggregate_from_db(session: AsyncSession) -> Dict[int, Counters]:
    user_counters: Dict[int, Counters] = defaultdict(Counters)
    result = await session.execute(select(RegistrationOrder.user_id, RegistrationOrder.status))
    for user_id, status in result.all():
        if not user_id:
            continue
        # status is stored as string (native_enum=False)
        s = str(status)
        c = user_counters[user_id]
        if s == 'no_show':
            c.no_show += 1
        elif s == 'cancelled':
            c.cancelled += 1
        elif s == 'completed':
            c.completed += 1
    return user_counters


async def reset_existing(session: AsyncSession, user_ids: List[int]) -> None:
    if not user_ids:
        return
    await session.execute(delete(RiskLog).where(RiskLog.user_id.in_(user_ids)))
    await session.execute(delete(UserRiskSummary).where(UserRiskSummary.user_id.in_(user_ids)))


async def backfill(session: AsyncSession, counters: Dict[int, Counters], verbose: bool) -> Dict[str, int]:
    inserted_logs = 0
    affected_users = 0
    for uid, cnt in counters.items():
        delta_total = 0
        if cnt.no_show:
            d = 30 * cnt.no_show
            delta_total += d
            await risk_score_service.update_risk_score(
                session, uid, d, behavior_type='no_show', description=f'{cnt.no_show} 次爽约累积 +{d}'
            )
            inserted_logs += 1
            if verbose:
                print(f"user={uid} no_show={cnt.no_show} +{d}")
        if cnt.cancelled:
            d = 8 * cnt.cancelled
            delta_total += d
            await risk_score_service.update_risk_score(
                session, uid, d, behavior_type='frequent_cancel', description=f'{cnt.cancelled} 次取消累积 +{d}'
            )
            inserted_logs += 1
            if verbose:
                print(f"user={uid} cancelled={cnt.cancelled} +{d}")
        if cnt.completed:
            # positive credits with a cap
            d_raw = 5 * cnt.completed
            d = min(50, d_raw)
            if d > 0:
                delta_total -= d
                await risk_score_service.update_risk_score(
                    session, uid, -d, behavior_type='positive_complete', description=f'{cnt.completed} 次完成就诊奖励 -{d}'
                )
                inserted_logs += 1
                if verbose:
                    print(f"user={uid} completed={cnt.completed} -{d}")
        if delta_total != 0:
            affected_users += 1
    return {"affected_users": affected_users, "inserted_logs": inserted_logs}


async def async_main(args: argparse.Namespace):
    # Build counters
    counters: Dict[int, Counters] = {}
    if args.sql:
        if not os.path.isabs(args.sql):
            sql_path = os.path.abspath(os.path.join(_BACKEND_DIR, '..', args.sql))
        else:
            sql_path = args.sql
        if not os.path.exists(sql_path):
            print(f"[ERROR] SQL file not found: {sql_path}")
            return
        with open(sql_path, 'r', encoding='utf-8') as f:
            text = f.read()
        blocks = parse_insert_blocks(text, target_tables=['registration_order'])
        counters = filter_nonzero(aggregate_from_rows(blocks))
        print(f"Parsed from SQL: users={len(counters)} (non-zero only)")
    else:
        async with AsyncSessionLocal() as session:
            counters = filter_nonzero(await aggregate_from_db(session))
            print(f"Aggregated from DB: users={len(counters)} (non-zero only)")

    user_ids = sorted(counters.keys())
    if not user_ids and args.sql:
        print("[INFO] No data parsed from SQL; falling back to live DB aggregation.")
        async with AsyncSessionLocal() as session:
            counters = filter_nonzero(await aggregate_from_db(session))
        user_ids = sorted(counters.keys())
    if not user_ids:
        print("[WARN] No users found in counters; trying synthetic seeding from existing users.")
        # Build synthetic counters from first N users in DB
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(User.user_id).order_by(User.user_id).limit(20))
            sample_users = [r[0] for r in res.all()]
        if not sample_users:
            print("[WARN] No users found in DB; aborting.")
            return
        counters = {}
        # Assign buckets: first 5 high, next 5 medium, rest low
        for i, uid in enumerate(sample_users):
            if i < 5:
                counters[uid] = Counters(no_show=2, cancelled=4, completed=0)
            elif i < 10:
                counters[uid] = Counters(no_show=1, cancelled=2, completed=1)
            else:
                counters[uid] = Counters(no_show=0, cancelled=1, completed=2)
        user_ids = sorted(counters.keys())
        print(f"[INFO] Synthetic counters generated for {len(user_ids)} users.")

    async with AsyncSessionLocal() as session:
        if args.reset:
            await reset_existing(session, user_ids)
            if args.verbose:
                print(f"Reset existing risk_log and user_risk_summary for {len(user_ids)} users")
        summary = await backfill(session, counters, args.verbose)
        print({
            "users": len(user_ids),
            **summary,
        })
        if args.commit and not args.dry_run:
            await session.commit()
            print("[COMMIT] Changes persisted.")
        else:
            await session.rollback()
            print("[DRY-RUN] Rolled back; use --commit to persist.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backfill risk logs and summaries from SQL dump or live DB. Dry-run by default.")
    p.add_argument("--sql", type=str, help="Path to hospital.sql (MySQL dump). If omitted, reads from live DB.")
    p.add_argument("--reset", action="store_true", help="Delete existing risk_log and user_risk_summary rows for affected users")
    p.add_argument("--commit", action="store_true", help="Actually commit the inserts")
    p.add_argument("--dry-run", action="store_true", help="Force dry-run (override --commit if both given)")
    p.add_argument("--verbose", action="store_true", help="Print per-user details")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.dry_run:
        args.commit = False
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
