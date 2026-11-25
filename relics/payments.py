"""Durable record of simulated reroll payments."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from core.storage import RUNTIME
from relics.calculator import REROLL_COST


ChestCounts = tuple[int, int, int]
PAYMENT_STORE_ERROR = 'Reroll purchases blocked because the payment record could not be accessed.'


@dataclass(frozen=True)
class PendingPayment:
    id: int
    balance_before: int
    expected_pool: ChestCounts

    @property
    def balance_after(self):
        return self.balance_before - REROLL_COST

    def blocked_message(self):
        pool = ', '.join(map(str, self.expected_pool))
        return (f'Next purchase blocked: a {REROLL_COST}-gem payment is unresolved. '
                f'To confirm it, read a fresh relic menu with {self.balance_after} gems '
                f'and the full returned chest pool ({pool} unopened, none refundable).')


class PaymentStore:
    def __init__(self, path: Path = RUNTIME / 'relic-payments.sqlite3'):
        self.path = path

    def pending(self) -> PendingPayment | None:
        try:
            self.path.stat()
        except FileNotFoundError:
            return None
        uri = self.path.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
            row = db.execute(
                'SELECT id, balance, relic, mythic, divine FROM payments WHERE pending = 1'
            ).fetchone()
        return PendingPayment(row[0], row[1], tuple(row[2:])) if row else None

    def reserve(self, balance: int, pool: ChestCounts) -> PendingPayment | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            db.execute('PRAGMA synchronous = FULL')
            db.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY, balance INTEGER NOT NULL,
                    relic INTEGER NOT NULL, mythic INTEGER NOT NULL, divine INTEGER NOT NULL,
                    pending INTEGER UNIQUE CHECK (pending = 1)
                )
            ''')
            insert = db.execute('''
                INSERT INTO payments (balance, relic, mythic, divine, pending)
                VALUES (?, ?, ?, ?, 1) ON CONFLICT(pending) DO NOTHING
            ''', (balance, *pool))
            return PendingPayment(insert.lastrowid, balance, pool) if insert.rowcount else None

    def confirm(self, payment: PendingPayment) -> bool:
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            db.execute('PRAGMA synchronous = FULL')
            return db.execute(
                'UPDATE payments SET pending = NULL WHERE id = ? AND pending = 1', (payment.id,)
            ).rowcount == 1
