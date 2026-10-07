import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, unquote

import requests
import urllib3
from lunardate import LunarDate


# ============================================================
# 基本設定
# ============================================================

CALENDAR_NAME = "台灣生活行事曆"
TIMEZONE = "Asia/Taipei"

DGPA_DATASET_URL = "https://data.gov.tw/dataset/14718"

KHH_API_URL = (
    "https://openapi.kcg.gov.tw/Api/Service/"
    "Get/95eec21d-4ee7-4920-94ff-d36727bc171f"
)

CURRENT_YEAR = datetime.now().year
YEARS = [CURRENT_YEAR, CURRENT_YEAR + 1]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}

OUTPUT_FILE = "taiwan.ics"


# ============================================================
# HTTPS
# ============================================================

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


# ============================================================
# HTTP
# ============================================================

def http_get(url, timeout=30):
    print(f"[HTTP] GET {url}")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
        verify=False,
    )

    response.raise_for_status()

    return response


# ============================================================
# 日期解析
# ============================================================

def parse_date(value):
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    # 2026/02/17
    # 2026-02-17
    # 2026.02.17

    m = re.search(
        r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})",
        text,
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

    # 20260217

    m = re.search(
        r"(\d{4})(\d{2})(\d{2})",
        text,
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

    # Excel serial date

    if text.isdigit():
        try:
            number = int(text)

            if 30000 <= number <= 60000:
                return (
                    date(1899, 12, 30)
                    + timedelta(days=number)
                )

        except Exception:
            pass

    return None


# ============================================================
# 取得 DGPA 資源連結
# ============================================================

def get_resource_links():
    response = http_get(DGPA_DATASET_URL)

    html = response.text

    links = []

    # href="..."

    for match in re.finditer(
        r'href=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    ):
        href = match.group(1)

        if ".csv" not in href.lower():
            continue

        href = href.replace(
            "&amp;",
            "&",
        )

        full_url = urljoin(
            DGPA_DATASET_URL,
            href,
        )

        if full_url not in links:
            links.append(full_url)

    # 頁面文字中的完整 CSV URL

    for match in re.finditer(
        r'https?://[^"\'>\s]+\.csv[^"\'>\s]*',
        html,
        flags=re.IGNORECASE,
    ):
        url = match.group(0).replace(
            "&amp;",
            "&",
        )

        if url not in links:
            links.append(url)

    print(
        f"[資料] 頁面找到候選資源："
        f"{len(links)} 個"
    )

    return links


# ============================================================
# 找指定年度官方 CSV
# ============================================================

def find_csv_url(year):
    roc_year = year - 1911

    print()
    print("=" * 60)
    print(
        f"[資料] 正在尋找 {year} 年政府辦公日曆"
    )
    print(
        f"[資料] 民國年：{roc_year}"
    )
    print("=" * 60)

    links = get_resource_links()

    print(
        f"[資料] 候選 CSV："
        f"{len(links)} 個"
    )

    candidates = []

    for url in links:
        decoded = unquote(url)
        decoded_lower = decoded.lower()

        score = 0

        if f"{roc_year}年" in decoded:
            score += 1000

        if (
            f"{roc_year}%e5%b9%b4".lower()
            in url.lower()
        ):
            score += 1000

        if "辦公日曆表" in decoded:
            score += 100

        if "辦公日曆" in decoded:
            score += 50

        if "Google行事曆專用" in decoded:
            score -= 10

        if str(year) in decoded:
            score += 30

        if score > 0:
            candidates.append(
                (
                    score,
                    url,
                    decoded,
                )
            )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    for score, url, decoded in candidates:
        print(
            f"[資料] 候選 {score}："
            f"{decoded}"
        )

    exact_candidates = [
        item
        for item in candidates
        if item[0] >= 1000
    ]

    if not exact_candidates:
        raise RuntimeError(
            f"找不到 {year} 年官方政府辦公日曆 CSV"
        )

    selected_url = exact_candidates[0][1]

    print()
    print(
        f"[資料] 嘗試 {year} 年 CSV："
        f"{unquote(selected_url)}"
    )

    response = http_get(
        selected_url,
        timeout=30,
    )

    print(
        f"[資料] ✓ 找到 {year} 年官方 CSV"
    )

    print(
        f"[資料] ★ 使用 CSV："
        f"{unquote(selected_url)}"
    )

    return response


# ============================================================
# 解析 CSV
# ============================================================

def read_dgpa_csv(year):
    response = find_csv_url(year)

    content = response.content

    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp950",
        "big5",
    ]

    decoded_text = None
    used_encoding = None

    for encoding in encodings:
        try:
            decoded_text = content.decode(
                encoding
            )

            used_encoding = encoding

            break

        except UnicodeDecodeError:
            continue

    if decoded_text is None:
        raise RuntimeError(
            f"{year} 年 CSV 無法解碼"
        )

    print(
        f"[資料] CSV 編碼："
        f"{used_encoding}"
    )

    sample = decoded_text[:5000]

    try:
        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=",\t;",
        )

    except Exception:
        dialect = csv.excel

    reader = csv.DictReader(
        io.StringIO(decoded_text),
        dialect=dialect,
    )

    fieldnames = reader.fieldnames or []

    print(
        f"[資料] 欄位："
        f"{fieldnames}"
    )

    rows = []

    for raw in reader:
        normalized = {}

        for key, value in raw.items():

            if key is None:
                continue

            clean_key = (
                str(key)
                .replace("\ufeff", "")
                .strip()
            )

            normalized[clean_key] = (
                ""
                if value is None
                else str(value).strip()
            )

        date_value = None

        for key in [
            "西元日期",
            "日期",
            "Date",
        ]:

            if key in normalized:

                date_value = parse_date(
                    normalized[key]
                )

                if date_value:
                    break

        if not date_value:
            continue

        holiday_value = ""

        for key in [
            "是否放假",
            "放假",
            "Holiday",
        ]:

            if key in normalized:

                holiday_value = normalized[key]

                break

        note_value = ""

        for key in [
            "備註",
            "備註說明",
            "說明",
            "Note",
        ]:

            if key in normalized:

                note_value = normalized[key]

                break

        rows.append({
            "date": date_value,
            "holiday": holiday_value,
            "note": note_value,
        })

    print(
        f"[資料] ✓ 成功解析 "
        f"{len(rows)} 筆 {year} 年資料"
    )

    return rows


