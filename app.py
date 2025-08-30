from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import altair as alt
import psycopg  # psycopg3

DB_URL = st.secrets.get("DATABASE_URL") or os.getenv("DATABASE_URL")
IS_PG = True

# Use Eastern Time for 'today' calculations
LOCAL_TZ = ZoneInfo("America/New_York")


def get_conn():
    """Return a Postgres connection to Neon DB."""
    if not DB_URL:
        st.error("`DATABASE_URL` is not set. Please configure your Neon DB URL.")
        st.stop()
    return psycopg.connect(DB_URL)


def Q(sql: str) -> str:
    """Convert SQLite-style '?' placeholders to psycopg '%s'."""
    return sql.replace("?", "%s")


def init_db() -> None:
    with get_conn() as conn:
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
        # Ensure date of birth column exists
        conn.execute("ALTER TABLE babies ADD COLUMN IF NOT EXISTS dob DATE")
        # Create weights table for tracking baby weight over time
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weights (
                id BIGSERIAL PRIMARY KEY,
                baby_id INTEGER NOT NULL REFERENCES babies(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                weight REAL NOT NULL,
                UNIQUE (baby_id, date)
            );
            """
        )


def delete_entry(conn, baby_id: int, when: datetime) -> int:
    ts = when.isoformat()
    cur = conn.execute(Q("DELETE FROM entries WHERE baby_id = ? AND ts = ?;"), (baby_id, ts))
    return cur.rowcount


def delete_day(conn, baby_id: int, day: date) -> int:
    start_dt = datetime.combine(day, time(0, 0))
    end_dt = datetime.combine(day, time(23, 59, 59))
    cur = conn.execute(
        Q("DELETE FROM entries WHERE baby_id = ? AND ts BETWEEN ? AND ?;"),
        (baby_id, start_dt.isoformat(), end_dt.isoformat()),
    )
    return cur.rowcount


def delete_all_for_baby(conn, baby_id: int) -> int:
    cur = conn.execute(Q("DELETE FROM entries WHERE baby_id = ?;"), (baby_id,))
    return cur.rowcount


def delete_everything(conn) -> None:
    conn.execute("DELETE FROM entries;")
    conn.execute("DELETE FROM babies;")

def delete_baby(conn, baby_id: int) -> int:
    cur = conn.execute(Q("DELETE FROM babies WHERE id = ?;"), (baby_id,))
    return cur.rowcount


def list_babies(conn) -> list[str]:
    cur = conn.execute("SELECT name FROM babies ORDER BY name ASC;")
    return [r[0] for r in cur.fetchall()]


def get_or_create_baby(conn, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Baby name cannot be empty")
    cur = conn.execute(Q("SELECT id FROM babies WHERE name = ?;"), (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(Q("INSERT INTO babies (name) VALUES (?) RETURNING id;"), (name,))
    return cur.fetchone()[0]


def upsert_entry(
    conn,
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
    # load all entries for baby, then filter by timestamp range in Python for correct inclusion
    cur = conn.execute(
        Q("""
        SELECT e.ts, e.milk, e.pee, e.poop
        FROM entries e
        WHERE e.baby_id = ?
        ORDER BY e.ts ASC;
        """),
        (baby_id,)
    )
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=["ts", "milk", "pee", "poop"])
    df = pd.DataFrame(rows, columns=["ts", "milk", "pee", "poop"])
    df["ts"] = pd.to_datetime(df["ts"])  # parse ISO timestamps
    # apply start/end filter in Python to handle timezone-aware strings
    df = df.loc[(df["ts"] >= start) & (df["ts"] <= end)]
    df["date"] = df["ts"].dt.date
    df["hour"] = df["ts"].dt.strftime("%I:%M %p")
    return df


def timeframe_to_range(option: str, custom_range: tuple[date, date] | None) -> tuple[datetime, datetime]:
    today = datetime.now(LOCAL_TZ).date()
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

    # Daily totals per event (stacked bar chart)
    daily_chart = (
        alt.Chart(df_long)
        .transform_aggregate(
            count="count()",
            groupby=["date", "event"],
        )
        .mark_bar()
        .encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y("count:Q", title="Count"),
            color=alt.Color("event:N", title="Event"),
            tooltip=["date:T", "event:N", alt.Tooltip("count:Q", title="Count")],
        )
        .properties(height=220)
    )
    st.subheader("Daily totals per event (stacked)")
    st.altair_chart(daily_chart, use_container_width=True)

    # Scatter plot of events over time, faceted by day
    scatter = (
        alt.Chart(df_long)
        .mark_point(size=60)
        .encode(
            x=alt.X(
                "ts:T",
                title="Timestamp",
                axis=alt.Axis(format="%I:%M %p", labelAngle=-45, labelOverlap=True),
                scale=alt.Scale(nice="hour"),
            ),
            y=alt.Y("event:N", sort=["Milk", "Poop", "Pee"], title="Event"),
            color=alt.Color("event:N", title="Event"),
            tooltip=["ts:T", "event:N"],
        )
        .facet(row=alt.Row("date:T", title="Date"))
        .resolve_scale(x="independent", y="shared")
    )
    st.subheader("Event scatter plots by day")
    st.altair_chart(scatter, use_container_width=True)

    # Compute average interval between events
    st.subheader("Average interval between events")
    avg_list = []
    for event, group in df_long.groupby("event"):
        times = group["ts"].sort_values()
        if len(times) >= 2:
            avg_delta = times.diff().mean()
            avg_list.append({"Event": event.title(), "Avg Interval": avg_delta})
        else:
            avg_list.append({"Event": event.title(), "Avg Interval": pd.NaT})
    avg_df = pd.DataFrame(avg_list)
    # Format intervals as strings
    avg_df["Avg Interval"] = avg_df["Avg Interval"].apply(
        lambda td: str(td) if pd.notnull(td) else "N/A"
    )
    st.table(avg_df)


def main() -> None:
    st.set_page_config(page_title="BabyData", page_icon="🍼", layout="wide")
    # DataFrame serialization uses Arrow by default in modern Streamlit
    st.title("BabyData: Hourly Baby Log 🍼")
    st.caption("Track milk, #1, and #2 by hour, with history and charts.")
    # Compute local today for entry defaults
    local_today = datetime.now(LOCAL_TZ).date()

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

    # Date of birth input and persistence
    with get_conn() as conn:
        cur = conn.execute(Q("SELECT dob FROM babies WHERE id = %s;"), (baby_id,))
        existing_dob = cur.fetchone()[0]
    dob_default = existing_dob or local_today
    dob = st.sidebar.date_input("Date of birth", value=dob_default)
    if dob != existing_dob and st.sidebar.button("Save DOB", key="save_dob"):
        with get_conn() as conn:
            conn.execute(Q("UPDATE babies SET dob = %s WHERE id = %s;"), (dob.isoformat(), baby_id))
            conn.commit()
        st.sidebar.success("Saved date of birth.")

    # Entry form
    st.subheader("Add or update an hourly entry")
    time_slots = [time(h, m) for h in range(24) for m in (0, 30)]
    col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
    with col1:
        entry_date = st.date_input("Date", value=local_today)
    with col2:
        selected_time = st.selectbox("Time", options=time_slots, format_func=lambda t: t.strftime("%I:%M %p"))

    # Pre-fill checkboxes with existing entry data if present
    entry_when = datetime.combine(entry_date, selected_time)
    with get_conn() as conn:
        cur = conn.execute(
            Q("""
            SELECT milk, pee, poop
            FROM entries
            WHERE baby_id = ? AND ts = ?;
            """),
            (baby_id, entry_when.isoformat()),
        )
        row = cur.fetchone()
    if row:
        default_milk, default_pee, default_poop = bool(row[0]), bool(row[1]), bool(row[2])
        # Warn user that existing data will be overwritten
        st.warning(
            f"Existing entry on {entry_when.strftime('%Y-%m-%d %I:%M %p')} detected; saving will overwrite it."
        )
    else:
        default_milk, default_pee, default_poop = False, False, False

    with col3:
        milk = st.checkbox("Milk 🍼", value=default_milk)
    with col4:
        pee = st.checkbox("#1 💧", value=default_pee)
        poop = st.checkbox("#2 💩", value=default_poop)

    when = datetime.combine(entry_date, selected_time)
    if st.button("Save entry", type="primary"):
        with get_conn() as conn:
            upsert_entry(conn, baby_id, when, milk, pee, poop)
            conn.commit()
        st.success(f"Saved {baby_name}'s entry for {when.strftime('%Y-%m-%d %I:%M %p')}")

    with st.expander("Manage data (delete)"):
        # Require correct DOB before allowing deletions
        confirm_del_dob = st.date_input("Confirm baby's date of birth to delete", value=dob, key="confirm_del_dob")
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
                if confirm_del_dob != dob:
                    st.error("Date of birth does not match; delete aborted.")
                else:
                    with get_conn() as conn:
                        count = delete_entry(conn, baby_id, del_when)
                        conn.commit()
                    st.warning(f"Deleted {count} entry for {del_when.strftime('%Y-%m-%d %I:%M %p')}")

    st.divider()

    # Weight tracking
    with st.expander("Track weight"):
        wt_date = st.date_input("Weight date", value=local_today, key="weight_date")
        weight_lbs = st.number_input("Pounds", min_value=0, step=1, format="%d", key="weight_lbs")
        weight_oz = st.number_input("Ounces", min_value=0, max_value=15, step=1, format="%d", key="weight_oz")
        if st.button("Save weight", key="save_weight"):
            total_weight = weight_lbs + weight_oz / 16
            with get_conn() as conn:
                conn.execute(
                    Q("""
                    INSERT INTO weights (baby_id, date, weight)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (baby_id, date) DO UPDATE SET weight = excluded.weight;
                    """),
                    (baby_id, wt_date.isoformat(), total_weight),
                )
                conn.commit()
            st.success(f"Saved weight for {wt_date.isoformat()}: {weight_lbs} lb {weight_oz} oz")

    # History and charts
    st.subheader("History & insights")
    # Compute local today for default date inputs
    local_today = datetime.now(LOCAL_TZ).date()

    # Quick overview of most recent events
    with get_conn() as conn:
        cur = conn.execute(
            Q("""
            SELECT
                MAX(ts) FILTER (WHERE milk = 1) AS last_milk,
                MAX(ts) FILTER (WHERE pee  = 1) AS last_pee,
                MAX(ts) FILTER (WHERE poop = 1) AS last_poop
            FROM entries
            WHERE baby_id = %s;
            """),
            (baby_id,),
        )
        last_milk_ts, last_pee_ts, last_poop_ts = cur.fetchone()
    cols_last = st.columns(3)
    for label, ts_str, col in [
        ("Milk", last_milk_ts, cols_last[0]),
        ("#1", last_pee_ts, cols_last[1]),
        ("#2", last_poop_ts, cols_last[2]),
    ]:
        if ts_str:
            ts = datetime.fromisoformat(ts_str)
            col.metric(f"Last {label}", ts.strftime("%Y-%m-%d %I:%M %p"))
        else:
            col.metric(f"Last {label}", "None")
    timeframe = st.selectbox(
        "Timeframe",
        ["Today", "Last 3 days", "Last 7 days", "Last 30 days", "Custom"],
        index=0,
    )

    custom_range = None
    if timeframe == "Custom":
        custom_range = st.date_input(
            "Pick date range",
            value=(local_today - timedelta(days=6), local_today),
        )
        if isinstance(custom_range, date):
            custom_range = (custom_range, custom_range)

    start_dt, end_dt = timeframe_to_range(timeframe, custom_range)

    with get_conn() as conn:
        df = fetch_entries(conn, baby_id, start_dt, end_dt)

    # Day of life metric
    with get_conn() as conn:
        cur = conn.execute(Q("SELECT dob FROM babies WHERE id = %s;"), (baby_id,))
        dob_val = cur.fetchone()[0]
    if dob_val:
        day_of_life = (local_today - dob_val).days + 1
        st.metric("Day of life", day_of_life)
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

    # Weight trend chart
    with get_conn() as conn:
        w_rows = conn.execute(
            Q("SELECT date, weight FROM weights WHERE baby_id = %s ORDER BY date ASC;"),
            (baby_id,),
        ).fetchall()
    if w_rows:
        w_df = pd.DataFrame(w_rows, columns=["date", "weight"])
        w_df["date"] = pd.to_datetime(w_df["date"])  # parse dates
        weight_chart = (
            alt.Chart(w_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y("weight:Q", title="Weight"),
                tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("weight:Q", title="Weight")],
            )
        )
        st.subheader("Weight over time")
        st.altair_chart(weight_chart, use_container_width=True)


if __name__ == "__main__":
    main()
