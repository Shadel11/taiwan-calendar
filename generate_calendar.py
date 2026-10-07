import csv
import io
import re
import uuid
import warnings
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, unquote

import requests
from lunardate import LunarDate
from urllib3.exceptions import InsecureRequestWarning


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

TIMEOUT = 30


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7"
    ),
}


# ============================================================
# SSL 警告
# ============================================================

warnings.filterwarnings(
    "ignore",
    category=InsecureRequestWarning,
)


# ============================================================
# HTTP
# ============================================================

def http_get(url, timeout=TIMEOUT):
    """
    一般 HTTP / HTTPS 請求。
    """

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
    )

    response.raise_for_status()

    return response


def http_get_dgpa(url, timeout=TIMEOUT):
    """
    DGPA FileConversion CSV 偶爾會遇到：

        CERTIFICATE_VERIFY_FAILED
        Missing Subject Key Identifier

    因此只有 DGPA CSV 下載使用 verify=False。

    注意：
    這裡仍然只下載「指定年度」的 CSV。
    """

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
        verify=False,
    )

    response.raise_for_status()

    return response


# ============================================================
# 日期
# ============================================================

def parse_date(value):
    """
    支援：

    YYYYMMDD
    YYYY/MM/DD
    YYYY-MM-DD
    YYYY.MM.DD
    YYYY-MM-DDTHH:MM:SS
    datetime
    date
    """

    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    value = str(value).strip()

    if not value:
        return None

    # YYYYMMDD
    m = re.fullmatch(
        r"(\d{4})(\d{2})(\d{2})",
        value,
    )

    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
        except ValueError:
            return None

    # YYYY/MM/DD
    # YYYY-MM-DD
    # YYYY.MM.DD
    m = re.fullmatch(
        r"(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})",
        value,
    )

    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
        except ValueError:
            return None

    # YYYY-MM-DDTHH...
    m = re.search(
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        value,
    )

    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
        except ValueError:
            return None

    return None


def roc_year_to_ad(value):
    """
    民國年轉西元：

    115 -> 2026
    116 -> 2027
    """

    try:
        roc = int(value)

        if 1 <= roc <= 200:
            return roc + 1911

    except Exception:
        pass

    return None


# ============================================================
# CSV 解碼
# ============================================================

