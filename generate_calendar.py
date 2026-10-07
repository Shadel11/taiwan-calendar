import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, unquote

import requests


# ============================================================
# 基本設定
# ============================================================

CALENDAR_NAME = "台灣生活行事曆"

TARGET_YEARS = [2026, 2027]

DGPA_DATASET = "https://data.gov.tw/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/Get/"
    "95eec21d-4ee7-4920-94ff-d36727bc171f"
)

OUTPUT_FILE = "taiwan.ics"

TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}


# ============================================================
# HTTP
# ============================================================

def http_get(url, timeout=TIMEOUT):
    return requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
    )


# ============================================================
# 日期解析
# ============================================================

def parse_date(value):
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    # YYYYMMDD
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None

    # YYYY/MM/DD
    if re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", text):
        try:
            return datetime.strptime(text, "%Y/%m/%d").date()
        except ValueError:
            return None

    # YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return None

    return None


# ============================================================
# CSV 解碼
# ============================================================

def decode_csv_content(content):
    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp950",
        "big5",
    ]

    for encoding in encodings:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue

    return content.decode("utf-8", errors="replace")


# ============================================================
# 取得 DGPA CSV
# ============================================================

def find_csv_url():
    response = http_get(DGPA_DATASET)
    response.raise_for_status()

    html = response.text

    candidates = []

    # 找出 data.gov.tw / DGPA CSV 連結
    patterns = [
        r'https?://[^"\']+\.csv[^"\']*',
        r'https?://[^"\']*FileConversion[^"\']*',
        r'FileConversion\?[^"\']+',
    ]

    for pattern in patterns:
        for match in re.findall(pattern, html, flags=re.I):
            candidates.append(match)

    # HTML entity 還原
    cleaned = []

    for url in candidates:
        url = url.replace("&amp;", "&")
        url = unquote(url)

        if url.startswith("/"):
            url = urljoin("https://www.dgpa.gov.tw", url)

        if url.startswith("FileConversion"):
            url = urljoin(
                "https://www.dgpa.gov.tw/",
                url,
            )

        cleaned.append(url)

    # 去重
    unique = []

    for url in cleaned:
        if url not in unique:
            unique.append(url)

    # 優先 CSV
    csv_urls = [
        url for url in unique
        if ".csv" in url.lower()
    ]

    if csv_urls:
        return csv_urls

    return unique


# ============================================================
# 取得指定年份 CSV
# ============================================================

def get_dgpa_csv(year):
    urls = find_csv_url()

    year_text = str(year)

    preferred = []

    for url in urls:
        decoded = unquote(url)

        if (
            year_text in decoded
            or f"{year - 1911}" in decoded
            or f"{year - 1911}年" in decoded
        ):
            preferred.append(url)

    candidates = preferred + [
        url for url in urls
        if url not in preferred
    ]

    last_error = None

    for url in candidates:
        try:
            print(f"讀取 {year} 政府辦公日曆：[{url}]")

            response = http_get(url)
            response.raise_for_status()

            content = response.content

            text = decode_csv_content(content)

            # 確認是不是我們需要的 DGPA CSV
            if "西元日期" not in text or "是否放假" not in text:
                continue

            return text, url

        except Exception as exc:
            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(f"找不到 {year} 政府辦公日曆 CSV")


# ============================================================
# CSV 欄位
# ============================================================

def normalize_field(value):
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\xa0", " ")
        .strip()
    )


