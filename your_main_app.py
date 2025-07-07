import streamlit as st
import pandas as pd
import re
import plotly.graph_objects as go
import os
import io
import requests
from auth import sign_out
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

COLOR_MAP = {
    "TE": "#1f77b4",
    "FK": "#ff7f0e",
    "TS": "#2ca02c",
    "FH": "#d62728",
    "Angle 1 - o": "#9467bd",
    "Angle 1 - a": "#8c564b",
    "Angle 1 - b": "#e377c2"
}

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

def main_app(user_email):
    st.title("Pitcher Biomechanics Tracker")
    st.success(f"Welcome, {user_email}!")

    if st.button("Logout"):
        sign_out()

    tab1, tab2, tab3, tab4 = st.tabs([" Upload Session", " View Sessions", " Compare Sessions", "Admin"])

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

                player_result = supabase.table("players").select("id").eq("name", name).eq("team", team).execute()
                if player_result.data:
                    player_id = player_result.data[0]["id"]
                else:
                    new_player = supabase.table("players").insert({
                        "name": name,
                        "team": team,
                        "notes": ""
                    }).execute()
                    player_id = new_player.data[0]["id"]

                supabase.table("sessions").insert({
                    "player_id": player_id,
                    "date": str(session_date),
                    "session_name": session_name,
                    "video_source": youtube_link if video_option == "YouTube Link" else video_source,
                    "kinovea_csv": csv_url,
                    "notes": notes
                }).execute()
                st.success("✅ Session uploaded!")
            elif submitted:
                st.warning("⚠️ Please upload a video (YouTube link or file).")

    with tab2:
        st.header("View & Analyze Session")
        players_response = supabase.table("players").select("*").execute()
        players_df = pd.DataFrame(players_response.data)

        if players_df.empty:
            st.warning("No players found.")
            return

        selected_player = st.selectbox("Select a player", players_df["name"])
        player_id = players_df[players_df["name"] == selected_player]["id"].values[0]

        session_response = supabase.table("sessions").select("*").eq("player_id", player_id).execute()
        session_df = pd.DataFrame(session_response.data)

        if session_df.empty:
            st.warning("No sessions found for this player.")
            return

        session_df["label"] = session_df["date"] + " - " + session_df["session_name"]
        selected_session = st.selectbox("Select a session", session_df["label"])
        session_row = session_df[session_df["label"] == selected_session].iloc[0]

        st.subheader("Video Playback")
        video_source = session_row["video_source"]
        if "youtube.com" in video_source or "youtu.be" in video_source:
            video_id = extract_youtube_id(video_source)
            if video_id:
                st.video(f"https://www.youtube.com/embed/{video_id}")
            else:
                st.warning("⚠️ Invalid YouTube link.")
        else:
            st.video(video_source)

        st.subheader("Session Notes")
        st.markdown(session_row["notes"].replace('\\n', '  \n') if session_row["notes"] else "_No notes provided._")

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

                if "Time (ms)" in kin_df.columns:
                    metrics = [col for col in kin_df.columns if col in COLOR_MAP]
                    selected_metrics = st.multiselect("Select metrics to show", metrics, default=metrics)
                    plot_custom_lines(kin_df, chart_key="view_plot", selected_metrics=selected_metrics)
                else:
                    st.warning("Column 'Time (ms)' not found.")
                    st.line_chart(kin_df.select_dtypes(include=['float', 'int']))
            except Exception as e:
                st.error(f"Error reading CSV: {e}")

    with tab3:
        st.header("Compare Two Sessions Side-by-Side")

        players_response = supabase.table("players").select("*").execute()
        players_df = pd.DataFrame(players_response.data)

        col1, col2 = st.columns(2)

        # LEFT session
        with col1:
            st.markdown("### Left Player")
            player_left = st.selectbox("Select Player (Left)", players_df["name"], key="left_player")
            player_left_id = players_df[players_df["name"] == player_left]["id"].values[0]

            left_sessions = supabase.table("sessions").select("*").eq("player_id", player_left_id).execute()
            left_df = pd.DataFrame(left_sessions.data)

            if left_df.empty:
                st.warning("No sessions for this player.")
            else:
                left_df["label"] = left_df["date"] + " - " + left_df["session_name"]
                session_left = st.selectbox("Select Session (Left)", left_df["label"], key="left_session")
                row = left_df[left_df["label"] == session_left].iloc[0]

                st.video(row["video_source"])
                st.markdown(f"**Notes:** {row['notes'] or '_No notes provided._'}")

                if row["kinovea_csv"]:
                    try:
                        response = requests.get(row["kinovea_csv"])
                        df = pd.read_csv(io.StringIO(response.text))
                        if "Time (ms)" in df.columns:
                            metrics = [col for col in df.columns if col in COLOR_MAP]
                            selected = st.multiselect("Metrics (Left)", metrics, default=metrics, key="left_metrics")
                            plot_custom_lines(df, chart_key="left_plot", selected_metrics=selected)
                        else:
                            st.warning("Missing 'Time (ms)' column.")
                    except Exception as e:
                        st.error(f"Left CSV load failed: {e}")

        # RIGHT session
        with col2:
            st.markdown("### Right Player")
            player_right = st.selectbox("Select Player (Right)", players_df["name"], key="right_player")
            player_right_id = players_df[players_df["name"] == player_right]["id"].values[0]

            right_sessions = supabase.table("sessions").select("*").eq("player_id", player_right_id).execute()
            right_df = pd.DataFrame(right_sessions.data)

            if right_df.empty:
                st.warning("No sessions for this player.")
            else:
                right_df["label"] = right_df["date"] + " - " + right_df["session_name"]
                session_right = st.selectbox("Select Session (Right)", right_df["label"], key="right_session")
                row = right_df[right_df["label"] == session_right].iloc[0]

                st.video(row["video_source"])
                st.markdown(f"**Notes:** {row['notes'] or '_No notes provided._'}")

                if row["kinovea_csv"]:
                    try:
                        response = requests.get(row["kinovea_csv"])
                        df = pd.read_csv(io.StringIO(response.text))
                        if "Time (ms)" in df.columns:
                            metrics = [col for col in df.columns if col in COLOR_MAP]
                            selected = st.multiselect("Metrics (Right)", metrics, default=metrics, key="right_metrics")
                            plot_custom_lines(df, chart_key="right_plot", selected_metrics=selected)
                        else:
                            st.warning("Missing 'Time (ms)' column.")
                    except Exception as e:
                        st.error(f"Right CSV load failed: {e}")

    with tab4:
        st.header("Admin Tools")

        players_df = pd.DataFrame(supabase.table("players").select("*").execute().data)
        sessions_df = pd.DataFrame(supabase.table("sessions").select("*").execute().data)

        # Delete Session
        st.subheader("🗑️ Delete a Session")

        player_options = players_df["name"]
        selected_player = st.selectbox("Select Player", player_options, key="admin_player")
        player_id = players_df[players_df["name"] == selected_player]["id"].values[0]

        session_options = supabase.table("sessions").select("*").eq("player_id", player_id).execute().data
        session_df = pd.DataFrame(session_options)

        if session_df.empty:
            st.info("This player has no sessions.")
        else:
            session_df["label"] = session_df["date"] + " - " + session_df["session_name"]
            label_map = {row["label"]: row["id"] for _, row in session_df.iterrows()}
            selected_label = st.selectbox("Select session to delete", list(label_map.keys()))
            selected_id = label_map[selected_label]
            session_row = session_df[session_df["id"] == selected_id].iloc[0]

            confirm = st.checkbox("I understand this will permanently delete this session.")
            if confirm and st.button("Confirm Delete Session"):
                try:
                    if session_row["video_source"].startswith("https://"):
                        filename = session_row["video_source"].split("/")[-1]
                        supabase.storage.from_("videos").remove([filename])
                    if session_row["kinovea_csv"] and session_row["kinovea_csv"].startswith("https://"):
                        filename = session_row["kinovea_csv"].split("/")[-1]
                        supabase.storage.from_("csvs").remove([filename])
                except Exception as e:
                    st.warning(f"Supabase file deletion error: {e}")

                supabase.table("sessions").delete().eq("id", selected_id).execute()
                st.success("Session deleted.")
                st.rerun()

        # Delete players with no sessions
        st.markdown("---")
        st.subheader("🧹 Delete Players With No Sessions")

        orphaned = players_df[~players_df["id"].isin(sessions_df["player_id"])]
        if orphaned.empty:
            st.info("No players found without session data.")
        else:
            st.warning(f"This will delete {len(orphaned)} player(s).")
            if st.button("Delete Players Without Sessions"):
                for player_id in orphaned["id"]:
                    supabase.table("players").delete().eq("id", player_id).execute()
                st.success("Deleted players without sessions.")
                st.rerun()