# ============================================================
# 判斷是否為放假
# ============================================================

def is_holiday_value(value):
    text = str(value).strip()

    return text in {
        "2",
        "放假",
        "假日",
        "是",
        "Y",
        "YES",
        "true",
        "True",
        "TRUE",
    }


# ============================================================
# 判斷是否為上班
# ============================================================

def is_workday_value(value):
    text = str(value).strip()

    return text in {
        "0",
        "上班",
        "工作日",
        "否",
        "N",
        "NO",
        "false",
        "False",
        "FALSE",
    }


# ============================================================
# 從文字直接判斷節日名稱
#
# ★ 這是這次修正的核心
#
# 不再只靠「附近日期猜節日」。
# 例如：
#
# 兒童節逢例假日，於清明節之次日補假
#
# 必須判定為：
#
# 兒童節
#
# 而不是清明節。
# ============================================================

def detect_holiday_name_from_text(text):
    if not text:
        return ""

    text = str(text).strip()

    # --------------------------------------------------------
    # 優先順序非常重要
    # --------------------------------------------------------

    keywords = [
        (
            "臺灣光復暨金門古寧頭大捷紀念日",
            "臺灣光復暨金門古寧頭大捷紀念日",
        ),
        (
            "台灣光復暨金門古寧頭大捷紀念日",
            "臺灣光復暨金門古寧頭大捷紀念日",
        ),
        (
            "孔子誕辰紀念日",
            "孔子誕辰紀念日／教師節",
        ),
        (
            "教師節",
            "孔子誕辰紀念日／教師節",
        ),
        (
            "行憲紀念日",
            "行憲紀念日",
        ),
        (
            "勞動節",
            "勞動節",
        ),
        (
            "兒童節",
            "兒童節",
        ),
        (
            "清明節",
            "清明節",
        ),
        (
            "民族掃墓節",
            "清明節",
        ),
        (
            "端午節",
            "端午節",
        ),
        (
            "中秋節",
            "中秋節",
        ),
        (
            "國慶日",
            "國慶日",
        ),
        (
            "和平紀念日",
            "228和平紀念日",
        ),
        (
            "228和平紀念日",
            "228和平紀念日",
        ),
        (
            "元旦",
            "元旦",
        ),
        (
            "開國紀念日",
            "元旦",
        ),
        (
            "中華民國開國紀念日",
            "元旦",
        ),
        (
            "除夕",
            "除夕",
        ),
        (
            "春節",
            "春節",
        ),
        (
            "農曆春節",
            "春節",
        ),
    ]

    for keyword, name in keywords:

        if keyword in text:

            return name

    return ""


