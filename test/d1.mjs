// Minimal D1 stand-in over node:sqlite, enough for the Worker's queries.
import { DatabaseSync } from "node:sqlite";
import fs from "node:fs";

class Statement {
  constructor(db, sql, params = []) { this.db = db; this.sql = sql; this.params = params; }
  bind(...params) { return new Statement(this.db, this.sql, params.map((p) => (p === undefined ? null : p))); }
  async all() { return { results: this.db.prepare(this.sql).all(...this.params) }; }
  async first() { return this.db.prepare(this.sql).get(...this.params) ?? null; }
  async run() { this.db.prepare(this.sql).run(...this.params); return { success: true }; }
}

export function makeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(fs.readFileSync(new URL("../worker/migrations/0001_init.sql", import.meta.url), "utf8"));
  return {
    raw: db,
    prepare: (sql) => new Statement(db, sql),
    batch: async (stmts) => { for (const s of stmts) await s.run(); return []; },
  };
}
