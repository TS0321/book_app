# streamlit_app.py
import os, requests, datetime as dt
import pandas as pd
import streamlit as st
from math import ceil

# ===== 設定 =====
BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")

#st.set_page_config(page_title="Pilates Admin", page_icon="🧘", layout="centered")
st.set_page_config(
    page_title="Pilates Admin",
    page_icon="static/shima_meiso.png",    # 例: プロジェクト直下/static/favicon.png
    layout="centered"
)
# 例：streamlit_app.py のタイトル部分を置き換え
col_icon, col_title = st.columns([1, 6])

with col_icon:
    st.image("static/shima_meiso.png", width=100)   # ← ここにアイコン画像のパス

with col_title:
    st.title("Home Pilates")                        # ← 文字タイトル


# ---- セッション & 共通関数 ----
_session = requests.Session()

@st.cache_data(ttl=5)
def fetch_bookings(params: dict):
    """FastAPI /api/bookings を叩いて予約JSONを返す（5秒キャッシュ）"""
    r = _session.get(f"{BACKEND}/api/bookings", params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    data.sort(key=lambda x: x["start_at"])
    return data

def render_booking_card(row: dict, key_prefix: str = ""):
    """一覧カード（Done / Booked / Delete を縦配置）"""
    bid = row["id"]
    start = pd.to_datetime(row["start_at"])
    end   = pd.to_datetime(row["end_at"])
    name  = row["name"]
    memo  = row.get("memo") or ""
    status_val = row["status"]
    fee = row.get("fee_jpy")

    with st.container(border=True):
        cols = st.columns([1, 2, 3, 1.6, 1.0], gap="small")

        with cols[0]:
            st.markdown(f"**#{bid}**")
            st.caption(start.strftime("%Y/%m/%d"))

        with cols[1]:
            st.markdown(f"🕒 **{start.strftime('%H:%M')} – {end.strftime('%H:%M')}**")
            st.caption(f"{int(row['minutes'])} 分")

        with cols[2]:
            st.markdown(f"👤 **{name}**")
            if memo:
                st.caption(f"📝 {memo}")

        with cols[3]:
            badge = {"Booked":"🔵 Booked", "Done":"🟢 Done", "Cancel":"🔴 Cancel"}.get(status_val, f"🔘 {status_val}")
            st.markdown(badge)
            if fee:
                st.caption(f"¥{fee:,}")

        with cols[4]:
            # prefix を key に付ける（'' ならそのまま）
            kp = (key_prefix + "-") if key_prefix else ""

            if st.button("Done",   key=f"{kp}done-{bid}",   use_container_width=True):
                rr = _session.post(f"{BACKEND}/api/bookings/{bid}/status", json={"action":"done"}, timeout=10)
                (st.success if rr.ok else st.error)("Done にしました" if rr.ok else f"更新失敗: {rr.status_code} {rr.text}")
                st.cache_data.clear(); st.rerun()

            if st.button("Booked", key=f"{kp}book-{bid}",   use_container_width=True):
                rr = _session.post(f"{BACKEND}/api/bookings/{bid}/status", json={"action":"book"}, timeout=10)
                (st.success if rr.ok else st.error)("Booked に戻しました" if rr.ok else f"更新失敗: {rr.status_code} {rr.text}")
                st.cache_data.clear(); st.rerun()

            if st.button("🗑 Delete", key=f"{kp}del-{bid}", use_container_width=True):
                rr = _session.delete(f"{BACKEND}/api/bookings/{bid}", timeout=10)
                if rr.status_code == 204:
                    st.success("削除しました")
                    st.cache_data.clear(); st.rerun()
                else:
                    st.error(f"削除失敗: {rr.status_code} {rr.text}")

# ---- 直近15分への切り上げ ----
def next_quarter(dt_now: dt.datetime) -> dt.datetime:
    m = int(ceil(dt_now.minute / 15) * 15)
    if m == 60:
        return dt_now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    return dt_now.replace(minute=m, second=0, microsecond=0)

tab_new, tab_list, tab_stats = st.tabs(["新規予約", "一覧操作", "月次集計"])

# ---------- 新規予約 ----------
with tab_new:
    with st.form("new_booking"):
        name = st.text_input("名前", value="")
        date_ = st.date_input("日付", value=dt.date.today())
        time_ = st.time_input("開始時刻", value=next_quarter(dt.datetime.now()).time())
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
            r = _session.post(f"{BACKEND}/api/bookings", json=payload, timeout=10)
            if r.status_code == 201:
                st.success("予約を作成しました！")
            elif r.status_code == 409:
                st.error("同時間帯に既存の予約があります。")
            elif r.status_code == 400:
                st.error("過去の時刻には予約できません。")
            else:
                st.error(f"作成に失敗しました: {r.status_code} {r.text}")
            st.cache_data.clear()
        except Exception as e:
            st.error(f"通信エラー: {e}")

# ---------- 一覧操作 ----------
with tab_list:
    st.subheader("予約一覧（カード表示）")

    # 期間指定（過去も未来も）
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("表示開始日", dt.date.today() - dt.timedelta(days=7))
    with col2:
        end_date   = st.date_input("表示終了日", dt.date.today() + dt.timedelta(days=14))

    status_filter = st.selectbox("ステータス", ["(すべて)", "Booked", "Done", "Cancel"])

    params = {
        "fr": dt.datetime.combine(start_date, dt.time.min).isoformat(),
        "to": dt.datetime.combine(end_date,   dt.time.max).isoformat(),
    }
    if status_filter != "(すべて)":
        params["status_eq"] = status_filter

    # ページング
    PAGE_SIZE = 20
    page = st.number_input("ページ", min_value=1, step=1, value=1)
    params["limit"]  = PAGE_SIZE
    params["offset"] = (int(page)-1) * PAGE_SIZE

    try:
        data = fetch_bookings(params)
        if not data:
            st.info("該当期間の予約はありません。")
        else:
            for row in data:
                render_booking_card(row, key_prefix="list")
    except Exception as e:
        st.error(f"取得エラー: {e}")

# ---------- 月次集計 ----------
with tab_stats:
    today = dt.date.today()
    year = st.number_input("年", 2000, 2100, today.year, step=1)
    month = st.number_input("月", 1, 12, today.month, step=1)
    if st.button("集計する"):
        try:
            r = _session.get(f"{BACKEND}/api/stats/monthly",
                             params={"year": int(year), "month": int(month)}, timeout=10)
            if r.ok:
                s = r.json()
                st.metric(label="今月の完了数(Done)", value=s["done_count"])
                st.metric(label="今月の合計(¥)", value=s["total_fee"])
            else:
                st.error(f"集計失敗: {r.status_code}")
        except Exception as e:
            st.error(f"通信エラー: {e}")

        # その月の Done 一覧もカードで表示
        start = dt.datetime(int(year), int(month), 1, 0, 0, 0)
        nextm = dt.datetime(int(year)+1, 1, 1) if int(month) == 12 else dt.datetime(int(year), int(month)+1, 1)
        end = nextm - dt.timedelta(seconds=1)

        params = {
            "fr": start.isoformat(),
            "to": end.isoformat(),
            "status_eq": "Done",
            "limit": 200, "offset": 0
        }

        st.divider()
        st.subheader(f"{year}年{month}月の Done 一覧")
        try:
            data = fetch_bookings(params)
            if not data:
                st.info("この月の Done はありません。")
            else:
                for row in data:
                    render_booking_card(row, key_prefix=f"stats-{year}-{month}")
        except Exception as e:
            st.error(f"取得エラー: {e}")
