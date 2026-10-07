import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, unquote

import requests
import urllib3

try:
    from lunardate import LunarDate
except ImportError:
    LunarDate = None


# ============================================================
# 基本設定
# ============================================================

CALENDAR_NAME = "台灣生活行事曆"

DGPA_DATASET = "https://data.gov.tw/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/Get/"
    "95eec21d-4ee7-4920-94ff-d36727bc171f"
)

CURRENT_YEAR = datetime.now().year
YEARS = [CURRENT_YEAR, CURRENT_YEAR + 1]


# ============================================================
# HTTP
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)

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

        except requests.RequestException as exc:
            last_error = exc

            if attempt < 2:
                continue

    raise last_error


# ============================================================
# 文字處理
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\ufeff", "")
    value = value.replace("\r", "")
    value = value.replace("\n", " ")
    value = value.replace("\xa0", " ")

    return value.strip()


def normalize_text(value):
    value = clean_text(value)

    value = value.replace("臺", "台")
    value = value.replace("　", " ")

    return re.sub(r"\s+", "", value)


# ============================================================
# 日期解析
# ============================================================

def parse_date(value):
    """
    支援 DGPA 實際 CSV 格式：

    20260101
    2026/01/01
    2026-01-01
    115/01/01
    115-01-01
    """

    value = clean_text(value)

    if not value:
        return None

    # --------------------------------------------------------
    # DGPA CSV 最重要的格式：YYYYMMDD
    # --------------------------------------------------------

    match = re.fullmatch(
        r"(\d{4})(\d{2})(\d{2})",
        value,
    )

    if match:
        try:
            return date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
        except ValueError:
            return None

    # --------------------------------------------------------
    # 其他常見格式
    # --------------------------------------------------------

    value = value.replace(
        ".",
        "/",
    )

    value = value.replace(
        "-",
        "/",
    )

    value = value.split(" ")[0]

    match = re.fullmatch(
        r"(\d{3,4})/(\d{1,2})/(\d{1,2})",
        value,
    )

    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))

    if year < 1911:
        year += 1911

    try:
        return date(
            year,
            month,
            day,
        )
    except ValueError:
        return None


# ============================================================
# 是否放假
# ============================================================

def is_holiday_value(value):
    value = normalize_text(value).upper()

    return value in {
        "2",
        "放假",
        "假日",
        "是",
        "Y",
        "YES",
        "TRUE",
    }


def is_workday_value(value):
    value = normalize_text(value).upper()

    return value in {
        "0",
        "上班",
        "工作日",
        "否",
        "N",
        "NO",
        "FALSE",
    }


# ============================================================
# DGPA 資源
# ============================================================

def get_resource_links():
    response = http_get(
        DGPA_DATASET
    )

    html = response.text

    links = re.findall(
        r'href=["\']([^"\']+\.csv[^"\']*)["\']',
        html,
        flags=re.IGNORECASE,
    )

    results = []

    for link in links:
        link = unquote(link)

        full_url = urljoin(
            DGPA_DATASET,
            link,
        )

        if full_url not in results:
            results.append(full_url)

    return results


def find_csv_url(year):
    links = get_resource_links()

    if not links:
        raise RuntimeError(
            "找不到 DGPA CSV 資源。"
        )

    roc_year = year - 1911

    candidates = []

    for url in links:

        text = normalize_text(url)

        score = 0

        if str(year) in text:
            score += 100

        if str(roc_year) in text:
            score += 100

        if "辦公日曆表" in text:
            score += 50

        if "google" in text.lower():
            score -= 100

        if "csv" in text.lower():
            score += 10

        candidates.append(
            (
                score,
                url,
            )
        )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]


# ============================================================
# CSV 解析
# ============================================================

def decode_csv(content):
    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp950",
        "big5",
    ]

    for encoding in encodings:
        try:
            return content.decode(
                encoding
            )
        except UnicodeDecodeError:
            continue

    return content.decode(
        "utf-8",
        errors="replace",
    )


def detect_column(fieldnames, keywords):
    normalized_fields = []

    for field in fieldnames:
        normalized_fields.append(
            (
                field,
                normalize_text(field),
            )
        )

    for keyword in keywords:

        keyword = normalize_text(
            keyword
        )

        for original, normalized_name in normalized_fields:

            if keyword in normalized_name:
                return original

    return None