def parse_dgpa_rows(year, csv_text):
    reader = csv.DictReader(io.StringIO(csv_text))

    if not reader.fieldnames:
        raise RuntimeError(f"{year} CSV 沒有欄位")

    fieldnames = [
        normalize_field(x)
        for x in reader.fieldnames
    ]

    print(f"{year} CSV 欄位：{fieldnames}")

    date_field = None
    holiday_field = None
    note_field = None

    for field in fieldnames:
        if field == "西元日期":
            date_field = field

        elif field == "是否放假":
            holiday_field = field

        elif field == "備註":
            note_field = field

    if not date_field or not holiday_field:
        raise RuntimeError(
            f"{year} CSV 找不到必要欄位"
        )

    rows = []

    for raw_row in reader:
        row = {
            normalize_field(k): normalize_field(v)
            for k, v in raw_row.items()
        }

        event_date = parse_date(row.get(date_field))

        if event_date is None:
            continue

        if event_date.year != year:
            continue

        holiday_value = row.get(holiday_field, "").strip()

        note = row.get(note_field, "") if note_field else ""

        # 0 = 上班
        # 2 = 放假
        if holiday_value not in ("0", "2"):
            continue

        rows.append({
            "date": event_date,
            "is_holiday": holiday_value == "2",
            "note": note,
        })

    print(f"{year} CSV 有效資料：{len(rows)} 筆")

    holiday_count = sum(
        1 for row in rows
        if row["is_holiday"]
    )

    work_count = len(rows) - holiday_count

    print(f"{year} 放假資料：{holiday_count} 筆")
    print(f"{year} 上班資料：{work_count} 筆")

    return rows


# ============================================================
# 文字判斷
# ============================================================

def contains_any(text, keywords):
    text = text or ""

    return any(
        keyword in text
        for keyword in keywords
    )


def is_makeup_holiday(note):
    return contains_any(
        note,
        [
            "補假",
            "補休",
            "補放",
        ],
    )


def is_makeup_workday(note):
    return contains_any(
        note,
        [
            "補班",
            "補行上班",
            "調整上班",
            "調整為上班",
            "補上班",
        ],
    )


# ============================================================
# 固定節日名稱
# ============================================================

def fixed_holiday_name(event_date):
    month_day = (
        event_date.month,
        event_date.day,
    )

    names = {
        (1, 1): "開國紀念日",
        (2, 28): "和平紀念日",
        (4, 4): "兒童節",
        (5, 1): "勞動節",
        (10, 10): "國慶日",
        (10, 25): "臺灣光復暨金門古寧頭大捷紀念日",
        (12, 25): "行憲紀念日",
    }

    return names.get(month_day)


# ============================================================
# 農曆
# ============================================================

def get_lunar_info(event_date):
    """
    使用 lunardate。
    只在真正需要判斷春節日期時使用。
    """

    try:
        from lunardate import LunarDate

        lunar = LunarDate.fromSolarDate(
            event_date.year,
            event_date.month,
            event_date.day,
        )

        return lunar.year, lunar.month, lunar.day

    except Exception:
        return None


def spring_festival_name(event_date):
    lunar = get_lunar_info(event_date)

    if not lunar:
        return None

    lunar_year, lunar_month, lunar_day = lunar

    if lunar_month == 1:
        if lunar_day == 1:
            return "初一"

        if lunar_day == 2:
            return "初二"

        if lunar_day == 3:
            return "初三"

    # 判斷除夕
    if lunar_month == 12:
        tomorrow = event_date + timedelta(days=1)
        tomorrow_lunar = get_lunar_info(tomorrow)

        if tomorrow_lunar:
            _, tomorrow_month, tomorrow_day = tomorrow_lunar

            if (
                tomorrow_month == 1
                and tomorrow_day == 1
            ):
                return "除夕"

    # 小年夜：
    # 農曆十二月二十八或二十九，
    # 且隔天為除夕。
    if lunar_month == 12 and lunar_day in (28, 29):
        tomorrow = event_date + timedelta(days=1)
        tomorrow_lunar = get_lunar_info(tomorrow)

        if tomorrow_lunar:
            _, tomorrow_month, tomorrow_day = tomorrow_lunar

            if (
                tomorrow_month == 12
                and tomorrow_day in (29, 30)
            ):
                return "小年夜"

    return None


# ============================================================
# 春節官方事件判斷
# ============================================================

