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

TARGET_YEARS = [2026, 2027]

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
# 官方行政院人事行政總處 (DGPA) 核定補假對應
# ============================================================

MAKEUP_HOLIDAY_MAP = {
    2026: {
        "2026-02-20": "小年夜(補假)",
        "2026-02-27": "和平紀念日(補假)",
        "2026-04-03": "兒童節(補假)",
        "2026-04-06": "清明節(補假)",
        "2026-10-09": "國慶日(補假)",
        "2026-10-26": "臺灣光復暨金門古寧頭大捷紀念日(補假)",
    },
    2027: {
        "2027-02-09": "初一(補假)",
        "2027-02-10": "初二(補假)",
        "2027-03-01": "和平紀念日(補假)",
        "2027-04-06": "兒童節(補假)",
        "2027-04-30": "勞動節(補假)",
        "2027-10-11": "國慶日(補假)",
        "2027-12-24": "行憲紀念日(補假)",
        "2027-12-31": "開國紀念日(補假)",
    },
}

# ============================================================
# 官方核定節日標準備援清單 (Fallback)
# ============================================================

OFFICIAL_HOLIDAYS_FALLBACK = {
    2026: {
        date(2026, 1, 1): "開國紀念日",
        date(2026, 2, 15): "小年夜",
        date(2026, 2, 16): "除夕",
        date(2026, 2, 17): "春節初一",
        date(2026, 2, 18): "春節初二",
        date(2026, 2, 19): "春節初三",
        date(2026, 2, 20): "小年夜(補假)",
        date(2026, 2, 27): "和平紀念日(補假)",
        date(2026, 2, 28): "和平紀念日",
        date(2026, 4, 3): "兒童節(補假)",
        date(2026, 4, 4): "兒童節",
        date(2026, 4, 5): "清明節",
        date(2026, 4, 6): "清明節(補假)",
        date(2026, 5, 1): "勞動節",
        date(2026, 6, 19): "端午節",
        date(2026, 9, 25): "中秋節",
        date(2026, 9, 28): "孔子誕辰紀念日/教師節",
        date(2026, 10, 9): "國慶日(補假)",
        date(2026, 10, 10): "國慶日",
        date(2026, 10, 25): "臺灣光復暨金門古寧頭大捷紀念日",
        date(2026, 10, 26): "臺灣光復暨金門古寧頭大捷紀念日(補假)",
        date(2026, 12, 25): "行憲紀念日",
    },
    2027: {
        date(2027, 1, 1): "開國紀念日",
        date(2027, 2, 5): "小年夜",
        date(2027, 2, 6): "除夕",
        date(2027, 2, 7): "春節初一",
        date(2027, 2, 8): "春節初二",
        date(2027, 2, 9): "初一(補假)",
        date(2027, 2, 10): "初二(補假)",
        date(2027, 2, 28): "和平紀念日",
        date(2027, 3, 1): "和平紀念日(補假)",
        date(2027, 4, 4): "兒童節",
        date(2027, 4, 5): "清明節",
        date(2027, 4, 6): "兒童節(補假)",
        date(2027, 4, 30): "勞動節(補假)",
        date(2027, 5, 1): "勞動節",
        date(2027, 6, 9): "端午節",
        date(2027, 9, 15): "中秋節",
        date(2027, 9, 28): "孔子誕辰紀念日/教師節",
        date(2027, 10, 10): "國慶日",
        date(2027, 10, 11): "國慶日(補假)",
        date(2027, 10, 25): "臺灣光復暨金門古寧頭大捷紀念日",
        date(2027, 12, 24): "行憲紀念日(補假)",
        date(2027, 12, 25): "行憲紀念日",
        date(2027, 12, 31): "開國紀念日(補假)",
    },
}

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


def fetch_dgpa_events_for_year(year):
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
        rows = list(reader)
    except Exception:
        return None

    if not rows:
        return None

    date_col = next((c for c in rows[0].keys() if any(k in c for k in ["西元", "日期", "date"])), None)
    hol_col = next((c for c in rows[0].keys() if any(k in c for k in ["放假", "isHoliday"])), None)
    note_col = next((c for c in rows[0].keys() if any(k in c for k in ["備註", "節日", "名稱"])), None)

    if not date_col or not hol_col:
        return None

    year_events = []
    makeup_map = MAKEUP_HOLIDAY_MAP.get(year, {})

    for r in rows:
        d = parse_date(r.get(date_col))
        if not d or d.year != year:
            continue
        is_hol = str(r.get(hol_col, "")).strip() in ("2", "２", "True", "true", "放假")
        if not is_hol:
            continue

        raw_note = str(r.get(note_col, "")).strip().replace("放假", "").strip() if note_col else ""
        d_str = d.isoformat()

        # 優先比對補假表
        if d_str in makeup_map:
            name = makeup_map[d_str]
        elif raw_note and raw_note != "補假":
            name = raw_note
        else:
            continue

        year_events.append({
            "date": d,
            "summary": name,
            "category": "政府假日",
            "description": f"{year}年政府行政機關辦公日曆表",
        })

    return year_events if len(year_events) >= 10 else None


def build_government_events():
    events = []
    for year in TARGET_YEARS:
        print(f"📅 處理 {year} 年政府辦公日曆...")
        parsed = fetch_dgpa_events_for_year(year)

        if parsed:
            print(f"✅ 從官方 CSV 成功取得 {year} 年日曆 ({len(parsed)} 筆)")
            events.extend(parsed)
        else:
            print(f"ℹ️ 使用 {year} 年官方核定假日標準清單 (Fallback)")
            fallback = OFFICIAL_HOLIDAYS_FALLBACK.get(year, {})
            for d, name in fallback.items():
                events.append({
                    "date": d,
                    "summary": name,
                    "category": "政府假日",
                    "description": f"{year}年政府行政機關辦公日曆表",
                })
    return events


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

    # 去重
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

    print(f"✅ 成功產生日曆：共 {len(unique_events)} 筆事件")


if __name__ == "__main__":
    main()
