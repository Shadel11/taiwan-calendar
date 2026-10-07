import csv
import html
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote

import requests
from lunarcalendar import LunarDate


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
    # 月/日: 名稱
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
#
# 目前專案目標為 2026、2027。
# 兩年清明節皆為 4/5。
#
# 未來若 TARGET_YEARS 擴充其他年份，
# 可再補進來。
# ============================================================

QINGMING_DATES = {
    2026: date(2026, 4, 5),
    2027: date(2027, 4, 5),
}


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# 工具：HTTP GET
# ============================================================

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

    raise RuntimeError(
        f"下載失敗：{url}\n原因：{last_error}"
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

    # 2026-01-01
    match = re.match(
        r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$",
        text,
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

    # 115/01/01、115-01-01
    match = re.match(
        r"^(\d{2,3})[-/](\d{1,2})[-/](\d{1,2})$",
        text,
    )

    if match:
        year = int(match.group(1))

        if year < 1911:
            year += 1911

        try:
            return date(
                year,
                int(match.group(2)),
                int(match.group(3)),
            )
        except ValueError:
            return None

    # 20260101
    match = re.match(
        r"^(\d{4})(\d{2})(\d{2})$",
        text,
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


# ============================================================
# 民國年
# ============================================================

def roc_year(year):
    return year - 1911


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

    return content.decode(
        "utf-8",
        errors="replace",
    )


# ============================================================
# 清理文字
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\ufeff", "")
    text = text.replace("\xa0", " ")

    return text.strip()


# ============================================================
# 找欄位
# ============================================================

def find_column(fieldnames, candidates):
    if not fieldnames:
        return None

    normalized = {}

    for field in fieldnames:
        key = clean_text(field)

        normalized[key] = field

    # 先完全相等
    for candidate in candidates:
        candidate = clean_text(candidate)

        if candidate in normalized:
            return normalized[candidate]

    # 再模糊比對
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

    response = http_get(
        DGPA_DATASET_URL,
        timeout=30,
    )

    page = response.text

    page = html.unescape(page)

    # 找所有 DGPA FileConversion CSV
    patterns = [
        r'https?://www\.dgpa\.gov\.tw/FileConversion\?[^"\']+',
        r'//www\.dgpa\.gov\.tw/FileConversion\?[^"\']+',
        r'/FileConversion\?[^"\']+',
    ]

    urls = []

    for pattern in patterns:
        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE,
        )

        for url in matches:
            url = html.unescape(url)
            url = unquote(url)

            if url.startswith("//"):
                url = "https:" + url

            elif url.startswith("/"):
                url = (
                    "https://www.dgpa.gov.tw"
                    + url
                )

            if (
                "FileConversion" in url
                and ".csv" in url.lower()
            ):
                urls.append(url)

    # 去重
    unique_urls = []

    for url in urls:
        if url not in unique_urls:
            unique_urls.append(url)

    # 優先找「該年度、非 Google 行事曆」
    target_patterns = [
        f"{roc}%E5%B9%B4",
        f"{roc}年",
        f"{year}",
    ]

    candidates = []

    for url in unique_urls:
        decoded = unquote(url)

        if "Google" in decoded:
            continue

        if any(
            pattern in decoded
            for pattern in target_patterns
        ):
            candidates.append(url)

    if candidates:
        return candidates[-1]

    # 再嘗試直接依 URL 裡的民國年搜尋
    for url in unique_urls:
        decoded = unquote(url)

        if (
            str(roc) in decoded
            and "Google" not in decoded
        ):
            return url

    raise RuntimeError(
        f"找不到 {year} 年 DGPA CSV。\n"
        f"請確認 data.gov.tw 資料集是否已有 {roc} 年資料。"
    )


# ============================================================
# 下載 DGPA CSV
# ============================================================

def download_dgpa_csv(year):
    url = get_dgpa_csv_url(year)

    print(f"📥 {year} DGPA CSV：")
    print(url)

    response = http_get(
        url,
        timeout=60,
    )

    return decode_csv_content(
        response.content
    )


# ============================================================
# 讀取 DGPA CSV
# ============================================================

def read_dgpa_rows(year):
    text = download_dgpa_csv(year)

    # 去掉 BOM
    text = text.lstrip("\ufeff")

    # 嘗試一般 CSV
    try:
        reader = csv.DictReader(
            io.StringIO(text)
        )

        rows = list(reader)

        if rows and reader.fieldnames:
            return rows

    except Exception:
        pass

    # 如果格式異常，嘗試 Sniffer
    try:
        sample = text[:4096]

        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=",;\t",
        )

        reader = csv.DictReader(
            io.StringIO(text),
            dialect=dialect,
        )

        rows = list(reader)

        if rows:
            return rows

    except Exception:
        pass

    raise RuntimeError(
        f"{year} DGPA CSV 無法解析。"
    )


# ============================================================
# 判斷是否放假
# ============================================================

def is_holiday_value(value):
    text = clean_text(value)

    if text in ("2", "２"):
        return True

    if text.lower() in (
        "true",
        "yes",
        "y",
        "holiday",
    ):
        return True

    if "放假" in text:
        return True

    return False


# ============================================================
# 找 DGPA 欄位
# ============================================================

def get_dgpa_columns(rows):
    if not rows:
        raise RuntimeError(
            "DGPA CSV 沒有資料。"
        )

    fieldnames = list(rows[0].keys())

    date_column = find_column(
        fieldnames,
        [
            "西元日期",
            "日期",
            "date",
        ],
    )

    holiday_column = find_column(
        fieldnames,
        [
            "是否放假",
            "放假",
            "isHoliday",
        ],
    )

    note_column = find_column(
        fieldnames,
        [
            "備註",
            "備註說明",
            "節日",
            "名稱",
            "note",
        ],
    )

    if not date_column:
        raise RuntimeError(
            "DGPA CSV 找不到「西元日期」欄位。"
        )

    if not holiday_column:
        raise RuntimeError(
            "DGPA CSV 找不到「是否放假」欄位。"
        )

    return (
        date_column,
        holiday_column,
        note_column,
    )


# ============================================================
# 農曆日期
# ============================================================

def get_lunar_date(gregorian_date):
    try:
        lunar = LunarDate.fromSolarDate(
            gregorian_date.year,
            gregorian_date.month,
            gregorian_date.day,
        )

        return lunar

    except Exception:
        return None


# ============================================================
# 農曆日期名稱
# ============================================================

def lunar_day_name(lunar):
    if lunar is None:
        return None

    month = lunar.month
    day = lunar.day

    # 小年夜
    #
    # 目前兩年都是農曆 12 月 28 日
    if month == 12 and day == 28:
        return "小年夜"

    # 除夕
    #
    # 農曆最後一天可能是 29 或 30
    if month == 12 and day in (29, 30):
        return "除夕"

    if month == 1:
        names = {
            1: "初一",
            2: "初二",
            3: "初三",
            4: "初四",
            5: "初五",
            6: "初六",
            7: "初七",
            8: "初八",
            9: "初九",
            10: "初十",
            11: "十一",
            12: "十二",
            13: "十三",
            14: "十四",
            15: "十五",
        }

        return names.get(day)

    return None


# ============================================================
# 春節期間判斷
#
# 注意：
# 絕對不能再用「農曆 1/15 前全部都是春節」。
#
# 只允許：
# 農曆 12/28、12/29、12/30
# 農曆 1/1～1/6
#
# 而且最後還要通過 DGPA「是否放假」判斷。
# ============================================================

def is_lunar_new_year_window(lunar):
    if lunar is None:
        return False

    if lunar.month == 12 and lunar.day in (
        28,
        29,
        30,
    ):
        return True

    if lunar.month == 1 and 1 <= lunar.day <= 6:
        return True

    return False


# ============================================================
# 找節日名稱
#
# 這裡的核心原則：
#
# 1. 先看官方補假表
# 2. 再看固定國定假日
# 3. 再看清明節
# 4. 再看農曆春節
# 5. 再看端午
# 6. 再看中秋
#
# 絕對不使用寬鬆日期區間。
# ============================================================

def get_base_holiday_name(
    gregorian_date,
    year,
):
    date_key = gregorian_date.isoformat()

    # --------------------------------------------------------
    # 補假
    # --------------------------------------------------------

    makeup_map = MAKEUP_HOLIDAY_MAP.get(
        year,
        {},
    )

    if date_key in makeup_map:
        return makeup_map[date_key]

    # --------------------------------------------------------
    # 固定節日
    # --------------------------------------------------------

    fixed_name = FIXED_HOLIDAYS.get(
        (
            gregorian_date.month,
            gregorian_date.day,
        )
    )

    if fixed_name:
        return fixed_name

    # --------------------------------------------------------
    # 清明節
    # --------------------------------------------------------

    qingming = QINGMING_DATES.get(year)

    if (
        qingming is not None
        and gregorian_date == qingming
    ):
        return "清明節"

    # --------------------------------------------------------
    # 農曆
    # --------------------------------------------------------

    lunar = get_lunar_date(
        gregorian_date
    )

    if lunar is None:
        return None

    # 春節只允許官方短範圍
    if is_lunar_new_year_window(lunar):
        return lunar_day_name(lunar)

    # 端午節
    if (
        lunar.month == 5
        and lunar.day == 5
    ):
        return "端午節"

    # 中秋節
    if (
        lunar.month == 8
        and lunar.day == 15
    ):
        return "中秋節"

    return None


# ============================================================
# 官方資料中的備註清理
# ============================================================

def normalize_official_note(note):
    text = clean_text(note)

    if not text:
        return ""

    # 去掉一些常見標記
    text = text.replace(
        "放假",
        "",
    ).strip()

    return text


# ============================================================
# 判斷是否為「真正的節日事件」
#
# 普通週末：
#   是否放假=2
#   但備註空白
#
# 這種不能加入 ICS。
#
# 真正國定假日：
#   有節日名稱
#
# 補假：
#   使用 MAKEUP_HOLIDAY_MAP
# ============================================================

def should_create_government_event(
    gregorian_date,
    is_holiday,
    official_name,
):
    if not is_holiday:
        return False

    if official_name:
        return True

    return False


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

        (
            date_column,
            holiday_column,
            note_column,
        ) = get_dgpa_columns(rows)

        year_events = []

        for row in rows:
            gregorian_date = parse_date(
                row.get(date_column)
            )

            if gregorian_date is None:
                continue

            if gregorian_date.year != year:
                continue

            holiday_value = row.get(
                holiday_column
            )

            is_holiday = is_holiday_value(
                holiday_value
            )

            if not is_holiday:
                continue

            official_note = ""

            if note_column:
                official_note = normalize_official_note(
                    row.get(note_column)
                )

            # ------------------------------------------------
            # 先用我們已確認的官方補假表
            # ------------------------------------------------

            date_key = gregorian_date.isoformat()

            makeup_map = MAKEUP_HOLIDAY_MAP.get(
                year,
                {},
            )

            if date_key in makeup_map:
                name = makeup_map[date_key]

            else:
                # --------------------------------------------
                # 正常國定假日
                # --------------------------------------------

                name = get_base_holiday_name(
                    gregorian_date,
                    year,
                )

                # --------------------------------------------
                # 如果日期名稱沒有算出來，
                # 再嘗試使用官方備註。
                #
                # 但只有在備註不是單純「補假」時才使用。
                # --------------------------------------------

                if (
                    not name
                    and official_note
                    and "補假" not in official_note
                ):
                    name = official_note

            # ------------------------------------------------
            # 最重要的過濾：
            #
            # 普通週末即使 DGPA 的「是否放假=2」，
            # 只要沒有節日名稱，就不建立事件。
            # ------------------------------------------------

            if not should_create_government_event(
                gregorian_date,
                is_holiday,
                name,
            ):
                continue

            # ------------------------------------------------
            # 防止出現：
            # 補假(補假)
            # ------------------------------------------------

            if name == "補假":
                continue

            # ------------------------------------------------
            # 防止重複
            # ------------------------------------------------

            duplicate = False

            for existing in year_events:
                if (
                    existing["date"]
                    == gregorian_date
                    and existing["summary"]
                    == name
                ):
                    duplicate = True
                    break

            if duplicate:
                continue

            event = {
                "date": gregorian_date,
                "summary": name,
                "category": "政府假日",
                "description": (
                    f"{year}年政府行政機關辦公日曆表"
                ),
            }

            year_events.append(event)

        # ----------------------------------------------------
        # 排序
        # ----------------------------------------------------

        year_events.sort(
            key=lambda item: item["date"]
        )

        # ----------------------------------------------------
        # 印出檢查
        # ----------------------------------------------------

        print("")
        print(f"✅ {year} 年實際建立事件：")

        for event in year_events:
            print(
                f"  {event['date']} "
                f"{event['summary']}"
            )

        print(
            f"📌 {year} 年政府節日事件數："
            f"{len(year_events)}"
        )

        events.extend(year_events)

    return events


# ============================================================
# 母親節
# ============================================================

def get_second_sunday_of_may(year):
    d = date(year, 5, 1)

    # weekday:
    # Monday = 0
    # Sunday = 6
    days_until_sunday = (
        6 - d.weekday()
    ) % 7

    first_sunday = d + timedelta(
        days=days_until_sunday
    )

    return first_sunday + timedelta(
        days=7
    )


# ============================================================
# 父親節
# ============================================================

def get_eighth_day_of_eighth_lunar_month(year):
    # 從 8/1 左右開始找農曆八月初八
    start = date(
        year,
        8,
        1,
    )

    for offset in range(40):
        current = start + timedelta(
            days=offset
        )

        lunar = get_lunar_date(
            current
        )

        if lunar is None:
            continue

        if (
            lunar.month == 8
            and lunar.day == 8
        ):
            return current

    return None


# ============================================================
# 建立母親節 / 父親節
# ============================================================

def build_family_events():
    events = []

    for year in TARGET_YEARS:
        mother_day = get_second_sunday_of_may(
            year
        )

        events.append(
            {
                "date": mother_day,
                "summary": "母親節",
                "category": "節日",
                "description": "每年五月第二個星期日",
            }
        )

        father_day = get_eighth_day_of_eighth_lunar_month(
            year
        )

        if father_day:
            events.append(
                {
                    "date": father_day,
                    "summary": "父親節",
                    "category": "節日",
                    "description": "農曆八月初八",
                }
            )

    return events


# ============================================================
# 日期解析：高雄市停班停課資料
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
        match = re.search(
            pattern,
            text,
        )

        if match:
            try:
                return date(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )
            except ValueError:
                pass

    return None


# ============================================================
# 遞迴找高雄 API 日期 / 標題 / 內容
# ============================================================

def recursive_find_strings(
    value,
    result=None,
):
    if result is None:
        result = []

    if isinstance(value, dict):
        for key, item in value.items():
            result.append(
                (
                    str(key),
                    str(item)
                    if not isinstance(
                        item,
                        (dict, list),
                    )
                    else "",
                )
            )

            recursive_find_strings(
                item,
                result,
            )

    elif isinstance(value, list):
        for item in value:
            recursive_find_strings(
                item,
                result,
            )

    return result


# ============================================================
# 高雄停班停課 API
# ============================================================

def get_kaohsiung_events():
    events = []

    try:
        response = http_get(
            KAOHSIUNG_API,
            timeout=30,
        )

        try:
            data = response.json()
        except Exception:
            print(
                "⚠️ 高雄停班停課 API "
                "不是有效 JSON，跳過。"
            )
            return events

        # ----------------------------------------------------
        # 將所有可能的資料整理出來
        # ----------------------------------------------------

        records = []

        if isinstance(data, list):
            records = data

        elif isinstance(data, dict):
            # 常見 API 結構
            for key in (
                "Data",
                "data",
                "Result",
                "result",
                "Records",
                "records",
            ):
                value = data.get(key)

                if isinstance(value, list):
                    records.extend(value)

        # 如果 API 本身是一筆資料
        if not records and isinstance(
            data,
            dict,
        ):
            records = [data]

        for record in records:
            if not isinstance(
                record,
                dict,
            ):
                continue

            text_parts = []

            for key, value in record.items():
                if isinstance(
                    value,
                    (str, int, float),
                ):
                    text_parts.append(
                        f"{key}:{value}"
                    )

            combined = " ".join(
                text_parts
            )

            if not combined:
                continue

            # ------------------------------------------------
            # 必須與停班停課相關
            # ------------------------------------------------

            keywords = [
                "停止上班",
                "停止上課",
                "停班",
                "停課",
                "天然災害",
                "颱風",
                "豪雨",
                "高雄市",
            ]

            if not any(
                keyword in combined
                for keyword in keywords
            ):
                continue

            event_date = (
                extract_date_from_text(
                    combined
                )
            )

            if event_date is None:
                continue

            if event_date.year not in TARGET_YEARS:
                continue

            # ------------------------------------------------
            # 判斷標題
            # ------------------------------------------------

            if (
                "停止上班及上課" in combined
                or (
                    "停止上班" in combined
                    and "停止上課" in combined
                )
            ):
                summary = "高雄市停班停課"

            elif "停止上班" in combined:
                summary = "高雄市停止上班"

            elif "停止上課" in combined:
                summary = "高雄市停止上課"

            else:
                summary = "高雄市停班停課公告"

            # ------------------------------------------------
            # 避免重複
            # ------------------------------------------------

            exists = False

            for event in events:
                if (
                    event["date"]
                    == event_date
                    and event["summary"]
                    == summary
                ):
                    exists = True
                    break

            if exists:
                continue

            events.append(
                {
                    "date": event_date,
                    "summary": summary,
                    "category": "高雄停班停課",
                    "description": combined,
                }
            )

        events.sort(
            key=lambda item: (
                item["date"],
                item["summary"],
            )
        )

        print("")
        print(
            f"🌧️ 高雄停班停課事件："
            f"{len(events)} 筆"
        )

    except Exception as exc:
        print("")
        print(
            "⚠️ 讀取高雄停班停課 API 失敗："
        )
        print(exc)

    return events


# ============================================================
# ICS Escape
# ============================================================

def ics_escape(value):
    if value is None:
        return ""

    text = str(value)

    text = text.replace(
        "\\",
        "\\\\",
    )

    text = text.replace(
        ";",
        "\\;",
    )

    text = text.replace(
        ",",
        "\\,",
    )

    text = text.replace(
        "\r\n",
        "\\n",
    )

    text = text.replace(
        "\n",
        "\\n",
    )

    text = text.replace(
        "\r",
        "\\n",
    )

    return text


# ============================================================
# ICS UID
# ============================================================

def make_uid(event):
    raw = (
        f"{event['date'].isoformat()}-"
        f"{event['summary']}-"
        f"{CALENDAR_NAME}"
    )

    return (
        str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            raw,
        ))
        + "@taiwan-calendar"
    )


