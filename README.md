# BabyData Streamlit App

A simple Streamlit app to log a baby's hourly events: Milk, #1 (pee), and #2 (poop). Supports multiple babies, persists data locally in SQLite, and includes charts and filters over time.

## Run locally

1. Optional: create and activate a virtual environment

   - Python 3: `python3 -m venv .venv && source .venv/bin/activate`

2. Install dependencies

   - `pip install -r requirements.txt`

3. Start the app

   - `streamlit run app.py`

4. Configure your Neon DB URL

   - Create `.streamlit/secrets.toml` with:

     ```toml
     DATABASE_URL = "<your Neon DB URL>"
     ```

   - Or set an environment variable:

     ```bash
     export DATABASE_URL="<your Neon DB URL>"
     ```

## Notes

- If `pip` is missing on Raspberry Pi OS Lite: `sudo apt install -y python3-pip python3-venv`.
- To stop the app, press `Ctrl+C` in the terminal.
- This app requires a Neon Postgres database; ensure `DATABASE_URL` is set as shown above.
