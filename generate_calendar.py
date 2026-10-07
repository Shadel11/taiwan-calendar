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

# 目前年份 + 下一年
YEARS = [CURRENT_YEAR, CURRENT_YEAR + 1]


# ============================================================
# HTTP 設定
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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# 工具：HTTP GET
# ============================================================

def http_get(url, timeout=30):
    """
    取得網頁 / API / CSV。
    關閉 VERIFY_X509_STRICT，避免 GitHub Actions 某些憑證鏈問題。
    """
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
# 工具：文字清理
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\ufeff", "")
    value = value.replace("\xa0", " ")

    return value.strip()


def normalize_text(value):
    value = clean_text(value)

    value = value.replace("臺", "台")
    value = value.replace("　", " ")
    value = re.sub(r"\s+", "", value)

    return value


# ============================================================
# 工具：民國日期 / 西元日期
# ============================================================

def parse_date(value):
    """
    嘗試解析：
    2026/01/01
    2026-01-01
    115/01/01
    115-01-01
    115.01.01
    """
    value = clean_text(value)

    if not value:
        return None

    value = value.replace(".", "/")
    value = value.replace("-", "/")

    # 移除時間
    value = value.split(" ")[0]

    match = re.match(r"^(\d{3,4})/(\d{1,2})/(\d{1,2})$", value)

    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))

    # 民國年
    if year < 1911:
        year += 1911

    try:
        return date(year, month, day)
    except ValueError:
        return None


# ============================================================
# 工具：判斷假日 / 上班日
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
        "TRUE/YES",
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
        "FALSE/NO",
    }


# ============================================================
# 找 DGPA CSV
# ============================================================

def get_resource_links():
    """
    從 data.gov.tw dataset 頁面找 CSV 資源。
    """

    response = http_get(DGPA_DATASET)
    html = response.text

    links = re.findall(
        r'href=["\']([^"\']+\.csv[^"\']*)["\']',
        html,
        flags=re.IGNORECASE,
    )

    results = []

    for link in links:
        link = unquote(link)
        full_url = urljoin(DGPA_DATASET, link)

        if full_url not in results:
            results.append(full_url)

    return results


def find_csv_url(year):
    """
    找最符合指定年份的 DGPA 辦公日曆 CSV。
    """

    links = get_resource_links()

    if not links:
        raise RuntimeError(
            "找不到 DGPA CSV 資源，請確認 data.gov.tw dataset 14718。"
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

        if "csv" in text.lower():
            score += 10

        candidates.append((score, url))

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]


# ============================================================
# 讀 DGPA CSV
# ============================================================

def decode_csv(content):
    """
    嘗試多種常見編碼。
    """

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

    return content.decode(
        "utf-8",
        errors="replace",
    )


def detect_column(fieldnames, keywords):
    """
    從欄位名稱中尋找符合關鍵字的欄位。
    """

    normalized = []

    for field in fieldnames:
        normalized.append(
            (
                field,
                normalize_text(field),
            )
        )

    for keyword in keywords:
        keyword = normalize_text(keyword)

        for original, normalized_name in normalized:
            if keyword in normalized_name:
                return original

    return None


def read_dgpa_csv(year):
    """
    讀取指定年份政府辦公日曆資料。

    回傳：
    [
        {
            "date": date(...),
            "holiday": True/False,
            "note": "...",
            ...
        }
    ]
    """

    csv_url = find_csv_url(year)

    print(f"讀取 {year} 政府辦公日曆：{csv_url}")

    response = http_get(csv_url)

    text = decode_csv(response.content)

    reader = csv.DictReader(
        io.StringIO(text)
    )

    fieldnames = reader.fieldnames or []

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
            f"{year} CSV 找不到日期欄位。"
        )

    if not holiday_field:
        raise RuntimeError(
            f"{year} CSV 找不到是否放假欄位。"
        )

    rows = []

    for row in reader:
        raw_date = row.get(date_field)

        current_date = parse_date(raw_date)

        if not current_date:
            continue

        if current_date.year != year:
            continue

        raw_holiday = row.get(holiday_field, "")

        holiday = is_holiday_value(
            raw_holiday
        )

        workday = is_workday_value(
            raw_holiday
        )

        note = ""

        if note_field:
            note = clean_text(
                row.get(note_field, "")
            )

        rows.append(
            {
                "date": current_date,
                "holiday": holiday,
                "workday": workday,
                "note": note,
                "raw": row,
            }
        )

    rows.sort(
        key=lambda item: item["date"]
    )

    return rows