# ============================================================
# 清理官方假日名稱
# ============================================================

def normalize_holiday_name(note):
    if not note:
        return ""

    text = str(note).strip()

    text = re.sub(
        r"\s+",
        "",
        text,
    )

    # --------------------------------------------------------
    # 先從完整備註判斷節日
    # --------------------------------------------------------

    detected = detect_holiday_name_from_text(
        text
    )

    if detected:
        return detected

    # --------------------------------------------------------
    # 再清除補假／補班文字
    # --------------------------------------------------------

    text = text.replace(
        "補假",
        "",
    )

    text = text.replace(
        "補休",
        "",
    )

    text = text.replace(
        "補放",
        "",
    )

    text = text.replace(
        "補行上班",
        "",
    )

    text = text.replace(
        "補行辦公",
        "",
    )

    text = text.replace(
        "調整放假",
        "",
    )

    text = text.replace(
        "調整上班",
        "",
    )

    text = text.replace(
        "調整辦公",
        "",
    )

    detected = detect_holiday_name_from_text(
        text
    )

    if detected:
        return detected

    text = text.replace(
        "放假",
        "",
    )

    text = text.strip(
        "，,、。；;:：()（）[]【】 "
    )

    return text


# ============================================================
# 農曆名稱
# ============================================================

LUNAR_DAY_NAMES = {
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
    16: "十六",
    17: "十七",
    18: "十八",
    19: "十九",
    20: "二十",
    21: "廿一",
    22: "廿二",
    23: "廿三",
    24: "廿四",
    25: "廿五",
    26: "廿六",
    27: "廿七",
    28: "廿八",
    29: "廿九",
    30: "三十",
}


def lunar_day_label(solar_date):

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

    lunar_month, lunar_day = lunar_day_label(
        solar_date
    )

    if lunar_month is None:
        return ""

    # 農曆十二月最後一天 = 除夕

    if lunar_month == 12:

        next_day = solar_date + timedelta(
            days=1
        )

        next_month, _ = lunar_day_label(
            next_day
        )

        if next_month == 1:
            return "除夕"

    # 農曆正月

    if lunar_month == 1:

        return LUNAR_DAY_NAMES.get(
            lunar_day,
            "",
        )

    return ""


# ============================================================
# 判斷是否屬於春節附近區段
# ============================================================

def is_lunar_new_year_period(solar_date):

    lunar_month, lunar_day = lunar_day_label(
        solar_date
    )

    # 初一～十五

    if (
        lunar_month == 1
        and lunar_day is not None
        and 1 <= lunar_day <= 15
    ):

        return True

    # 除夕

    if lunar_special_name(solar_date) == "除夕":
        return True

    # 小年夜

    next_day = solar_date + timedelta(
        days=1
    )

    if lunar_special_name(next_day) == "除夕":
        return True

    return False


# ============================================================
# 找附近官方假日名稱
#
# ★ 只作最後備援
#
# 補假日期不再優先使用這個函式。
# ============================================================

def find_nearby_holiday_name(
    rows,
    target_date,
):

    candidates = []

    for row in rows:

        d = row["date"]

        if d == target_date:
            continue

        distance = abs(
            (d - target_date).days
        )

        if distance > 7:
            continue

        note = row["note"]

        name = normalize_holiday_name(
            note
        )

        if not name:
            continue

        if name == "春節":

            score = 100 - distance

        else:

            score = 50 - distance

        candidates.append(
            (
                score,
                name,
            )
        )

    if not candidates:
        return ""

    candidates.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    return candidates[0][1]


# ============================================================
# ★ 判斷補假真正對應的節日
# ============================================================

