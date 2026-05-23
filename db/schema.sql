PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- categories
-- Simple lookup. parent_id allows optional subcategories.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO categories (name) VALUES
    ('Groceries'),
    ('Dining'),
    ('Transport'),
    ('Bills'),
    ('Rent'),
    ('Health'),
    ('Personal Care'),
    ('Living'),
    ('Shopping'),
    ('Entertainment'),
    ('Travel'),
    ('Income'),
    ('Transfers'),
    ('Other');

-- ------------------------------------------------------------
-- accounts
-- One row per card / bank account being ingested.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,         -- e.g. "Amex Platinum"
    type        TEXT NOT NULL                 -- 'credit' | 'debit' | 'savings'
                CHECK(type IN ('credit', 'debit', 'savings')),
    currency    TEXT NOT NULL DEFAULT 'AUD',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- merchants
-- Canonical merchant names, each pinned to a category.
-- Populated interactively via the CLI categorisation loop.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,         -- normalised, e.g. "Woolworths"
    category_id INTEGER NOT NULL REFERENCES categories(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- merchant_aliases
-- Maps raw messy description strings to a canonical merchant.
-- e.g. "WOOLWORTHS 3142 SYDNEY" -> merchants.id for "Woolworths"
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchant_aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_description TEXT NOT NULL UNIQUE,     -- exact string from bank
    merchant_id     INTEGER NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- transactions
-- Clean records only — raw scrape data is not persisted.
-- Amounts: negative = expense, positive = income/refund.
-- "Already processed" is inferred from the latest date per
-- account_id vs the statement window of each CSV file.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,            -- ISO 8601: 'YYYY-MM-DD'
    amount          REAL NOT NULL,            -- negative = expense, positive = income
    description     TEXT NOT NULL,            -- raw string from source
    merchant_id     INTEGER REFERENCES merchants(id) ON DELETE SET NULL,
    category_id     INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    notes           TEXT,                     -- optional manual annotation
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_date        ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category    ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_transactions_account     ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_merchant    ON transactions(merchant_id);