def is_spring_period(year, event_date):
    """
    僅處理已知政府辦公日曆的春節區間。

    2026：2/14～2/22
    2027：2/4～2/10

    注意：
    這只是「判斷春節區域」，
    並不代表區域內每一天都建立事件。
    普通週末仍會被排除。
    """

    if year == 2026:
        return date(2026, 2, 14) <= event_date <= date(2026, 2, 22)

    if year == 2027:
        return date(2027, 2, 4) <= event_date <= date(2027, 2, 10)

    return False


def get_spring_event_name(row, year):
    event_date = row["date"]

    if not is_spring_period(year, event_date):
        return None

    # 先看農曆名稱
    lunar_name = spring_festival_name(event_date)

    if lunar_name:
        return lunar_name

    # 補假
    if is_makeup_holiday(row["note"]):
        return "春節(補假)"

    # 春節區域內只有官方標記的補假或節日才建立
    note = row["note"]

    if contains_any(
        note,
        [
            "春節",
            "除夕",
            "小年夜",
        ],
    ):
        return "春節"

    return None


# ============================================================
# 判斷某天是不是「真正官方事件」
# ============================================================

def get_event_name(row, year):
    event_date = row["date"]
    note = row["note"]

    # --------------------------------------------------------
    # 1. 上班日：只有官方明確寫補班才建立
    # --------------------------------------------------------

    if not row["is_holiday"]:
        if is_makeup_workday(note):
            base_name = fixed_holiday_name(event_date)

            if base_name:
                return f"[補班]{base_name}"

            return "[補班]"

        return None

    # --------------------------------------------------------
    # 2. 春節
    # --------------------------------------------------------

    spring_name = get_spring_event_name(
        row,
        year,
    )

    if spring_name:
        if is_makeup_holiday(note):
            # 已經是「春節補假」時
            if spring_name in (
                "初一",
                "初二",
                "初三",
                "除夕",
                "小年夜",
            ):
                return spring_name

            return "春節(補假)"

        return spring_name

    # --------------------------------------------------------
    # 3. 官方補假
    # --------------------------------------------------------

    if is_makeup_holiday(note):
        base_name = fixed_holiday_name(event_date)

        if base_name:
            return f"{base_name}(補假)"

        # 依備註判斷原本是哪個節日
        if contains_any(note, ["兒童節"]):
            return "兒童節(補假)"

        if contains_any(note, ["清明"]):
            return "清明節(補假)"

        if contains_any(note, ["勞動節"]):
            return "勞動節(補假)"

        if contains_any(note, ["端午"]):
            return "端午節(補假)"

        if contains_any(note, ["中秋"]):
            return "中秋節(補假)"

        if contains_any(note, ["國慶"]):
            return "國慶日(補假)"

        if contains_any(note, ["臺灣光復", "光復"]):
            return "臺灣光復暨金門古寧頭大捷紀念日(補假)"

        if contains_any(note, ["行憲"]):
            return "行憲紀念日(補假)"

        if contains_any(note, ["和平"]):
            return "和平紀念日(補假)"

        if contains_any(note, ["開國"]):
            return "開國紀念日(補假)"

    # --------------------------------------------------------
    # 4. 固定日期節日
    # --------------------------------------------------------

    fixed_name = fixed_holiday_name(event_date)

    if fixed_name:
        # 關鍵：
        # 固定節日本身才建立，
        # 不因為它落在連假中，就把前後週末一起建立。
        return fixed_name

    # --------------------------------------------------------
    # 5. 依官方備註判斷真正節日
    # --------------------------------------------------------

    if contains_any(note, ["清明"]):
        return "清明節"

    if contains_any(note, ["端午"]):
        return "端午節"

    if contains_any(note, ["中秋"]):
        return "中秋節"

    if contains_any(note, ["孔子誕辰", "教師節"]):
        return "孔子誕辰紀念日/教師節"

    if contains_any(note, ["勞動節"]):
        return "勞動節"

    if contains_any(note, ["國慶"]):
        return "國慶日"

    if contains_any(note, ["臺灣光復", "光復"]):
        return "臺灣光復暨金門古寧頭大捷紀念日"

    if contains_any(note, ["行憲"]):
        return "行憲紀念日"

    if contains_any(note, ["和平紀念日"]):
        return "和平紀念日"

    if contains_any(note, ["開國紀念日"]):
        return "開國紀念日"

    if contains_any(note, ["兒童節"]):
        return "兒童節"

    # --------------------------------------------------------
    # 6. 其他普通放假日
    # --------------------------------------------------------
    #
    # 這裡故意 return None。
    #
    # 因為：
    # 週六、週日雖然「是否放假=2」，
    # 但不是我們要建立的節日事件。
    #

    return None