# ============================================================
# 農曆工具
# ============================================================

def lunar_day_label(solar_date):
    """
    回傳：
    (農曆月份, 農曆日期)

    LunarDate 套件若不可用則回傳 None。
    """

    if LunarDate is None:
        return None, None

    try:
        lunar = LunarDate.fromSolarDate(
            solar_date.year,
            solar_date.month,
            solar_date.day,
        )

        return lunar.month, lunar.day

    except Exception:
        return None, None


def lunar_special_name(solar_date):
    """
    判斷特定農曆日期。

    注意：
    這裡只負責「真正的農曆日期」，
    不會把附近日期誤認成節日。
    """

    lunar_month, lunar_day = lunar_day_label(
        solar_date
    )

    if lunar_month is None:
        return None

    # 農曆正月
    if lunar_month == 1:
        if lunar_day == 1:
            return "初一"

        if lunar_day == 2:
            return "初二"

        if lunar_day == 3:
            return "初三"

    return None


# ============================================================
# 節日固定日期
# ============================================================

def fixed_holiday_name(current_date):
    """
    固定日期節日。
    """

    month_day = (
        current_date.month,
        current_date.day,
    )

    fixed = {
        (1, 1): "元旦",
        (2, 28): "和平紀念日",
        (5, 1): "勞動節",
        (10, 10): "國慶日",
        (10, 25): "臺灣光復暨金門古寧頭大捷紀念日",
        (12, 25): "行憲紀念日",
    }

    return fixed.get(month_day)


# ============================================================
# 備註判斷
# ============================================================

def is_spring_festival_note(note):
    text = normalize_text(note)

    return any(
        keyword in text
        for keyword in [
            "春節",
            "農曆春節",
            "除夕",
        ]
    )


def is_makeup_holiday_note(note):
    text = normalize_text(note)

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
    text = normalize_text(note)

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
# 從備註取得節日名稱
# ============================================================

def extract_holiday_name_from_note(note):
    """
    從 DGPA 備註找節日名稱。

    注意：
    不直接把「除夕及春節」整段期間全部標成除夕。
    春節特殊日期會另外透過農曆日期處理。
    """

    text = normalize_text(note)

    if not text:
        return None

    # 最優先：明確節日名稱
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
                "教師節",
                "孔子誕辰紀念日",
            ],
            "教師節",
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
            ],
            "元旦",
        ),
    ]

    for keywords, name in mappings:
        for keyword in keywords:
            if keyword in text:
                return name

    return None


# ============================================================
# 判斷是否為春節官方區間
# ============================================================

def is_spring_festival_period(rows, current_date):
    """
    判斷目前日期是否處於政府公告的春節連假區間。

    不靠「附近日期」猜測。
    必須先找到連續的政府放假區間。
    """

    holiday_dates = {
        row["date"]
        for row in rows
        if row["holiday"]
    }

    if current_date not in holiday_dates:
        return False

    # 找目前連續放假區間的開始與結束
    start = current_date
    end = current_date

    while start - timedelta(days=1) in holiday_dates:
        start -= timedelta(days=1)

    while end + timedelta(days=1) in holiday_dates:
        end += timedelta(days=1)

    length = (
        end - start
    ).days + 1

    # 春節連假通常是最長的一段之一
    if length >= 5:
        for row in rows:
            if start <= row["date"] <= end:
                if is_spring_festival_note(
                    row["note"]
                ):
                    return True

    return False