def find_makeup_holiday_name(
    rows,
    target_date,
    note,
):

    # --------------------------------------------------------
    # 第一優先：
    # 直接從當天官方備註判斷
    # --------------------------------------------------------

    name = detect_holiday_name_from_text(
        note
    )

    if name:
        return name

    # --------------------------------------------------------
    # 第二優先：
    # 往前找 3 天內的官方節日
    # --------------------------------------------------------

    previous_candidates = []

    for row in rows:

        d = row["date"]

        if d >= target_date:
            continue

        distance = (
            target_date - d
        ).days

        if distance > 3:
            continue

        name = detect_holiday_name_from_text(
            row["note"]
        )

        if not name:
            continue

        previous_candidates.append(
            (
                distance,
                name,
            )
        )

    previous_candidates.sort(
        key=lambda x: x[0]
    )

    if previous_candidates:
        return previous_candidates[0][1]

    # --------------------------------------------------------
    # 第三優先：
    # 往後找 3 天
    # --------------------------------------------------------

    next_candidates = []

    for row in rows:

        d = row["date"]

        if d <= target_date:
            continue

        distance = (
            d - target_date
        ).days

        if distance > 3:
            continue

        name = detect_holiday_name_from_text(
            row["note"]
        )

        if not name:
            continue

        next_candidates.append(
            (
                distance,
                name,
            )
        )

    next_candidates.sort(
        key=lambda x: x[0]
    )

    if next_candidates:
        return next_candidates[0][1]

    # --------------------------------------------------------
    # 最後才使用舊的附近搜尋
    # --------------------------------------------------------

    return find_nearby_holiday_name(
        rows,
        target_date,
    )


# ============================================================
# 建立政府假日事件
# ============================================================

def build_government_events(
    year,
    rows,
):

    events = []

    # --------------------------------------------------------
    # 第一階段：
    # 官方放假日
    # --------------------------------------------------------

    for row in rows:

        d = row["date"]

        if d.year != year:
            continue

        holiday = is_holiday_value(
            row["holiday"]
        )

        note = row["note"]

        if not holiday:
            continue

        weekday = d.weekday()

        is_weekday = weekday < 5

        has_note = bool(
            str(note).strip()
        )

        spring_festival = (
            is_lunar_new_year_period(d)
        )

        # 普通週末沒有特殊備註時，不建立事件

        if not (
            is_weekday
            or has_note
            or spring_festival
        ):
            continue

        # ----------------------------------------------------
        # 先直接從官方備註抓節日名稱
        # ----------------------------------------------------

        name = detect_holiday_name_from_text(
            note
        )

        # ----------------------------------------------------
        # 如果官方備註沒有明確名稱
        # 才使用原本清理方法
        # ----------------------------------------------------

        if not name:

            name = normalize_holiday_name(
                note
            )

        # ----------------------------------------------------
        # 春節特殊處理
        # ----------------------------------------------------

        if spring_festival:

            lunar_name = lunar_special_name(
                d
            )

            # 除夕

            if lunar_name == "除夕":

                name = "除夕"

            # 初一～十五

            elif lunar_name:

                name = lunar_name

            # 小年夜

            elif (
                lunar_special_name(
                    d + timedelta(days=1)
                )
                == "除夕"
            ):

                name = "小年夜"

            elif not name:

                name = "春節"

        # ----------------------------------------------------
        # 官方備註沒有名稱
        # ----------------------------------------------------

        if not name:

            nearby = find_nearby_holiday_name(
                rows,
                d,
            )

            if nearby:
                name = nearby

        if not name:

            name = "政府放假日"

        # ----------------------------------------------------
        # 補假
        #
        # ★ 這裡改成：
        # 先從「當天備註」判斷真正節日。
        # 不再讓附近的清明節／行憲紀念日搶走名稱。
        # ----------------------------------------------------

        note_text = str(note)

        is_makeup_holiday = (
            "補假" in note_text
            or "補休" in note_text
            or "補放" in note_text
        )

        if is_makeup_holiday:

            makeup_name = find_makeup_holiday_name(
                rows,
                d,
                note_text,
            )

            if makeup_name:

                name = makeup_name

            if not name.endswith("(補假)"):

                name = f"{name}(補假)"

        events.append({
            "date": d,
            "summary": name,
            "source": "government",
        })

    # --------------------------------------------------------
    # 第二階段：
    # 官方上班日中的補班／調整上班
    # --------------------------------------------------------

    for row in rows:

        d = row["date"]

        if d.year != year:
            continue

        if not is_workday_value(
            row["holiday"]
        ):
            continue

        note = str(
            row["note"] or ""
        )

        if not any(
            keyword in note
            for keyword in [
                "補行上班",
                "調整上班",
                "補班",
                "調整辦公",
                "補行辦公",
            ]
        ):
            continue

        holiday_name = detect_holiday_name_from_text(
            note
        )

        if not holiday_name:

            holiday_name = normalize_holiday_name(
                note
            )

        if not holiday_name:

            holiday_name = find_nearby_holiday_name(
                rows,
                d,
            )

        if not holiday_name:

            holiday_name = "政府假日"

        events.append({
            "date": d,
            "summary": f"[補班]{holiday_name}",
            "source": "government",
        })

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for event in events:

        key = (
            event["date"],
            event["summary"],
        )

        unique[key] = event

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: (
            x["date"],
            x["summary"],
        )
    )

    return result