# ============================================================
# 建立政府事件
# ============================================================

def build_government_events(year, rows):
    events = []

    general_count = 0
    makeup_holiday_count = 0
    makeup_work_count = 0

    for row in rows:
        name = get_event_name(row, year)

        if not name:
            continue

        event_date = row["date"]

        if name.startswith("[補班]"):
            makeup_work_count += 1

        elif "(補假)" in name:
            makeup_holiday_count += 1

        else:
            general_count += 1

        events.append({
            "date": event_date,
            "summary": name,
            "source": "政府",
        })

    return (
        events,
        general_count,
        makeup_holiday_count,
        makeup_work_count,
    )


# ============================================================
# 家庭節日
# ============================================================

def second_sunday_of_may(year):
    d = date(year, 5, 1)

    # weekday(): Monday=0 ... Sunday=6
    days_to_sunday = (6 - d.weekday()) % 7

    first_sunday = d + timedelta(
        days=days_to_sunday
    )

    return first_sunday + timedelta(days=7)


def build_family_events(year):
    events = []

    # 母親節
    mother_day = second_sunday_of_may(year)

    events.append({
        "date": mother_day,
        "summary": "母親節",
        "source": "家庭節日",
    })

    # 父親節
    father_day = date(year, 8, 8)

    events.append({
        "date": father_day,
        "summary": "父親節",
        "source": "家庭節日",
    })

    return events


# ============================================================
# 高雄停班停課 API
# ============================================================