# ============================================================
# 春節事件名稱
# ============================================================

def get_spring_festival_event_name(
    rows,
    current_date,
    note,
):
    """
    春節名稱判斷：

    真正農曆日期：
        小年夜
        除夕
        初一
        初二
        初三

    補假：
        春節(補假)

    其他官方春節連假：
        春節
    """

    if not is_spring_festival_period(
        rows,
        current_date,
    ):
        return None

    lunar_month, lunar_day = lunar_day_label(
        current_date
    )

    # 農曆正月初一～初三
    if lunar_month == 1:
        if lunar_day == 1:
            return "初一"

        if lunar_day == 2:
            return "初二"

        if lunar_day == 3:
            return "初三"

    # 農曆除夕
    if lunar_month == 12:
        tomorrow = current_date + timedelta(days=1)

        next_month, next_day = lunar_day_label(
            tomorrow
        )

        if next_month == 1 and next_day == 1:
            return "除夕"

    # 農曆小年夜
    tomorrow = current_date + timedelta(days=1)

    tomorrow_month, tomorrow_day = lunar_day_label(
        tomorrow
    )

    if (
        tomorrow_month == 12
        and tomorrow_day in (29, 30)
    ):
        return "小年夜"

    # 補假
    if is_makeup_holiday_note(note):
        return "春節(補假)"

    # 其餘官方春節連假
    return "春節"


# ============================================================
# 取得官方連假區間
# ============================================================

def get_holiday_runs(rows):
    """
    將連續「政府放假」日期切成區段。

    重要：
    普通週末雖然也是 holiday=True，
    但如果沒有任何正式節日 anchor，
    就不會被加入行事曆。
    """

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
        if current == previous + timedelta(days=1):
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


# ============================================================
# 判斷連假區間是否為正式節日
# ============================================================

def run_has_official_anchor(
    rows,
    start,
    end,
):
    """
    判斷連續假日區間是否有正式節日 anchor。

    anchor 包括：
    - 固定國定假日
    - 備註明確寫出節日
    - 春節
    - 補假
    """

    for row in rows:
        current_date = row["date"]

        if not (
            start
            <= current_date
            <= end
        ):
            continue

        fixed_name = fixed_holiday_name(
            current_date
        )

        if fixed_name:
            return True

        note_name = extract_holiday_name_from_note(
            row["note"]
        )

        if note_name:
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
# 取得官方節日名稱
# ============================================================

def get_official_event_name(
    rows,
    row,
    run_start,
    run_end,
):
    """
    決定官方事件名稱。
    """

    current_date = row["date"]
    note = row["note"]

    # --------------------------------------------------------
    # 春節優先處理
    # --------------------------------------------------------

    spring_name = get_spring_festival_event_name(
        rows,
        current_date,
        note,
    )

    if spring_name:
        return spring_name

    # --------------------------------------------------------
    # 補假
    # --------------------------------------------------------

    if is_makeup_holiday_note(note):
        base_name = extract_holiday_name_from_note(
            note
        )

        if base_name:
            return f"{base_name}(補假)"

        fixed_name = fixed_holiday_name(
            current_date
        )

        if fixed_name:
            return f"{fixed_name}(補假)"

        # 2027/12/31 是 2028 元旦補假
        if (
            current_date.month == 12
            and current_date.day == 31
        ):
            return "元旦(補假)"

        return "補假"

    # --------------------------------------------------------
    # 備註中的正式節日
    # --------------------------------------------------------

    note_name = extract_holiday_name_from_note(
        note
    )

    if note_name:
        return note_name

    # --------------------------------------------------------
    # 固定日期節日
    # --------------------------------------------------------

    fixed_name = fixed_holiday_name(
        current_date
    )

    if fixed_name:
        return fixed_name

    # --------------------------------------------------------
    # 春節期間 fallback
    # --------------------------------------------------------

    if is_spring_festival_note(note):
        return "春節"

    return "政府放假日"


# ============================================================
# 找補班對應的節日名稱
# ============================================================