# ============================================================
# ICS 產生器
# ============================================================

def build_ics(events):
    now = datetime.now(
        timezone.utc
    )

    dtstamp = now.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Taiwan Calendar//TW//",
        f"X-WR-CALNAME:{ics_escape(CALENDAR_NAME)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-TIMEZONE:Asia/Taipei",
    ]

    # --------------------------------------------------------
    # 時區
    # --------------------------------------------------------

    lines.extend(
        [
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
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    events = sorted(
        events,
        key=lambda item: (
            item["date"],
            item["summary"],
        ),
    )

    # --------------------------------------------------------
    # 事件
    # --------------------------------------------------------

    for event in events:
        event_date = event["date"]

        start = event_date.strftime(
            "%Y%m%d"
        )

        end = (
            event_date
            + timedelta(days=1)
        ).strftime(
            "%Y%m%d"
        )

        summary = ics_escape(
            event["summary"]
        )

        description = ics_escape(
            event.get(
                "description",
                "",
            )
        )

        uid = make_uid(event)

        lines.extend(
            [
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
            ]
        )

    lines.append(
        "END:VCALENDAR"
    )

    return "\r\n".join(
        lines
    ) + "\r\n"


# ============================================================
# 統計
# ============================================================

def print_statistics(events):
    print("")
    print("=" * 60)
    print("📊 行事曆統計")
    print("=" * 60)

    for year in TARGET_YEARS:
        year_events = [
            event
            for event in events
            if event["date"].year == year
        ]

        government_events = [
            event
            for event in year_events
            if event["category"] == "政府假日"
        ]

        family_events = [
            event
            for event in year_events
            if event["category"] == "節日"
        ]

        kaohsiung_events = [
            event
            for event in year_events
            if event["category"]
            == "高雄停班停課"
        ]

        makeup_events = [
            event
            for event in government_events
            if "(補假)" in event["summary"]
        ]

        normal_holidays = [
            event
            for event in government_events
            if "(補假)" not in event["summary"]
        ]

        print("")
        print(f"【{year}】")
        print(
            f"政府假日：{len(government_events)}"
        )
        print(
            f"一般國定假日：{len(normal_holidays)}"
        )
        print(
            f"補假：{len(makeup_events)}"
        )
        print(
            f"母親節/父親節：{len(family_events)}"
        )
        print(
            f"高雄停班停課：{len(kaohsiung_events)}"
        )
        print(
            f"ICS 事件總數：{len(year_events)}"
        )

        print("")
        print("政府假日明細：")

        for event in government_events:
            print(
                f"  {event['date']} "
                f"{event['summary']}"
            )


# ============================================================
# 主程式
# ============================================================

def main():
    print("")
    print("=" * 60)
    print("🇹🇼 台灣生活行事曆產生器")
    print("=" * 60)

    all_events = []

    # --------------------------------------------------------
    # 政府國定假日
    # --------------------------------------------------------

    government_events = (
        build_government_events()
    )

    all_events.extend(
        government_events
    )

    # --------------------------------------------------------
    # 母親節 / 父親節
    # --------------------------------------------------------

    family_events = (
        build_family_events()
    )

    all_events.extend(
        family_events
    )

    # --------------------------------------------------------
    # 高雄停班停課
    # --------------------------------------------------------

    kaohsiung_events = (
        get_kaohsiung_events()
    )

    all_events.extend(
        kaohsiung_events
    )

    # --------------------------------------------------------
    # 去除完全重複
    # --------------------------------------------------------

    unique_events = []

    seen = set()

    for event in all_events:
        key = (
            event["date"],
            event["summary"],
        )

        if key in seen:
            continue

        seen.add(key)

        unique_events.append(
            event
        )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    unique_events.sort(
        key=lambda item: (
            item["date"],
            item["summary"],
        )
    )

    # --------------------------------------------------------
    # 建立 ICS
    # --------------------------------------------------------

    ics_content = build_ics(
        unique_events
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        file.write(
            ics_content
        )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    print_statistics(
        unique_events
    )

    print("")
    print("=" * 60)
    print("✅ taiwan.ics 已成功產生")
    print("=" * 60)
    print(
        f"📄 檔案：{OUTPUT_FILE}"
    )
    print(
        f"📌 總事件數："
        f"{len(unique_events)}"
    )
    print("")


if __name__ == "__main__":
    main()