def read_dgpa_csv(year):
    csv_url = find_csv_url(
        year
    )

    print(
        f"讀取 {year} 政府辦公日曆：{csv_url}"
    )

    response = http_get(
        csv_url
    )

    text = decode_csv(
        response.content
    )

    reader = csv.DictReader(
        io.StringIO(text)
    )

    fieldnames = reader.fieldnames or []

    print(
        f"{year} CSV 欄位：{fieldnames}"
    )

    date_field = detect_column(
        fieldnames,
        [
            "西元日期",
            "日期",
            "date",
        ],
    )

    holiday_field = detect_column(
        fieldnames,
        [
            "是否放假",
            "放假",
            "holiday",
        ],
    )

    note_field = detect_column(
        fieldnames,
        [
            "備註",
            "節日",
            "說明",
            "note",
        ],
    )

    if not date_field:
        raise RuntimeError(
            f"{year} 找不到日期欄位。"
        )

    if not holiday_field:
        raise RuntimeError(
            f"{year} 找不到是否放假欄位。"
        )

    rows = []

    for row in reader:

        current_date = parse_date(
            row.get(
                date_field,
                "",
            )
        )

        if not current_date:
            continue

        if current_date.year != year:
            continue

        holiday_value = row.get(
            holiday_field,
            "",
        )

        note = ""

        if note_field:
            note = clean_text(
                row.get(
                    note_field,
                    "",
                )
            )

        rows.append(
            {
                "date": current_date,
                "holiday": is_holiday_value(
                    holiday_value
                ),
                "workday": is_workday_value(
                    holiday_value
                ),
                "note": note,
            }
        )

    rows.sort(
        key=lambda item: item["date"]
    )

    print(
        f"{year} CSV 有效資料：{len(rows)} 筆"
    )

    print(
        f"{year} 放假資料："
        f"{sum(1 for row in rows if row['holiday'])} 筆"
    )

    print(
        f"{year} 上班資料："
        f"{sum(1 for row in rows if row['workday'])} 筆"
    )

    return rows


# ============================================================
# 農曆
# ============================================================

def lunar_day_label(solar_date):
    if LunarDate is None:
        return None, None

    try:
        lunar = LunarDate.fromSolarDate(
            solar_date.year,
            solar_date.month,
            solar_date.day,
        )

        return (
            lunar.month,
            lunar.day,
        )

    except Exception:
        return None, None


# ============================================================
# 固定節日
# ============================================================

def fixed_holiday_name(current_date):

    fixed = {
        (1, 1): "開國紀念日",
        (2, 28): "和平紀念日",
        (5, 1): "勞動節",
        (10, 10): "國慶日",
        (
            10,
            25,
        ): "臺灣光復暨金門古寧頭大捷紀念日",
        (
            12,
            25,
        ): "行憲紀念日",
    }

    return fixed.get(
        (
            current_date.month,
            current_date.day,
        )
    )


# ============================================================
# 備註判斷
# ============================================================

def is_spring_festival_note(note):
    text = normalize_text(
        note
    )

    return any(
        keyword in text
        for keyword in [
            "春節",
            "農曆春節",
            "除夕",
            "小年夜",
        ]
    )


def is_makeup_holiday_note(note):
    text = normalize_text(
        note
    )

    return any(
        keyword in text
        for keyword in [
            "補假",
            "補休",
            "補放",
            "調整放假",
        ]
    )


def is_makeup_workday_note(note):
    text = normalize_text(
        note
    )

    return any(
        keyword in text
        for keyword in [
            "補行上班",
            "調整上班",
            "補班",
            "調整辦公",
            "補行辦公",
        ]
    )


# ============================================================
# 備註節日名稱
# ============================================================

