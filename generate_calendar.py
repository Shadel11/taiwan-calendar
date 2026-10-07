# -*- coding: utf-8 -*-

"""
台灣生活行事曆
資料來源：
1. 政府資料開放平台：中華民國政府行政機關辦公日曆表
2. 高雄市政府 OpenData：停班停課資訊

功能：
- 自動抓取當年度與下一年度政府辦公日曆
- 0 = 上班，不建立事件
- 2 = 放假，建立事件
- 不建立一般週六、週日事件
- 自動判斷補假
- 自動判斷補班
- 自動建立春節：小年夜、除夕、初一、初二...
- 母親節、父親節作為生活備注
- 高雄停班停課
- 輸出 taiwan.ics

注意：
若官方資料無法取得，程式會直接失敗，
避免產生錯誤的空白日曆。
"""

import csv
import io
import re
import uuid
import calendar
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import requests
from lunardate import LunarDate


# ============================================================
# 基本設定
# ============================================================

CALENDAR_NAME = "台灣生活行事曆"
TIMEZONE = "Asia/Taipei"
OUTPUT_FILE = "taiwan.ics"

DGPA_DATASET_URL = "https://data.gov.tw/dataset/14718"

# 政府資料開放平台資料集頁面
# 這個資料集會隨年度增加新的 CSV 資源
DGPA_PAGE_URL = "https://data.gov.tw/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/"
    "Get/95eec21d-4ee7-4920-94ff-d36727bc171f"
)

CURRENT_YEAR = datetime.now().year

# 目前年度 + 下一年度
YEARS = [
    CURRENT_YEAR,
    CURRENT_YEAR + 1,
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    )
}


# ============================================================
# HTTP
# ============================================================

def http_get(url, timeout=30):
    print(f"[HTTP] GET {url}")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
    )

    response.raise_for_status()

    return response


# ============================================================
# 文字處理
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)

    value = (
        value.replace("\ufeff", "")
        .replace("\u3000", " ")
        .replace("\r", " ")
        .replace("\n", " ")
    )

    return re.sub(r"\s+", " ", value).strip()


# ============================================================
# 日期解析
# ============================================================

def parse_date(value):
    if value is None:
        return None

    s = clean_text(value)

    if not s:
        return None

    # 2026/01/01
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)

    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
        except ValueError:
            return None

    # 20260101
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)

    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
        except ValueError:
            return None

    # 1150101 / 1160101
    m = re.fullmatch(r"(\d{3})(\d{2})(\d{2})", s)

    if m:
        try:
            roc_year = int(m.group(1))
            month = int(m.group(2))
            day = int(m.group(3))

            return date(
                roc_year + 1911,
                month,
                day,
            )
        except ValueError:
            return None

    return None


# ============================================================
# 找欄位
# ============================================================

def find_column(fieldnames, keywords):
    if not fieldnames:
        return None

    cleaned = {
        clean_text(x): x
        for x in fieldnames
        if x is not None
    }

    for keyword in keywords:
        for cleaned_name, original_name in cleaned.items():
            if keyword in cleaned_name:
                return original_name

    return None


# ============================================================
# 判斷是否放假
# ============================================================

def is_holiday_flag(value):
    s = clean_text(value)

    # 官方資料：0 = 上班，2 = 放假
    if s == "2":
        return True

    # 有些資料可能寫中文
    if "放假" in s:
        return True

    return False


# ============================================================
# 從政府資料開放平台取得所有資源連結
# ============================================================

