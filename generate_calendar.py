import csv
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import date, timedelta


CALENDAR_NAME = "🇹🇼 台灣生活行事曆"

DGPA_DATASET = "https://data.gov.tw/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/Get/"
    "95eec21d-4ee7-4920-94ff-d36727bc171f"
)


# =========================================================
# 基本網路工具
# =========================================================

def fetch_text(url, timeout=60):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Taiwan-Life-Calendar-GitHub"
        }
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8-sig", errors="replace")


def fetch_json(url, timeout=90):
    text = fetch_text(url, timeout)
    return json.loads(text)


# =========================================================
# 日期工具
# =========================================================

def normalize_date(value):
    value = str(value).strip()

    # YYYY/MM/DD
    match = re.match(
        r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
        value
    )

    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    return None


# =========================================================
# 抓取行政院人事行政總處辦公日曆 CSV
# =========================================================

def get_official_calendar_url(year):
    """
    從 data.gov.tw 官方資料集頁面，
    找出指定年度的政府行政機關辦公日曆 CSV。

    目前官方頁面會出現：
    115年 → 2026
    116年 → 2027

    不直接猜 UUID，而是每天重新讀官方資料集頁面。
    """

    roc_year = year - 1911

    html = fetch_text(DGPA_DATASET)

    # 把 HTML entity 還原
    html = (
        html
        .replace("&amp;", "&")
        .replace("&#x2F;", "/")
        .replace("&#47;", "/")
    )

    # 找出 href
    links = re.findall(
        r'href=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE
    )

    candidates = []

    for link in links:

        decoded = urllib.parse.unquote(link)

        if str(roc_year) not in decoded:
            continue

        if ".csv" not in decoded.lower():
            continue

        # 政府資料目前多使用 FileConversion
        if "FileConversion" not in decoded:
            continue

        if link.startswith("/"):
            link = "https://data.gov.tw" + link

        candidates.append(link)

    # 優先選一般辦公日曆，
    # 不選 Google 行事曆專用版本
    for link in candidates:
        decoded = urllib.parse.unquote(link)

        if "Google" not in decoded:
            return link

    if candidates:
        return candidates[0]

    return None


def get_official_holidays(year):

    url = get_official_calendar_url(year)

    if not url:
        print(f"找不到 {year} 官方辦公日曆 CSV")
        return []

    print(f"{year} 官方辦公日曆：{url}")

    try:
        text = fetch_text(url, timeout=90)
    except Exception as error:
        print(f"{year} CSV 下載失敗：{error}")
        return []

    # CSV 有可能是 UTF-8 BOM
    text = text.lstrip("\ufeff")

    reader = csv.DictReader(io.StringIO(text))

    holidays = []

    for row in reader:

        raw_date = (
            row.get("西元日期")
            or row.get("日期")
            or row.get("date")
            or ""
        ).strip()

        holiday_value = (
            row.get("是否放假")
            or row.get("isHoliday")
            or ""
        ).strip()

        # 官方規則：
        # 0 = 上班
        # 2 = 放假
        if holiday_value != "2":
            continue

        event_date = normalize_date(raw_date)

        if not event_date:
            continue

        description = (
            row.get("備註")
            or row.get("備註說明")
            or row.get("description")
            or ""
        ).strip()

        holidays.append({
            "date": event_date,
            "title": "🇹🇼 台灣放假日",
            "description": (
                "資料來源：行政院人事行政總處"
                "\n政府行政機關辦公日曆表"
                + (
                    "\n備註：" + description
                    if description
                    else ""
                )
            )
        })

    print(
        f"{year} 官方放假日取得："
        f"{len(holidays)} 天"
    )

    return holidays


# =========================================================
# 高雄市天然災害停班停課
# =========================================================

def get_kaohsiung_closures():

    try:
        data = fetch_json(
            KAOHSIUNG_API,
            timeout=90
        )
    except Exception as error:
        print(
            "高雄停班停課資料取得失敗："
            f"{error}"
        )
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

        # 只處理停止上班／停止上課
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

            events.append({
                "date": event_date,
                "title": "🌪️ 高雄市停班停課",
                "description": (
                    "資料來源：高雄市政府人事處"
                    "\n天然災害停止上班上課相關訊息"
                    "\n"
                    + format_record(record)
                )
            })

    print(
        "高雄停班停課資料取得："
        f"{len(events)} 筆"
    )

    return events


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


# =========================================================
# 母親節
# =========================================================

def mothers_day(year):

    # 五月第二個星期日
    d = date(year, 5, 1)

    while d.weekday() != 6:
        d += timedelta(days=1)

    return (
        d + timedelta(days=7)
    ).isoformat()


# =========================================================
# ICS 工具
# =========================================================

def escape_ics(value):

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


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

    return "\r\n".join([
        "BEGIN:VEVENT",
        f"UID:{uid}@taiwan-calendar",
        (
            "DTSTART;VALUE=DATE:"
            f"{d.strftime('%Y%m%d')}"
        ),
        (
            "DTEND;VALUE=DATE:"
            f"{end_date.replace('-', '')}"
        ),
        f"SUMMARY:{escape_ics(title)}",
        (
            "DESCRIPTION:"
            f"{escape_ics(description)}"
        ),
        "END:VEVENT",
    ])


def format_record(record):

    parts = []

    if not isinstance(record, dict):
        return str(record)

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


# =========================================================
# 產生完整行事曆
# =========================================================

def generate_calendar():

    today = date.today()

    # 今年 + 明年
    years = [
        today.year,
        today.year + 1
    ]

    events = []

    # -----------------------------------------------------
    # 官方國定／政府行政機關放假日
    # -----------------------------------------------------

    for year in years:

        try:

            holidays = get_official_holidays(
                year
            )

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
                f"官方辦公日曆 {year} "
                f"取得失敗：{error}"
            )

    # -----------------------------------------------------
    # 母親節
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # 父親節
    # -----------------------------------------------------

    for year in years:

        event_date = f"{year}-08-08"

        events.append(
            make_event(
                f"fathers-day-{year}",
                event_date,
                "💙 父親節",
                "備注｜台灣常見慶祝節日，不屬政府國定假日"
            )
        )

    # -----------------------------------------------------
    # 高雄停班停課
    # -----------------------------------------------------

    try:

        closures = get_kaohsiung_closures()

        for item in closures:

            events.append(
                make_event(
                    f"kaohsiung-{item['date']}",
                    item["date"],
                    item["title"],
                    item["description"]
                )
            )

    except Exception as error:

        print(
            "高雄停班停課取得失敗："
            f"{error}"
        )

    # -----------------------------------------------------
    # 去除重複 UID
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # 按日期排序
    # -----------------------------------------------------

    events.sort(
        key=lambda event:
        event
        .split(
            "DTSTART;VALUE=DATE:"
        )[1]
        .split("\r\n")[0]
    )

    # -----------------------------------------------------
    # 建立 VCALENDAR
    # -----------------------------------------------------

    calendar = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        (
            "PRODID:"
            "//Taiwan Life Calendar"
            "//GitHub//ZH-TW"
        ),
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        "X-WR-TIMEZONE:Asia/Taipei",
    ]

    calendar.extend(events)

    calendar.append(
        "END:VCALENDAR"
    )

    return (
        "\r\n".join(calendar)
        + "\r\n"
    )


# =========================================================
# 主程式
# =========================================================

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
        "========================================"
    )
    print(
        "taiwan.ics 產生完成！"
    )
    print(
        "========================================"
    )
