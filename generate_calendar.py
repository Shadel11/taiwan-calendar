import csv
import html
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote

import requests
import urllib3

# 抑制 verify=False 警告
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
# 2026 / 2027 官方補假對應
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
# 官方固定國定節日
# ============================================================

FIXED_HOLIDAYS = {
    (1, 1): "開國紀念日",
    (2, 28): "和平紀念日",
    (4, 4): "兒童節",
    (5, 1): "勞動節",
    (9, 28): "孔子誕辰紀念日/教師節",
    (10, 10): "國慶日",
    (10, 25): "臺灣光復暨金門古寧頭大捷紀念日",
    (12, 25): "行憲紀念日",
}

# 清明節
QINGMING_DATES = {
    2026: date(2026, 4, 5),
    2027: date(2027, 4, 5),
}

# ============================================================
# 2026 / 2027 農曆節日對應表（不依賴外部套件）
# ============================================================

LUNAR_FESTIVAL_DATES = {
    # 2026 農曆節日
    date(2026, 2, 15): "小年夜",
    date(2026, 2, 16): "除夕",
    date(2026, 2, 17): "初一",
    date(2026, 2, 18): "初二",
    date(2026, 2, 19): "初三",
    date(2026, 2, 20): "初四",
    date(2026, 2, 21): "初五",
    date(2026, 6, 19): "端午節",
    date(2026, 9, 25): "中秋節",

    # 2027 農曆節日
    date(2027, 2, 5): "小年夜",
    date(2027, 2, 6): "除夕",
    date(2027, 2, 7): "初一",
    date(2027, 2, 8): "初二",
    date(2027, 2, 9): "初三",
    date(2027, 2, 10): "初四",
    date(2027, 2, 11): "初五",
    date(2027, 6, 9): "端午節",
    date(2027, 9, 15): "中秋節",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def http_get(url, timeout=30):
    last_error = None
    for attempt in range(3):
        try:
            response = SESSION.get(url, timeout=timeout, verify=False)
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                continue
    raise RuntimeError(f"下載失敗：{url}\n原因：{last_error}")


def parse_date(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    match = re.match(r"^(\d{2,3})[-/](\d{1,2})[-/](\d{1,2})$", text)
    if match:
        year = int(match.group(1))
        if year < 1911:
            year += 1911
        try:
            return date(year, int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    match = re.match(r"^(\d{4})(\d{2})(\d{2})$", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

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


def clean_text(value):
    if value is None:
        return ""
    return str(value).replace("\ufeff", "").replace("\xa0", " ").strip()


def find_column(fieldnames, candidates):
    if not fieldnames:
        return None
    normalized = {clean_text(f): f for f in fieldnames}
    for c in candidates:
        c_clean = clean_text(c)
        if c_clean in normalized:
            return normalized[c_clean]
    for f in fieldnames:
        f_clean = clean_text(f)
        for c in candidates:
            if clean_text(c) in f_clean:
                return f
    return None


def get_dgpa_csv_url(year):
    roc = roc_year(year)
    response = http_get(DGPA_DATASET_URL, timeout=30)
    page = html.unescape(response.text)

    patterns = [
        r'https?://www\.dgpa\.gov\.tw/FileConversion\?[^"\']+',
        r'//www\.dgpa\.gov\.tw/FileConversion\?[^"\']+',
        r'/FileConversion\?[^"\']+',
    ]

    urls = []
    for pattern in patterns:
        for url in re.findall(pattern, page, flags=re.IGNORECASE):
            url = unquote(html.unescape(url))
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = "https://www.dgpa.gov.tw" + url
            if "FileConversion" in url and ".csv" in url.lower():
                urls.append(url)

    unique_urls = list(dict.fromkeys(urls))
    target_patterns = [f"{roc}%E5%B9%B4", f"{roc}年", f"{year}"]

    candidates = [
        u for u in unique_urls
        if "Google" not in unquote(u) and any(p in unquote(u) for p in target_patterns)
    ]
    if candidates:
        return candidates[-1]

    for u in unique_urls:
        decoded = unquote(u)
        if str(roc) in decoded and "Google" not in decoded:
            return u

    raise RuntimeError(f"找不到 {year} 年 DGPA CSV 資料。")


def download_dgpa_csv(year):
    url = get_dgpa_csv_url(year)
    print(f"📥 {year} DGPA CSV：\n{url}")
    return decode_csv_content(http_get(url, timeout=60).content)


def read_dgpa_rows(year):
    text = download_dgpa_csv(year).lstrip("\ufeff")
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if rows and reader.fieldnames:
            return rows
    except Exception:
        pass

    try:
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = list(reader)
        if rows:
            return rows
    except Exception:
        pass

    raise RuntimeError(f"{year} DGPA CSV 無法解析。")


def is_holiday_value(value):
    text = clean_text(value)
    if text in ("2", "２"):
        return True
    if text.lower() in ("true", "yes", "y", "holiday"):
        return True
    return "放假" in text


def get_dgpa_columns(rows):
    if not rows:
        raise RuntimeError("DGPA CSV 沒有資料。")
    fieldnames = list(rows[0].keys())
    date_col = find_column(fieldnames, ["西元日期", "日期", "date"])
    holiday_col = find_column(fieldnames, ["是否放假", "放假", "isHoliday"])
    note_col = find_column(fieldnames, ["備註", "備註說明", "節日", "名稱", "note"])

    if not date_col or not holiday_col:
        raise RuntimeError("DGPA CSV 欄位名稱比對失敗。")
    return date_col, holiday_col, note_col


def get_base_holiday_name(gregorian_date, year):
    date_key = gregorian_date.isoformat()

    # 1. 補假清單
    makeup_map = MAKEUP_HOLIDAY_MAP.get(year, {})
    if date_key in makeup_map:
        return makeup_map[date_key]

    # 2. 國曆固定節日
    fixed = FIXED_HOLIDAYS.get((gregorian_date.month, gregorian_date.day))
    if fixed:
        return fixed

    # 3. 清明節
    if gregorian_date == QINGMING_DATES.get(year):
        return "清明節"

    # 4. 農曆節日（查表）
    if gregorian_date in LUNAR_FESTIVAL_DATES:
        return LUNAR_FESTIVAL_DATES[gregorian_date]

    return None


def normalize_official_note(note):
    return clean_text(note).replace("放假", "").strip()


def build_government_events():
    events = []
    for year in TARGET_YEARS:
        print(f"\n{'=' * 60}\n📅 讀取 {year} 年政府辦公日曆\n{'=' * 60}")
        rows = read_dgpa_rows(year)
        date_col, holiday_col, note_col = get_dgpa_columns(rows)
        year_events = []

        for row in rows:
            g_date = parse_date(row.get(date_col))
            if not g_date or g_date.year != year:
                continue

            if not is_holiday_value(row.get(holiday_col)):
                continue

            note = normalize_official_note(row.get(note_col)) if note_col else ""
            name = get_base_holiday_name(g_date, year)
            if not name and note and "補假" not in note:
                name = note

            if not name or name == "補假":
                continue

            if any(e["date"] == g_date and e["summary"] == name for e in year_events):
                continue

            year_events.append({
                "date": g_date,
                "summary": name,
                "category": "政府假日",
                "description": f"{year}年政府行政機關辦公日曆表",
            })

        year_events.sort(key=lambda item: item["date"])
        for e in year_events:
            print(f"  {e['date']} {e['summary']}")
        print(f"📌 {year} 年政府節日事件數：{len(year_events)}")
        events.extend(year_events)

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


def extract_date_from_text(text):
    if not text:
        return None
    patterns = [r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", r"(20\d{2})年(\d{1,2})月(\d{1,2})日"]
    for pat in patterns:
        m = re.search(pat, str(text))
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
    return None


def get_kaohsiung_events():
    events = []
    try:
        res = http_get(KAOHSIUNG_API, timeout=30)
        data = res.json()
        records = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for k in ("Data", "data", "Result", "result", "Records", "records"):
                if isinstance(data.get(k), list):
                    records.extend(data[k])
            if not records:
                records = [data]

        keywords = ["停止上班", "停止上課", "停班", "停課", "天然災害", "颱風", "豪雨", "高雄市"]
        for record in records:
            if not isinstance(record, dict):
                continue
            text = " ".join(f"{k}:{v}" for k, v in record.items() if isinstance(v, (str, int, float)))
            if not any(kw in text for kw in keywords):
                continue

            event_date = extract_date_from_text(text)
            if not event_date or event_date.year not in TARGET_YEARS:
                continue

            if "停止上班及上課" in text or ("停止上班" in text and "停止上課" in text):
                summary = "高雄市停班停課"
            elif "停止上班" in text:
                summary = "高雄市停止上班"
            elif "停止上課" in text:
                summary = "高雄市停止上課"
            else:
                summary = "高雄市停班停課公告"

            if any(e["date"] == event_date and e["summary"] == summary for e in events):
                continue

            events.append({
                "date": event_date,
                "summary": summary,
                "category": "高雄停班停課",
                "description": text,
            })
    except Exception as exc:
        print(f"⚠️ 讀取高雄停班停課 API 失敗（屬正常現象）：{exc}")

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
    print(f"\n{'=' * 60}\n🇹🇼 台灣生活行事曆產生器\n{'=' * 60}")
    all_events = build_government_events() + build_family_events() + get_kaohsiung_events()

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

    print(f"\n{'=' * 60}\n✅ {OUTPUT_FILE} 已成功產生，共 {len(unique_events)} 個事件\n{'=' * 60}\n")


if __name__ == "__main__":
    main()
