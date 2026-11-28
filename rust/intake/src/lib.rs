use std::path::PathBuf;
use std::time::Duration;

use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use rusqlite::functions::FunctionFlags;
use rusqlite::{params, Connection, OpenFlags, OptionalExtension, Row, TransactionBehavior};

type Receipt = (String, String, String);

fn receipt(row: &Row<'_>) -> rusqlite::Result<Receipt> {
    Ok((row.get(0)?, row.get(1)?, row.get(2)?))
}

fn register_record(path: PathBuf, key: Vec<u8>, name: String) -> rusqlite::Result<Option<Receipt>> {
    let mut db =
        Connection::open_with_flags(path, OpenFlags::default() | OpenFlags::SQLITE_OPEN_NOFOLLOW)?;
    db.busy_timeout(Duration::from_secs(10))?;
    db.pragma_update(None, "trusted_schema", false)?;
    // 初回作成と再送判定も同じトランザクションに含める。
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS receipts (
            key_digest BLOB NOT NULL PRIMARY KEY,
            receipt_id TEXT NOT NULL UNIQUE DEFAULT ('int-' || lower(hex(randomblob(16)))),
            name TEXT NOT NULL,
            registered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE INDEX IF NOT EXISTS receipts_by_name ON receipts(name COLLATE NOCASE, receipt_id);",
    )?;
    let existing = tx
        .query_row(
            "SELECT receipt_id, name, registered_at FROM receipts WHERE key_digest = ?1",
            [&key],
            receipt,
        )
        .optional()?;
    let result = match existing {
        Some(previous) => (previous.1 == name).then_some(previous),
        None => Some(tx.query_row(
            "INSERT INTO receipts (key_digest, name) VALUES (?1, ?2)
             RETURNING receipt_id, name, registered_at",
            params![key, name],
            receipt,
        )?),
    };
    tx.commit()?;
    Ok(result)
}

#[pyfunction]
fn register(
    py: Python<'_>,
    path: PathBuf,
    key: Vec<u8>,
    name: String,
) -> PyResult<Option<Receipt>> {
    py.detach(move || register_record(path, key, name))
        .map_err(|_| PyOSError::new_err("案件受付台帳を更新できません"))
}

#[pymodule]
fn _intake_index(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(register, module)?)?;
    module.add_function(wrap_pyfunction!(link, module)?)?;
    module.add_function(wrap_pyfunction!(search, module)?)?;
    Ok(())
}

type SearchRow = (String, String, String, Option<String>);

#[pyfunction]
#[pyo3(signature = (path, query, exact_id, limit, secrets = Vec::new()))]
fn search(
    py: Python<'_>,
    path: PathBuf,
    query: String,
    exact_id: bool,
    limit: u16,
    secrets: Vec<String>,
) -> PyResult<Vec<SearchRow>> {
    py.detach(move || search_records(path, query, exact_id, limit, secrets))
        .map_err(|_| PyOSError::new_err("受付台帳を読み取れません"))
}

fn prefix_pattern(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_")
        + "%"
}

fn search_records(
    path: PathBuf,
    query: String,
    exact_id: bool,
    limit: u16,
    secrets: Vec<String>,
) -> rusqlite::Result<Vec<SearchRow>> {
    use rusqlite::types::Value;
    let mut db = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NOFOLLOW,
    )?;
    db.busy_timeout(Duration::from_secs(5))?;
    db.pragma_update(None, "trusted_schema", false)?;
    let masked = !secrets.is_empty();
    if masked {
        let flags = FunctionFlags::SQLITE_UTF8
            | FunctionFlags::SQLITE_DETERMINISTIC
            | FunctionFlags::SQLITE_DIRECTONLY;
        db.create_scalar_function("display_text", 1, flags, move |ctx| {
            let mut text: String = ctx.get(0)?;
            for secret in &secrets {
                text = text.replace(secret.as_str(), "[REDACTED]");
            }
            Ok(text)
        })?;
    }
    let tx = db.transaction()?;
    // 初回連結前の台帳でも、同じスナップショットを移行せずに読む。
    let has_links: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='receipt_campaigns')",
        [],
        |row| row.get(0),
    )?;
    let mut sql = String::from("SELECT receipts.receipt_id, name, registered_at, ");
    sql.push_str(if has_links {
        "campaign_id FROM receipts LEFT JOIN receipt_campaigns USING(receipt_id) "
    } else {
        "NULL FROM receipts "
    });
    let mut args = vec![Value::Integer(i64::from(limit))];
    if exact_id {
        sql.push_str("WHERE receipts.receipt_id = ?2 ");
        if masked {
            sql.push_str("AND display_text(receipts.receipt_id) = ?2 ");
        }
        args.push(Value::Text(query));
    } else if query.is_empty() {
        sql.push_str("ORDER BY receipts.rowid DESC ");
    } else if masked {
        sql.push_str(r"WHERE display_text(name) LIKE ?2 ESCAPE '\' ");
        args.push(Value::Text(prefix_pattern(&query)));
        // マスクは '[' から始まる。この前までの接頭辞は元の索引でも絞り込める。
        let unchanged_prefix = query.split('[').next().unwrap_or("");
        if !unchanged_prefix.is_empty() {
            sql.push_str(r"AND name LIKE ?3 ESCAPE '\' ");
            args.push(Value::Text(prefix_pattern(unchanged_prefix)));
        }
        sql.push_str("ORDER BY display_text(name) COLLATE NOCASE, display_text(receipts.receipt_id), receipts.rowid ");
    } else {
        sql.push_str(
            r"WHERE name LIKE ?2 ESCAPE '\' ORDER BY name COLLATE NOCASE, receipts.receipt_id ",
        );
        args.push(Value::Text(prefix_pattern(&query)));
    }
    sql.push_str("LIMIT ?1");
    let mut statement = tx.prepare(&sql)?;
    let rows = statement.query_map(rusqlite::params_from_iter(args), |row| {
        Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
    })?;
    rows.collect()
}

#[pyfunction]
fn link(
    py: Python<'_>,
    path: PathBuf,
    receipt_id: String,
    campaign_id: String,
) -> PyResult<Option<String>> {
    py.detach(move || link_record(path, receipt_id, campaign_id))
        .map_err(|_| PyOSError::new_err("案件受付台帳を更新できません"))
}

fn link_record(
    path: PathBuf,
    receipt_id: String,
    campaign_id: String,
) -> rusqlite::Result<Option<String>> {
    let mut db = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NOFOLLOW,
    )?;
    db.busy_timeout(Duration::from_secs(10))?;
    db.pragma_update(None, "trusted_schema", false)?;
    db.pragma_update(None, "foreign_keys", true)?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let exists: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM receipts WHERE receipt_id = ?1)",
        [&receipt_id],
        |row| row.get(0),
    )?;
    if !exists {
        return Ok(None);
    }
    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS receipt_campaigns (
            receipt_id TEXT PRIMARY KEY REFERENCES receipts(receipt_id),
            campaign_id TEXT NOT NULL
        )",
    )?;
    tx.execute(
        "INSERT INTO receipt_campaigns(receipt_id, campaign_id) VALUES (?1, ?2)
         ON CONFLICT(receipt_id) DO NOTHING",
        params![receipt_id, campaign_id],
    )?;
    let saved = tx.query_row(
        "SELECT campaign_id FROM receipt_campaigns WHERE receipt_id = ?1",
        [&receipt_id],
        |row| row.get(0),
    )?;
    tx.commit()?;
    Ok(Some(saved))
}
