import csv
import io
import json
import re
import urllib.request
from datetime import date, timedelta


# ============================================================
# 基本設定
# ============================================================

CALENDAR_NAME = "🇹🇼 台灣生活行事曆"

DGPA_DATASET = "https://data.gov.tw/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/Get/"
    "95eec21d-4ee7-4920-94ff-d36727bc171f"
)


# ============================================================
# 網路下載
# ============================================================

def fetch_text(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Taiwan-Life-Calendar-GitHub"
        }
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8-sig")


def fetch_json(url, timeout=30):
    text = fetch_text(url, timeout)
    return json.loads(text)


# ============================================================
# 找官方辦公日曆 CSV
# ============================================================

def get_official_calendar_url(year):
    roc_year = year - 1911

    html = fetch_text(DGPA_DATASET)

    patterns = [
        rf'href=["\']([^"\']*{roc_year}年中華民國政府行政機關辦公日曆表[^"\']*\.csv[^"\']*)["\']',
        rf'href=["\']([^"\']*{roc_year}[^"\']*\.csv[^"\']*)["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)

        if match:
            url = match.group(1)

            url = (
                url
                .replace("&amp;", "&")
                .replace("&#x2F;", "/")
            )

            if url.startswith("/"):
                url = "https://data.gov.tw" + url

            return url

    return None


# ============================================================
# 官方國定假日
# ============================================================

def get_official_holidays(year):
    url = get_official_calendar_url(year)

    if not url:
        return []

    text = fetch_text(url)

    reader = csv.DictReader(io.StringIO(text))

    holidays = []

    for row in reader:

        raw_date = (
            row.get("西元日期")
            or row.get("日期")
            or ""
        ).strip()

        is_holiday = (
            row.get("是否放假")
            or ""
        ).strip()

        if not raw_date:
            continue

        if is_holiday != "2":
            continue

        event_date = normalize_date(raw_date)

        if not event_date:
            continue

        description = (
            row.get("備註")
            or ""
        ).strip()

        holidays.append(
            {
                "date": event_date,
                "title": "🇹🇼 台灣放假日",
                "description": (
                    "資料來源："
                    "行政院人事行政總處政府行政機關辦公日曆表"
                    + (
                        "\n備註：" + description
                        if description
                        else ""
                    )
                )
            }
        )

    return holidays


# ============================================================
# 高雄停班停課
# ============================================================

def get_kaohsiung_closures():
    try:
        data = fetch_json(KAOHSIUNG_API)

    except Exception:
        return []

    records = find_records(data)

    events = []

    for record in records:

        text = json.dumps(
            record,
            ensure_ascii=False
        )

        normalized = (
            text
            .replace(" ", "")
            .replace("\n", "")
        )

        # 只抓真正停止上班 / 停止上課
        if not any(
            keyword in normalized
            for keyword in [
                "停止上班上課",
                "停止上班",
                "停止上課",
                "停班停課",
            ]
        ):
            continue

        # 排除「照常」
        if any(
            keyword in normalized
            for keyword in [
                "照常上班上課",
                "照常上班",
                "照常上課",
            ]
        ):
            continue

        dates = extract_dates(record)

        for event_date in dates:

            events.append(
                {
                    "date": event_date,
                    "title": "🌪️ 高雄市停班停課",
                    "description": (
                        "資料來源：高雄市政府人事處"
                        "\n"
                        + format_record(record)
                    )
                }
            )

    return events


# ============================================================
# 尋找 JSON 裡的資料陣列
# ============================================================

def find_records(data):

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    preferred = [
        "data",
        "Data",
        "result",
        "Result",
        "records",
        "Records",
        "resultData",
        "ResultData",
    ]

    for key in preferred:

        if key in data:

            result = find_records(data[key])

            if result:
                return result

    for value in data.values():

        if isinstance(value, list):
            return value

    return []


# ============================================================
# 找日期
# ============================================================

def extract_dates(record):

    text = json.dumps(
        record,
        ensure_ascii=False
    )

    dates = set()

    # 西元日期
    western = re.findall(
        r"20\d{2}[/-]\d{1,2}[/-]\d{1,2}",
        text
    )

    for value in western:

        normalized = normalize_date(value)

        if normalized:
            dates.add(normalized)

    # 民國日期
    roc = re.findall(
        r"1\d{2}[/-]\d{1,2}[/-]\d{1,2}",
        text
    )

    for value in roc:

        parts = re.split(
            r"[/-]",
            value
        )

        if len(parts) != 3:
            continue

        year = int(parts[0]) + 1911
        month = int(parts[1])
        day = int(parts[2])

        try:

            event_date = date(
                year,
                month,
                day
            )

            dates.add(
                event_date.isoformat()
            )

        except ValueError:
            pass

    return sorted(dates)


# ============================================================
# 日期標準化
# ============================================================

def normalize_date(value):

    value = str(value).strip()

    match = re.match(
        r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
        value
    )

    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))

    try:

        return date(
            year,
            month,
            day
        ).isoformat()

    except ValueError:
        return None


