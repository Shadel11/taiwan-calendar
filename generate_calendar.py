import csv
import html
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# 基本設定
# ============================================================

CALENDAR_NAME = "台灣生活行事曆"

# 從 2026 年開始，動態涵蓋至明年（確保 2026 永遠在清單內，未來年份自動遞增）
START_YEAR = 2026
CURRENT_YEAR = max(datetime.now().year, START_YEAR)
TARGET_YEARS = sorted(list(set(range(START_YEAR, CURRENT_YEAR + 2))))

DGPA_DATASET_URL = "https://data.gov.tw/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/Get/"
    "95eec21d-4ee7-4920-94ff-d36727bc171f"
)

OUTPUT_FILE = "taiwan.ics"

TIMEZONE = "Asia/Taipei"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
}

# ============================================================
# 固定國定節日定義表
# ============================================================

FIXED_HOLIDAYS_MAP = {
    (1, 1): "元旦",
    (2, 28): "和平紀念日",
    (4, 4): "兒童節",
    (5, 1): "勞動節",
    (9, 28): "教師節/孔子誕辰紀念日",
    (10, 10): "國慶日",
    (10, 25): "臺灣光復暨金門古寧頭大捷紀念日",
    (12, 25): "聖誕節/行憲紀念日",
}

# 清明節精確演算法 (4/4 或 4/5)
def get_qingming_date(year):
    # 21世紀清明節回歸公式
    y = year % 100
    day = int((y * 0.2422 + 4.81) - int((y - 1) / 4))
    return date(year, 4, day)

# ============================================================
# 名稱習慣規範轉換
# ============================================================

def format_custom_summary(text):
    if not text:
        return ""
    text = str(text).strip()

    if "開國紀念日" in text:
        text = text.replace("開國紀念日", "元旦")

    if "孔子誕辰紀念日/教師節" in text:
        text = text.replace("孔子誕辰紀念日/教師節", "教師節/孔子誕辰紀念日")
    elif "孔子誕辰紀念日" in text and "教師節" not in text:
        text = "教師節/孔子誕辰紀念日"

    if "行憲紀念日" in text and "聖誕節" not in text:
        text = text.replace("行憲紀念日", "聖誕節/行憲紀念日")

    return text

# ============================================================
# HTTP 請求模組
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def http_get(url, timeout=30):
    for attempt in range(3):
        try:
            response = SESSION.get(url, timeout=timeout, verify=False)
            response.raise_for_status()
            return response
        except Exception:
            if attempt < 2:
                continue
    return None


def roc_year(year):
    return year - 1911


def decode_csv_content(content):
    for enc in ["utf-8-sig", "utf-8", "cp950", "big5"]:
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    m = re.match(r"^(\d{4})[-/]?(\d{2})[-/]?(\d{2})$", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{2,3})[-/](\d{1,2})[-/](\d{1,2})$", text)
    if m:
        y = int(m.group(1)) + 1911 if int(m.group(1)) < 1911 else int(m.group(1))
        try:
            return date(y, int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None

# ============================================================
# 政府 CSV 下載與解析
# ============================================================

def fetch_dgpa_csv_rows(year):
    roc = roc_year(year)
    resp = http_get(DGPA_DATASET_URL, timeout=20)
    if not resp:
        return None

    page = html.unescape(resp.text)
    urls = re.findall(r'https?://www\.dgpa\.gov\.tw/FileConversion\?[^"\']+', page)
    target_urls = [
        unquote(u) for u in urls
        if (str(roc) in unquote(u) or str(year) in unquote(u)) and ".csv" in u.lower() and "Google" not in unquote(u)
    ]

    if not target_urls:
        return None

    csv_resp = http_get(target_urls[-1], timeout=30)
    if not csv_resp:
        return None

    text = decode_csv_content(csv_resp.content).lstrip("\ufeff")
    try:
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)
    except Exception:
        return None

# ============================================================
# 通用連假推算演算法（支援向前、向後雙向自動反推補假來源）
# ============================================================

