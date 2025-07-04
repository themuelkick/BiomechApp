import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import re
import plotly.graph_objects as go
import os
import io
import requests
from auth import sign_out
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === CONSTANTS ===
VIDEO_DIR = "videos"
DATA_DIR = "data"
DB_PATH = "pitcher_biomech.db"
os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

COLOR_MAP = {
    "TE": "#1f77b4",
    "FK": "#ff7f0e",
    "TS": "#2ca02c",
    "FH": "#d62728",
    "Angle 1 - o": "#9467bd",
    "Angle 1 - a": "#8c564b",
    "Angle 1 - b": "#e377c2"
}

def is_missing_file(path):
    if not path:
        return True
    if isinstance(path, str):
        if path.startswith("http"):
            # For Supabase URLs, you could optionally ping them, but for now assume always valid
            return False
        else:
            return not os.path.exists(path)
    return True

# === DB Init ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        team TEXT,
        notes TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER,
        date TEXT,
        session_name TEXT,
        video_source TEXT,
        kinovea_csv TEXT,
        notes TEXT,
        FOREIGN KEY(player_id) REFERENCES players(id)
    )''')
    conn.commit()
    conn.close()

init_db()

# === Utility Functions ===
def extract_youtube_id(url):
    patterns = [
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"v=([a-zA-Z0-9_-]{11})",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def plot_custom_lines(df, x_col="Time (ms)", chart_key="default", selected_metrics=None):
    fig = go.Figure()
    metrics = selected_metrics if selected_metrics else COLOR_MAP.keys()

    for col in df.columns:
        if col in metrics and col in COLOR_MAP and col != x_col:
            fig.add_trace(go.Scatter(
                x=df[x_col],
                y=df[col],
                mode='lines',
                name=col,
                line=dict(color=COLOR_MAP.get(col, "#cccccc"))
            ))
    fig.update_layout(
        xaxis_title=x_col,
        yaxis_title="Speed (px/s)",
        height=400,
        legend_title="Metric",
        template="simple_white"
    )
    st.plotly_chart(fig, use_container_width=True, key=chart_key)

# === MAIN APP ===
def main_app(user_email):
    st.title("Pitcher Biomechanics Tracker")
    st.success(f"Welcome, {user_email}!")

    if st.button("Logout"):
        sign_out()

    tab1, tab2, tab3, tab4 = st.tabs([" Upload Session", " View Sessions", " Compare Sessions", "Admin"])

    # === TAB 1: Upload Session ===
    with tab1:
        st.header("Upload New Session")

        with st.form("upload_form"):
            name = st.text_input("Player Name")
            team = st.text_input("Team")
            session_name = st.text_input("Session Name")
            session_date = st.date_input("Session Date")
            video_option = st.radio("Video Source", ["YouTube Link", "Upload Video File"])
            notes = st.text_area("Notes")

            youtube_link = ""
            video_source = ""
            video_filename = ""
            csv_url = None

            if video_option == "YouTube Link":
                youtube_link = st.text_input("YouTube Link")
            else:
                uploaded_video = st.file_uploader("Upload Video File", type=["mp4", "mov", "avi"])
                if uploaded_video:
                    video_filename = f"{name.replace(' ', '_')}_{session_name.replace(' ', '_')}.mp4"
                    try:
                        supabase.storage.from_("videos").upload(
                            path=video_filename,
                            file=uploaded_video.getvalue(),
                            file_options={"content-type": uploaded_video.type}
                        )
                        video_source = f"https://{SUPABASE_URL.split('//')[1]}/storage/v1/object/public/videos/{video_filename}"
                    except Exception as e:
                        st.error(f"Video upload to Supabase failed: {e}")

            csv_file = st.file_uploader("Upload Kinovea CSV", type="csv")
            submitted = st.form_submit_button("Upload")

            if submitted and (youtube_link or video_source):
                if csv_file:
                    csv_filename = f"{name.replace(' ', '_')}_{session_name.replace(' ', '_')}.csv"
                    try:
                        supabase.storage.from_("csvs").upload(
                            path=csv_filename,
                            file=csv_file.getvalue(),
                            file_options={"content-type": "text/csv"}
                        )
                        csv_url = f"https://{SUPABASE_URL.split('//')[1]}/storage/v1/object/public/csvs/{csv_filename}"
                    except Exception as e:
                        st.error(f"CSV upload to Supabase failed: {e}")

                final_video_source = youtube_link if video_option == "YouTube Link" else video_source
                final_csv_path = csv_url  # No local path used now

                # DB insert
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()

                c.execute("SELECT id FROM players WHERE LOWER(name)=? AND LOWER(team)=?", (name.lower(), team.lower()))
                result = c.fetchone()

                if result:
                    player_id = int(result[0])
                else:
                    c.execute("INSERT INTO players (name, team, notes) VALUES (?, ?, ?)", (name, team, ""))
                    player_id = int(c.lastrowid)

                c.execute('''INSERT INTO sessions 
                             (player_id, date, session_name, video_source, kinovea_csv, notes)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (player_id, str(session_date), session_name, final_video_source, final_csv_path, notes))
                conn.commit()
                conn.close()
                st.success("✅ Session uploaded!")

            elif submitted:
                st.warning("⚠️ Please upload a video (YouTube link or file).")

    # === TAB 2: View Sessions ===
    with tab2:
        st.header("View & Analyze Session")

        conn = sqlite3.connect(DB_PATH)
        player_df = pd.read_sql_query("SELECT * FROM players", conn)

        selected_player = st.selectbox("Select a player", player_df["name"])
        player_id = int(player_df[player_df["name"] == selected_player]["id"].values[0])

        session_df = pd.read_sql_query("SELECT * FROM sessions WHERE player_id = ?", conn, params=(player_id,))

        if session_df.empty:
            st.warning("No sessions found for this player.")
        else:
            session_df["label"] = session_df["date"] + " - " + session_df["session_name"]
            selected_session = st.selectbox("Select a session", session_df["label"])

            session_match = session_df[session_df["label"] == selected_session]
            if not session_match.empty:
                session_row = session_match.iloc[0]

                st.subheader("Video Playback")
                video_source = session_row["video_source"]

                if video_source.startswith("http"):
                    if "youtube.com" in video_source or "youtu.be" in video_source:
                        video_id = extract_youtube_id(video_source)
                        if video_id:
                            st.video(f"https://www.youtube.com/embed/{video_id}")
                        else:
                            st.warning("⚠️ Could not extract video ID. Check the YouTube link.")
                    else:
                        st.video(video_source)
                else:
                    if os.path.exists(video_source):
                        st.video(video_source)
                    else:
                        st.warning("⚠️ Local video file not found.")

                st.subheader("Session Notes")
                st.markdown(session_row["notes"].replace('\n', '  \n') if session_row["notes"] else "_No notes provided._", unsafe_allow_html=True)


                st.subheader("Kinematic Data")
                csv_path = session_row["kinovea_csv"]
                if not csv_path:
                    st.info("No Kinovea data uploaded for this session.")
                else:
                    try:
                        if csv_path.startswith("http"):
                            response = requests.get(csv_path)
                            kin_df = pd.read_csv(io.StringIO(response.text))
                        else:
                            kin_df = pd.read_csv(csv_path)

                        st.write(kin_df.head())

                        if "Time (ms)" in kin_df.columns:
                            available_metrics_view = [col for col in kin_df.columns if col in COLOR_MAP]
                            selected_metrics_view = st.multiselect(
                                "Select metrics to show",
                                options=available_metrics_view,
                                default=available_metrics_view,
                                key="view_metric_select"
                            )
                            plot_custom_lines(kin_df, chart_key="view_plot", selected_metrics=selected_metrics_view)
                        else:
                            st.warning("Column 'Time (ms)' not found. Plotting by row index.")
                            st.line_chart(kin_df.select_dtypes(include=['float', 'int']))

                    except Exception as e:
                        st.error(f"Error reading CSV: {e}")

    # === TAB 3: Compare Sessions ===
    with tab3:
        st.header("Compare Two Sessions Side-by-Side")

        conn = sqlite3.connect(DB_PATH)
        player_df = pd.read_sql_query("SELECT * FROM players", conn)

        col1, col2 = st.columns(2)

        # === LEFT SESSION ===
        with col1:
            st.markdown("### Left Player")
            player_left = st.selectbox("Select Player (Left)", player_df["name"], key="left_player")
            player_left_id = int(player_df[player_df["name"] == player_left]["id"].values[0])
            left_sessions = pd.read_sql_query("SELECT * FROM sessions WHERE player_id = ?", conn,
                                              params=(player_left_id,))

            if left_sessions.empty:
                st.warning("No sessions found for this player.")
            else:
                left_sessions["label"] = left_sessions["date"] + " - " + left_sessions["session_name"]
                session_left = st.selectbox("Select Session (Left)", left_sessions["label"], key="left_session")
                left_match = left_sessions[left_sessions["label"] == session_left]
                if not left_match.empty:
                    left_row = left_match.iloc[0]
                    video_source = left_row["video_source"]

                    if video_source.startswith("http"):
                        if "youtube.com" in video_source or "youtu.be" in video_source:
                            video_id = extract_youtube_id(video_source)
                            if video_id:
                                st.video(f"https://www.youtube.com/embed/{video_id}")
                            else:
                                st.warning("⚠️ Invalid YouTube link for left session.")
                        else:
                            st.video(video_source)
                    else:
                        if os.path.exists(video_source):
                            st.video(video_source)
                        else:
                            st.warning("⚠️ Local video file not found for left session.")

                    st.subheader("Session Notes (Left)")
                    st.markdown(left_row["notes"].replace('\n', '  \n') if left_row["notes"] else "_No notes provided._", unsafe_allow_html=True)

                    csv_path_left = left_row["kinovea_csv"]
                    if not csv_path_left:
                        st.info("No Kinovea data uploaded for this session.")
                    else:
                        try:
                            if csv_path_left.startswith("http"):
                                response = requests.get(csv_path_left)
                                df_left = pd.read_csv(io.StringIO(response.text))
                            else:
                                df_left = pd.read_csv(csv_path_left)

                            if "Time (ms)" in df_left.columns:
                                available_metrics_left = [col for col in df_left.columns if col in COLOR_MAP]
                                selected_left_metrics = st.multiselect(
                                    "Select metrics to show (Left)",
                                    options=available_metrics_left,
                                    default=available_metrics_left,
                                    key="metric_select_left"
                                )
                                plot_custom_lines(df_left, chart_key="left_plot",
                                                  selected_metrics=selected_left_metrics)
                            else:
                                st.warning("Column 'Time (ms)' not found in left session.")
                                st.line_chart(df_left.select_dtypes(include=['float', 'int']))
                        except Exception as e:
                            st.error(f"Error reading left CSV: {e}")

        # === RIGHT SESSION ===
        with col2:
            st.markdown("### Right Player")
            player_right = st.selectbox("Select Player (Right)", player_df["name"], key="right_player")
            player_right_id = int(player_df[player_df["name"] == player_right]["id"].values[0])
            right_sessions = pd.read_sql_query("SELECT * FROM sessions WHERE player_id = ?", conn,
                                               params=(player_right_id,))

            if right_sessions.empty:
                st.warning("No sessions found for this player.")
            else:
                right_sessions["label"] = right_sessions["date"] + " - " + right_sessions["session_name"]
                session_right = st.selectbox("Select Session (Right)", right_sessions["label"], key="right_session")
                right_match = right_sessions[right_sessions["label"] == session_right]
                if not right_match.empty:
                    right_row = right_match.iloc[0]
                    video_source = right_row["video_source"]

                    if video_source.startswith("http"):
                        if "youtube.com" in video_source or "youtu.be" in video_source:
                            video_id = extract_youtube_id(video_source)
                            if video_id:
                                st.video(f"https://www.youtube.com/embed/{video_id}")
                            else:
                                st.warning("⚠️ Invalid YouTube link for right session.")
                        else:
                            st.video(video_source)
                    else:
                        if os.path.exists(video_source):
                            st.video(video_source)
                        else:
                            st.warning("⚠️ Local video file not found for right session.")

                    st.subheader("Session Notes (Right)")
                    st.markdown(right_row["notes"].replace('\n', '  \n') if right_row["notes"] else "_No notes provided._", unsafe_allow_html=True)


                    csv_path_right = right_row["kinovea_csv"]
                    if not csv_path_right:
                        st.info("No Kinovea data uploaded for this session.")
                    else:
                        try:
                            if csv_path_right.startswith("http"):
                                response = requests.get(csv_path_right)
                                df_right = pd.read_csv(io.StringIO(response.text))
                            else:
                                df_right = pd.read_csv(csv_path_right)

                            if "Time (ms)" in df_right.columns:
                                available_metrics_right = [col for col in df_right.columns if col in COLOR_MAP]
                                selected_right_metrics = st.multiselect(
                                    "Select metrics to show (Right)",
                                    options=available_metrics_right,
                                    default=available_metrics_right,
                                    key="metric_select_right"
                                )
                                plot_custom_lines(df_right, chart_key="right_plot",
                                                  selected_metrics=selected_right_metrics)
                            else:
                                st.warning("Column 'Time (ms)' not found in right session.")
                                st.line_chart(df_right.select_dtypes(include=['float', 'int']))
                        except Exception as e:
                            st.error(f"Error reading right CSV: {e}")

    # === TAB 4: Admin Tools ===
    with tab4:
        with st.expander(" Admin Tools"):
            st.subheader("Delete Players or Sessions")

            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()

            players_df = pd.read_sql("SELECT * FROM players", conn)
            sessions_df = pd.read_sql("SELECT * FROM sessions", conn)

            # ---- Delete a Session ----
            st.markdown("### Delete a Session")

            selected_admin_player = st.selectbox("Select a player (to delete their session)", players_df["name"],
                                                 key="admin_player_select")
            admin_player_id = int(players_df[players_df["name"] == selected_admin_player]["id"].values[0])

            player_sessions_df = pd.read_sql("SELECT * FROM sessions WHERE player_id = ?", conn,
                                             params=(admin_player_id,))
            player_sessions_df["label"] = player_sessions_df["date"] + " - " + player_sessions_df["session_name"]

            if player_sessions_df.empty:
                st.warning("This player has no sessions.")
            else:
                session_to_delete = st.selectbox("Select a session to delete", player_sessions_df["label"],
                                                 key="admin_session_select")

                if st.button(" Delete Selected Session"):
                    session_row = player_sessions_df[player_sessions_df["label"] == session_to_delete].iloc[0]
                    csv_path = session_row["kinovea_csv"]
                    video_source = session_row["video_source"]

                    try:
                        if not csv_path.startswith("http") and os.path.exists(csv_path):
                            os.remove(csv_path)
                        if video_source and isinstance(video_source, str) and not video_source.startswith("http"):
                            if os.path.exists(video_source):
                                os.remove(video_source)

                        c.execute("DELETE FROM sessions WHERE id = ?", (session_row["id"],))
                        conn.commit()

                        st.success(f"✅ Deleted session: {session_to_delete}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error deleting session: {e}")

            # ---- Delete a Player ----
            st.markdown("---")
            st.markdown("### Delete a Player (Only if they have no sessions)")

            player_names = players_df["name"].tolist()
            selected_player = st.selectbox("Select a player to delete", player_names, key="delete_player")
            player_row = players_df[players_df["name"] == selected_player].iloc[0]

            player_sessions = sessions_df[sessions_df["player_id"] == player_row["id"]]

            if not player_sessions.empty:
                st.warning("This player has sessions and cannot be deleted. Please delete all their sessions first.")
            else:
                if st.button(" Delete Selected Player"):
                    try:
                        c.execute("DELETE FROM players WHERE id = ?", (player_row["id"],))
                        conn.commit()
                        st.success("✅ Player deleted successfully.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error deleting player: {e}")

            # ---- Clean Up Broken Sessions ----
            st.markdown("---")
            st.subheader(" Clean Up Broken Sessions")

            if st.button("Remove Sessions with Missing CSVs or Local Videos"):
                removed_count = 0
                sessions_df = pd.read_sql("SELECT * FROM sessions", conn)

                for _, row in sessions_df.iterrows():
                    csv_path = row["kinovea_csv"]
                    video_source = row["video_source"]

                    if is_missing_file(csv_path) or is_missing_file(video_source):
                        try:
                            c.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
                            conn.commit()
                            removed_count += 1
                        except Exception as e:
                            st.error(f" Error deleting session {row['session_name']}: {e}")

                if removed_count > 0:
                    st.success(f"✅ Removed {removed_count} broken session(s) with missing CSV or local video.")
                    st.rerun()
                else:
                    st.info("No broken sessions found.")

            # ---- Clean Up Orphaned Players ----
            st.markdown("---")
            st.subheader(" Clean Up Players With No Sessions")

            if st.button("Remove Players With No Sessions"):
                orphaned_players = pd.read_sql(
                    "SELECT * FROM players WHERE id NOT IN (SELECT DISTINCT player_id FROM sessions)",
                    conn
                )
                count = 0
                for _, row in orphaned_players.iterrows():
                    try:
                        c.execute("DELETE FROM players WHERE id = ?", (row["id"],))
                        conn.commit()
                        count += 1
                    except Exception as e:
                        st.error(f"Error deleting player {row['name']}: {e}")

                if count > 0:
                    st.success(f"✅ Removed {count} player(s) with no sessions.")
                    st.rerun()
                else:
                    st.info("No players without sessions found.")
    # Debug: Show raw data
    if st.checkbox(" Show Raw Database (Players + Sessions)", value=False):
        conn = sqlite3.connect(DB_PATH)
        players = pd.read_sql("SELECT * FROM players", conn)
        sessions = pd.read_sql("SELECT * FROM sessions", conn)
        st.subheader("Players Table")
        st.dataframe(players)
        st.subheader("Sessions Table")
        st.dataframe(sessions)
