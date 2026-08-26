import Database from 'better-sqlite3';
import fs from 'node:fs';
import path from 'node:path';
import { config } from '../config.js';
import { SCHEMA_SQL } from './schema.js';

let instance: Database.Database | null = null;

export function db(): Database.Database {
  if (instance) return instance;

  fs.mkdirSync(path.dirname(config.databasePath), { recursive: true });
  const conn = new Database(config.databasePath);
  conn.pragma('journal_mode = WAL');

  conn.exec(SCHEMA_SQL);
  conn.prepare('INSERT OR IGNORE INTO settings (id) VALUES (1)').run();

  instance = conn;
  return conn;
}

export function closeDb(): void {
  instance?.close();
  instance = null;
}
