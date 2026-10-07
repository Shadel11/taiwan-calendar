# -*- coding: utf-8 -*-

"""
台灣生活行事曆
資料來源：
1. 行政院人事行政總處－中華民國政府行政機關辦公日曆表
2. 高雄市政府公開資料 API－停班停課
3. lunardate－農曆日期轉換

功能：
- 自動抓取當年度＋下一年度政府辦公日曆
- 普通週六、週日不建立事件
- 國定假日即使落在週末仍保留
- 補假自動標示：○○節(補假)
- 補班自動標示：[補班]○○節
- 春節依政府實際放假日期標示：
  小年夜、除夕、初一、初二……
- 母親節、父親節作為備注事件
- 高雄停班停課
- 不使用 Emoji
"""

import csv
import io
import json
import re
import ssl
import time
import uuid
import urllib.request
from datetime import date, datetime, timedelta

from lunardate import LunarDate


# =========================================================
# 基本設定
# =========================================================

CALENDAR_NAME = "台灣生活行事曆"

DGPA_DATASET_URL = "https://data.gov.tw/dataset/14718"

DGPA_API_URL = "https://data.gov.tw/api/v2/rest/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/Get/"
    "95eec21d-4ee7-4920-94ff-d36727bc171f"
)

OUTPUT_FILE = "taiwan.ics"

CURRENT_YEAR = datetime.now().year
YEARS = [CURRENT_YEAR, CURRENT_YEAR + 1]


# =========================================================
# HTTP
# =========================================================

SSL_CONTEXT = ssl.create_default_context()