# ============================================================
# 母親節／父親節
# ============================================================

def get_mothers_day(year):

    d = date(
        year,
        5,
        1,
    )

    while d.weekday() != 6:

        d += timedelta(
            days=1
        )

    return d + timedelta(
        days=7
    )


def get_fathers_day(year):

    return date(
        year,
        8,
        8,
    )


def build_family_events(year):

    return [
        {
            "date": get_mothers_day(year),
            "summary": "母親節",
            "source": "note",
        },
        {
            "date": get_fathers_day(year),
            "summary": "父親節",
            "source": "note",
        },
    ]


# ============================================================
# 高雄停班停課日期解析
# ============================================================

def parse_khh_date(value):

    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()

    if not text:
        return None

    # 西元

    m = re.search(
        r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})",
        text,
    )

    if m:

        try:

            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )

        except ValueError:

            pass

    # 民國

    m = re.search(
        r"(\d{3})[-/.](\d{1,2})[-/.](\d{1,2})",
        text,
    )

    if m:

        try:

            return date(
                int(m.group(1)) + 1911,
                int(m.group(2)),
                int(m.group(3)),
            )

        except ValueError:

            pass

    # 純數字

    m = re.search(
        r"(?<!\d)(\d{8})(?!\d)",
        text,
    )

    if m:

        raw = m.group(1)

        try:

            return date(
                int(raw[0:4]),
                int(raw[4:6]),
                int(raw[6:8]),
            )

        except ValueError:

            pass

    return None


# ============================================================
# 遞迴找高雄 API 資料列
# ============================================================

def recursive_find_khh_records(obj):

    records = []

    if isinstance(obj, list):

        for item in obj:

            records.extend(
                recursive_find_khh_records(
                    item
                )
            )

    elif isinstance(obj, dict):

        lower_keys = {
            str(k).lower().strip()
            for k in obj.keys()
        }

        date_like = any(
            (
                "date" in key
                or "日期" in key
                or "發布" in key
            )
            for key in lower_keys
        )

        if date_like:

            records.append(obj)

        for value in obj.values():

            if isinstance(
                value,
                (dict, list),
            ):

                records.extend(
                    recursive_find_khh_records(
                        value
                    )
                )

    return records


# ============================================================
# 從資料列找日期
# ============================================================

def find_date_in_khh_record(record):

    preferred_keys = [
        "日期",
        "停班停課日期",
        "停班日期",
        "停課日期",
        "Date",
        "date",
        "DATE",
    ]

    for preferred in preferred_keys:

        for key, value in record.items():

            if str(key).strip() == preferred:

                parsed = parse_khh_date(
                    value
                )

                if parsed:
                    return parsed

    for key, value in record.items():

        key_text = str(key)

        if any(
            word in key_text
            for word in [
                "日期",
                "Date",
                "date",
            ]
        ):

            parsed = parse_khh_date(
                value
            )

            if parsed:
                return parsed

    record_text = " ".join(
        str(v)
        for v in record.values()
    )

    return parse_khh_date(
        record_text
    )


# ============================================================
# 高雄停班停課
# ============================================================

def build_khh_events():

    print()
    print("=" * 60)
    print("[高雄] 正在取得高雄市停班停課資料")
    print("=" * 60)

    try:

        response = http_get(
            KHH_API_URL,
            timeout=30,
        )

        data = response.json()

    except Exception as exc:

        print(
            "[高雄] ⚠️ 目前無法取得停班停課資料："
            f"{exc}"
        )

        return []

    records = recursive_find_khh_records(
        data
    )

    print(
        f"[高雄] API 找到候選資料列："
        f"{len(records)} 筆"
    )

    events = []

    for record in records:

        if not isinstance(
            record,
            dict,
        ):
            continue

        record_text = " ".join(
            str(v)
            for v in record.values()
        )

        # 只收高雄

        if not any(
            keyword in record_text
            for keyword in [
                "高雄",
                "高雄市",
            ]
        ):
            continue

        # 只收停班停課

        if not any(
            keyword in record_text
            for keyword in [
                "停止上班",
                "停止上課",
                "停班",
                "停課",
            ]
        ):
            continue

        date_value = find_date_in_khh_record(
            record
        )

        if not date_value:
            continue

        events.append({
            "date": date_value,
            "summary": "高雄停班停課",
            "source": "kaohsiung",
        })

    # 去重

    unique = {}

    for event in events:

        key = (
            event["date"],
            event["summary"],
        )

        unique[key] = event

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: x["date"]
    )

    print(
        f"[統計] 高雄停班停課："
        f"{len(result)} 筆"
    )

    return result


