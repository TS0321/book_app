# streamlit_app.py
import os, requests, datetime as dt
import pandas as pd
import streamlit as st
from math import ceil

# 追加：Doneを日別に色付けして表示するカレンダー
import calendar

def render_done_calendar(year: int, month: int, rows: list[dict]):
    """
    rows: /api/bookings で取得した Done の予約（start_at を含む）
    同日内に1件でも Done があれば、その日のセルを緑でハイライト。
    セル内に 件数 と 合計金額（fee_jpy）も表示。
    """
    # 日別の件数と金額集計
    day_count = {}
    day_sum = {}
    for r in rows:
        d = pd.to_datetime(r["start_at"]).date()
        if d.year == year and d.month == month:
            day_count[d.day] = day_count.get(d.day, 0) + 1
            if r.get("fee_jpy"):
                day_sum[d.day] = day_sum.get(d.day, 0) + int(r["fee_jpy"])

    cal = calendar.Calendar(firstweekday=6)  # 日曜始まり
    weeks = cal.monthdayscalendar(year, month)  # [[日, 月, 火, 水, 木, 金, 土], ...]
    weekdays_ja = ["日", "月", "火", "水", "木", "金", "土"]

    # スタイル
    css = """
    <style>
      .cal { width: 100%; border-collapse: collapse; table-layout: fixed; }
      .cal th, .cal td { border: 1px solid #ddd; vertical-align: top; padding: 6px; height: 92px; }
      .cal th { background: #f7f7f7; text-align:center; font-weight:600; }
      .cal .daynum { font-weight:600; float:right; }
      .cal .done { background: #e9f7ef; }           /* Doneがある日の背景 */
      .cal .count { display:inline-block; font-size: 12px; padding: 2px 6px; border-radius: 10px; background:#d1f0dc; margin-top: 6px;}
      .cal .sum { font-size: 12px; color:#2c7a4b; margin-top: 4px; display:block; }
      .cal .empty { background:#fafafa; }
    </style>
    """

    # HTML組み立て
    html = ['<table class="cal">']
    # ヘッダ
    html.append("<tr>" + "".join(f"<th>{w}</th>" for w in weekdays_ja) + "</tr>")
    # 各週
    for w in weeks:
        html.append("<tr>")
        for day in w:
            if day == 0:
                html.append('<td class="empty"></td>')
                continue
            classes = []
            if day in day_count:
                classes.append("done")
            c = " ".join(classes)
            cnt = day_count.get(day, 0)
            sm  = day_sum.get(day, 0)
            badge = f'<div class="count">{cnt} 件</div>' if cnt else ""
            yen   = f'<span class="sum">¥{sm:,}</span>' if sm else ""
            html.append(f'<td class="{c}"><span class="daynum">{day}</span>{badge}{yen}</td>')
        html.append("</tr>")
    html.append("</table>")

    st.markdown(css + "\n" + "\n".join(html), unsafe_allow_html=True)


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

tab_new, tab_list, tab_stats, tab_feedback = st.tabs(["新規予約", "一覧操作", "月次集計", "要望リスト"])


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
        # ① メトリクス（件数・合計）
        try:
            r = _session.get(f"{BACKEND}/api/stats/monthly",
                             params={"year": int(year), "month": int(month)}, timeout=10)
            if r.ok:
                s = r.json()
                c1, c2 = st.columns(2)
                with c1: st.metric(label="完了数 (Done)", value=s["done_count"])
                with c2: st.metric(label="合計 (¥)", value=s["total_fee"])
            else:
                st.error(f"集計失敗: {r.status_code}")
                st.stop()
        except Exception as e:
            st.error(f"通信エラー: {e}")
            st.stop()

        # ② その月の Done を取得してカレンダー描画
        start = dt.datetime(int(year), int(month), 1, 0, 0, 0)
        nextm = dt.datetime(int(year)+1, 1, 1) if int(month) == 12 else dt.datetime(int(year), int(month)+1, 1)
        end = nextm - dt.timedelta(seconds=1)

        params = {
            "fr": start.isoformat(),
            "to": end.isoformat(),
            "status_eq": "Done",
            "limit": 500, "offset": 0
        }

        try:
            rows = fetch_bookings(params)  # JSON list
        except Exception as e:
            st.error(f"取得エラー: {e}")
            st.stop()

        st.subheader(f"{year}年{month}月")
        render_done_calendar(int(year), int(month), rows)

# ---------- 要望リスト ----------
with tab_feedback:
    st.subheader("アプリへの要望を書いてください")

    with st.form("feedback_form"):
        fb_text = st.text_area("要望内容", height=120, placeholder="例: カレンダーに色分け機能を追加してほしい")
        submitted = st.form_submit_button("送信")
    if submitted:
        if not fb_text.strip():
            st.warning("内容を入力してください")
        else:
            try:
                r = _session.post(f"{BACKEND}/api/feedback", json={"text": fb_text.strip()}, timeout=10)
                if r.status_code == 201:
                    st.success("要望を送信しました。ありがとうございます！")
                    st.cache_data.clear()
                else:
                    st.error(f"送信失敗: {r.status_code} {r.text}")
            except Exception as e:
                st.error(f"通信エラー: {e}")

    st.divider()
    st.subheader("これまでの要望")
    try:
        r = _session.get(f"{BACKEND}/api/feedback", timeout=10)
        if r.ok:
            fb_list = r.json()
            if not fb_list:
                st.info("まだ要望はありません。")
            else:
                for fb in fb_list:
                    t = pd.to_datetime(fb["created_at"]).strftime("%Y/%m/%d %H:%M")
                    st.markdown(f"**{t}**  \n{fb['text']}")
        else:
            st.error(f"取得失敗: {r.status_code}")
    except Exception as e:
        st.error(f"通信エラー: {e}")