def decode_csv(content):
    """
    DGPA CSV 可能使用：

    UTF-8 BOM
    UTF-8
    CP950
    Big5
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


# ============================================================
# 欄位處理
# ============================================================

def normalize_key(value):
    if value is None:
        return ""

    return (
        str(value)
        .strip()
        .replace("\ufeff", "")
        .replace(" ", "")
        .replace("\t", "")
        .replace("\r", "")
        .replace("\n", "")
    )


def find_column(fieldnames, candidates):
    """
    不分大小寫、空白尋找欄位。
    """

    normalized = {}

    for field in fieldnames or []:

        normalized[
            normalize_key(field).lower()
        ] = field

    for candidate in candidates:

        key = normalize_key(candidate).lower()

        if key in normalized:
            return normalized[key]

    return None


# ============================================================
# DGPA CSV 解析
# ============================================================

def parse_dgpa_csv(content, target_year):
    """
    解析指定年度 DGPA CSV。

    官方資料主要欄位：

        西元日期
        星期
        是否放假
        備註

    是否放假：

        0 = 上班
        2 = 放假
    """

    text = decode_csv(content)

    reader = csv.DictReader(
        io.StringIO(text)
    )

    if not reader.fieldnames:
        raise ValueError(
            "CSV 沒有欄位"
        )

    date_col = find_column(
        reader.fieldnames,
        [
            "西元日期",
            "日期",
            "date",
        ],
    )

    holiday_col = find_column(
        reader.fieldnames,
        [
            "是否放假",
            "isHoliday",
            "isholiday",
            "holiday",
        ],
    )

    note_col = find_column(
        reader.fieldnames,
        [
            "備註",
            "備註欄",
            "description",
            "說明",
            "節日",
            "節日名稱",
        ],
    )

    if not date_col:
        raise ValueError(
            "找不到日期欄位："
            f"{reader.fieldnames}"
        )

    if not holiday_col:
        raise ValueError(
            "找不到是否放假欄位："
            f"{reader.fieldnames}"
        )

    rows = []

    for raw in reader:

        d = parse_date(
            raw.get(date_col)
        )

        if d is None:
            continue

        if d.year != target_year:
            continue

        holiday_value = str(
            raw.get(holiday_col, "")
        ).strip()

        note = ""

        if note_col:

            note = str(
                raw.get(note_col, "")
            ).strip()

        rows.append(
            {
                "date": d,
                "is_holiday": holiday_value == "2",
                "note": note,
            }
        )

    if not rows:
        raise ValueError(
            f"CSV 找不到 {target_year} 年有效資料"
        )

    rows.sort(
        key=lambda x: x["date"]
    )

    return rows


# ============================================================
# data.gov.tw 找指定年度 CSV
# ============================================================

def extract_dgpa_csv_links(html, target_year):
    """
    從 data.gov.tw dataset 14718 找出指定年度
    DGPA CSV。

    重要：

    2026 -> 民國115
    2027 -> 民國116

    只抓指定年度。

    不會：
        115
        114
        113
        112
        ...

    一路下載。

    同時排除：
        Google 行事曆專用

    因為我們需要標準政府辦公日曆 CSV。
    """

    roc_year = target_year - 1911

    # --------------------------------------------------------
    # 抓出 href
    # --------------------------------------------------------

    hrefs = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )

    candidates = []

    for href in hrefs:

        if not href:
            continue

        href = unquote(
            href.strip()
        )

        full_url = urljoin(
            DGPA_DATASET_URL,
            href,
        )

        lower_url = full_url.lower()

        # 必須是 DGPA
        if "dgpa.gov.tw" not in lower_url:
            continue

        # 必須是 CSV
        if ".csv" not in lower_url:
            continue

        # ----------------------------------------------------
        # 只允許目標民國年份
        # ----------------------------------------------------

        decoded = unquote(
            full_url
        )

        target_patterns = [
            f"{roc_year}年",
            f"{roc_year}%e5%b9%b4",
        ]

        if not any(
            pattern.lower() in decoded.lower()
            for pattern in target_patterns
        ):
            continue

        # ----------------------------------------------------
        # 排除 Google 專用 CSV
        # ----------------------------------------------------

        if "google" in decoded.lower():
            continue

        if "行事曆專用" in decoded:
            continue

        candidates.append(
            full_url
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = []

    seen = set()

    for url in candidates:

        if url in seen:
            continue

        seen.add(url)

        unique.append(url)

    return unique


def get_dgpa_csv(year):
    """
    只取得指定年度 DGPA CSV。

    例如：

        2026 -> 115年
        2027 -> 116年

    絕不下載其他年度。
    """

    roc_year = year - 1911

    print()
    print(
        f"搜尋 {year} 年政府辦公日曆 "
        f"(民國 {roc_year} 年)"
    )

    response = http_get(
        DGPA_DATASET_URL
    )

    html = response.text

    candidates = extract_dgpa_csv_links(
        html,
        year,
    )

    if not candidates:
        raise RuntimeError(
            f"找不到 {year} 年 "
            f"(民國 {roc_year} 年) "
            "DGPA 官方 CSV。"
        )

    print(
        f"找到 {len(candidates)} 個 "
        f"{year} 年 CSV 候選檔案"
    )

    last_error = None

    for url in candidates:

        try:

            decoded_url = unquote(url)

            print(
                f"讀取 {year} 政府辦公日曆："
            )

            print(
                decoded_url
            )

            response = http_get_dgpa(
                url
            )

            rows = parse_dgpa_csv(
                response.content,
                year,
            )

            print(
                f"成功讀取 {year} 政府辦公日曆："
                f"{len(rows)} 天"
            )

            return rows

        except Exception as exc:

            last_error = exc

            print(
                "⚠️ 此年度候選檔案無法使用："
                f"{exc}"
            )

    raise RuntimeError(
        f"{year} 年 DGPA CSV 無法讀取："
        f"{last_error}"
    )


# ============================================================
# 備註清理
# ============================================================

def clean_note(note):
    if not note:
        return ""

    note = str(note).strip()

    # 移除常見符號
    note = re.sub(
        r"^\s*[●•◆◇■□★☆]+\s*",
        "",
        note,
    )

    # 合併多餘空白
    note = re.sub(
        r"\s+",
        " ",
        note,
    )

    return note.strip()


# ============================================================
# 農曆
# ============================================================

def get_lunar_date(d):
    try:

        return LunarDate.fromSolarDate(
            d.year,
            d.month,
            d.day,
        )

    except Exception:

        return None


def lunar_day_text(day):
    """
    1 -> 初一
    2 -> 初二
    ...
    10 -> 初十
    11 -> 十一
    15 -> 十五
    """

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

    return names.get(
        day,
        f"初{day}",
    )


def is_chinese_new_year_area(d):
    """
    只用來「命名」，不是用來判斷是否放假。

    這一點非常重要。

    絕對不能因為日期位於：

        農曆十二月
        或
        農曆正月

    就建立事件。

    真正是否放假一定由 DGPA 決定。
    """

    lunar = get_lunar_date(d)

    if lunar is None:
        return False

    if lunar.month == 12:
        return lunar.day >= 28

    if lunar.month == 1:
        return lunar.day <= 15

    return False


def get_spring_festival_name(d):
    """
    春節名稱只負責「命名」。

    注意：

    這個函式本身不代表該日期放假。

    呼叫者必須先確認：
        DGPA isHoliday == 2
    """

    lunar = get_lunar_date(d)

    if lunar is None:
        return "春節"

    # --------------------------------------------------------
    # 除夕
    # --------------------------------------------------------

    if lunar.month == 12:

        # 農曆最後一天
        next_day = d + timedelta(days=1)
        next_lunar = get_lunar_date(
            next_day
        )

        if (
            next_lunar is not None
            and next_lunar.month == 1
            and next_lunar.day == 1
        ):
            return "除夕"

        # 如果最後一天判斷因曆法資料異常失敗，
        # 保留保守判斷
        if lunar.day in (29, 30):
            return "除夕"

        # 小年夜：
        # 除夕前一天
        if lunar.day in (28, 29):
            return "小年夜"

    # --------------------------------------------------------
    # 正月
    # --------------------------------------------------------

    if lunar.month == 1:

        if 1 <= lunar.day <= 15:
            return lunar_day_text(
                lunar.day
            )

    return "春節"


# ============================================================
# 官方節日名稱
# ============================================================

def get_fixed_holiday_name(d):
    """
    只處理固定日期節日。

    不負責判斷該日期是否放假。
    """

    fixed = {
        (1, 1): "開國紀念日",
        (2, 28): "和平紀念日",
        (4, 4): "兒童節",
        (4, 5): "清明節",
        (5, 1): "勞動節",
        (9, 28): "孔子誕辰紀念日/教師節",
        (10, 10): "國慶日",
        (10, 25): "臺灣光復暨金門古寧頭大捷紀念日",
        (12, 25): "行憲紀念日",
    }

    return fixed.get(
        (d.month, d.day)
    )


def get_lunar_holiday_name(d):
    """
    處理：

    端午節
    中秋節

    同樣只負責命名。
    """

    lunar = get_lunar_date(d)

    if lunar is None:
        return None

    if (
        lunar.month == 5
        and lunar.day == 5
    ):
        return "端午節"

    if (
        lunar.month == 8
        and lunar.day == 15
    ):
        return "中秋節"

    return None


def get_base_holiday_name(d, note=""):
    """
    取得政府假日的最終名稱。

    優先順序：

    1. 春節
    2. 固定日期節日
    3. 端午 / 中秋
    4. 官方備註
    5. 無法判斷則 None
    """

    note = clean_note(
        note
    )

    # --------------------------------------------------------
    # 春節
    # --------------------------------------------------------

    if is_chinese_new_year_area(d):

        return get_spring_festival_name(
            d
        )

    # --------------------------------------------------------
    # 固定日期
    # --------------------------------------------------------

    fixed_name = get_fixed_holiday_name(
        d
    )

    if fixed_name:
        return fixed_name

    # --------------------------------------------------------
    # 農曆節日
    # --------------------------------------------------------

    lunar_name = get_lunar_holiday_name(
        d
    )

    if lunar_name:
        return lunar_name

    # --------------------------------------------------------
    # 官方備註
    # --------------------------------------------------------

    if note:
        return note

    return None


# ============================================================
# 判斷補假
# ============================================================

def is_makeup_holiday(d, note):
    """
    判斷官方放假日是否為補假。

    最優先依官方備註判斷。

    例如：

        補假
        補休
        調整放假

    都視為補假。
    """

    text = clean_note(
        note
    )

    if not text:
        return False

    keywords = [
        "補假",
        "補休",
        "調整放假",
    ]

    return any(
        keyword in text
        for keyword in keywords
    )


# ============================================================
# 建立政府事件
# ============================================================

def build_government_events(
    year,
    rows,
):
    """
    核心邏輯：

    DGPA 是唯一的「放假日期來源」。

    不自行猜測：
        哪個週末是假日
        哪個農曆日是假日
        春節放幾天

    所以：

        isHoliday=2
            ↓
        才有資格進一步建立事件

    這樣可以避免：

        初八
        初九
        十五

    等農曆日期被錯誤產生。
    """

    holiday_rows = [
        row
        for row in rows
        if row["is_holiday"]
    ]

    work_rows = [
        row
        for row in rows
        if not row["is_holiday"]
    ]

    holiday_by_date = {
        row["date"]: row
        for row in holiday_rows
    }

    work_by_date = {
        row["date"]: row
        for row in work_rows
    }

    events = []

    # ========================================================
    # 補班
    #
    # 官方 CSV：
    # 週六 / 週日
    # 但 isHoliday = 0
    #
    # 這就是調整上班日。
    # ========================================================

    makeup_work_dates = set()

    for d in work_by_date:

        if d.weekday() >= 5:
            makeup_work_dates.add(
                d
            )

    # ========================================================
    # 補假
    # ========================================================

    makeup_holiday_dates = set()

    for d, row in holiday_by_date.items():

        note = clean_note(
            row.get("note", "")
        )

        if is_makeup_holiday(
            d,
            note,
        ):
            makeup_holiday_dates.add(
                d
            )

    # ========================================================
    # 建立政府放假事件
    # ========================================================

    for d in sorted(
        holiday_by_date.keys()
    ):

        row = holiday_by_date[d]

        note = clean_note(
            row.get("note", "")
        )

        # ----------------------------------------------------
        # 重要：
        #
        # 普通週六、週日不建立事件。
        #
        # 只有：
        #
        # 1. 平日政府假日
        # 2. 官方補假
        # 3. 官方節日區間中的週末
        #
        # 才建立。
        #
        # 但「官方節日區間」不能靠農曆範圍猜。
        # 因此這裡使用「附近平日假日」來確認。
        # ----------------------------------------------------

        weekday = d.weekday()

        is_weekend = (
            weekday >= 5
        )

        # ----------------------------------------------------
        # 補假
        # ----------------------------------------------------

        if d in makeup_holiday_dates:

            base_name = get_base_holiday_name(
                d,
                note,
            )

            if (
                is_chinese_new_year_area(d)
            ):
                title = "春節(補假)"

            elif base_name:
                title = (
                    f"{base_name}(補假)"
                )

            else:
                title = "補假"

            events.append(
                {
                    "date": d,
                    "end_date": d + timedelta(days=1),
                    "title": title,
                    "category": "government",
                }
            )

            continue

        # ----------------------------------------------------
        # 平日放假
        # ----------------------------------------------------

        if not is_weekend:

            base_name = get_base_holiday_name(
                d,
                note,
            )

            if base_name:

                events.append(
                    {
                        "date": d,
                        "end_date": d + timedelta(days=1),
                        "title": base_name,
                        "category": "government",
                    }
                )

            continue

        # ----------------------------------------------------
        # 週末放假
        #
        # 不代表所有週末都要進 ICS。
        #
        # 判斷方式：
        #
        # 只有官方備註明確有節日，
        # 或這一天屬於「官方節日連續假期」。
        #
        # 這裡用附近平日政府假日判斷。
        # ----------------------------------------------------

        if note:

            base_name = get_base_holiday_name(
                d,
                note,
            )

            if base_name:

                events.append(
                    {
                        "date": d,
                        "end_date": d + timedelta(days=1),
                        "title": base_name,
                        "category": "government",
                    }
                )

            continue

        # ----------------------------------------------------
        # 沒有備註的週末：
        #
        # 如果前後幾天存在相同節日的平日假日，
        # 視為該連假中的週末。
        #
        # 只搜尋 ±3 天。
        #
        # 這是為了避免：
        #
        # 2/13 初八
        # 2/14 初九
        # 2/20 十五
        #
        # 被當成春節假日。
        # ----------------------------------------------------

        nearby_names = set()

        for offset in range(
            -3,
            4,
        ):

            if offset == 0:
                continue

            nearby = d + timedelta(
                days=offset
            )

            nearby_row = holiday_by_date.get(
                nearby
            )

            if not nearby_row:
                continue

            if nearby.weekday() >= 5:
                continue

            nearby_name = get_base_holiday_name(
                nearby,
                nearby_row.get(
                    "note",
                    "",
                ),
            )

            if nearby_name:
                nearby_names.add(
                    nearby_name
                )

        # ----------------------------------------------------
        # 如果附近平日有節日，
        # 再判斷是否屬於連假。
        #
        # 但春節特殊：
        # 只允許真正位於官方春節區段附近的日期。
        # ----------------------------------------------------

        title = None

        for nearby_name in nearby_names:

            # 春節附近週末
            # 必須真的位於官方假日資料的連續區間。
            if (
                nearby_name in {
                    "小年夜",
                    "除夕",
                    "初一",
                    "初二",
                    "初三",
                    "初四",
                    "初五",
                    "初六",
                    "春節",
                }
            ):

                # 只允許附近 1~3 天內確實有
                # 春節官方放假日。
                official_spring_nearby = False

                for offset in range(
                    -3,
                    4,
                ):

                    if offset == 0:
                        continue

                    check = d + timedelta(
                        days=offset
                    )

                    check_row = holiday_by_date.get(
                        check
                    )

                    if not check_row:
                        continue

                    if not is_chinese_new_year_area(
                        check
                    ):
                        continue

                    official_spring_nearby = True
                    break

                if official_spring_nearby:

                    # 這裡重新確認：
                    # 只有當本日自身也在官方假期
                    # 所涵蓋的連續區域才加入。
                    #
                    # 例如 2027：
                    #
                    # 2/4 除夕
                    # 2/5 初一前
                    # 2/6 初一
                    # 2/7 初二
                    # 2/8...
                    #
                    # 但 2/13、2/14 不會因為還在
                    # 農曆正月就進來。
                    if any(
                        abs(
                            (
                                d - check
                            ).days
                        ) <= 1
                        for check in holiday_by_date
                        if (
                            check != d
                            and
                            check in holiday_by_date
                            and
                            check.weekday() < 5
                            and
                            is_chinese_new_year_area(
                                check
                            )
                        )
                    ):
                        title = nearby_name
                        break

            else:

                title = nearby_name
                break

        if title:

            # 春節週末直接使用農曆名稱
            # 但只有真正連假中的日期才會到這裡。
            spring_name = get_spring_festival_name(
                d
            )

            if (
                is_chinese_new_year_area(d)
                and spring_name != "春節"
            ):
                title = spring_name

            events.append(
                {
                    "date": d,
                    "end_date": d + timedelta(days=1),
                    "title": title,
                    "category": "government",
                }
            )

    # ========================================================
    # 建立補班
    # ========================================================

    for d in sorted(
        makeup_work_dates
    ):

        title = None

        # ----------------------------------------------------
        # 優先尋找附近的官方假日
        # ----------------------------------------------------

        for offset in range(
            1,
            8,
        ):

            before = d - timedelta(
                days=offset
            )

            after = d + timedelta(
                days=offset
            )

            # 前方
            if before in holiday_by_date:

                before_row = holiday_by_date[
                    before
                ]

                base_name = get_base_holiday_name(
                    before,
                    before_row.get(
                        "note",
                        "",
                    ),
                )

                if base_name:

                    # 春節補班不要出現奇怪的：
                    # [補班]初八
                    #
                    # 官方補班如果屬春節，
                    # 統一使用「春節」。
                    if is_chinese_new_year_area(
                        before
                    ):
                        title = "[補班]春節"
                    else:
                        title = (
                            f"[補班]{base_name}"
                        )

                    break

            # 後方
            if after in holiday_by_date:

                after_row = holiday_by_date[
                    after
                ]

                base_name = get_base_holiday_name(
                    after,
                    after_row.get(
                        "note",
                        "",
                    ),
                )

                if base_name:

                    if is_chinese_new_year_area(
                        after
                    ):
                        title = "[補班]春節"
                    else:
                        title = (
                            f"[補班]{base_name}"
                        )

                    break

        if not title:

            title = "[補班]政府行政機關辦公日曆"

        events.append(
            {
                "date": d,
                "end_date": d + timedelta(days=1),
                "title": title,
                "category": "makeup_work",
            }
        )

    # ========================================================
    # 最終去重
    # ========================================================

    unique = {}

    for event in events:

        key = (
            event["date"],
            event["title"],
        )

        unique[key] = event

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: (
            x["date"],
            x["title"],
        )
    )

    return result


# ============================================================
# 母親節
# ============================================================

def get_mothers_day(year):
    """
    5 月第二個星期日。
    """

    d = date(
        year,
        5,
        1,
    )

    while d.weekday() != 6:
        d += timedelta(
            days=1
        )

    d += timedelta(
        days=7
    )

    return d


# ============================================================
# 父親節
# ============================================================

def get_fathers_day(year):
    """
    8 月 8 日。
    """

    return date(
        year,
        8,
        8,
    )


# ============================================================
# 高雄市停班停課
# ============================================================

def walk_json(value):
    """
    遞迴搜尋 JSON 中所有 dict/list。
    """

    if isinstance(
        value,
        dict,
    ):

        yield value

        for child in value.values():

            yield from walk_json(
                child
            )

    elif isinstance(
        value,
        list,
    ):

        for child in value:

            yield from walk_json(
                child
            )


def find_first_value(
    obj,
    keys,
):
    """
    不分大小寫尋找第一個符合欄位。
    """

    keyset = {
        str(k).strip().lower()
        for k in keys
    }

    for item in walk_json(
        obj
    ):

        if not isinstance(
            item,
            dict,
        ):
            continue

        for key, value in item.items():

            if (
                str(key).strip().lower()
                in keyset
            ):

                if value not in (
                    None,
                    "",
                ):

                    return value

    return None


def parse_kaohsiung_events():
    """
    高雄市停班停課 API。

    API 無資料時：
    回傳空集合，不讓整個行事曆失敗。
    """

    print()
    print(
        "讀取高雄市停班停課資料..."
    )

    try:

        response = http_get(
            KAOHSIUNG_API,
            timeout=20,
        )

        data = response.json()

    except Exception as exc:

        print(
            "⚠️ 高雄市停班停課 API "
            f"無法讀取：{exc}"
        )

        return []

    events = []

    for item in walk_json(
        data
    ):

        if not isinstance(
            item,
            dict,
        ):
            continue

        date_value = find_first_value(
            item,
            [
                "date",
                "日期",
                "停班停課日期",
                "發布日期",
            ],
        )

        if not date_value:
            continue

        d = parse_date(
            date_value
        )

        if not d:
            continue

        if d.year not in TARGET_YEARS:
            continue

        title = find_first_value(
            item,
            [
                "title",
                "標題",
                "主旨",
                "說明",
                "內容",
            ],
        )

        location = find_first_value(
            item,
            [
                "district",
                "區域",
                "地區",
                "行政區",
            ],
        )

        if title:

            title = str(
                title
            ).strip()

        else:

            title = "高雄市停班停課"

        if "高雄" not in title:

            title = (
                f"高雄市{title}"
            )

        if location:

            location = str(
                location
            ).strip()

            if (
                location
                and location not in title
                and "全市" not in title
            ):

                title = (
                    f"{title} - {location}"
                )

        events.append(
            {
                "date": d,
                "end_date": d + timedelta(days=1),
                "title": title,
                "category": "kaohsiung",
            }
        )

    # ========================================================
    # 去重
    # ========================================================

    unique = {}

    for event in events:

        key = (
            event["date"],
            event["title"],
        )

        unique[key] = event

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: (
            x["date"],
            x["title"],
        )
    )

    print(
        f"高雄市停班停課："
        f"{len(result)}"
    )

    return result


# ============================================================
# ICS
# ============================================================

def ics_escape(value):
    if value is None:
        return ""

    value = str(
        value
    )

    return (
        value
        .replace(
            "\\",
            "\\\\",
        )
        .replace(
            ";",
            "\\;",
        )
        .replace(
            ",",
            "\\,",
        )
        .replace(
            "\r\n",
            "\\n",
        )
        .replace(
            "\n",
            "\\n",
        )
    )


def event_uid(event):
    """
    穩定 UUID。

    同一個：
        日期 + 標題

    永遠產生相同 UID。

    這對 iPhone 訂閱非常重要，
    避免重新產生 ICS 後重複事件。
    """

    raw = (
        f"{event['date'].isoformat()}"
        f"|{event['title']}"
    )

    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "taiwan-calendar:" + raw,
        )
    )


def format_ics_date(d):
    return d.strftime(
        "%Y%m%d"
    )


def build_ics(events):
    """
    建立 iCalendar。
    """

    now = datetime.utcnow().strftime(
        "%Y%m%dT%H%M%SZ"
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Shadel11//Taiwan Calendar//TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(CALENDAR_NAME)}",
        "X-WR-TIMEZONE:Asia/Taipei",
    ]

    for event in sorted(
        events,
        key=lambda x: (
            x["date"],
            x["title"],
        ),
    ):

        start = event["date"]

        end = event["end_date"]

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event_uid(event)}",
                f"DTSTAMP:{now}",
                (
                    "DTSTART;VALUE=DATE:"
                    f"{format_ics_date(start)}"
                ),
                (
                    "DTEND;VALUE=DATE:"
                    f"{format_ics_date(end)}"
                ),
                (
                    "SUMMARY:"
                    f"{ics_escape(event['title'])}"
                ),
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
        )

    lines.append(
        "END:VCALENDAR"
    )

    return (
        "\r\n".join(lines)
        + "\r\n"
    )


# ============================================================
# 統計
# ============================================================

def print_event_statistics(
    all_events,
    statistics,
):
    print()
    print(
        "=" * 60
    )

    print(
        "產生結果"
    )

    print(
        "=" * 60
    )

    print(
        "政府一般放假事件："
        f"{statistics['government']}"
    )

    print(
        "政府補班事件："
        f"{statistics['makeup_work']}"
    )

    print(
        "母親節："
        f"{statistics['mother']}"
    )

    print(
        "父親節："
        f"{statistics['father']}"
    )

    print(
        "高雄市停班停課："
        f"{statistics['kaohsiung']}"
    )

    print(
        "全部事件："
        f"{len(all_events)}"
    )

    print()

    for year in TARGET_YEARS:

        count = sum(
            1
            for event in all_events
            if event["date"].year == year
        )

        print(
            f"{year}：{count} 筆"
        )

    print(
        "=" * 60
    )


# ============================================================
# 主程式
# ============================================================

def main():

    print(
        "=" * 60
    )

    print(
        "台灣生活行事曆產生器"
    )

    print(
        "=" * 60
    )

    all_events = []

    statistics = {
        "government": 0,
        "makeup_work": 0,
        "mother": 0,
        "father": 0,
        "kaohsiung": 0,
    }

    # ========================================================
    # DGPA 官方假日
    # ========================================================

    for year in TARGET_YEARS:

        print()
        print(
            f"========== {year} =========="
        )

        rows = get_dgpa_csv(
            year
        )

        government_events = (
            build_government_events(
                year,
                rows,
            )
        )

        for event in government_events:

            all_events.append(
                event
            )

            if (
                event["category"]
                == "makeup_work"
            ):

                statistics[
                    "makeup_work"
                ] += 1

            else:

                statistics[
                    "government"
                ] += 1

    # ========================================================
    # 母親節
    # ========================================================

    for year in TARGET_YEARS:

        d = get_mothers_day(
            year
        )

        all_events.append(
            {
                "date": d,
                "end_date": d + timedelta(days=1),
                "title": "母親節",
                "category": "mother",
            }
        )

        statistics[
            "mother"
        ] += 1

    # ========================================================
    # 父親節
    # ========================================================

    for year in TARGET_YEARS:

        d = get_fathers_day(
            year
        )

        all_events.append(
            {
                "date": d,
                "end_date": d + timedelta(days=1),
                "title": "父親節",
                "category": "father",
            }
        )

        statistics[
            "father"
        ] += 1

    # ========================================================
    # 高雄市停班停課
    # ========================================================

    kaohsiung_events = (
        parse_kaohsiung_events()
    )

    all_events.extend(
        kaohsiung_events
    )

    statistics[
        "kaohsiung"
    ] = len(
        kaohsiung_events
    )

    # ========================================================
    # 最終去重
    # ========================================================

    unique = {}

    for event in all_events:

        key = (
            event["date"],
            event["title"],
        )

        unique[key] = event

    all_events = list(
        unique.values()
    )

    all_events.sort(
        key=lambda x: (
            x["date"],
            x["title"],
        )
    )

    # ========================================================
    # 輸出統計
    # ========================================================

    print_event_statistics(
        all_events,
        statistics,
    )

    # ========================================================
    # 輸出 ICS
    # ========================================================

    ics_content = build_ics(
        all_events
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        f.write(
            ics_content
        )

    print()
    print(
        f"已產生：{OUTPUT_FILE}"
    )

    print(
        f"事件總數："
        f"{len(all_events)}"
    )

    print(
        "=" * 60
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    main()