def extract_holiday_name_from_note(note):

    text = normalize_text(
        note
    )

    if not text:
        return None

    mappings = [
        (
            [
                "兒童節",
            ],
            "兒童節",
        ),
        (
            [
                "清明節",
                "民族掃墓節",
            ],
            "清明節",
        ),
        (
            [
                "端午節",
            ],
            "端午節",
        ),
        (
            [
                "中秋節",
            ],
            "中秋節",
        ),
        (
            [
                "孔子誕辰紀念日",
                "教師節",
            ],
            "孔子誕辰紀念日/教師節",
        ),
        (
            [
                "國慶日",
            ],
            "國慶日",
        ),
        (
            [
                "臺灣光復",
                "台灣光復",
                "古寧頭大捷",
            ],
            "臺灣光復暨金門古寧頭大捷紀念日",
        ),
        (
            [
                "行憲紀念日",
            ],
            "行憲紀念日",
        ),
        (
            [
                "和平紀念日",
                "二二八",
                "228",
            ],
            "和平紀念日",
        ),
        (
            [
                "勞動節",
            ],
            "勞動節",
        ),
        (
            [
                "元旦",
                "開國紀念日",
            ],
            "開國紀念日",
        ),
    ]

    for keywords, name in mappings:

        for keyword in keywords:

            if normalize_text(
                keyword
            ) in text:

                return name

    return None


# ============================================================
# 春節名稱
# ============================================================

def get_spring_name(
    rows,
    current_date,
    note,
):

    if not is_spring_festival_period(
        rows,
        current_date,
    ):
        return None

    lunar_month, lunar_day = (
        lunar_day_label(
            current_date
        )
    )

    # --------------------------------------------------------
    # 初一
    # --------------------------------------------------------

    if (
        lunar_month == 1
        and lunar_day == 1
    ):
        return "初一"

    # --------------------------------------------------------
    # 初二
    # --------------------------------------------------------

    if (
        lunar_month == 1
        and lunar_day == 2
    ):
        return "初二"

    # --------------------------------------------------------
    # 初三
    # --------------------------------------------------------

    if (
        lunar_month == 1
        and lunar_day == 3
    ):
        return "初三"

    # --------------------------------------------------------
    # 除夕
    # --------------------------------------------------------

    tomorrow = (
        current_date
        + timedelta(days=1)
    )

    tomorrow_month, tomorrow_day = (
        lunar_day_label(
            tomorrow
        )
    )

    if (
        tomorrow_month == 1
        and tomorrow_day == 1
    ):
        return "除夕"

    # --------------------------------------------------------
    # 小年夜
    # --------------------------------------------------------

    if (
        lunar_month == 12
        and lunar_day in (28, 29)
    ):
        if (
            tomorrow_month == 12
            and tomorrow_day in (29, 30)
        ):
            return "小年夜"

    # --------------------------------------------------------
    # 補假
    # --------------------------------------------------------

    if is_makeup_holiday_note(
        note
    ):
        return "春節(補假)"

    return "春節"


# ============================================================
# 春節區間
# ============================================================

def is_spring_festival_period(
    rows,
    current_date,
):

    holiday_dates = {
        row["date"]
        for row in rows
        if row["holiday"]
    }

    if current_date not in holiday_dates:
        return False

    start = current_date
    end = current_date

    while (
        start - timedelta(days=1)
        in holiday_dates
    ):
        start -= timedelta(days=1)

    while (
        end + timedelta(days=1)
        in holiday_dates
    ):
        end += timedelta(days=1)

    length = (
        end - start
    ).days + 1

    if length < 5:
        return False

    for row in rows:

        if not (
            start
            <= row["date"]
            <= end
        ):
            continue

        if is_spring_festival_note(
            row["note"]
        ):
            return True

    return False


# ============================================================
# 假日連續區間
# ============================================================

def get_holiday_runs(rows):

    holiday_dates = sorted(
        row["date"]
        for row in rows
        if row["holiday"]
    )

    if not holiday_dates:
        return []

    runs = []

    start = holiday_dates[0]
    previous = holiday_dates[0]

    for current in holiday_dates[1:]:

        if (
            current
            == previous + timedelta(days=1)
        ):
            previous = current
            continue

        runs.append(
            (
                start,
                previous,
            )
        )

        start = current
        previous = current

    runs.append(
        (
            start,
            previous,
        )
    )

    return runs


def run_has_official_anchor(
    rows,
    start,
    end,
):

    for row in rows:

        current_date = row["date"]

        if not (
            start
            <= current_date
            <= end
        ):
            continue

        if fixed_holiday_name(
            current_date
        ):
            return True

        if extract_holiday_name_from_note(
            row["note"]
        ):
            return True

        if is_spring_festival_note(
            row["note"]
        ):
            return True

        if is_makeup_holiday_note(
            row["note"]
        ):
            return True

    return False


# ============================================================
# 官方事件名稱
# ============================================================