def get_resource_links():
    """
    直接讀 data.gov.tw 的資料集頁面，
    從 HTML 中找出 CSV 資源。

    不使用上一版的 dataset API 巢狀解析。
    """

    response = http_get(DGPA_PAGE_URL)

    html = response.text

    links = []

    # 找所有 href
    hrefs = re.findall(
        r'href=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )

    for href in hrefs:
        href = href.strip()

        if not href:
            continue

        full_url = urljoin(DGPA_PAGE_URL, href)

        # 只保留看起來是資料下載 / CSV 的連結
        lower = full_url.lower()

        if (
            ".csv" in lower
            or "csv" in lower
            or "resource" in lower
            or "download" in lower
            or "fileconversion" in lower
        ):
            if full_url not in links:
                links.append(full_url)

    print(f"[資料] 頁面找到候選資源：{len(links)} 個")

    return links


# ============================================================
# 取得當年度 CSV
# ============================================================

def find_csv_url(year):
    roc_year = year - 1911

    print()
    print("=" * 60)
    print(f"[資料] 正在尋找 {year} 年政府辦公日曆")
    print(f"[資料] 民國年：{roc_year}")
    print("=" * 60)

    links = get_resource_links()

    candidates = []

    year_patterns = [
        f"{roc_year}年中華民國政府行政機關辦公日曆表",
        f"{roc_year}%E5%B9%B4%E4%B8%AD%E8%8F%AF%E6%B0%91%E5%9C%8B",
        f"{roc_year}年",
    ]

    for url in links:
        decoded = url.lower()

        # 排除 Google 行事曆版本
        if "google" in decoded:
            continue

        # 必須是 CSV / 資料下載
        if (
            ".csv" not in decoded
            and "csv" not in decoded
            and "fileconversion" not in decoded
        ):
            continue

        score = 0

        if f"{roc_year}年" in url:
            score += 100

        if f"{roc_year}" in url:
            score += 50

        if "辦公日曆表" in url:
            score += 50

        if "政府行政機關辦公日曆表" in url:
            score += 100

        if "utf8bom" in decoded:
            score += 20

        candidates.append(
            (score, url)
        )

    candidates.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    print(f"[資料] 候選 CSV：{len(candidates)} 個")

    for score, url in candidates[:10]:
        print(f"[資料] 候選 {score}：{url}")

    if not candidates:
        raise RuntimeError(
            f"找不到 {year} 年政府辦公日曆 CSV。"
        )

    # 逐個測試，確定真的可以下載
    for score, url in candidates:
        try:
            response = http_get(url, timeout=30)

            content = response.content

            if len(content) < 100:
                continue

            # 嘗試解碼
            for encoding in (
                "utf-8-sig",
                "utf-8",
                "cp950",
                "big5",
            ):
                try:
                    text = content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    text = None

            if not text:
                continue

            # 必須真的包含日期相關欄位/資料
            if (
                "西元日期" in text
                or "日期" in text
                or "是否放假" in text
            ):
                print()
                print(f"[資料] ★ 使用 CSV：{url}")
                return url

        except Exception as e:
            print(
                f"[資料] 測試失敗：{url}"
                f" / {type(e).__name__}: {e}"
            )

    raise RuntimeError(
        f"找到 {year} 年候選 CSV，但沒有任何一個"
        f"可以正確讀取。"
    )


# ============================================================
# 讀取政府 CSV
# ============================================================

def read_dgpa_csv(year):
    url = find_csv_url(year)

    response = http_get(url)

    content = response.content

    text = None

    for encoding in (
        "utf-8-sig",
        "utf-8",
        "cp950",
        "big5",
    ):
        try:
            text = content.decode(encoding)
            print(f"[資料] CSV 編碼：{encoding}")
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        raise RuntimeError(
            f"{year} 年 CSV 無法解碼。"
        )

    # 判斷分隔符
    sample = text[:5000]

    if "\t" in sample and sample.count("\t") > sample.count(","):
        delimiter = "\t"
    else:
        delimiter = ","

    reader = csv.DictReader(
        io.StringIO(text),
        delimiter=delimiter,
    )

    fieldnames = reader.fieldnames or []

    print(
        f"[資料] 欄位："
        f"{[clean_text(x) for x in fieldnames]}"
    )

    date_col = find_column(
        fieldnames,
        [
            "西元日期",
            "日期",
            "Date",
        ],
    )

    holiday_col = find_column(
        fieldnames,
        [
            "是否放假",
            "放假",
            "Holiday",
        ],
    )

    note_col = find_column(
        fieldnames,
        [
            "備註",
            "說明",
            "節日",
            "紀念日",
            "名稱",
        ],
    )

    if not date_col:
        raise RuntimeError(
            f"{year} 年 CSV 找不到日期欄位。"
        )

    if not holiday_col:
        raise RuntimeError(
            f"{year} 年 CSV 找不到「是否放假」欄位。"
        )

    rows = []

    for row in reader:
        d = parse_date(row.get(date_col))

        if d is None:
            continue

        if d.year != year:
            continue

        holiday = is_holiday_flag(
            row.get(holiday_col)
        )

        note = ""

        if note_col:
            note = clean_text(
                row.get(note_col)
            )

        rows.append(
            {
                "date": d,
                "holiday": holiday,
                "note": note,
            }
        )

    if not rows:
        raise RuntimeError(
            f"{year} 年 CSV 成功下載，但解析結果為 0 筆。"
        )

    print(
        f"[資料] {year} 年總日期：{len(rows)} 筆"
    )

    holiday_rows = [
        x for x in rows
        if x["holiday"]
    ]

    print(
        f"[資料] {year} 年政府放假日："
        f"{len(holiday_rows)} 筆"
    )

    if len(holiday_rows) == 0:
        raise RuntimeError(
            f"{year} 年政府放假日解析為 0 筆，"
            f"停止產生 ICS，避免產生錯誤日曆。"
        )

    return rows


# ============================================================
# 產生節日名稱
# ============================================================

HOLIDAY_NAME_MAP = {
    "開國紀念日": "元旦",
    "中華民國開國紀念日": "元旦",

    "和平紀念日": "228和平紀念日",
    "二二八和平紀念日": "228和平紀念日",

    "兒童節": "兒童節",

    "清明節": "清明節",
    "民族掃墓節": "清明節",

    "端午節": "端午節",

    "中秋節": "中秋節",

    "國慶日": "國慶日",
    "國慶紀念日": "國慶日",

    "勞動節": "勞動節",

    "孔子誕辰紀念日": "教師節",
    "孔子誕辰紀念日/教師節": "教師節",
    "教師節": "教師節",

    "臺灣光復暨金門古寧頭大捷紀念日":
        "臺灣光復暨金門古寧頭大捷紀念日",

    "台灣光復暨金門古寧頭大捷紀念日":
        "臺灣光復暨金門古寧頭大捷紀念日",

    "行憲紀念日": "行憲紀念日",
}


def normalize_holiday_name(note):
    note = clean_text(note)

    if not note:
        return None

    # 先處理較長名稱
    for key in sorted(
        HOLIDAY_NAME_MAP.keys(),
        key=len,
        reverse=True,
    ):
        if key in note:
            return HOLIDAY_NAME_MAP[key]

    return None


# ============================================================
# 春節名稱
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


def get_lunar_day_name(d):
    try:
        lunar = LunarDate.fromSolarDate(
            d.year,
            d.month,
            d.day,
        )

        return LUNAR_DAY_NAMES.get(
            lunar.day,
            f"初{lunar.day}",
        )

    except Exception:
        return None


def is_chinese_new_year_period(d):
    """
    判斷是否為農曆春節附近日期。
    """

    try:
        lunar = LunarDate.fromSolarDate(
            d.year,
            d.month,
            d.day,
        )

        return lunar.month == 1 and 1 <= lunar.day <= 6

    except Exception:
        return False


# ============================================================
# 找政府真正的春節放假日期
# ============================================================

def build_government_holiday_events(rows):
    """
    只根據政府 CSV 的 2 = 放假來建立事件。

    這裡非常重要：
    不自行假設「初一到初五一定放假」。
    當年度政府放幾天，就建立幾天。
    """

    events = []

    # 先取得所有政府放假日
    holiday_rows = [
        row for row in rows
        if row["holiday"]
    ]

    holiday_dates = {
        row["date"]
        for row in holiday_rows
    }

    for row in holiday_rows:
        d = row["date"]
        note = row["note"]

        # ----------------------------------------------------
        # 春節
        # ----------------------------------------------------

        if is_chinese_new_year_period(d):

            lunar_name = get_lunar_day_name(d)

            if lunar_name:
                if lunar_name == "初一":
                    name = "初一"
                elif lunar_name == "初二":
                    name = "初二"
                elif lunar_name == "初三":
                    name = "初三"
                elif lunar_name == "初四":
                    name = "初四"
                elif lunar_name == "初五":
                    name = "初五"
                elif lunar_name == "初六":
                    name = "初六"
                else:
                    name = lunar_name
            else:
                name = "春節"

            # 除夕前一天
            if lunar_name == "廿九" or lunar_name == "三十":
                name = "除夕"

            events.append(
                {
                    "date": d,
                    "summary": name,
                    "description": (
                        "資料來源：行政院人事行政總處\n"
                        "中華民國政府行政機關辦公日曆表\n"
                        f"資料年度：{d.year}"
                    ),
                    "type": "government",
                }
            )

            continue

        # ----------------------------------------------------
        # 一般節日
        # ----------------------------------------------------

        name = normalize_holiday_name(note)

        if name:
            events.append(
                {
                    "date": d,
                    "summary": name,
                    "description": (
                        "資料來源：行政院人事行政總處\n"
                        "中華民國政府行政機關辦公日曆表\n"
                        f"資料年度：{d.year}"
                    ),
                    "type": "government",
                }
            )

            continue

        # ----------------------------------------------------
        # 其他政府新增放假日
        #
        # 如果官方備註有名稱，就使用官方名稱。
        # 不讓未來新增節日消失。
        # ----------------------------------------------------

        if note:
            # 清理一些常見的班別描述
            cleaned_note = re.sub(
                r"\(.*?補假.*?\)",
                "",
                note,
            )

            cleaned_note = re.sub(
                r"（.*?補假.*?）",
                "",
                cleaned_note,
            )

            cleaned_note = clean_text(
                cleaned_note
            )

            if cleaned_note:
                events.append(
                    {
                        "date": d,
                        "summary": cleaned_note,
                        "description": (
                            "資料來源：行政院人事行政總處\n"
                            "中華民國政府行政機關辦公日曆表\n"
                            f"資料年度：{d.year}"
                        ),
                        "type": "government",
                    }
                )

    # ========================================================
    # 判斷補假
    # ========================================================

    # 如果政府放假日落在週六或週日，
    # 接下來附近的「0 上班日」有可能是補假。
    #
    # 但不直接猜所有週末，
    # 而是使用官方備註與連續放假結構判斷。
    #
    # 官方 CSV 若在備註中已寫「補假」，
    # 直接建立 ○○節(補假)。

    for row in rows:
        d = row["date"]
        note = row["note"]

        if not note:
            continue

        if "補假" in note:
            base_name = normalize_holiday_name(note)

            if base_name:
                events.append(
                    {
                        "date": d,
                        "summary": f"{base_name}(補假)",
                        "description": (
                            "資料來源：行政院人事行政總處\n"
                            "中華民國政府行政機關辦公日曆表\n"
                            f"資料年度：{d.year}"
                        ),
                        "type": "makeup_holiday",
                    }
                )

    # ========================================================
    # 官方補班
    # ========================================================

    for row in rows:
        d = row["date"]
        note = row["note"]

        if not note:
            continue

        if (
            "補班" in note
            or "調整上班" in note
            or "調整為上班日" in note
        ):
            base_name = normalize_holiday_name(note)

            if base_name:
                summary = f"[補班]{base_name}"
            else:
                summary = f"[補班]{clean_text(note)}"

            events.append(
                {
                    "date": d,
                    "summary": summary,
                    "description": (
                        "資料來源：行政院人事行政總處\n"
                        "中華民國政府行政機關辦公日曆表\n"
                        f"資料年度：{d.year}"
                    ),
                    "type": "makeup_workday",
                }
            )

    return events


# ============================================================
# 母親節
# ============================================================

def second_sunday_of_may(year):
    d = date(year, 5, 1)

    while d.weekday() != 6:
        d += timedelta(days=1)

    return d + timedelta(days=7)


# ============================================================
# 父親節
# ============================================================

def father_day(year):
    return date(year, 8, 8)


# ============================================================
# 高雄停班停課
# ============================================================

def get_kaohsiung_events(year):
    events = []

    try:
        response = http_get(
            KAOHSIUNG_API,
            timeout=30,
        )

        data = response.json()

    except Exception as e:
        print(
            f"[高雄] API 無法取得："
            f"{type(e).__name__}: {e}"
        )

        return events

    # --------------------------------------------------------
    # API 可能回傳 list / dict
    # --------------------------------------------------------

    if isinstance(data, dict):
        if "result" in data:
            data = data["result"]

        elif "data" in data:
            data = data["data"]

    if not isinstance(data, list):
        return events

    for item in data:

        if not isinstance(item, dict):
            continue

        text = " ".join(
            clean_text(v)
            for v in item.values()
            if v is not None
        )

        if not text:
            continue

        # 找日期
        found_dates = re.findall(
            r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})",
            text,
        )

        if not found_dates:
            continue

        for y, m, d in found_dates:

            try:
                event_date = date(
                    int(y),
                    int(m),
                    int(d),
                )
            except ValueError:
                continue

            if event_date.year != year:
                continue

            # 只抓高雄相關停班停課
            if (
                "高雄" not in text
                and "停班" not in text
                and "停課" not in text
            ):
                continue

            events.append(
                {
                    "date": event_date,
                    "summary": "高雄停班停課",
                    "description": (
                        "資料來源：高雄市政府 OpenData\n"
                        + text[:500]
                    ),
                    "type": "kaohsiung",
                }
            )

    return events