# ============================================================
# 母親節
# ============================================================

def mothers_day(year):

    d = date(year, 5, 1)

    while d.weekday() != 6:
        d += timedelta(days=1)

    return (
        d + timedelta(days=7)
    ).isoformat()


# ============================================================
# ICS 文字轉義
# ============================================================

def escape_ics(value):

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


# ============================================================
# 建立 VEVENT
# ============================================================

def make_event(
    uid,
    event_date,
    title,
    description
):

    d = date.fromisoformat(event_date)

    end_date = (
        d + timedelta(days=1)
    ).isoformat()

    return "\r\n".join(
        [
            "BEGIN:VEVENT",
            f"UID:{uid}@taiwan-calendar",
            f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{end_date.replace('-', '')}",
            f"SUMMARY:{escape_ics(title)}",
            f"DESCRIPTION:{escape_ics(description)}",
            "END:VEVENT",
        ]
    )


# ============================================================
# 格式化高雄資料
# ============================================================

def format_record(record):

    parts = []

    for key, value in record.items():

        if value is None:
            continue

        value = str(value).strip()

        if not value:
            continue

        parts.append(
            f"{key}：{value}"
        )

    return "\n".join(parts)


# ============================================================
# 產生 ICS
# ============================================================

def generate_calendar():

    today = date.today()

    years = [
        today.year,
        today.year + 1,
    ]

    events = []

    # --------------------------------------------------------
    # 官方國定假日
    # --------------------------------------------------------

    for year in years:

        try:

            holidays = get_official_holidays(year)

            for item in holidays:

                events.append(
                    make_event(
                        f"official-{item['date']}",
                        item["date"],
                        item["title"],
                        item["description"]
                    )
                )

        except Exception as error:

            print(
                f"官方辦公日曆 {year} 取得失敗：{error}"
            )


    # --------------------------------------------------------
    # 母親節
    # --------------------------------------------------------

    for year in years:

        event_date = mothers_day(year)

        events.append(
            make_event(
                f"mothers-day-{year}",
                event_date,
                "❤️ 母親節",
                "備注｜台灣常見慶祝節日，不屬政府國定假日"
            )
        )


    # --------------------------------------------------------
    # 父親節
    # --------------------------------------------------------

    for year in years:

        event_date = (
            f"{year}-08-08"
        )

        events.append(
            make_event(
                f"fathers-day-{year}",
                event_date,
                "💙 父親節",
                "備注｜台灣常見慶祝節日，不屬政府國定假日"
            )
        )


    # --------------------------------------------------------
    # 高雄停班停課
    # --------------------------------------------------------

    try:

        closures = (
            get_kaohsiung_closures()
        )

        for item in closures:

            events.append(
                make_event(
                    (
                        "kaohsiung-"
                        + item["date"]
                    ),
                    item["date"],
                    item["title"],
                    item["description"]
                )
            )

    except Exception as error:

        print(
            f"高雄停班停課取得失敗：{error}"
        )


    # --------------------------------------------------------
    # 去除重複
    # --------------------------------------------------------

    unique = {}

    for event in events:

        uid = None

        for line in event.split("\r\n"):

            if line.startswith("UID:"):
                uid = line
                break

        if uid:
            unique[uid] = event

    events = list(
        unique.values()
    )


    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    events.sort(
        key=lambda event: event
        .split("DTSTART;VALUE=DATE:")[1]
        .split("\r\n")[0]
    )


    # --------------------------------------------------------
    # ICS
    # --------------------------------------------------------

    calendar = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Taiwan Life Calendar//GitHub//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        "X-WR-TIMEZONE:Asia/Taipei",
    ]

    calendar.extend(events)

    calendar.append(
        "END:VCALENDAR"
    )

    return "\r\n".join(calendar) + "\r\n"


# ============================================================
# 寫入檔案
# ============================================================

if __name__ == "__main__":

    ics = generate_calendar()

    with open(
        "taiwan.ics",
        "w",
        encoding="utf-8",
        newline=""
    ) as file:

        file.write(ics)

    print(
        "taiwan.ics 產生完成！"
    )