def get_official_event_name(
    rows,
    row,
    run_start,
    run_end,
):

    current_date = row["date"]
    note = row["note"]

    # --------------------------------------------------------
    # 春節
    # --------------------------------------------------------

    spring_name = get_spring_name(
        rows,
        current_date,
        note,
    )

    if spring_name:
        return spring_name

    # --------------------------------------------------------
    # 備註明確補假
    # --------------------------------------------------------

    if is_makeup_holiday_note(
        note
    ):

        name = extract_holiday_name_from_note(
            note
        )

        if name:
            return f"{name}(補假)"

        # 2027/12/31
        if (
            current_date.month == 12
            and current_date.day == 31
        ):
            return "開國紀念日(補假)"

        # 若備註只有「補假」
        # 從區間內尋找正式節日
        for candidate in rows:

            if not (
                run_start
                <= candidate["date"]
                <= run_end
            ):
                continue

            candidate_name = (
                extract_holiday_name_from_note(
                    candidate["note"]
                )
            )

            if candidate_name:
                return (
                    f"{candidate_name}(補假)"
                )

            fixed_name = fixed_holiday_name(
                candidate["date"]
            )

            if fixed_name:
                return (
                    f"{fixed_name}(補假)"
                )

        return "補假"

    # --------------------------------------------------------
    # 備註明確節日
    # --------------------------------------------------------

    note_name = extract_holiday_name_from_note(
        note
    )

    if note_name:
        return note_name

    # --------------------------------------------------------
    # 固定日期
    # --------------------------------------------------------

    fixed_name = fixed_holiday_name(
        current_date
    )

    if fixed_name:
        return fixed_name

    # --------------------------------------------------------
    # 如果是官方連假區間內的週末
    # 找該區間的節日名稱
    # --------------------------------------------------------

    for candidate in rows:

        if not (
            run_start
            <= candidate["date"]
            <= run_end
        ):
            continue

        candidate_name = (
            extract_holiday_name_from_note(
                candidate["note"]
            )
        )

        if candidate_name:
            return candidate_name

        fixed_name = fixed_holiday_name(
            candidate["date"]
        )

        if fixed_name:
            return fixed_name

    return "政府放假日"


# ============================================================
# 補班
# ============================================================

def find_makeup_workday_name(
    rows,
    workday_row,
):

    note = workday_row["note"]

    name = extract_holiday_name_from_note(
        note
    )

    if name:
        return name

    current_date = workday_row["date"]

    if (
        current_date.month == 12
        and current_date.day == 31
    ):
        return "開國紀念日"

    candidates = []

    for row in rows:

        if row is workday_row:
            continue

        distance = abs(
            (
                row["date"]
                - current_date
            ).days
        )

        if distance > 14:
            continue

        candidate_name = (
            extract_holiday_name_from_note(
                row["note"]
            )
        )

        if not candidate_name:
            candidate_name = fixed_holiday_name(
                row["date"]
            )

        if candidate_name:
            candidates.append(
                (
                    distance,
                    candidate_name,
                )
            )

    if candidates:

        candidates.sort(
            key=lambda item: item[0]
        )

        return candidates[0][1]

    return "政府假日"


# ============================================================
# 政府事件
# ============================================================

def build_government_events():

    events = []

    for year in YEARS:

        rows = read_dgpa_csv(
            year
        )

        holiday_runs = get_holiday_runs(
            rows
        )

        official_runs = []

        for start, end in holiday_runs:

            if run_has_official_anchor(
                rows,
                start,
                end,
            ):
                official_runs.append(
                    (
                        start,
                        end,
                    )
                )

        # ----------------------------------------------------
        # 官方放假
        # ----------------------------------------------------

        for start, end in official_runs:

            for row in rows:

                if not row["holiday"]:
                    continue

                current_date = row["date"]

                if not (
                    start
                    <= current_date
                    <= end
                ):
                    continue

                summary = get_official_event_name(
                    rows,
                    row,
                    start,
                    end,
                )

                events.append(
                    {
                        "date": current_date,
                        "summary": summary,
                        "source": "DGPA",
                    }
                )

        # ----------------------------------------------------
        # 補班
        # ----------------------------------------------------

        for row in rows:

            if not row["workday"]:
                continue

            if not is_makeup_workday_note(
                row["note"]
            ):
                continue

            name = find_makeup_workday_name(
                rows,
                row,
            )

            events.append(
                {
                    "date": row["date"],
                    "summary": f"[補班]{name}",
                    "source": "DGPA",
                }
            )

    return events


