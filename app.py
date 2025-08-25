from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st
import altair as alt


APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "babydata.db"


DB_URL = st.secrets.get("DATABASE_URL") or os.getenv("DATABASE_URL")
IS_PG = bool(DB_URL)

if IS_PG:
    import psycopg  # psycopg3


def get_conn():
    """
    Return a DB connection usable with `with get_conn() as conn:` in both SQLite and Postgres.
    """
    if IS_PG:
        return psycopg.connect(DB_URL)  # sslmode handled in URL (Neon requires it)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn


def Q(sql: str) -> str:
    """
    Convert SQLite-style '?' placeholders to psycopg '%s' when using Postgres.
    """
    return sql.replace("?", "%s") if IS_PG else sql


def init_db() -> None:
    with get_conn() as conn:
        if IS_PG:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS babies (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    id BIGSERIAL PRIMARY KEY,
                    baby_id INTEGER NOT NULL REFERENCES babies(id) ON DELETE CASCADE,
                    ts TEXT NOT NULL,
                    milk INTEGER NOT NULL DEFAULT 0,
                    pee  INTEGER NOT NULL DEFAULT 0,
                    poop INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (baby_id, ts)
                );
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS babies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    baby_id INTEGER NOT NULL,
                    ts TEXT NOT NULL, -- ISO timestamp at minute resolution
                    milk INTEGER NOT NULL DEFAULT 0,
                    pee INTEGER NOT NULL DEFAULT 0,
                    poop INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (baby_id, ts),
                    FOREIGN KEY (baby_id) REFERENCES babies (id) ON DELETE CASCADE
                );
                """
            )


def delete_entry(conn: sqlite3.Connection, baby_id: int, when: datetime) -> int:
    ts = when.isoformat()
    cur = conn.execute(Q("DELETE FROM entries WHERE baby_id = ? AND ts = ?;"), (baby_id, ts))
    return cur.rowcount


def delete_day(conn: sqlite3.Connection, baby_id: int, day: date) -> int:
    start_dt = datetime.combine(day, time(0, 0))
    end_dt = datetime.combine(day, time(23, 59, 59))
    cur = conn.execute(
        Q("DELETE FROM entries WHERE baby_id = ? AND ts BETWEEN ? AND ?;"),
        (baby_id, start_dt.isoformat(), end_dt.isoformat()),
    )
    return cur.rowcount


def delete_all_for_baby(conn: sqlite3.Connection, baby_id: int) -> int:
    cur = conn.execute(Q("DELETE FROM entries WHERE baby_id = ?;"), (baby_id,))
    return cur.rowcount


def delete_everything(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM entries;")
    conn.execute("DELETE FROM babies;")

def delete_baby(conn: sqlite3.Connection, baby_id: int) -> int:
    cur = conn.execute(Q("DELETE FROM babies WHERE id = ?;"), (baby_id,))
    return cur.rowcount


def list_babies(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("SELECT name FROM babies ORDER BY name ASC;")
    return [r[0] for r in cur.fetchall()]


def get_or_create_baby(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Baby name cannot be empty")
    cur = conn.execute(Q("SELECT id FROM babies WHERE name = ?;"), (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(Q("INSERT INTO babies (name) VALUES (?);"), (name,))
    return cur.lastrowid


def upsert_entry(
    conn: sqlite3.Connection,
    baby_id: int,
    when: datetime,
    milk: bool,
    pee: bool,
    poop: bool,
) -> None:
    ts = when.isoformat()
    conn.execute(
        Q("""
        INSERT INTO entries (baby_id, ts, milk, pee, poop)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(baby_id, ts) DO UPDATE SET
            milk=excluded.milk,
            pee=excluded.pee,
            poop=excluded.poop;
        """),
        (baby_id, ts, int(milk), int(pee), int(poop)),
    )


def fetch_entries(
    conn: sqlite3.Connection,
    baby_id: int,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    cur = conn.execute(
        Q("""
        SELECT e.ts, e.milk, e.pee, e.poop
        FROM entries e
        WHERE e.baby_id = ? AND e.ts BETWEEN ? AND ?
        ORDER BY e.ts ASC;
        """),
        (baby_id, start.isoformat(), end.isoformat()),
    )
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=["ts", "milk", "pee", "poop"])  # empty
    df = pd.DataFrame(rows, columns=["ts", "milk", "pee", "poop"])
    df["ts"] = pd.to_datetime(df["ts"])  # parse ISO timestamps
    df["date"] = df["ts"].dt.date
    df["hour"] = df["ts"].dt.strftime("%I:%M %p")
    return df


def timeframe_to_range(option: str, custom_range: tuple[date, date] | None) -> tuple[datetime, datetime]:
    today = date.today()
    if option == "Today":
        start_d = today
        end_d = today
    elif option == "Last 3 days":
        start_d = today - timedelta(days=2)
        end_d = today
    elif option == "Last 7 days":
        start_d = today - timedelta(days=6)
        end_d = today
    elif option == "Last 30 days":
        start_d = today - timedelta(days=29)
        end_d = today
    else:  # Custom range
        if not custom_range or len(custom_range) != 2:
            start_d = today
            end_d = today
        else:
            start_d, end_d = custom_range
    start_dt = datetime.combine(start_d, time(0, 0))
    end_dt = datetime.combine(end_d, time(23, 59, 59))
    return start_dt, end_dt


def render_charts(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No entries for the selected range yet.")
        return

    # Aggregate per day and per event
    df_long = df.melt(id_vars=["ts", "date", "hour"], value_vars=["milk", "pee", "poop"],
                      var_name="event", value_name="value")
    df_long = df_long[df_long["value"] == 1]

    # Daily totals
    daily_chart = (
        alt.Chart(df_long)
        .mark_bar()
        .encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y("count():Q", title="Events"),
            color=alt.Color("event:N", title="Type"),
            tooltip=["date:T", "event:N", alt.Tooltip("count():Q", title="Events")],
        )
        .properties(height=220)
    )
    st.subheader("Daily totals")
    st.altair_chart(daily_chart, use_container_width=True)

    # Scatter plot of events over time
    scatter = (
        alt.Chart(df_long)
        .mark_point(size=60)
        .encode(
            x=alt.X(
                "ts:T",
                title="Timestamp",
                axis=alt.Axis(format="%I:%M %p"),
                scale=alt.Scale(nice="hour"),
            ),
            y=alt.Y("event:N", sort=["Milk", "Poop", "Pee"], title="Event"),
            color=alt.Color("event:N", title="Event"),
            tooltip=["ts:T", "event:N"],
        )
    )
    st.subheader("Event scatter plot")
    st.altair_chart(scatter, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="BabyData", page_icon="🍼", layout="wide")
    # DataFrame serialization uses Arrow by default in modern Streamlit
    st.title("BabyData: Hourly Baby Log 🍼")
    st.caption("Track milk, #1, and #2 by hour, with history and charts.")

    init_db()

    # Sidebar: choose or add baby
    st.sidebar.header("Baby")
    with get_conn() as conn:
        existing = list_babies(conn)

    mode = st.sidebar.radio("Select mode", ["Select existing", "Add new"], horizontal=True)
    if mode == "Select existing" and existing:
        baby_name = st.sidebar.selectbox("Choose a baby", existing)
    elif mode == "Select existing" and not existing:
        st.sidebar.info("No babies yet. Add one below.")
        baby_name = ""
    else:
        baby_name = st.sidebar.text_input("New baby name", value="")
        if st.sidebar.button("Add baby", use_container_width=True) and baby_name.strip():
            with get_conn() as conn:
                try:
                    _ = get_or_create_baby(conn, baby_name)
                    conn.commit()
                    st.sidebar.success(f"Added baby '{baby_name}'.")
                except sqlite3.IntegrityError:
                    st.sidebar.warning("Baby already exists.")

            with get_conn() as conn:
                existing = list_babies(conn)

    if not baby_name:
        st.info("Select or add a baby to begin logging.")
        return

    with get_conn() as conn:
        baby_id = get_or_create_baby(conn, baby_name)
        conn.commit()

    # Entry form
    st.subheader("Add or update an hourly entry")
    time_slots = [time(h, m) for h in range(24) for m in (0, 30)]
    col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
    with col1:
        entry_date = st.date_input("Date", value=date.today())
    with col2:
        selected_time = st.selectbox("Time", options=time_slots, format_func=lambda t: t.strftime("%I:%M %p"))
    with col3:
        milk = st.checkbox("Milk 🍼", value=False)
    with col4:
        pee = st.checkbox("#1 💧", value=False)
        poop = st.checkbox("#2 💩", value=False)

    when = datetime.combine(entry_date, selected_time)
    if st.button("Save entry", type="primary"):
        with get_conn() as conn:
            upsert_entry(conn, baby_id, when, milk, pee, poop)
            conn.commit()
        st.success(f"Saved {baby_name}'s entry for {when.strftime('%Y-%m-%d %I:%M %p')}")

    with st.expander("Manage data (delete)"):
        c1, c2, c3 = st.columns([2, 2, 3])
        with c1:
            del_time = st.selectbox(
                "Time to delete",
                options=time_slots,
                format_func=lambda t: t.strftime("%I:%M %p"),
                key="del_hour",
            )
            del_when = datetime.combine(entry_date, del_time)
            if st.button("Delete this time", key="btn_del_hour"):
                with get_conn() as conn:
                    count = delete_entry(conn, baby_id, del_when)
                    conn.commit()
                st.warning(f"Deleted {count} entry for {del_when.strftime('%Y-%m-%d %I:%M %p')}")

        with c2:
            del_day = st.date_input("Day to delete", value=entry_date, key="del_day")
            if st.button("Delete this day", key="btn_del_day"):
                with get_conn() as conn:
                    count = delete_day(conn, baby_id, del_day)
                    conn.commit()
                st.warning(f"Deleted {count} entries on {del_day.isoformat()}")

        with c3:
            st.markdown("Danger zone")
            scope = st.selectbox("Scope", ["Selected baby", "All babies"])
            confirm_text = st.text_input("Type DELETE to confirm", key="confirm_all")
            if st.button("Delete all data", key="btn_del_all"):
                if confirm_text != "DELETE":
                    st.error("Confirmation text does not match. Type DELETE to proceed.")
                else:
                    with get_conn() as conn:
                        if scope == "Selected baby":
                            count = delete_all_for_baby(conn, baby_id)
                            conn.commit()
                            st.error(f"Deleted {count} entries for {baby_name}.")
                        else:
                            delete_everything(conn)
                            conn.commit()
                            st.error("Deleted ALL babies and entries.")

            # Delete baby record
            confirm_baby = st.text_input("Type DELETE BABY to confirm", key="confirm_baby")
            if st.button("Delete baby", key="btn_del_baby"):
                if confirm_baby != "DELETE BABY":
                    st.error("Confirmation text does not match. Type DELETE BABY to proceed.")
                else:
                    with get_conn() as conn:
                        delete_baby(conn, baby_id)
                        conn.commit()
                    st.error(f"Deleted baby {baby_name} and all its data.")

    st.divider()

    # History and charts
    st.subheader("History & insights")
    timeframe = st.selectbox(
        "Timeframe",
        ["Today", "Last 3 days", "Last 7 days", "Last 30 days", "Custom"],
        index=2,
    )

    custom_range = None
    if timeframe == "Custom":
        custom_range = st.date_input("Pick date range", value=(date.today() - timedelta(days=6), date.today()))
        if isinstance(custom_range, date):
            custom_range = (custom_range, custom_range)

    start_dt, end_dt = timeframe_to_range(timeframe, custom_range)

    with get_conn() as conn:
        df = fetch_entries(conn, baby_id, start_dt, end_dt)

    # Show quick metrics
    if not df.empty:
        total_milk = int(df["milk"].sum())
        total_pee = int(df["pee"].sum())
        total_poop = int(df["poop"].sum())
        m1, m2, m3 = st.columns(3)
        m1.metric("Milk", total_milk)
        m2.metric("#1", total_pee)
        m3.metric("#2", total_poop)

    with st.expander("Show raw entries"):
        st.dataframe(df.sort_values("ts", ascending=False), use_container_width=True)

    render_charts(df)


if __name__ == "__main__":
    main()
