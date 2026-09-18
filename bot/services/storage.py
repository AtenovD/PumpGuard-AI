from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    lang TEXT NOT NULL DEFAULT 'ru',
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS required_channels (
    lang TEXT PRIMARY KEY,
    channel TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_tokens (
    chain TEXT NOT NULL DEFAULT 'robinhood',
    mint TEXT NOT NULL,
    symbol TEXT,
    name TEXT,
    creator TEXT,
    first_seen_at INTEGER NOT NULL,
    PRIMARY KEY (chain, mint)
);

CREATE TABLE IF NOT EXISTS creators (
    chain TEXT NOT NULL DEFAULT 'robinhood',
    creator TEXT NOT NULL,
    rugs INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    last_seen_at INTEGER NOT NULL,
    PRIMARY KEY (chain, creator)
);

CREATE TABLE IF NOT EXISTS daily_counters (
    day TEXT PRIMARY KEY,
    trades INTEGER NOT NULL DEFAULT 0,
    realized_pnl_sol REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS positions (
    chain TEXT NOT NULL DEFAULT 'robinhood',
    mint TEXT NOT NULL,
    symbol TEXT,
    entry_price REAL NOT NULL,
    sol_spent REAL NOT NULL,
    score REAL NOT NULL,
    creator TEXT,
    opened_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    exit_price REAL,
    closed_at INTEGER,
    close_reason TEXT,
    token_amount REAL,
    peak_pnl_pct REAL NOT NULL DEFAULT 0,
    exit_proceeds REAL,
    PRIMARY KEY (chain, mint)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL DEFAULT 'robinhood',
    mint TEXT NOT NULL,
    symbol TEXT,
    score REAL,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    chain TEXT NOT NULL DEFAULT 'robinhood',
    mint TEXT NOT NULL,
    price REAL NOT NULL,
    observed_at INTEGER NOT NULL,
    PRIMARY KEY (chain, mint)
);

CREATE TABLE IF NOT EXISTS oauth_pending (
    state TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    code_verifier TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    user_id INTEGER PRIMARY KEY,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    expires_at INTEGER NOT NULL,
    token_type TEXT NOT NULL DEFAULT 'Bearer',
    scope TEXT,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    mint TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    subject TEXT NOT NULL,
    symbol TEXT,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    score REAL,
    payload TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_health (
    component TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    value REAL NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_subject ON decision_records(chain, subject);
CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decision_records(created_at);
CREATE INDEX IF NOT EXISTS idx_positions_open ON positions(status) WHERE status = 'open';
"""


@dataclass
class UserRecord:
    user_id: int
    username: str | None
    lang: str


@dataclass
class Position:
    mint: str
    symbol: str | None
    entry_price: float
    sol_spent: float
    score: float
    creator: str | None
    opened_at: int
    status: str
    chain: str = "robinhood"
    exit_price: float | None = None
    closed_at: int | None = None
    close_reason: str | None = None
    # v1.1: token amount received, best net profit seen (drives the trailing
    # stop) and the net ETH a sale returned after fees and price impact.
    # `sol_spent` is a legacy column name; the value is ETH on Robinhood Chain.
    token_amount: float | None = None
    peak_pnl_pct: float = 0.0
    exit_proceeds: float | None = None


_POSITION_COLUMNS = (
    "mint, symbol, entry_price, sol_spent, score, creator, opened_at, status, "
    "chain, exit_price, closed_at, close_reason, token_amount, peak_pnl_pct, exit_proceeds"
)


@dataclass
class OAuthTokenRecord:
    user_id: int
    access_token_encrypted: str
    refresh_token_encrypted: str | None
    expires_at: int
    token_type: str
    scope: str | None


class Storage:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._migrate_chain_dimension()
        await self._migrate_v11()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Apply additive migrations needed by databases created by older releases."""
        cursor = await self.db.execute("PRAGMA table_info(positions)")
        columns = {row[1] for row in await cursor.fetchall()}
        additions = {
            "exit_price": "REAL",
            "closed_at": "INTEGER",
            "close_reason": "TEXT",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                await self.db.execute(f"ALTER TABLE positions ADD COLUMN {name} {sql_type}")

        seen_columns = await self._columns("seen_tokens")
        if seen_columns and "creator" not in seen_columns:
            await self.db.execute("ALTER TABLE seen_tokens ADD COLUMN creator TEXT")

    async def _migrate_v11(self) -> None:
        """v1.1 additive columns. Runs after the chain-dimension rebuild, which
        recreates `positions` without them for very old databases."""
        columns = await self._columns("positions")
        for name, sql_type in {
            "token_amount": "REAL",
            "peak_pnl_pct": "REAL NOT NULL DEFAULT 0",
            "exit_proceeds": "REAL",
        }.items():
            if name not in columns:
                await self.db.execute(f"ALTER TABLE positions ADD COLUMN {name} {sql_type}")

    async def _columns(self, table: str) -> set[str]:
        cursor = await self.db.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in await cursor.fetchall()}

    async def _migrate_chain_dimension(self) -> None:
        """Preserve old Solana data while moving identity keys to (chain, address)."""
        if "chain" not in await self._columns("seen_tokens"):
            await self.db.executescript(
                """
                ALTER TABLE seen_tokens RENAME TO seen_tokens_legacy;
                CREATE TABLE seen_tokens (
                    chain TEXT NOT NULL DEFAULT 'robinhood', mint TEXT NOT NULL, symbol TEXT,
                    name TEXT, creator TEXT, first_seen_at INTEGER NOT NULL, PRIMARY KEY (chain, mint)
                );
                INSERT INTO seen_tokens (chain, mint, symbol, name, first_seen_at)
                    SELECT 'solana', mint, symbol, name, first_seen_at FROM seen_tokens_legacy;
                DROP TABLE seen_tokens_legacy;
                """
            )
        if "chain" not in await self._columns("creators"):
            await self.db.executescript(
                """
                ALTER TABLE creators RENAME TO creators_legacy;
                CREATE TABLE creators (
                    chain TEXT NOT NULL DEFAULT 'robinhood', creator TEXT NOT NULL,
                    rugs INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
                    last_seen_at INTEGER NOT NULL, PRIMARY KEY (chain, creator)
                );
                INSERT INTO creators (chain, creator, rugs, wins, last_seen_at)
                    SELECT 'solana', creator, rugs, wins, last_seen_at FROM creators_legacy;
                DROP TABLE creators_legacy;
                """
            )
        if "chain" not in await self._columns("positions"):
            await self.db.executescript(
                """
                ALTER TABLE positions RENAME TO positions_legacy;
                CREATE TABLE positions (
                    chain TEXT NOT NULL DEFAULT 'robinhood', mint TEXT NOT NULL, symbol TEXT,
                    entry_price REAL NOT NULL, sol_spent REAL NOT NULL, score REAL NOT NULL,
                    creator TEXT, opened_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                    exit_price REAL, closed_at INTEGER, close_reason TEXT,
                    PRIMARY KEY (chain, mint)
                );
                INSERT INTO positions (chain, mint, symbol, entry_price, sol_spent, score, creator,
                    opened_at, status, exit_price, closed_at, close_reason)
                    SELECT 'solana', mint, symbol, entry_price, sol_spent, score, creator,
                    opened_at, status, exit_price, closed_at, close_reason
                    FROM positions_legacy;
                DROP TABLE positions_legacy;
                """
            )
        if "chain" not in await self._columns("signals"):
            await self.db.execute(
                "ALTER TABLE signals ADD COLUMN chain TEXT NOT NULL DEFAULT 'solana'"
            )

    async def connect_readonly(self) -> None:
        """Open a dashboard-safe connection that cannot issue database writes."""
        path = Path(self._db_path).expanduser().resolve()
        if path.exists():
            self._db = await aiosqlite.connect(f"file:{path}?mode=ro", uri=True)
        else:
            # A missing fresh DB is represented as an empty in-memory database;
            # the dashboard remains read-only and renders zero-state pages.
            self._db = await aiosqlite.connect(":memory:")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Storage.connect() was not called"
        return self._db

    # --- users ---------------------------------------------------------

    async def get_user(self, user_id: int) -> UserRecord | None:
        cursor = await self.db.execute(
            "SELECT user_id, username, lang FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return UserRecord(*row) if row else None

    async def upsert_user(self, user_id: int, username: str | None, lang: str | None = None) -> None:
        now = int(time.time())
        existing = await self.get_user(user_id)
        if existing is None:
            await self.db.execute(
                "INSERT INTO users (user_id, username, lang, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, lang or "ru", now, now),
            )
        else:
            await self.db.execute(
                "UPDATE users SET username = ?, lang = ?, last_seen_at = ? WHERE user_id = ?",
                (username, lang or existing.lang, now, user_id),
            )
        await self.db.commit()

    async def all_user_ids(self) -> list[int]:
        cursor = await self.db.execute("SELECT user_id FROM users")
        return [r[0] for r in await cursor.fetchall()]

    # --- xAI OAuth ------------------------------------------------------

    async def create_oauth_pending(self, state: str, user_id: int, code_verifier: str) -> None:
        await self.db.execute(
            "INSERT INTO oauth_pending (state, user_id, code_verifier, created_at) VALUES (?, ?, ?, ?)",
            (state, user_id, code_verifier, int(time.time())),
        )
        await self.db.commit()

    async def consume_oauth_pending(self, state: str, ttl_seconds: int = 600) -> tuple[int, str] | None:
        now = int(time.time())
        await self.db.execute("DELETE FROM oauth_pending WHERE created_at < ?", (now - ttl_seconds,))
        cursor = await self.db.execute(
            "DELETE FROM oauth_pending WHERE state = ? AND created_at >= ? RETURNING user_id, code_verifier",
            (state, now - ttl_seconds),
        )
        row = await cursor.fetchone()
        await self.db.commit()
        return (int(row[0]), str(row[1])) if row else None

    async def store_oauth_tokens(
        self, user_id: int, access_token_encrypted: str,
        refresh_token_encrypted: str | None, expires_at: int,
        token_type: str = "Bearer", scope: str | None = None,
    ) -> None:
        now = int(time.time())
        await self.db.execute(
            "INSERT INTO oauth_tokens (user_id, access_token_encrypted, refresh_token_encrypted, "
            "expires_at, token_type, scope, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET access_token_encrypted=excluded.access_token_encrypted, "
            "refresh_token_encrypted=excluded.refresh_token_encrypted, expires_at=excluded.expires_at, "
            "token_type=excluded.token_type, scope=excluded.scope, updated_at=excluded.updated_at",
            (user_id, access_token_encrypted, refresh_token_encrypted, expires_at, token_type, scope, now),
        )
        await self.db.commit()

    async def get_oauth_tokens(self, user_id: int) -> OAuthTokenRecord | None:
        cursor = await self.db.execute(
            "SELECT user_id, access_token_encrypted, refresh_token_encrypted, expires_at, token_type, scope "
            "FROM oauth_tokens WHERE user_id = ?", (user_id,),
        )
        row = await cursor.fetchone()
        return OAuthTokenRecord(*row) if row else None

    async def save_decision(
        self, chain: str, subject: str, symbol: str | None, stage: str, outcome: str,
        score: float | None, payload: str,
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO decision_records (chain, subject, symbol, stage, outcome, score, payload, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chain, subject, symbol, stage, outcome, score, payload, int(time.time())),
        )
        await self.db.commit()
        return int(cursor.lastrowid)

    async def get_decision(self, decision_id: int) -> dict[str, object] | None:
        cursor = await self.db.execute(
            "SELECT id, chain, subject, symbol, stage, outcome, score, payload, created_at "
            "FROM decision_records WHERE id = ?", (decision_id,),
        )
        row = await cursor.fetchone()
        return self._decision_row(row) if row else None

    async def recent_decisions(self, limit: int = 20, subject: str | None = None) -> list[dict[str, object]]:
        sql = ("SELECT id, chain, subject, symbol, stage, outcome, score, payload, created_at "
               "FROM decision_records")
        params: tuple = ()
        if subject:
            sql += " WHERE subject = ?"
            params = (subject,)
        sql += " ORDER BY id DESC LIMIT ?"
        cursor = await self.db.execute(sql, params + (max(1, min(limit, 200)),))
        return [self._decision_row(row) for row in await cursor.fetchall()]

    @staticmethod
    def _decision_row(row: tuple) -> dict[str, object]:
        return {
            "id": row[0], "chain": row[1], "subject": row[2], "symbol": row[3], "stage": row[4],
            "outcome": row[5], "score": row[6], "payload": row[7], "created_at": row[8],
        }

    async def save_analysis_snapshot(self, chain: str, mint: str, payload: str) -> int:
        cursor = await self.db.execute(
            "INSERT INTO analysis_snapshots (chain, mint, payload, created_at) VALUES (?, ?, ?, ?)",
            (chain, mint, payload, int(time.time())),
        )
        await self.db.commit()
        return int(cursor.lastrowid)

    async def get_analysis_snapshot(self, snapshot_id: int) -> str | None:
        cursor = await self.db.execute(
            "SELECT payload FROM analysis_snapshots WHERE id = ?", (snapshot_id,)
        )
        row = await cursor.fetchone()
        return str(row[0]) if row else None

    # --- required channels ----------------------------------------------

    async def set_required_channel(self, lang: str, channel: str | None) -> None:
        if channel is None:
            await self.db.execute("DELETE FROM required_channels WHERE lang = ?", (lang,))
        else:
            await self.db.execute(
                "INSERT INTO required_channels (lang, channel) VALUES (?, ?) "
                "ON CONFLICT(lang) DO UPDATE SET channel = excluded.channel",
                (lang, channel),
            )
        await self.db.commit()

    async def get_required_channel(self, lang: str) -> str | None:
        cursor = await self.db.execute("SELECT channel FROM required_channels WHERE lang = ?", (lang,))
        row = await cursor.fetchone()
        return row[0] if row else None

    # --- seen tokens (monitor dedup) --------------------------------------

    async def is_seen(self, mint: str, chain: str = "robinhood") -> bool:
        cursor = await self.db.execute(
            "SELECT 1 FROM seen_tokens WHERE chain = ? AND mint = ?", (chain, mint)
        )
        return await cursor.fetchone() is not None

    async def mark_seen(
        self, mint: str, symbol: str | None = None, name: str | None = None, chain: str = "robinhood",
        creator: str | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO seen_tokens (chain, mint, symbol, name, creator, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chain, mint, symbol, name, creator, int(time.time())),
        )
        await self.db.commit()

    async def creator_chains(self, creator: str) -> set[str]:
        """Every chain this creator address has a recorded launch under.

        Used for cross-signal detection: the same address launching both a
        Robinhood token and a Robinhood NFT collection is a notable pattern
        either way it goes (a serious builder, or a coordinated scam).
        """
        cursor = await self.db.execute(
            "SELECT DISTINCT chain FROM seen_tokens WHERE creator = ?", (creator,)
        )
        return {row[0] for row in await cursor.fetchall()}

    async def find_similar_recent(
        self, symbol: str | None, name: str | None, since_seconds: int,
        exclude_mint: str, chain: str = "robinhood",
    ) -> list[str]:
        """Normalized exact-match lookup for a copycat launch reusing a recent name/symbol.

        Deliberately simple (case/punctuation-insensitive exact match, not fuzzy) — cheap,
        no external dependency, and copycat scams overwhelmingly reuse the name verbatim.
        """
        def norm(s: str | None) -> str:
            return "".join(ch for ch in (s or "").lower() if ch.isalnum())

        target_symbol, target_name = norm(symbol), norm(name)
        if not target_symbol and not target_name:
            return []

        cutoff = int(time.time()) - since_seconds
        cursor = await self.db.execute(
            "SELECT mint, symbol, name FROM seen_tokens "
            "WHERE chain = ? AND first_seen_at >= ? AND mint != ?",
            (chain, cutoff, exclude_mint),
        )
        matches = []
        for mint, sym, nm in await cursor.fetchall():
            if (target_symbol and norm(sym) == target_symbol) or (target_name and norm(nm) == target_name):
                matches.append(mint)
        return matches

    async def recent_token_names(
        self, since_seconds: int, exclude_mint: str, chain: str = "robinhood"
    ) -> list[tuple[str, str | None, str | None]]:
        cutoff = int(time.time()) - since_seconds
        cursor = await self.db.execute(
            "SELECT mint, symbol, name FROM seen_tokens "
            "WHERE chain = ? AND first_seen_at >= ? AND mint != ?",
            (chain, cutoff, exclude_mint),
        )
        return [(str(row[0]), row[1], row[2]) for row in await cursor.fetchall()]

    # --- reputation book ---------------------------------------------------

    async def creator_rugs(self, creator: str, chain: str = "robinhood") -> int:
        cursor = await self.db.execute(
            "SELECT rugs FROM creators WHERE chain = ? AND creator = ?", (chain, creator)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def creator_stats(self, creator: str, chain: str = "robinhood") -> tuple[int, int]:
        """Returns (rugs, wins) for a creator, (0, 0) if never seen before."""
        cursor = await self.db.execute(
            "SELECT rugs, wins FROM creators WHERE chain = ? AND creator = ?", (chain, creator)
        )
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else (0, 0)

    async def record_creator_outcome(
        self, creator: str, is_rug: bool, chain: str = "robinhood"
    ) -> None:
        now = int(time.time())
        cursor = await self.db.execute(
            "SELECT rugs, wins FROM creators WHERE chain = ? AND creator = ?", (chain, creator)
        )
        row = await cursor.fetchone()
        rugs, wins = row if row else (0, 0)
        if is_rug:
            rugs += 1
        else:
            wins += 1
        await self.db.execute(
            "INSERT INTO creators (chain, creator, rugs, wins, last_seen_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chain, creator) DO UPDATE SET rugs = ?, wins = ?, last_seen_at = ?",
            (chain, creator, rugs, wins, now, rugs, wins, now),
        )
        await self.db.commit()

    async def forget_stale_creators(self, older_than_days: int) -> int:
        cutoff = int(time.time()) - older_than_days * 86400
        cursor = await self.db.execute("DELETE FROM creators WHERE last_seen_at < ?", (cutoff,))
        await self.db.commit()
        return cursor.rowcount or 0

    # --- daily counters (risk manager) -----------------------------------

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    async def get_daily_counters(self) -> tuple[int, float]:
        day = self._today()
        cursor = await self.db.execute("SELECT trades, realized_pnl_sol FROM daily_counters WHERE day = ?", (day,))
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else (0, 0.0)

    async def record_trade(self, pnl_sol: float = 0.0) -> None:
        day = self._today()
        trades, pnl = await self.get_daily_counters()
        await self.db.execute(
            "INSERT INTO daily_counters (day, trades, realized_pnl_sol) VALUES (?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET trades = ?, realized_pnl_sol = ?",
            (day, trades + 1, pnl + pnl_sol, trades + 1, pnl + pnl_sol),
        )
        await self.db.commit()

    async def record_pnl_only(self, pnl_sol: float) -> None:
        day = self._today()
        trades, pnl = await self.get_daily_counters()
        await self.db.execute(
            "INSERT INTO daily_counters (day, trades, realized_pnl_sol) VALUES (?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET realized_pnl_sol = ?",
            (day, trades, pnl + pnl_sol, pnl + pnl_sol),
        )
        await self.db.commit()

    # --- positions (dry-run) -----------------------------------------------

    async def open_position(self, p: Position) -> None:
        await self.db.execute(
            "INSERT INTO positions (chain, mint, symbol, entry_price, sol_spent, score, creator, "
            "opened_at, status, token_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (p.chain, p.mint, p.symbol, p.entry_price, p.sol_spent, p.score, p.creator,
             p.opened_at, p.token_amount),
        )
        await self.db.commit()

    async def update_position_peak(self, mint: str, peak_pnl_pct: float, chain: str = "robinhood") -> None:
        await self.db.execute(
            "UPDATE positions SET peak_pnl_pct = ? WHERE chain = ? AND mint = ? AND status = 'open'",
            (peak_pnl_pct, chain, mint),
        )
        await self.db.commit()

    async def open_exposure(self) -> float:
        """Total ETH currently committed to open dry-run positions."""
        cursor = await self.db.execute(
            "SELECT COALESCE(SUM(sol_spent), 0) FROM positions WHERE status = 'open'"
        )
        (total,) = await cursor.fetchone()
        return float(total)

    async def closes_since(self, reason: str, since: int) -> list[int]:
        """Timestamps of positions closed for `reason` at or after `since`."""
        cursor = await self.db.execute(
            "SELECT closed_at FROM positions WHERE status = 'closed' AND close_reason = ? "
            "AND closed_at IS NOT NULL AND closed_at >= ? ORDER BY closed_at",
            (reason, since),
        )
        return [int(row[0]) for row in await cursor.fetchall()]

    async def close_position(
        self,
        mint: str,
        exit_price: float | None = None,
        close_reason: str | None = None,
        closed_at: int | None = None,
        chain: str = "robinhood",
        exit_proceeds: float | None = None,
    ) -> None:
        await self.db.execute(
            "UPDATE positions SET status = 'closed', exit_price = ?, closed_at = ?, close_reason = ?, "
            "exit_proceeds = ? WHERE chain = ? AND mint = ?",
            (exit_price, int(time.time()) if closed_at is None else closed_at, close_reason,
             exit_proceeds, chain, mint),
        )
        await self.db.commit()

    async def open_positions(self) -> list[Position]:
        cursor = await self.db.execute(
            f"SELECT {_POSITION_COLUMNS} FROM positions WHERE status = 'open' ORDER BY opened_at DESC"
        )
        return [Position(*row) for row in await cursor.fetchall()]

    async def open_positions_marked(self) -> list[tuple[Position, float | None]]:
        """Open positions with the latest observed spot price, if one was recorded."""
        cursor = await self.db.execute(
            "SELECT p.mint, p.symbol, p.entry_price, p.sol_spent, p.score, p.creator, p.opened_at, "
            "p.status, p.chain, p.exit_price, p.closed_at, p.close_reason, p.token_amount, "
            "p.peak_pnl_pct, p.exit_proceeds, s.price FROM positions p "
            "LEFT JOIN price_snapshots s ON s.chain = p.chain AND s.mint = p.mint "
            "WHERE p.status = 'open' ORDER BY p.opened_at DESC"
        )
        return [(Position(*row[:15]), row[15]) for row in await cursor.fetchall()]

    async def closed_positions_since(self, timestamp: int | None = None) -> list[Position]:
        sql = (
            f"SELECT {_POSITION_COLUMNS} FROM positions p WHERE status = 'closed' "
            "AND EXISTS (SELECT 1 FROM signals s WHERE s.chain = p.chain "
            "AND s.mint = p.mint AND s.outcome = 'bought')"
        )
        params: tuple[int, ...] = ()
        if timestamp is not None:
            sql += " AND COALESCE(closed_at, opened_at) >= ?"
            params = (timestamp,)
        sql += " ORDER BY COALESCE(closed_at, opened_at)"
        cursor = await self.db.execute(sql, params)
        return [Position(*row) for row in await cursor.fetchall()]

    async def open_position_count(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) FROM positions WHERE status = 'open'")
        (count,) = await cursor.fetchone()
        return count

    async def record_price_snapshot(
        self, mint: str, price: float, chain: str = "robinhood"
    ) -> None:
        await self.db.execute(
            "INSERT INTO price_snapshots (chain, mint, price, observed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chain, mint) DO UPDATE SET price = excluded.price, "
            "observed_at = excluded.observed_at",
            (chain, mint, price, int(time.time())),
        )
        await self.db.commit()

    async def set_runtime_health(self, component: str, status: str, value: float) -> None:
        await self.db.execute(
            "INSERT INTO runtime_health (component, status, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(component) DO UPDATE SET status=excluded.status, value=excluded.value, "
            "updated_at=excluded.updated_at",
            (component, status, value, int(time.time())),
        )
        await self.db.commit()

    # --- signal log (for /stats) -------------------------------------------

    async def log_signal(
        self, mint: str, symbol: str | None, score: float | None, stage: str,
        outcome: str, detail: str = "", chain: str = "robinhood",
    ) -> None:
        await self.db.execute(
            "INSERT INTO signals (chain, mint, symbol, score, stage, outcome, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chain, mint, symbol, score, stage, outcome, detail, int(time.time())),
        )
        await self.db.commit()

    async def signals_since(self, timestamp: int | None = None) -> list[dict[str, object]]:
        sql = "SELECT mint, symbol, score, stage, outcome, detail, created_at, chain FROM signals"
        params: tuple[int, ...] = ()
        if timestamp is not None:
            sql += " WHERE created_at >= ?"
            params = (timestamp,)
        sql += " ORDER BY created_at"
        cursor = await self.db.execute(sql, params)
        return [
            {
                "mint": row[0],
                "symbol": row[1],
                "score": row[2],
                "stage": row[3],
                "outcome": row[4],
                "detail": row[5],
                "created_at": row[6],
                "chain": row[7],
            }
            for row in await cursor.fetchall()
        ]

    async def stats(self) -> dict[str, int | float | str]:
        day_ago = int(time.time()) - 86400
        cursor = await self.db.execute("SELECT COUNT(*) FROM users")
        (users_total,) = await cursor.fetchone()

        cursor = await self.db.execute("SELECT COUNT(*) FROM signals WHERE created_at >= ?", (day_ago,))
        (screened_24h,) = await cursor.fetchone()

        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM signals WHERE outcome = 'bought' AND created_at >= ?", (day_ago,)
        )
        (bought_24h,) = await cursor.fetchone()

        cursor = await self.db.execute(
            "SELECT chain, COUNT(*) FROM signals WHERE created_at >= ? GROUP BY chain ORDER BY chain",
            (day_ago,),
        )
        chain_rows = await cursor.fetchall()
        chains_24h = ", ".join(f"{chain}: {count}" for chain, count in chain_rows) or "—"

        trades_today, pnl_today = await self.get_daily_counters()
        open_positions = await self.open_position_count()

        cursor = await self.db.execute("SELECT COUNT(*) FROM creators WHERE rugs > 0")
        (blocked_creators,) = await cursor.fetchone()

        return {
            "users_total": users_total,
            "screened_24h": screened_24h,
            "bought_24h": bought_24h,
            "chains_24h": chains_24h,
            "trades_today": trades_today,
            "pnl_today": round(pnl_today, 4),
            "open_positions": open_positions,
            "blocked_creators": blocked_creators,
        }