def find_values_recursively(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key, value

            yield from find_values_recursively(value)

    elif isinstance(obj, list):
        for item in obj:
            yield from find_values_recursively(item)


def parse_kaohsiung_events():
    try:
        response = http_get(
            KAOHSIUNG_API,
            timeout=TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        events = []

        # API 格式可能變動，因此採遞迴搜尋
        for key, value in find_values_recursively(data):

            key_text = str(key).lower()

            if not any(
                word in key_text
                for word in [
                    "date",
                    "日期",
                    "time",
                    "時間",
                ]
            ):
                continue

            if not isinstance(value, str):
                continue

            parsed = parse_date(value[:10])

            if not parsed:
                continue

            if parsed.year not in TARGET_YEARS:
                continue

            events.append({
                "date": parsed,
                "summary": "高雄市停班停課",
                "source": "高雄市",
            })

        # 去重
        unique = {}

        for event in events:
            key = (
                event["date"],
                event["summary"],
            )

            unique[key] = event

        return list(unique.values())

    except Exception as exc:
        print(
            f"高雄停班停課 API 讀取失敗：{exc}"
        )

        return []


# ============================================================
# 去重
# ============================================================

def deduplicate_events(events):
    unique = {}

    for event in events:
        key = (
            event["date"],
            event["summary"],
        )

        unique[key] = event

    return sorted(
        unique.values(),
        key=lambda x: (
            x["date"],
            x["summary"],
        ),
    )


# ============================================================
# ICS Escape
# ============================================================

def ics_escape(text):
    text = str(text)

    return (
        text
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


# ============================================================
# UID
# ============================================================

def make_uid(event):
    key = (
        f"{event['source']}|"
        f"{event['date'].isoformat()}|"
        f"{event['summary']}"
    )

    uid = uuid.uuid5(
        uuid.NAMESPACE_URL,
        key,
    )

    return f"{uid}@taiwan-calendar"


# ============================================================
# ICS
# ============================================================

def build_ics(events):
    now = datetime.utcnow().strftime(
        "%Y%m%dT%H%M%SZ"
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Shadel11//Taiwan Calendar//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(CALENDAR_NAME)}",
        "X-WR-TIMEZONE:Asia/Taipei",
    ]

    for event in events:
        event_date = event["date"]

        start = event_date.strftime("%Y%m%d")
        end = (
            event_date + timedelta(days=1)
        ).strftime("%Y%m%d")

        uid = make_uid(event)

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            f"SUMMARY:{ics_escape(event['summary'])}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")

    return "\r\n".join(lines) + "\r\n"


# ============================================================
# 主程式
# ============================================================

def main():
    print("開始產生台灣生活行事曆")

    current_year = datetime.now().year

    print(f"目前年份：{current_year}")
    print(f"產生年份：{TARGET_YEARS}")

    all_events = []

    government_events = []

    total_general = 0
    total_makeup_holiday = 0
    total_makeup_work = 0

    # --------------------------------------------------------
    # DGPA
    # --------------------------------------------------------

    for year in TARGET_YEARS:
        csv_text, csv_url = get_dgpa_csv(year)

        rows = parse_dgpa_rows(
            year,
            csv_text,
        )

        (
            events,
            general_count,
            makeup_holiday_count,
            makeup_work_count,
        ) = build_government_events(
            year,
            rows,
        )

        government_events.extend(events)

        total_general += general_count
        total_makeup_holiday += makeup_holiday_count
        total_makeup_work += makeup_work_count

    print(f"政府事件：{len(government_events)}")

    # --------------------------------------------------------
    # 家庭節日
    # --------------------------------------------------------

    family_events = []

    for year in TARGET_YEARS:
        family_events.extend(
            build_family_events(year)
        )

    mother_count = sum(
        1
        for event in family_events
        if event["summary"] == "母親節"
    )

    father_count = sum(
        1
        for event in family_events
        if event["summary"] == "父親節"
    )

    print(f"家庭節日：{len(family_events)}")

    # --------------------------------------------------------
    # 高雄
    # --------------------------------------------------------

    kaohsiung_events = parse_kaohsiung_events()

    print(
        f"高雄停班停課："
        f"{len(kaohsiung_events)}"
    )

    # --------------------------------------------------------
    # 合併
    # --------------------------------------------------------

    all_events.extend(government_events)
    all_events.extend(family_events)
    all_events.extend(kaohsiung_events)

    all_events = deduplicate_events(
        all_events
    )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    print(
        f"政府一般放假事件："
        f"{total_general}"
    )

    print(
        f"政府補假事件："
        f"{total_makeup_holiday}"
    )

    print(
        f"政府補班事件："
        f"{total_makeup_work}"
    )

    print(
        f"母親節："
        f"{mother_count}"
    )

    print(
        f"父親節："
        f"{father_count}"
    )

    print(
        f"高雄市停班停課："
        f"{len(kaohsiung_events)}"
    )

    print(
        f"全部事件："
        f"{len(all_events)}"
    )

    print("=" * 60)

    for year in TARGET_YEARS:
        count = sum(
            1
            for event in all_events
            if event["date"].year == year
        )

        print(
            f"{year}：{count} 筆"
        )

    print("=" * 60)

    # --------------------------------------------------------
    # 寫入 ICS
    # --------------------------------------------------------

    ics_content = build_ics(
        all_events
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        file.write(ics_content)

    print(
        f"已產生：{OUTPUT_FILE}"
    )

    print(
        f"事件總數：{len(all_events)}"
    )

    print("完成。")


if __name__ == "__main__":
    main()
