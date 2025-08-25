# BabyData Streamlit App

A simple Streamlit app to log a baby's hourly events: Milk, #1 (pee), and #2 (poop). Supports multiple babies, persists data locally in SQLite, and includes charts and filters over time.

## Run locally

1. Optional: create and activate a virtual environment

   - Python 3: `python3 -m venv .venv && source .venv/bin/activate`

2. Install dependencies

   - `pip install -r requirements.txt`

3. Start the app

   - `streamlit run app.py`

The first run creates a local SQLite database file `babydata.db` in this folder.

## Notes

- If `pip` is missing on Raspberry Pi OS Lite: `sudo apt install -y python3-pip python3-venv`.
- To stop the app, press `Ctrl+C` in the terminal.
- Data is stored locally; back up `babydata.db` to save history.

