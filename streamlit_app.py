# streamlit_app.py
import os, io, requests, datetime as dt
import pandas as pd
import streamlit as st

# ===== 設定 =====
BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="Pilates Admin", page_icon="🧘", layout="centered")
st.title("🧘 Home Pilates 管理")

tab_new, tab_list, tab_stats = st.tabs(["新規予約", "一覧操作", "月次集計"])

# ---------- 新規予約 ----------
with tab_new:
    with st.form("new_booking"):
        name = st.text_input("名前", value="")
        date_ = st.date_input("日付", value=dt.date.today())
        time_ = st.time_input("開始時刻", value=dt.time(9, 0))
        minutes = st.number_input("所要(分)", min_value=10, max_value=240, step=5, value=30)
        memo = st.text_area("メモ", value="", height=80)
        submitted = st.form_submit_button("予約を作成")
    if submitted:
        payload = {
            "name": name,
            "start_date": str(date_),
            "start_time": time_.strftime("%H:%M"),
            "minutes": int(minutes),
            "memo": memo,
        }
        try:
            r = requests.post(f"{BACKEND}/api/bookings", json=payload, timeout=10)
            if r.status_code == 201:
                st.success("予約を作成しました！")
            elif r.status_code == 409:
                st.error("同時間帯に既存の予約があります。")
            else:
                st.error(f"作成に失敗しました: {r.status_code} {r.text}")
        except Exception as e:
            st.error(f"通信エラー: {e}")

# ---------- 一覧操作 ----------
with tab_list:
    col1, col2 = st.columns(2)
    with col1:
        days = st.number_input("表示日数", 1, 60, 14)
    with col2:
        status_filter = st.selectbox("ステータス", ["(すべて)", "Booked", "Done", "Cancel"])
    fr = dt.datetime.now().astimezone().replace(tzinfo=None)
    to = fr + dt.timedelta(days=int(days))
    params = {"fr": fr.isoformat(), "to": to.isoformat()}
    if status_filter != "(すべて)":
        params["status_eq"] = status_filter
    try:
        res = requests.get(f"{BACKEND}/api/bookings", params=params, timeout=10)
        data = res.json()
        df = pd.DataFrame(data)
        if not df.empty:
            df["start_at"] = pd.to_datetime(df["start_at"])
            df["end_at"] = pd.to_datetime(df["end_at"])
            df_display = df[["id","name","start_at","end_at","minutes","status","fee_jpy","memo"]]
            st.dataframe(df_display, use_container_width=True, hide_index=True)
            st.caption(f"{len(df)} 件")
            # 操作用
            target_id = st.number_input("操作対象ID", min_value=1, value=int(df.iloc[0]["id"]))
            act = st.selectbox("アクション", ["book","done","cancel"])
            if st.button("実行"):
                rr = requests.post(f"{BACKEND}/api/bookings/{target_id}/status", json={"action": act}, timeout=10)
                if rr.ok:
                    st.success("更新しました。上部の更新ボタン/再実行で反映を確認してください。")
                else:
                    st.error(f"更新失敗: {rr.status_code} {rr.text}")
        else:
            st.info("表示対象の予約がありません。")
    except Exception as e:
        st.error(f"取得エラー: {e}")

# ---------- 月次集計 ----------
with tab_stats:
    today = dt.date.today()
    year = st.number_input("年", 2000, 2100, today.year, step=1)
    month = st.number_input("月", 1, 12, today.month, step=1)
    if st.button("集計する"):
        try:
            r = requests.get(f"{BACKEND}/api/stats/monthly", params={"year": int(year), "month": int(month)}, timeout=10)
            if r.ok:
                s = r.json()
                st.metric(label="今月の完了数(Done)", value=s["done_count"])
                st.metric(label="今月の合計(¥)", value=s["total_fee"])
            else:
                st.error(f"集計失敗: {r.status_code}")
        except Exception as e:
            st.error(f"通信エラー: {e}")