def process_year_holidays(rows, year):
    if not rows:
        return []

    date_col = next((c for c in rows[0].keys() if any(k in c for k in ["西元", "日期", "date"])), None)
    hol_col = next((c for c in rows[0].keys() if any(k in c for k in ["放假", "isHoliday"])), None)
    note_col = next((c for c in rows[0].keys() if any(k in c for k in ["備註", "節日", "名稱"])), None)

    if not date_col or not hol_col:
        return []

    calendar_map = {}
    for r in rows:
        d = parse_date(r.get(date_col))
        if not d or d.year != year:
            continue
        is_hol = str(r.get(hol_col, "")).strip() in ("2", "２", "True", "true", "放假")
        note = str(r.get(note_col, "")).strip().replace("放假", "").strip() if note_col else ""
        calendar_map[d] = {"is_holiday": is_hol, "note": note}

    events = []
    sorted_dates = sorted(calendar_map.keys())

    # 1. 自動識別春節區間並依序編號
    chuxi_date = None
    for d in sorted_dates:
        if d.month in (1, 2) and calendar_map[d]["note"] == "除夕":
            chuxi_date = d
            break

    cny_names = {}
    if chuxi_date:
        order_names = ["初一", "初二", "初三", "初四(補假初一)", "初五(補假初二)", "初六(補假)"]
        curr_d = chuxi_date + timedelta(days=1)
        idx = 0
        while curr_d in calendar_map and calendar_map[curr_d]["is_holiday"] and idx < len(order_names):
            cny_names[curr_d] = order_names[idx]
            curr_d += timedelta(days=1)
            idx += 1

    # 2. 清明節日期確認
    qingming_d = get_qingming_date(year)

    # 3. 逐日產生事件，雙向推導「補假」來源
    for d in sorted_dates:
        info = calendar_map[d]
        if not info["is_holiday"]:
            continue

        raw_note = info["note"]
        final_summary = ""

        # 春節連假自動名稱優先
        if d in cny_names:
            final_summary = cny_names[d]
        elif raw_note in ("小年夜", "除夕"):
            final_summary = raw_note
        elif "補假" in raw_note:
            # 雙向搜尋：向前找 1~4 天（週一補週日/週六） 或 向後找 1~2 天（週五提前補週六，例如 10/09 補 10/10 國慶日）
            candidate_deltas = [-1, -2, -3, -4, 1, 2]
            for delta in candidate_deltas:
                target_d = d + timedelta(days=delta)
                # 遇清明節
                if target_d == qingming_d:
                    final_summary = "清明節(補假)"
                    break
                # 遇固定國定假日落在週末 (週五補週六、週一補週日)
                if (target_d.month, target_d.day) in FIXED_HOLIDAYS_MAP:
                    if target_d.weekday() in (5, 6):
                        holiday_name = FIXED_HOLIDAYS_MAP[(target_d.month, target_d.day)]
                        final_summary = f"{holiday_name}(補假)"
                        break
                # 遇小年夜/除夕落在週末
                if target_d in calendar_map and calendar_map[target_d]["note"] in ("小年夜", "除夕"):
                    if target_d.weekday() in (5, 6):
                        final_summary = f"{calendar_map[target_d]['note']}(補假)"
                        break

            # 若仍無法推導，且原備註有特定說明（如「兒童節補假」），則採用其名稱
            if not final_summary:
                if raw_note != "補假":
                    final_summary = format_custom_summary(raw_note)
                else:
                    final_summary = "補假"
        elif raw_note:
            final_summary = format_custom_summary(raw_note)

        # 排除無明確節日的一般單純例假日與未知「補假」
        if final_summary and final_summary != "補假":
            events.append({
                "date": d,
                "summary": final_summary,
                "category": "政府假日",
                "description": f"{year}年政府行政機關辦公日曆表",
            })

    return events


def build_government_events():
    events = []
    for year in TARGET_YEARS:
        print(f"📅 自動下載與解析 {year} 年政府辦公日曆...")
        rows = fetch_dgpa_csv_rows(year)
        if rows:
            parsed = process_year_holidays(rows, year)
            print(f"✅ 成功自適應取得 {year} 年日曆共 {len(parsed)} 筆事件")
            events.extend(parsed)
        else:
            print(f"⚠️ 無法取得 {year} 年 DGPA 資料")
    return events


# ============================================================
# 母親節 / 父親節（每年自動推算）
# ============================================================

def get_second_sunday_of_may(year):
    d = date(year, 5, 1)
    days_until_sunday = (6 - d.weekday()) % 7
    return d + timedelta(days=days_until_sunday + 7)


def build_family_events():
    events = []
    for year in TARGET_YEARS:
        events.append({
            "date": get_second_sunday_of_may(year),
            "summary": "母親節",
            "category": "節日",
            "description": "每年五月第二個星期日",
        })
        events.append({
            "date": date(year, 8, 8),
            "summary": "父親節",
            "category": "節日",
            "description": "每年8月8日父親節",
        })
    return events


# ============================================================
# 高雄停班停課
# ============================================================

def get_kaohsiung_events():
    events = []
    res = http_get(KAOHSIUNG_API, timeout=15)
    if not res:
        return events
    try:
        data = res.json()
        records = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for k in ("Data", "data", "Result", "result", "Records", "records"):
                if isinstance(data.get(k), list):
                    records.extend(data[k])
        for record in records:
            if not isinstance(record, dict):
                continue
            text = " ".join(f"{k}:{v}" for k, v in record.items() if isinstance(v, (str, int, float)))
            if not any(kw in text for kw in ["停止上班", "停止上課", "停班", "停課"]):
                continue
            m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
            if not m:
                continue
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if d.year in TARGET_YEARS:
                events.append({
                    "date": d,
                    "summary": "高雄市停班停課",
                    "category": "高雄停班停課",
                    "description": text,
                })
    except Exception:
        pass
    return events


# ============================================================
# ICS 格式輸出
# ============================================================

def ics_escape(val):
    if not val:
        return ""
    text = str(val).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def make_uid(event):
    raw = f"{event['date'].isoformat()}-{event['summary']}-{CALENDAR_NAME}"
    return f"{uuid.uuid5(uuid.NAMESPACE_URL, raw)}@taiwan-calendar"


def build_ics(events):
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Taiwan Calendar//TW//",
        f"X-WR-CALNAME:{ics_escape(CALENDAR_NAME)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-TIMEZONE:Asia/Taipei",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Taipei",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for event in sorted(events, key=lambda item: (item["date"], item["summary"])):
        start = event["date"].strftime("%Y%m%d")
        end = (event["date"] + timedelta(days=1)).strftime("%Y%m%d")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{make_uid(event)}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            f"SUMMARY:{ics_escape(event['summary'])}",
            f"CATEGORIES:{ics_escape(event.get('category', ''))}",
            f"DESCRIPTION:{ics_escape(event.get('description', ''))}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    all_events = build_government_events() + build_family_events() + get_kaohsiung_events()

    # 全域嚴格去重
    unique_events = []
    seen = set()
    for e in all_events:
        key = (e["date"], e["summary"])
        if key not in seen:
            seen.add(key)
            unique_events.append(e)

    unique_events.sort(key=lambda item: (item["date"], item["summary"]))

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(unique_events))

    print(f"✅ 成功產生日曆：共 {len(unique_events)} 筆事件（涵蓋年份：{TARGET_YEARS}）。")


if __name__ == "__main__":
    main()