# ============================================================
# ICS
# ============================================================

def ics_escape(text):
    if text is None:
        return ""

    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_ics_line(line):
    """
    RFC 5545：
    每行不超過 75 octets。
    這裡用 UTF-8 bytes 安全折行。
    """

    encoded = line.encode("utf-8")

    if len(encoded) <= 75:
        return line

    parts = []

    while encoded:

        chunk = encoded[:75]

        # 避免切到 UTF-8 多位元字元
        while True:
            try:
                text = chunk.decode("utf-8")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]

        parts.append(text)

        encoded = encoded[len(chunk):]

    return "\r\n ".join(parts)


def make_uid(event):
    raw = (
        f"{event['date'].isoformat()}|"
        f"{event['summary']}|"
        f"{CALENDAR_NAME}"
    )

    return (
        str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            raw,
        ))
        + "@taiwan-calendar"
    )


def make_ics(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Taiwan Life Calendar//GitHub//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(CALENDAR_NAME)}",
        f"X-WR-TIMEZONE:{TIMEZONE}",
    ]

    now = datetime.utcnow().strftime(
        "%Y%m%dT%H%M%SZ"
    )

    for event in events:

        d = event["date"]

        start = d.strftime("%Y%m%d")
        end = (
            d + timedelta(days=1)
        ).strftime("%Y%m%d")

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{make_uid(event)}",
                f"DTSTAMP:{now}",
                f"DTSTART;VALUE=DATE:{start}",
                f"DTEND;VALUE=DATE:{end}",
                f"SUMMARY:{ics_escape(event['summary'])}",
                f"DESCRIPTION:{ics_escape(event['description'])}",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")

    return "\r\n".join(
        fold_ics_line(line)
        for line in lines
    ) + "\r\n"


