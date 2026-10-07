import csv
import html
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote

import requests
import urllib3
from lunarcalendar import LunarDate

# 抑制 verify=False 所產生的警告訊息
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
#
# 這裡直接按照 DGPA 已核定的辦公日曆表處理。
#
# 2026：
# 02/20 = 小年夜補假
# 02/27 = 和平紀念日補假
# 04/03 = 兒童節補假
# 04/06 = 清明節補假
# 10/09 = 國慶日補假
# 10/26 = 臺灣光復暨金門古寧頭大捷紀念日補假
#
# 2027：
# 02/09 = 初一補假
# 02/10 = 初二補假
# 03/01 = 和平紀念日補假
# 04/06 = 兒童節補假
# 04/30 = 勞動節補假
# 10/11 = 國慶日補假
# 12/24 = 行憲紀念日補假
# 12/31 = 2028 開國紀念日補假
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
# 官方節日日期
#
# 這些是「節日本身」的日期。
# 補假另外由 MAKEUP_HOLIDAY_MAP 處理。
# ============================================================

FIXED_HOLIDAYS = {
    # (月, 日): 名稱
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
# HTTP Session
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def http_get(url, timeout=30):
    last_error = None
    for attempt in range(3):
        try:
            response = SESSION.get(
                url,
                timeout=timeout,
                verify=False,
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                continue

    raise RuntimeError(f"下載失敗：{url}\n原因：{last_error}")


# ============================================================
# 日期解析
# ============================================================

def parse_date(value):
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    # 2026-01-01 / 2026/01/01
    match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    # 115/01/01、115-01-01
    match = re.match(r"^(\d{2,3})[-/](\d{1,2})[-/](\d{1,2})$", text)
    if match:
        year = int(match.group(1))
        if year < 1911:
            year += 1911
        try:
            return date(year, int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    # 20260101
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
    encodings = ["utf-8-sig", "utf-8", "cp950", "big5"]
    for encoding in encodings:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def clean_text(value):
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\ufeff", "").replace("\xa0", " ")
    return text.strip()


def find_column(fieldnames, candidates):
    if not fieldnames:
        return None

    normalized = {clean_text(field): field for field in fieldnames}

    for candidate in candidates:
        candidate = clean_text(candidate)
        if candidate in normalized:
            return normalized[candidate]

    for field in fieldnames:
        field_text = clean_text(field)
        for candidate in candidates:
            candidate = clean_text(candidate)
            if candidate in field_text:
                return field

    return None


# ============================================================
# 從 data.gov.tw 找官方 CSV
# ============================================================

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
        matches = re.findall(pattern, page, flags=re.IGNORECASE)
        for url in matches:
            url = html.unescape(url)
            url = unquote(url)
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = "https://www.dgpa.gov.tw" + url

            if "FileConversion" in url and ".csv" in url.lower():
                urls.append(url)

    unique_urls = list(dict.fromkeys(urls))

    target_patterns = [f"{roc}%E5%B9%B4", f"{roc}年", f"{year}"]
    candidates = []
    for url in unique_urls:
        decoded = unquote(url)
        if "Google" in decoded:
            continue
        if any(pattern in decoded for pattern in target_patterns):
            candidates.append(url)

    if candidates:
        return candidates[-1]

    for url in unique_urls:
        decoded = unquote(url)
        if str(roc) in decoded and "Google" not in decoded:
            return url

    raise RuntimeError(
        f"找不到 {year} 年 DGPA CSV。\n"
        f"請確認 data.gov.tw 資料集是否已有 {roc} 年資料。"
    )


def download_dgpa_csv(year):
    url = get_dgpa_csv_url(year)
    print(f"📥 {year} DGPA CSV：\n{url}")
    response = http_get(url, timeout=60)
    return decode_csv_content(response.content)


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
    if "放假" in text:
        return True
    return False


def get_dgpa_columns(rows):
    if not rows:
        raise RuntimeError("DGPA CSV 沒有資料。")

    fieldnames = list(rows[0].keys())
    date_column = find_column(fieldnames, ["西元日期", "日期", "date"])
    holiday_column = find_column(fieldnames, ["是否放假", "放假", "isHoliday"])
    note_column = find_column(fieldnames, ["備註", "備註說明", "節日", "名稱", "note"])

    if not date_column:
        raise RuntimeError("DGPA CSV 找不到「西元日期」欄位。")
    if not holiday_column:
        raise RuntimeError("DGPA CSV 找不到「是否放假」欄位。")

    return date_column, holiday_column, note_column


# ============================================================
# 農曆日期與節日判斷
# ============================================================

def get_lunar_date(gregorian_date):
    try:
        return LunarDate.fromSolarDate(
            gregorian_date.year,
            gregorian_date.month,
            gregorian_date.day,
        )
    except Exception:
        return None


def lunar_day_name(lunar):
    if lunar is None:
        return None

    month = lunar.month
    day = lunar.day

    if month == 12 and day == 28:
        return "小年夜"
    if month == 12 and day in (29, 30):
        return "除夕"

    if month == 1:
        names = {
            1: "初一", 2: "初二", 3: "初三", 4: "初四", 5: "初五",
            6: "初六", 7: "初七", 8: "初八", 9: "初九", 10: "初十",
            11: "十一", 12: "十二", 13: "十三", 14: "十四", 15: "十五",
        }
        return names.get(day)

    return None


def is_lunar_new_year_window(lunar):
    if lunar is None:
        return False
    if lunar.month == 12 and lunar.day in (28, 29, 30):
        return True
    if lunar.month == 1 and 1 <= lunar.day <= 6:
        return True
    return False


def get_base_holiday_name(gregorian_date, year):
    date_key = gregorian_date.isoformat()

    # 1. 補假
    makeup_map = MAKEUP_HOLIDAY_MAP.get(year, {})
    if date_key in makeup_map:
        return makeup_map[date_key]

    # 2. 固定國定假日
    fixed_name = FIXED_HOLIDAYS.get((gregorian_date.month, gregorian_date.day))
    if fixed_name:
        return fixed_name

    # 3. 清明節
    qingming = QINGMING_DATES.get(year)
    if qingming is not None and gregorian_date == qingming:
        return "清明節"

    # 4. 農曆節日
    lunar = get_lunar_date(gregorian_date)
    if lunar is None:
        return None

    if is_lunar_new_year_window(lunar):
        return lunar_day_name(lunar)

    if lunar.month == 5 and lunar.day == 5:
        return "端午節"

    if lunar.month == 8 and lunar.day == 15:
        return "中秋節"

    return None


def normalize_official_note(note):
    text = clean_text(note)
    if not text:
        return ""
    return text.replace("放假", "").strip()


def should_create_government_event(gregorian_date, is_holiday, official_name):
    if not is_holiday:
        return False
    return bool(official_name)


# ============================================================
# 建立政府節日事件
# ============================================================

def build_government_events():
    events = []

    for year in TARGET_YEARS:
        print("")
        print("=" * 60)
        print(f"📅 讀取 {year} 年政府行政機關辦公日曆")
        print("=" * 60)

        rows = read_dgpa_rows(year)
        date_column, holiday_column, note_column = get_dgpa_columns(rows)
        year_events = []

        for row in rows:
            gregorian_date = parse_date(row.get(date_column))
            if gregorian_date is None or gregorian_date.year != year:
                continue

            holiday_value = row.get(holiday_column)
            is_holiday = is_holiday_value(holiday_value)
            if not is_holiday:
                continue

            official_note = ""
            if note_column:
                official_note = normalize_official_note(row.get(note_column))

            date_key = gregorian_date.isoformat()
            makeup_map = MAKEUP_HOLIDAY_MAP.get(year, {})

            if date_key in makeup_map:
                name = makeup_map[date_key]
            else:
                name = get_base_holiday_name(gregorian_date, year)
                if not name and official_note and "補假" not in official_note:
                    name = official_note

            if not should_create_government_event(gregorian_date, is_holiday, name):
                continue

            if name == "補假":
                continue

            # 避免重複
            if any(e["date"] == gregorian_date and e["summary"] == name for e in year_events):
                continue

            year_events.append({
                "date": gregorian_date,
                "summary": name,
                "category": "政府假日",
                "description": f"{year}年政府行政機關辦公日曆表",
            })

        year_events.sort(key=lambda item: item["date"])

        print(f"\n✅ {year} 年實際建立事件：")
        for event in year_events:
            print(f"  {event['date']} {event['summary']}")
        print(f"📌 {year} 年政府節日事件數：{len(year_events)}")

        events.extend(year_events)

    return events


# ============================================================
# 母親節 / 父親節
# ============================================================

def get_second_sunday_of_may(year):
    d = date(year, 5, 1)
    days_until_sunday = (6 - d.weekday()) % 7
    first_sunday = d + timedelta(days=days_until_sunday)
    return first_sunday + timedelta(days=7)


def build_family_events():
    events = []
    for year in TARGET_YEARS:
        # 母親節：每年五月第二個星期日
        events.append({
            "date": get_second_sunday_of_may(year),
            "summary": "母親節",
            "category": "節日",
            "description": "每年五月第二個星期日",
        })

        # 父親節：每年 8 月 8 日（台灣固定國曆八八節）
        events.append({
            "date": date(year, 8, 8),
            "summary": "父親節",
            "category": "節日",
            "description": "每年8月8日父親節",
        })

    return events


# ============================================================
# 高雄停班停課 API
# ============================================================

def extract_date_from_text(text):
    if not text:
        return None
    text = str(text)
    patterns = [
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})",
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                pass
    return None


def get_kaohsiung_events():
    events = []
    try:
        response = http_get(KAOHSIUNG_API, timeout=30)
        try:
            data = response.json()
        except Exception:
            print("⚠️ 高雄停班停課 API 不是有效 JSON，跳過。")
            return events

        records = []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            for key in ("Data", "data", "Result", "result", "Records", "records"):
                value = data.get(key)
                if isinstance(value, list):
                    records.extend(value)
            if not records:
                records = [data]

        keywords = ["停止上班", "停止上課", "停班", "停課", "天然災害", "颱風", "豪雨", "高雄市"]

        for record in records:
            if not isinstance(record, dict):
                continue

            text_parts = [
                f"{k}:{v}" for k, v in record.items() if isinstance(v, (str, int, float))
            ]
            combined = " ".join(text_parts)
            if not combined or not any(kw in combined for kw in keywords):
                continue

            event_date = extract_date_from_text(combined)
            if event_date is None or event_date.year not in TARGET_YEARS:
                continue

            if "停止上班及上課" in combined or ("停止上班" in combined and "停止上課" in combined):
                summary = "高雄市停班停課"
            elif "停止上班" in combined:
                summary = "高雄市停止上班"
            elif "停止上課" in combined:
                summary = "高雄市停止上課"
            else:
                summary = "高雄市停班停課公告"

            if any(e["date"] == event_date and e["summary"] == summary for e in events):
                continue

            events.append({
                "date": event_date,
                "summary": summary,
                "category": "高雄停班停課",
                "description": combined,
            })

        events.sort(key=lambda item: (item["date"], item["summary"]))
        print(f"\n🌧️ 高雄停班停課事件：{len(events)} 筆")

    except Exception as exc:
        print("\n⚠️ 讀取高雄停班停課 API 失敗：")
        print(exc)

    return events


# ============================================================
# ICS 格式輸出
# ============================================================

def ics_escape(value):
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return text


def make_uid(event):
    raw = f"{event['date'].isoformat()}-{event['summary']}-{CALENDAR_NAME}"
    return f"{uuid.uuid5(uuid.NAMESPACE_URL, raw)}@taiwan-calendar"


def build_ics(events):
    now = datetime.now(timezone.utc)
    dtstamp = now.strftime("%Y%m%dT%H%M%SZ")

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

    events = sorted(events, key=lambda item: (item["date"], item["summary"]))

    for event in events:
        event_date = event["date"]
        start = event_date.strftime("%Y%m%d")
        end = (event_date + timedelta(days=1)).strftime("%Y%m%d")
        summary = ics_escape(event["summary"])
        description = ics_escape(event.get("description", ""))
        uid = make_uid(event)

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            f"SUMMARY:{summary}",
            f"CATEGORIES:{ics_escape(event.get('category', ''))}",
            f"DESCRIPTION:{description}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def print_statistics(events):
    print("\n" + "=" * 60)
    print("📊 行事曆統計")
    print("=" * 60)

    for year in TARGET_YEARS:
        year_events = [e for e in events if e["date"].year == year]
        government_events = [e for e in year_events if e["category"] == "政府假日"]
        family_events = [e for e in year_events if e["category"] == "節日"]
        kaohsiung_events = [e for e in year_events if e["category"] == "高雄停班停課"]
        makeup_events = [e for e in government_events if "(補假)" in e["summary"]]
        normal_holidays = [e for e in government_events if "(補假)" not in e["summary"]]

        print(f"\n【{year}】")
        print(f"政府假日：{len(government_events)}")
        print(f"一般國定假日：{len(normal_holidays)}")
        print(f"補假：{len(makeup_events)}")
        print(f"母親節/父親節：{len(family_events)}")
        print(f"高雄停班停課：{len(kaohsiung_events)}")
        print(f"ICS 事件總數：{len(year_events)}")


# ============================================================
# 主程式
# ============================================================

def main():
    print("\n" + "=" * 60)
    print("🇹🇼 台灣生活行事曆產生器")
    print("=" * 60)

    all_events = []
    all_events.extend(build_government_events())
    all_events.extend(build_family_events())
    all_events.extend(get_kaohsiung_events())

    # 去重
    unique_events = []
    seen = set()
    for event in all_events:
        key = (event["date"], event["summary"])
        if key not in seen:
            seen.add(key)
            unique_events.append(event)

    unique_events.sort(key=lambda item: (item["date"], item["summary"]))

    ics_content = build_ics(unique_events)
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as file:
        file.write(ics_content)

    print_statistics(unique_events)

    print("\n" + "=" * 60)
    print("✅ taiwan.ics 已成功產生")
    print("=" * 60)
    print(f"📄 檔案：{OUTPUT_FILE}")
    print(f"📌 總事件數：{len(unique_events)}\n")


if __name__ == "__main__":
    main()