# ============================================================
# 母親節 / 父親節
# ============================================================

def second_sunday_of_may(year):

    current = date(
        year,
        5,
        1,
    )

    count = 0

    while True:

        if current.weekday() == 6:

            count += 1

            if count == 2:
                return current

        current += timedelta(days=1)


def build_family_events():

    events = []

    for year in YEARS:

        events.append(
            {
                "date": second_sunday_of_may(
                    year
                ),
                "summary": "母親節",
                "source": "家庭節日",
            }
        )

        events.append(
            {
                "date": date(
                    year,
                    8,
                    8,
                ),
                "summary": "父親節",
                "source": "家庭節日",
            }
        )

    return events


# ============================================================
# 高雄市停班停課
# ============================================================

def recursive_records(value):

    if isinstance(value, dict):

        yield value

        for child in value.values():
            yield from recursive_records(
                child
            )

    elif isinstance(value, list):

        for item in value:
            yield from recursive_records(
                item
            )


def find_value_by_keys(
    record,
    keywords,
):

    for key, value in record.items():

        normalized_key = normalize_text(
            key
        )

        for keyword in keywords:

            if normalize_text(
                keyword
            ) in normalized_key:

                return value

    return None


def parse_api_date(value):

    if value is None:
        return None

    value = clean_text(
        value
    )

    parsed = parse_date(
        value
    )

    if parsed:
        return parsed

    match = re.search(
        r"(\d{4})(\d{2})(\d{2})",
        value,
    )

    if match:

        try:
            return date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )

        except ValueError:
            return None

    return None


def build_khh_events():

    events = []

    try:

        response = http_get(
            KAOHSIUNG_API,
            timeout=15,
        )

        data = response.json()

    except Exception as exc:

        print(
            f"高雄停班停課 API 讀取失敗：{exc}"
        )

        return events

    seen = set()

    for record in recursive_records(
        data
    ):

        if not isinstance(
            record,
            dict,
        ):
            continue

        raw_date = find_value_by_keys(
            record,
            [
                "日期",
                "date",
                "發布日期",
                "公告日期",
                "停班停課日期",
            ],
        )

        current_date = parse_api_date(
            raw_date
        )

        if not current_date:
            continue

        if current_date.year not in YEARS:
            continue

        combined_text = " ".join(
            clean_text(value)
            for value in record.values()
            if isinstance(
                value,
                (
                    str,
                    int,
                    float,
                ),
            )
        )

        normalized = normalize_text(
            combined_text
        )

        if not any(
            keyword in normalized
            for keyword in [
                "停止上班",
                "停止上課",
                "停班",
                "停課",
                "災害停止辦公及上課",
            ]
        ):
            continue

        key = (
            current_date,
            "高雄市停班停課",
        )

        if key in seen:
            continue

        seen.add(key)

        events.append(
            {
                "date": current_date,
                "summary": "高雄市停班停課",
                "source": "高雄市政府",
            }
        )

    return events


# ============================================================
# 去重
# ============================================================

def deduplicate_events(events):

    result = []
    seen = set()

    for event in events:

        key = (
            event["date"],
            event["summary"],
        )

        if key in seen:
            continue

        seen.add(key)

        result.append(
            event
        )

    result.sort(
        key=lambda item: (
            item["date"],
            item["summary"],
        )
    )

    return result


# ============================================================
# ICS
# ============================================================

def ics_escape(value):

    value = str(
        value
    )

    value = value.replace(
        "\\",
        "\\\\",
    )

    value = value.replace(
        ";",
        "\\;",
    )

    value = value.replace(
        ",",
        "\\,",
    )

    value = value.replace(
        "\r\n",
        "\\n",
    )

    value = value.replace(
        "\n",
        "\\n",
    )

    return value


def fold_ics_line(line):

    encoded = line.encode(
        "utf-8"
    )

    if len(encoded) <= 75:
        return [line]

    result = []

    current = bytearray()

    for char in line:

        char_bytes = char.encode(
            "utf-8"
        )

        if (
            len(current)
            + len(char_bytes)
            > 75
        ):

            result.append(
                current.decode(
                    "utf-8"
                )
            )

            current = bytearray()

        current.extend(
            char_bytes
        )

    if current:
        result.append(
            current.decode(
                "utf-8"
            )
        )

    return (
        [result[0]]
        + [
            " " + part
            for part in result[1:]
        ]
    )