def http_get(url, timeout=40):
    """
    下載文字內容。
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Taiwan Life Calendar GitHub Actions)"
            )
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=SSL_CONTEXT,
    ) as response:

        raw = response.read()

        # 優先 UTF-8，其次 Big5
        for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                pass

        return raw.decode("utf-8", errors="replace")


def http_get_json(url, timeout=40):
    text = http_get(url, timeout=timeout)
    return json.loads(text)


# =========================================================
# 工具
# =========================================================

def normalize_text(value):
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\ufeff", "")
    text = text.replace("\r", "")
    text = text.replace("\n", " ")
    return text.strip()


def parse_date(value):
    """
    支援：
    20260101
    2026/01/01
    2026-01-01
    """
    text = normalize_text(value)

    digits = re.sub(r"[^0-9]", "", text)

    if len(digits) == 8:
        try:
            return datetime.strptime(
                digits,
                "%Y%m%d",
            ).date()
        except ValueError:
            pass

    return None


def escape_ics(text):
    """
    RFC 5545 基本文字跳脫。
    """
    text = str(text)

    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\\n")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "\\n")

    return text


def fold_ics_line(line, limit=75):
    """
    RFC 5545 line folding。
    """
    result = []

    while len(line) > limit:
        result.append(line[:limit])
        line = " " + line[limit:]

    result.append(line)

    return "\r\n".join(result)


# =========================================================
# 政府辦公日曆資料
# =========================================================

def get_dataset_resources():
    """
    從 data.gov.tw dataset API 找到政府辦公日曆 CSV。
    """
    data = http_get_json(DGPA_API_URL)

    resources = []

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():

                key_lower = str(key).lower()

                if key_lower in {
                    "resourcedownloadurl",
                    "resourceurl",
                    "downloadurl",
                }:
                    if isinstance(item, str):
                        resources.append(item)

                walk(item)

        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)

    # 去除重複
    result = []

    for url in resources:
        if url not in result:
            result.append(url)

    return result


def find_csv_url(year):
    """
    找指定年份的政府辦公日曆 CSV。

    116年 = 2027
    115年 = 2026
    """
    roc_year = year - 1911

    resources = get_dataset_resources()

    candidates = []

    for url in resources:

        decoded = url.lower()

        # CSV
        if ".csv" not in decoded:
            continue

        # 優先找年份
        year_match = (
            f"{roc_year}年",
            f"{roc_year}%e5%b9%b4",
            f"{roc_year}%e5%b9%b4",
            f"{roc_year}",
        )

        score = 0

        for item in year_match:
            if item.lower() in decoded:
                score += 10

        # Google 行事曆版本也可以，但一般 CSV 優先
        if "google" in decoded:
            score -= 2

        candidates.append((score, url))

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    return candidates[0][1]


def parse_dgpa_csv(text, year):
    """
    解析政府辦公日曆 CSV。

    政府資料主要欄位：
    西元日期
    星期
    是否放假
    備註
    """

    # 先判斷 delimiter
    sample = text[:5000]

    if "\t" in sample:
        delimiter = "\t"
    else:
        delimiter = ","

    reader = csv.reader(
        io.StringIO(text),
        delimiter=delimiter,
    )

    rows = list(reader)

    if not rows:
        return []

    # 找真正資料列
    parsed = []

    for row in rows:

        if not row:
            continue

        row = [
            normalize_text(x)
            for x in row
        ]

        # 找日期
        parsed_date = None

        date_index = None

        for index, value in enumerate(row):

            d = parse_date(value)

            if d is not None:
                if d.year == year:
                    parsed_date = d
                    date_index = index
                    break

        if parsed_date is None:
            continue

        # 找「是否放假」
        holiday_flag = ""

        # 通常日期後面會有星期、是否放假
        for value in row[date_index + 1:]:

            clean = normalize_text(value)

            if clean in {"0", "2"}:
                holiday_flag = clean
                break

        # 如果沒有找到，嘗試整列找
        if holiday_flag == "":
            for value in row:
                if normalize_text(value) in {"0", "2"}:
                    holiday_flag = normalize_text(value)

        # 找備註
        note_parts = []

        for value in row:
            clean = normalize_text(value)

            if not clean:
                continue

            if clean in {
                "0",
                "2",
                "六",
                "日",
                "一",
                "二",
                "三",
                "四",
                "五",
            }:
                continue

            if parse_date(clean) is not None:
                continue

            note_parts.append(clean)

        note = " ".join(note_parts)

        parsed.append(
            {
                "date": parsed_date,
                "is_holiday": holiday_flag == "2",
                "note": note,
                "raw": row,
            }
        )

    # 去除重複日期
    unique = {}

    for item in parsed:
        unique[item["date"]] = item

    return sorted(
        unique.values(),
        key=lambda x: x["date"],
    )


def load_year_calendar(year):
    print(f"\n[資料] 正在取得 {year} 年政府辦公日曆...")

    url = find_csv_url(year)

    if not url:
        raise RuntimeError(
            f"找不到 {year} 年政府辦公日曆 CSV"
        )

    print(f"[資料] CSV：{url}")

    text = http_get(url)

    rows = parse_dgpa_csv(
        text,
        year,
    )

    if not rows:
        raise RuntimeError(
            f"{year} 年 CSV 無法解析"
        )

    print(
        f"[資料] {year} 年共解析 {len(rows)} 天"
    )

    return rows


# =========================================================
# 節日名稱
# =========================================================

HOLIDAY_KEYWORDS = {
    "元旦": [
        "元旦",
        "開國紀念日",
    ],

    "228和平紀念日": [
        "228",
        "二二八",
        "和平紀念日",
    ],

    "兒童節": [
        "兒童節",
    ],

    "清明節": [
        "清明節",
        "民族掃墓節",
    ],

    "端午節": [
        "端午節",
    ],

    "中秋節": [
        "中秋節",
    ],

    "國慶日": [
        "國慶日",
        "國慶",
        "國民體育日",
    ],

    "和平紀念日": [
        "和平紀念日",
    ],

    "教師節": [
        "教師節",
    ],

    "臺灣光復暨金門古寧頭大捷紀念日": [
        "臺灣光復",
        "台灣光復",
        "古寧頭",
    ],

    "行憲紀念日": [
        "行憲紀念日",
    ],

    "小年夜": [
        "小年夜",
    ],

    "除夕": [
        "除夕",
    ],
}


def clean_official_note(note):
    """
    清理政府 CSV 備註。
    """
    note = normalize_text(note)

    # 移除一些純日期/格式資訊
    note = re.sub(
        r"\d{4}/\d{1,2}/\d{1,2}",
        "",
        note,
    )

    note = re.sub(
        r"\d{4}-\d{1,2}-\d{1,2}",
        "",
        note,
    )

    return note.strip()


def contains_any(text, keywords):
    text = normalize_text(text)

    return any(
        keyword in text
        for keyword in keywords
    )


def identify_holiday_name(note):
    """
    從政府備註判斷一般節日名稱。
    """
    note = clean_official_note(note)

    # 春節先由農曆邏輯處理
    spring_keywords = [
        "春節",
        "農曆春節",
        "農曆年",
        "春節期間",
        "小年夜",
        "除夕",
    ]

    if contains_any(
        note,
        spring_keywords,
    ):
        return None

    # 特殊節日順序
    for name, keywords in HOLIDAY_KEYWORDS.items():

        if name in {
            "小年夜",
            "除夕",
            "和平紀念日",
        }:
            continue

        if contains_any(
            note,
            keywords,
        ):
            return name

    return None


# =========================================================
# 農曆
# =========================================================

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


def lunar_info(gregorian_date):
    """
    取得農曆年月日。
    """
    lunar = LunarDate.fromSolarDate(
        gregorian_date.year,
        gregorian_date.month,
        gregorian_date.day,
    )

    return (
        lunar.year,
        lunar.month,
        lunar.day,
    )


def find_lunar_new_year(year):
    """
    找出該西元年度內的農曆正月初一。
    """
    start = date(year, 1, 1)
    end = date(year, 3, 15)

    current = start

    while current <= end:

        lunar_year, lunar_month, lunar_day = (
            lunar_info(current)
        )

        if lunar_month == 1 and lunar_day == 1:
            return current

        current += timedelta(days=1)

    return None


def spring_festival_names(year):
    """
    找出春節相關日期。

    注意：
    這裡只建立「政府 CSV 明確列為放假」的日期，
    不會自己硬編初一到初五全部放假。

    小年夜 = 除夕前一天
    除夕 = 初一前一天
    """
    new_year = find_lunar_new_year(year)

    if not new_year:
        return {}

    result = {}

    result[new_year - timedelta(days=2)] = "小年夜"
    result[new_year - timedelta(days=1)] = "除夕"

    # 初一開始最多往後 15 天，
    # 實際是否建立事件由政府放假資料決定。
    for offset in range(0, 15):

        d = new_year + timedelta(days=offset)

        _, month, lunar_day = lunar_info(d)

        if month != 1:
            continue

        if lunar_day in LUNAR_DAY_NAMES:
            result[d] = LUNAR_DAY_NAMES[lunar_day]

    return result


# =========================================================
# 補假 / 補班
# =========================================================

def is_weekend(d):
    return d.weekday() >= 5


def find_nearby_holiday_name(
    current_date,
    holiday_map,
):
    """
    找補班日期附近的節日名稱。
    """
    for distance in range(1, 8):

        for delta in (
            -distance,
            distance,
        ):

            candidate = (
                current_date
                + timedelta(days=delta)
            )

            if candidate in holiday_map:

                name = holiday_map[candidate]

                if name:
                    return name

    return "國定假日"


def detect_makeup_workday(
    item,
    holiday_map,
):
    """
    判斷政府 CSV 中的補班日。

    是否放假 = 0
    且備註明確出現：
    補班 / 補行上班 / 調整上班
    """
    if item["is_holiday"]:
        return None

    note = clean_official_note(
        item["note"]
    )

    if not contains_any(
        note,
        [
            "補班",
            "補行上班",
            "補上班",
            "調整上班",
        ],
    ):
        return None

    holiday_name = find_nearby_holiday_name(
        item["date"],
        holiday_map,
    )

    return f"[補班]{holiday_name}"


def is_makeup_holiday_note(note):
    note = clean_official_note(note)

    return contains_any(
        note,
        [
            "補假",
            "補休",
            "調整放假",
        ],
    )


# =========================================================
# 事件建立
# =========================================================

def build_events_for_year(year, rows):
    """
    將政府辦公日曆轉成我們自己的事件。
    """

    events = []

    spring_map = spring_festival_names(
        year
    )

    # -----------------------------------------------------
    # 第一階段：
    # 建立「真正節日」日期對照表
    # -----------------------------------------------------

    holiday_map = {}

    for item in rows:

        d = item["date"]

        if not item["is_holiday"]:
            continue

        note = clean_official_note(
            item["note"]
        )

        # 普通週末：
        # 沒有節日名稱、沒有補假意義 → 不進事件
        name = identify_holiday_name(
            note
        )

        # 春節
        if d in spring_map:
            name = spring_map[d]

        if name:
            holiday_map[d] = name

    # -----------------------------------------------------
    # 第二階段：
    # 建立事件
    # -----------------------------------------------------

    for item in rows:

        d = item["date"]

        note = clean_official_note(
            item["note"]
        )

        # ---------------------------------------------
        # A. 放假日
        # ---------------------------------------------

        if item["is_holiday"]:

            name = holiday_map.get(d)

            # 補假
            if is_makeup_holiday_note(note):

                # 如果本身已經有節日名稱
                if name:
                    name = f"{name}(補假)"

                else:
                    # 找附近原始節日
                    original = find_nearby_holiday_name(
                        d,
                        holiday_map,
                    )

                    name = f"{original}(補假)"

            # 春節
            elif d in spring_map:

                name = spring_map[d]

            # 一般節日
            elif name:
                pass

            # -----------------------------------------
            # 最重要：
            # 沒有節日意義的普通週末直接跳過
            # -----------------------------------------
            else:
                continue

            events.append(
                {
                    "date": d,
                    "summary": name,
                    "description": (
                        "資料來源：行政院人事行政總處\n"
                        "中華民國政府行政機關辦公日曆表\n"
                        f"資料年度：{year}"
                    ),
                }
            )

            continue

        # ---------------------------------------------
        # B. 補班
        # ---------------------------------------------

        makeup_name = detect_makeup_workday(
            item,
            holiday_map,
        )

        if makeup_name:

            events.append(
                {
                    "date": d,
                    "summary": makeup_name,
                    "description": (
                        "資料來源：行政院人事行政總處\n"
                        "中華民國政府行政機關辦公日曆表\n"
                        f"資料年度：{year}"
                    ),
                }
            )

    return events


# =========================================================
# 母親節 / 父親節
# =========================================================

def nth_weekday_of_month(
    year,
    month,
    weekday,
    n,
):
    """
    weekday:
    Monday=0
    Sunday=6
    """
    d = date(
        year,
        month,
        1,
    )

    count = 0

    while d.month == month:

        if d.weekday() == weekday:

            count += 1

            if count == n:
                return d

        d += timedelta(days=1)

    return None


def build_family_days(year):
    """
    台灣常見節慶備注：

    母親節：5 月第二個星期日
    父親節：8 月 8 日
    """

    events = []

    mothers_day = nth_weekday_of_month(
        year,
        5,
        6,
        2,
    )

    if mothers_day:
        events.append(
            {
                "date": mothers_day,
                "summary": "母親節",
                "description": (
                    "備注：台灣常見節慶，非政府行政機關國定假日"
                ),
            }
        )

    fathers_day = date(
        year,
        8,
        8,
    )

    events.append(
        {
            "date": fathers_day,
            "summary": "父親節",
            "description": (
                "備注：台灣常見節慶，非政府行政機關國定假日"
            ),
        }
    )

    return events


# =========================================================
# 高雄停班停課
# =========================================================

def build_kaohsiung_events():
    """
    高雄市停班停課 API。

    API 若暫時無法連線，不影響國定假日產生。
    """

    events = []

    for attempt in range(2):

        try:

            print(
                f"[高雄] 嘗試取得停班停課資料 "
                f"({attempt + 1}/2)"
            )

            data = http_get_json(
                KAOHSIUNG_API,
                timeout=30,
            )

            # API 格式可能改變，所以遞迴尋找資料
            records = []

            def walk(value):

                if isinstance(value, list):

                    for item in value:

                        if isinstance(item, dict):
                            records.append(item)

                        walk(item)

                elif isinstance(value, dict):

                    for item in value.values():
                        walk(item)

            walk(data)

            for record in records:

                text = " ".join(
                    normalize_text(v)
                    for v in record.values()
                )

                if "停班" not in text:
                    continue

                if "高雄" not in text:
                    continue

                # 找日期
                event_date = None

                for value in record.values():

                    event_date = parse_date(
                        value
                    )

                    if event_date:
                        break

                if not event_date:
                    continue

                events.append(
                    {
                        "date": event_date,
                        "summary": "高雄市停班停課",
                        "description": (
                            "資料來源：高雄市政府公開資料"
                        ),
                    }
                )

            print(
                f"[高雄] 成功取得 {len(events)} 筆"
            )

            return events

        except Exception as e:

            print(
                f"[高雄] 取得失敗：{e}"
            )

            time.sleep(2)

    print(
        "[高雄] 本次無法取得資料，略過。"
    )

    return events


# =========================================================
# ICS
# =========================================================

def make_uid(event):
    raw = (
        f"{event['date'].isoformat()}|"
        f"{event['summary']}"
    )

    return (
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                raw,
            )
        )
        + "@taiwan-calendar"
    )


def make_ics_event(event):
    d = event["date"]

    next_day = d + timedelta(days=1)

    lines = [
        "BEGIN:VEVENT",
        f"UID:{make_uid(event)}",
        f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{next_day.strftime('%Y%m%d')}",
        f"SUMMARY:{escape_ics(event['summary'])}",
        f"DESCRIPTION:{escape_ics(event['description'])}",
        "END:VEVENT",
    ]

    return "\r\n".join(
        fold_ics_line(line)
        for line in lines
    )


def build_ics(events):
    """
    建立完整 ICS。
    """

    # 日期 + 名稱去重
    unique = {}

    for event in events:

        key = (
            event["date"],
            event["summary"],
        )

        unique[key] = event

    events = sorted(
        unique.values(),
        key=lambda x: (
            x["date"],
            x["summary"],
        ),
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Taiwan Life Calendar//GitHub//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_ics(CALENDAR_NAME)}",
        "X-WR-TIMEZONE:Asia/Taipei",
    ]

    for event in events:
        lines.append(
            make_ics_event(event)
        )

    lines.append(
        "END:VCALENDAR"
    )

    return "\r\n".join(lines) + "\r\n"


# =========================================================
# 主程式
# =========================================================

def main():

    print("=" * 60)
    print("台灣生活行事曆開始產生")
    print("=" * 60)

    all_events = []

    # -----------------------------------------------------
    # 政府國定假日
    # -----------------------------------------------------

    year_counts = {}

    for year in YEARS:

        try:

            rows = load_year_calendar(
                year
            )

            events = build_events_for_year(
                year,
                rows,
            )

            all_events.extend(
                events
            )

            year_counts[year] = len(
                events
            )

        except Exception as e:

            print(
                f"[錯誤] {year} 年資料處理失敗：{e}"
            )

    # -----------------------------------------------------
    # 母親節 / 父親節
    # -----------------------------------------------------

    family_events = []

    for year in YEARS:

        family_events.extend(
            build_family_days(year)
        )

    all_events.extend(
        family_events
    )

    # -----------------------------------------------------
    # 高雄停班停課
    # -----------------------------------------------------

    khh_events = build_kaohsiung_events()

    # 只保留目前＋下一年度
    khh_events = [
        event
        for event in khh_events
        if event["date"].year in YEARS
    ]

    all_events.extend(
        khh_events
    )

    # -----------------------------------------------------
    # 寫出 ICS
    # -----------------------------------------------------

    ics = build_ics(
        all_events
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        f.write(ics)

    # -----------------------------------------------------
    # 統計
    # -----------------------------------------------------

    print("\n" + "=" * 60)
    print("產生完成")
    print("=" * 60)

    for year in YEARS:

        print(
            f"[統計] {year} 年政府假日事件："
            f"{year_counts.get(year, 0)} 筆"
        )

    print(
        f"[統計] 母親節："
        f"{sum(1 for e in family_events if e['summary'] == '母親節')} 筆"
    )

    print(
        f"[統計] 父親節："
        f"{sum(1 for e in family_events if e['summary'] == '父親節')} 筆"
    )

    print(
        f"[統計] 高雄停班停課："
        f"{len(khh_events)} 筆"
    )

    print(
        f"[統計] 總行事曆事件："
        f"{len(set((e['date'], e['summary']) for e in all_events))} 個"
    )

    print("\n[檔案]")
    print(OUTPUT_FILE)

    print("\n[前 40 筆事件]")
    print("-" * 60)

    unique_events = sorted(
        {
            (
                e["date"],
                e["summary"],
            ): e
            for e in all_events
        }.values(),
        key=lambda x: (
            x["date"],
            x["summary"],
        ),
    )

    for event in unique_events[:40]:

        print(
            event["date"].isoformat(),
            event["summary"],
        )

    print("\n完成。")


if __name__ == "__main__":
    main()