def find_makeup_workday_name(
    rows,
    workday_row,
):
    """
    判斷 [補班] 後面應該是哪個節日。

    優先從備註找，
    找不到再往前後找最近的正式節日。
    """

    note = normalize_text(
        workday_row["note"]
    )

    # --------------------------------------------------------
    # 直接從補班日期的備註找
    # --------------------------------------------------------

    name = extract_holiday_name_from_note(
        note
    )

    if name:
        return name

    # --------------------------------------------------------
    # 如果是 12/31，通常是隔年元旦補班 / 調整
    # --------------------------------------------------------

    current_date = workday_row["date"]

    if (
        current_date.month == 12
        and current_date.day == 31
    ):
        return "元旦"

    # --------------------------------------------------------
    # 往前後 14 天找正式節日
    # --------------------------------------------------------

    candidates = []

    for row in rows:
        if row is workday_row:
            continue

        other_date = row["date"]

        distance = abs(
            (
                other_date - current_date
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
                other_date
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
# 建立政府事件
# ============================================================

def build_government_events():
    """
    建立官方政府假日 + 補班事件。

    這裡是整份程式最重要的修正：
    不再把普通週末全部加入。
    """

    events = []

    for year in YEARS:
        rows = read_dgpa_csv(year)

        # ----------------------------------------------------
        # 先建立正式連續假日區間
        # ----------------------------------------------------

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
        # 加入官方放假事件
        # ----------------------------------------------------

        for start, end in official_runs:

            for row in rows:

                current_date = row["date"]

                if not (
                    start
                    <= current_date
                    <= end
                ):
                    continue

                if not row["holiday"]:
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
        # 加入官方補班
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

        # ----------------------------------------------------
        # 特殊處理：12/31 元旦補假
        # ----------------------------------------------------

        for row in rows:

            current_date = row["date"]

            if (
                current_date.month == 12
                and current_date.day == 31
                and row["holiday"]
            ):
                already_exists = any(
                    event["date"] == current_date
                    and "元旦" in event["summary"]
                    for event in events
                )

                if not already_exists:
                    events.append(
                        {
                            "date": current_date,
                            "summary": "元旦(補假)",
                            "source": "DGPA",
                        }
                    )

    return events


# ============================================================
# 母親節 / 父親節
# ============================================================

def second_sunday_of_may(year):
    """
    取得該年度五月第二個星期日。
    """

    current = date(
        year,
        5,
        1,
    )

    sunday_count = 0

    while True:

        if current.weekday() == 6:
            sunday_count += 1

            if sunday_count == 2:
                return current

        current += timedelta(days=1)


def build_family_events():
    """
    母親節 / 父親節。
    """

    events = []

    for year in YEARS:

        mother_day = second_sunday_of_may(
            year
        )

        events.append(
            {
                "date": mother_day,
                "summary": "母親節",
                "source": "家庭節日",
            }
        )

        father_day = date(
            year,
            8,
            8,
        )

        events.append(
            {
                "date": father_day,
                "summary": "父親節",
                "source": "家庭節日",
            }
        )

    return events


# ============================================================
# 高雄市停班停課 API
# ============================================================

def recursive_records(value):
    """
    遞迴尋找 API JSON 中的 dict。
    """

    if isinstance(value, dict):
        yield value

        for child in value.values():
            yield from recursive_records(child)

    elif isinstance(value, list):
        for item in value:
            yield from recursive_records(item)


def find_value_by_keys(record, keywords):
    """
    從 dict 中找可能的欄位。
    """

    for key, value in record.items():

        normalized_key = normalize_text(key)

        for keyword in keywords:
            if normalize_text(keyword) in normalized_key:
                return value

    return None


def parse_api_date(value):
    if value is None:
        return None

    value = clean_text(value)

    # 先嘗試完整日期
    parsed = parse_date(value)

    if parsed:
        return parsed

    # YYYYMMDD
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
    """
    取得高雄市停班停課公告。

    只保留 YEARS 範圍內資料，
    避免 API 歷史資料污染行事曆。
    """

    events = []

    try:
        response = http_get(
            KAOHSIUNG_API,
            timeout=30,
        )

        data = response.json()

    except Exception as exc:
        print(
            f"高雄停班停課 API 讀取失敗：{exc}"
        )
        return events

    seen = set()

    for record in recursive_records(data):

        if not isinstance(record, dict):
            continue

        # ----------------------------------------------------
        # 日期
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 找停班 / 停課內容
        # ----------------------------------------------------

        combined_text = " ".join(
            clean_text(value)
            for value in record.values()
            if isinstance(
                value,
                (str, int, float),
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

        # ----------------------------------------------------
        # 避免同一天重複
        # ----------------------------------------------------

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
# 去除重複事件
# ============================================================

def deduplicate_events(events):
    """
    同一天 + 同標題只保留一筆。
    """

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
        result.append(event)

    result.sort(
        key=lambda item: (
            item["date"],
            item["summary"],
        )
    )

    return result


# ============================================================
# ICS Escape
# ============================================================

def ics_escape(value):
    """
    RFC 5545 基本文字 escaping。
    """

    value = str(value)

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


# ============================================================
# ICS Line Folding
# ============================================================

def fold_ics_line(line):
    """
    將長行折疊成 RFC 5545 可接受格式。

    以 UTF-8 bytes 計算，
    每段不超過 75 bytes。
    """

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

    # RFC continuation line 必須前面加空白
    return [
        result[0]
    ] + [
        " " + part
        for part in result[1:]
    ]


# ============================================================
# UUID
# ============================================================

def make_uid(event):
    """
    使用固定 UUID5，
    讓同一事件每次產生相同 UID。
    """

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


# ============================================================
# ICS 建立
# ============================================================

def build_ics(events):
    """
    建立完整 ICS。
    """

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

        uid = make_uid(event)

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
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
    """
    印出事件統計，
    方便 GitHub Actions 查看。
    """

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

    mother = [
        event
        for event in family
        if event["summary"] == "母親節"
    ]

    father = [
        event
        for event in family
        if event["summary"] == "父親節"
    ]

    print("")
    print("=" * 60)
    print("台灣生活行事曆 統計")
    print("=" * 60)

    print(
        f"政府一般放假事件：{len(normal_government)}"
    )

    print(
        f"政府補假事件：{len(makeup_holiday)}"
    )

    print(
        f"政府補班事件：{len(makeup_workday)}"
    )

    print(
        f"母親節：{len(mother)}"
    )

    print(
        f"父親節：{len(father)}"
    )

    print(
        f"高雄市停班停課：{len(khh)}"
    )

    print(
        f"全部事件：{len(events)}"
    )

    print("=" * 60)

    # --------------------------------------------------------
    # 各年份統計
    # --------------------------------------------------------

    for year in YEARS:

        year_events = [
            event
            for event in events
            if event["date"].year == year
        ]

        print(
            f"{year}：{len(year_events)} 筆"
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

    # --------------------------------------------------------
    # 政府假日
    # --------------------------------------------------------

    government_events = (
        build_government_events()
    )

    print(
        f"政府事件：{len(government_events)}"
    )

    # --------------------------------------------------------
    # 母親節 / 父親節
    # --------------------------------------------------------

    family_events = (
        build_family_events()
    )

    print(
        f"家庭節日：{len(family_events)}"
    )

    # --------------------------------------------------------
    # 高雄市停班停課
    # --------------------------------------------------------

    khh_events = (
        build_khh_events()
    )

    print(
        f"高雄停班停課：{len(khh_events)}"
    )

    # --------------------------------------------------------
    # 合併
    # --------------------------------------------------------

    events = (
        government_events
        + family_events
        + khh_events
    )

    events = deduplicate_events(
        events
    )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    print_statistics(
        events
    )

    # --------------------------------------------------------
    # 建立 ICS
    # --------------------------------------------------------

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
    print(
        "完成。"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
