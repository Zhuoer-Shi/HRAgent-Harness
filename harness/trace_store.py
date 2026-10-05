# -*- coding: utf-8 -*-
"""
TraceStore —— 把每次运行的 span 链路落进 SQLite，让失败可以被查询、聚合、归因。

为什么需要它（对应 PRD 5.6 / FR-006 的「失败可定位率」）：
- 之前 Trace 只落 JSON 文件，每份文件只有 trace_id + spans，
  **没有记录这是哪个用例、哪一轮、过没过、失败断言是什么**。
- 于是你没法回答一个面试官必问的问题：「你的失败，哪些是引擎缺陷、
  哪些是模型内容质量问题、哪些是评测断言写错了？」—— 因为数据根本查不出来。
- 这个类用 sqlite3（Python 标准库，零第三方依赖）落两张表：
  - traces：一条运行一行，带 case_id / 轮次 / 通过与否 / 失败断言 / 用量。
  - spans ：每次运行的完整 span 树，支持单条回放。

诚实口径：这是**轻量自建的可观测存储**，不是 OpenTelemetry / 生产级 Trace 平台。
对「验证一个受控 Agent 运行时」这个目标够用；真实生产要上标准方案。
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id      TEXT PRIMARY KEY,
    case_id       TEXT,
    case_title    TEXT,
    category      TEXT,
    round         INTEGER,
    backend       TEXT,
    status        TEXT,          -- 运行结束状态（done / awaiting_input / error ...）
    passed        INTEGER,       -- 裁判是否判过（1/0）
    reason        TEXT,
    failed_checks TEXT,          -- 失败断言列表，JSON 数组
    span_count    INTEGER,
    tool_calls    INTEGER,
    gate_count    INTEGER,
    failed_spans  INTEGER,
    total_tokens  INTEGER,
    total_ms      REAL,
    created_at    REAL
);
CREATE TABLE IF NOT EXISTS spans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT,
    span_id      TEXT,
    parent_id    TEXT,
    type         TEXT,
    name         TEXT,
    input        TEXT,
    output       TEXT,
    duration_ms  REAL,
    tokens       INTEGER,
    status       TEXT,           -- ok / failed / repaired
    error        TEXT,
    meta         TEXT            -- JSON 文本
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_traces_passed ON traces(passed);
"""


class TraceStore:
    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- 写入 ----------

    def save_trace(self, trace: Dict[str, Any]) -> None:
        """落一条 trace + 它的所有 span（同 trace_id 重复落则替换，支持重跑）。"""
        s = trace.get("summary") or {}
        failed_checks = trace.get("failed_checks") or []
        self.conn.execute(
            """
            INSERT OR REPLACE INTO traces
            (trace_id, case_id, case_title, category, round, backend, status,
             passed, reason, failed_checks, span_count, tool_calls, gate_count,
             failed_spans, total_tokens, total_ms, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trace["trace_id"],
                trace.get("case_id", ""),
                trace.get("case_title", ""),
                trace.get("category", ""),
                trace.get("round", 1),
                trace.get("backend", ""),
                trace.get("status", ""),
                1 if trace.get("passed") else 0,
                trace.get("reason", ""),
                json.dumps(failed_checks, ensure_ascii=False),
                int(s.get("span_count", 0)),
                int(s.get("tool_calls", 0)),
                int(s.get("gate_count", 0)),
                int(s.get("failed_spans", 0)),
                int(s.get("total_tokens", 0)),
                float(s.get("total_ms", 0)),
                time.time(),
            ),
        )
        # 先清掉旧 span（重跑时避免残留），再写入最新
        self.conn.execute("DELETE FROM spans WHERE trace_id=?", (trace["trace_id"],))
        for sp in trace.get("spans", []):
            self.conn.execute(
                """
                INSERT INTO spans
                (trace_id, span_id, parent_id, type, name, input, output,
                 duration_ms, tokens, status, error, meta)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    trace["trace_id"],
                    sp.get("span_id", ""),
                    sp.get("parent_id"),
                    sp.get("type", ""),
                    sp.get("name", ""),
                    sp.get("input", ""),
                    sp.get("output", ""),
                    float(sp.get("duration_ms", 0)),
                    int(sp.get("tokens", 0)),
                    sp.get("status", "ok"),
                    sp.get("error", ""),
                    json.dumps(sp.get("meta") or {}, ensure_ascii=False),
                ),
            )
        self.conn.commit()

    # ---------- 读取 ----------

    def stats(self) -> Dict[str, Any]:
        """总览：trace 数、span 数、失败数、各 span 类型分布。"""
        n_traces = self.conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        n_spans = self.conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
        n_failed_traces = self.conn.execute(
            "SELECT COUNT(*) FROM traces WHERE passed=0"
        ).fetchone()[0]
        n_failed_spans = self.conn.execute(
            "SELECT COUNT(*) FROM spans WHERE status='failed'"
        ).fetchone()[0]
        span_types = {
            r["type"]: r["c"]
            for r in self.conn.execute(
                "SELECT type, COUNT(*) AS c FROM spans GROUP BY type ORDER BY c DESC"
            )
        }
        return {
            "n_traces": n_traces,
            "n_spans": n_spans,
            "n_failed_traces": n_failed_traces,
            "n_failed_spans": n_failed_spans,
            "span_types": span_types,
        }

    def list_traces(self, limit: int = 500) -> List[Dict[str, Any]]:
        """trace 列表（倒序，最新的在前）。"""
        rows = self.conn.execute(
            """
            SELECT trace_id, case_id, case_title, category, round, backend, status,
                   passed, reason, failed_checks, span_count, gate_count,
                   failed_spans, total_tokens, total_ms
            FROM traces ORDER BY created_at DESC, trace_id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["failed_checks"] = json.loads(d["failed_checks"] or "[]")
            out.append(d)
        return out

    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """单条 trace + 完整 span 树（按 parent_id 构建，供回放）。"""
        t = self.conn.execute(
            "SELECT * FROM traces WHERE trace_id=?", (trace_id,)
        ).fetchone()
        if t is None:
            return None
        d = dict(t)
        d["failed_checks"] = json.loads(d["failed_checks"] or "[]")
        spans = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM spans WHERE trace_id=? ORDER BY id", (trace_id,)
            ).fetchall()
        ]
        for sp in spans:
            sp["meta"] = json.loads(sp["meta"] or "{}")
        d["spans"] = spans
        return d

    def failed_span_breakdown(self) -> List[Dict[str, Any]]:
        """失败归因：按「失败的 span 类型 × 失败 span 名」聚合，找最常见的故障点。"""
        rows = self.conn.execute(
            """
            SELECT type, name, status, COUNT(*) AS c
            FROM spans
            WHERE status IN ('failed', 'repaired')
            GROUP BY type, name, status
            ORDER BY c DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