def make_uid(event):

    seed = (
        f"{event['date'].isoformat()}|"
        f"{event['summary']}"
    )

    return (
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                seed,
            )
        )
        + "@taiwan-calendar"
    )


def build_ics(events):

    now_utc = datetime.now(
        timezone.utc
    ).strftime(
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

        current_date = event["date"]

        date_text = current_date.strftime(
            "%Y%m%d"
        )

        next_date = (
            current_date
            + timedelta(days=1)
        )

        next_date_text = next_date.strftime(
            "%Y%m%d"
        )

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{make_uid(event)}",
                f"DTSTAMP:{now_utc}",
                f"DTSTART;VALUE=DATE:{date_text}",
                f"DTEND;VALUE=DATE:{next_date_text}",
                f"SUMMARY:{ics_escape(event['summary'])}",
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
        )

    lines.append(
        "END:VCALENDAR"
    )

    folded_lines = []

    for line in lines:
        folded_lines.extend(
            fold_ics_line(line)
        )

    return (
        "\r\n".join(
            folded_lines
        )
        + "\r\n"
    )


# ============================================================
# 統計
# ============================================================

def print_statistics(events):

    government = [
        event
        for event in events
        if event["source"] == "DGPA"
    ]

    family = [
        event
        for event in events
        if event["source"] == "家庭節日"
    ]

    khh = [
        event
        for event in events
        if event["source"] == "高雄市政府"
    ]

    makeup_holiday = [
        event
        for event in government
        if "(補假)" in event["summary"]
    ]

    makeup_workday = [
        event
        for event in government
        if event["summary"].startswith(
            "[補班]"
        )
    ]

    normal_government = [
        event
        for event in government
        if "(補假)" not in event["summary"]
        and not event["summary"].startswith(
            "[補班]"
        )
    ]

    print("")
    print("=" * 60)
    print("台灣生活行事曆 統計")
    print("=" * 60)

    print(
        f"政府一般放假事件："
        f"{len(normal_government)}"
    )

    print(
        f"政府補假事件："
        f"{len(makeup_holiday)}"
    )

    print(
        f"政府補班事件："
        f"{len(makeup_workday)}"
    )

    print(
        f"母親節："
        f"{sum(1 for e in family if e['summary'] == '母親節')}"
    )

    print(
        f"父親節："
        f"{sum(1 for e in family if e['summary'] == '父親節')}"
    )

    print(
        f"高雄市停班停課："
        f"{len(khh)}"
    )

    print(
        f"全部事件："
        f"{len(events)}"
    )

    print("=" * 60)

    for year in YEARS:

        year_events = [
            event
            for event in events
            if event["date"].year == year
        ]

        print(
            f"{year}："
            f"{len(year_events)} 筆"
        )

    print("=" * 60)
    print("")


# ============================================================
# 主程式
# ============================================================

def main():

    print("")
    print("=" * 60)
    print("開始產生台灣生活行事曆")
    print("=" * 60)

    print(
        f"目前年份：{CURRENT_YEAR}"
    )

    print(
        f"產生年份：{YEARS}"
    )

    government_events = (
        build_government_events()
    )

    print(
        f"政府事件："
        f"{len(government_events)}"
    )

    family_events = (
        build_family_events()
    )

    print(
        f"家庭節日："
        f"{len(family_events)}"
    )

    khh_events = (
        build_khh_events()
    )

    print(
        f"高雄停班停課："
        f"{len(khh_events)}"
    )

    events = (
        government_events
        + family_events
        + khh_events
    )

    events = deduplicate_events(
        events
    )

    print_statistics(
        events
    )

    ics = build_ics(
        events
    )

    output_file = "taiwan.ics"

    with open(
        output_file,
        "w",
        encoding="utf-8",
        newline="",
    ) as file:

        file.write(
            ics
        )

    print(
        f"已產生：{output_file}"
    )

    print(
        f"事件總數：{len(events)}"
    )

    print("")
    print("完成。")
    print("=" * 60)


if __name__ == "__main__":
    main()