# ============================================================
# ICS Escape
# ============================================================

def ics_escape(text):

    text = str(text)

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

    return text


# ============================================================
# ICS RFC 5545 UTF-8 折行
# ============================================================

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


# ============================================================
# UID
# ============================================================

def make_uid(
    event_date,
    summary,
):

    key = (
        f"{event_date.isoformat()}"
        f"|{summary}"
    )

    return (
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                key,
            )
        )
        + "@taiwan-calendar"
    )


# ============================================================
# 寫入 ICS
# ============================================================

def write_ics(events):

    now = datetime.now(
        timezone.utc
    )

    dtstamp = now.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Shadel11//Taiwan Calendar//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        f"X-WR-TIMEZONE:{TIMEZONE}",
    ]

    for event in sorted(
        events,
        key=lambda x: (
            x["date"],
            x["summary"],
        ),
    ):

        event_date = event["date"]
        summary = event["summary"]

        start = event_date.strftime(
            "%Y%m%d"
        )

        end = (
            event_date
            + timedelta(days=1)
        ).strftime(
            "%Y%m%d"
        )

        uid = make_uid(
            event_date,
            summary,
        )

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            f"SUMMARY:{ics_escape(summary)}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ])

    lines.append(
        "END:VCALENDAR"
    )

    folded_lines = []

    for line in lines:

        folded_lines.extend(
            fold_ics_line(line)
        )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        f.write(
            "\r\n".join(
                folded_lines
            )
        )

        f.write(
            "\r\n"
        )

    print(
        f"[ICS] ✓ 已寫入："
        f"{OUTPUT_FILE}"
    )


# ============================================================
# 主程式
# ============================================================

def main():

    print()
    print("=" * 70)
    print("台灣生活行事曆：開始更新")
    print("=" * 70)

    print(
        f"[設定] 目前年份："
        f"{CURRENT_YEAR}"
    )

    print(
        f"[設定] 更新年份："
        f"{YEARS}"
    )

    all_events = []

    # ========================================================
    # 政府假日
    # ========================================================

    for year in YEARS:

        rows = read_dgpa_csv(
            year
        )

        government_events = (
            build_government_events(
                year,
                rows,
            )
        )

        print(
            f"[統計] {year} 年政府假日事件："
            f"{len(government_events)} 筆"
        )

        all_events.extend(
            government_events
        )

    # ========================================================
    # 母親節／父親節
    # ========================================================

    family_events = []

    for year in YEARS:

        family_events.extend(
            build_family_events(
                year
            )
        )

    print(
        f"[統計] 母親節／父親節："
        f"{len(family_events)} 筆"
    )

    all_events.extend(
        family_events
    )

    # ========================================================
    # 高雄停班停課
    # ========================================================

    khh_events = build_khh_events()

    all_events.extend(
        khh_events
    )

    # ========================================================
    # 最終去重
    # ========================================================

    unique = {}

    for event in all_events:

        key = (
            event["date"],
            event["summary"],
        )

        unique[key] = event

    all_events = list(
        unique.values()
    )

    all_events.sort(
        key=lambda x: (
            x["date"],
            x["summary"],
        )
    )

    # ========================================================
    # 寫入 ICS
    # ========================================================

    write_ics(
        all_events
    )

    # ========================================================
    # 統計
    # ========================================================

    print(
        f"[統計] 總行事曆事件："
        f"{len(all_events)} 個"
    )

    print()
    print("=" * 70)
    print(
        f"[完成] ✓ 已產生："
        f"{OUTPUT_FILE}"
    )

    print(
        f"[完成] ✓ 總事件數："
        f"{len(all_events)}"
    )

    print("=" * 70)


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    main()