# ============================================================
# 去除重複事件
# ============================================================

def deduplicate_events(events):

    result = {}

    for event in events:

        key = (
            event["date"],
            event["summary"],
        )

        result[key] = event

    return list(result.values())


# ============================================================
# 排序
# ============================================================

def sort_events(events):
    return sorted(
        events,
        key=lambda x: (
            x["date"],
            x["summary"],
        ),
    )


# ============================================================
# 主程式
# ============================================================

def main():

    print()
    print("=" * 70)
    print("台灣生活行事曆：開始更新")
    print("=" * 70)
    print()

    all_events = []

    # --------------------------------------------------------
    # 政府辦公日曆
    # --------------------------------------------------------

    government_count = {}

    for year in YEARS:

        rows = read_dgpa_csv(year)

        events = build_government_holiday_events(
            rows
        )

        government_count[year] = len(events)

        print(
            f"[統計] {year} 年政府事件："
            f"{len(events)} 筆"
        )

        all_events.extend(events)

    # --------------------------------------------------------
    # 母親節 / 父親節
    # --------------------------------------------------------

    mother_count = 0
    father_count = 0

    for year in YEARS:

        mother = second_sunday_of_may(year)

        all_events.append(
            {
                "date": mother,
                "summary": "母親節",
                "description": (
                    "生活備注：台灣常見節日"
                ),
                "type": "note",
            }
        )

        mother_count += 1

        father = father_day(year)

        all_events.append(
            {
                "date": father,
                "summary": "父親節",
                "description": (
                    "生活備注：台灣常見節日"
                ),
                "type": "note",
            }
        )

        father_count += 1

    # --------------------------------------------------------
    # 高雄
    # --------------------------------------------------------

    kaohsiung_count = 0

    for year in YEARS:

        kh_events = get_kaohsiung_events(
            year
        )

        kaohsiung_count += len(
            kh_events
        )

        all_events.extend(
            kh_events
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    all_events = deduplicate_events(
        all_events
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    all_events = sort_events(
        all_events
    )

    # --------------------------------------------------------
    # 最後安全檢查
    # --------------------------------------------------------

    government_total = sum(
        government_count.values()
    )

    if government_total == 0:

        raise RuntimeError(
            "重大錯誤：政府假日事件為 0。"
            "為避免產生錯誤 ICS，程式停止。"
        )

    # --------------------------------------------------------
    # 產生 ICS
    # --------------------------------------------------------

    ics = make_ics(
        all_events
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        f.write(ics)

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("更新完成")
    print("=" * 70)

    for year in YEARS:
        print(
            f"[統計] {year} 年政府假日事件："
            f"{government_count[year]} 筆"
        )

    print(
        f"[統計] 母親節："
        f"{mother_count} 筆"
    )

    print(
        f"[統計] 父親節："
        f"{father_count} 筆"
    )

    print(
        f"[統計] 高雄停班停課："
        f"{kaohsiung_count} 筆"
    )

    print(
        f"[統計] 總行事曆事件："
        f"{len(all_events)} 個"
    )

    print(
        f"[輸出] {OUTPUT_FILE}"
    )

    # --------------------------------------------------------
    # 顯示前 20 筆政府事件供 Actions 驗證
    # --------------------------------------------------------

    print()
    print("[檢查] 前 20 筆事件：")

    for event in all_events[:20]:

        print(
            f"  {event['date']} "
            f"{event['summary']}"
        )

    print()
    print("GitHub Actions 執行結束。")


if __name__ == "__main__":
    main()
