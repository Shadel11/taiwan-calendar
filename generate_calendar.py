import csv
import io
import re
import uuid
import calendar
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, unquote

import requests
import urllib3
from lunardate import LunarDate


# ============================================================
# 基本設定
# ============================================================

CALENDAR_NAME = "台灣生活行事曆"
TIMEZONE = "Asia/Taipei"
OUTPUT_FILE = "taiwan.ics"

DGPA_DATASET_URL = "https://data.gov.tw/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/Get/"
    "95eec21d-4ee7-4920-94ff-d36727bc171f"
)

CURRENT_YEAR = datetime.now().year
YEARS = [CURRENT_YEAR, CURRENT_YEAR + 1]


# ============================================================
# HTTP 設定
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "Chrome/130 Safari/537.36"
    )
}


# DGPA 網站的 SSL 憑證在 GitHub Actions / Python requests
# 環境可能出現 Subject Key Identifier 驗證問題。
urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


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
# 日期工具
# ============================================================

def parse_date(value):
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = text.replace("/", "-").replace(".", "-")

    # 2026-01-01
    match = re.match(
        r"^(\d{4})-(\d{1,2})-(\d{1,2})$",
        text
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

    # 20260101
    match = re.match(
        r"^(\d{4})(\d{2})(\d{2})$",
        text
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

    # 民國 115/01/01
    match = re.match(
        r"^(\d{2,3})-(\d{1,2})-(\d{1,2})$",
        text
    )

    if match:
        try:
            year = int(match.group(1)) + 1911
            return date(
                year,
                int(match.group(2)),
                int(match.group(3)),
            )
        except ValueError:
            return None

    return None


def is_weekend(d):
    return d.weekday() >= 5


def format_date(d):
    return d.strftime("%Y-%m-%d")


# ============================================================
# 政府資料開放平台
# ============================================================

def get_resource_links():
    response = http_get(DGPA_DATASET_URL)

    html = response.text

    links = []

    # href="..."
    for match in re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    ):
        url = urljoin(DGPA_DATASET_URL, match)

        decoded = unquote(url)

        if (
            ".csv" in decoded.lower()
            or "fileconversion" in decoded.lower()
        ):
            links.append(url)

    # 去重
    result = []
    seen = set()

    for url in links:
        if url not in seen:
            seen.add(url)
            result.append(url)

    return result


def find_csv_url(year):
    """
    找指定西元年份的政府行政機關辦公日曆 CSV。

    例如：
    2026 -> 民國115年
    2027 -> 民國116年

    重要：
    DGPA URL 可能是：
    115%e5%b9%b4
    解碼後才是：
    115年

    因此一定先 unquote 再判斷年份。
    """

    roc_year = year - 1911

    print()
    print("=" * 60)
    print(f"[資料] 正在尋找 {year} 年政府辦公日曆")
    print(f"[資料] 民國年：{roc_year}")
    print("=" * 60)

    resource_links = get_resource_links()

    print(
        f"[資料] 頁面找到候選資源："
        f"{len(resource_links)} 個"
    )

    csv_links = []

    for url in resource_links:
        decoded = unquote(url)

        if ".csv" in decoded.lower():
            csv_links.append(url)

    print(
        f"[資料] 候選 CSV："
        f"{len(csv_links)} 個"
    )

    candidates = []

    for url in csv_links:
        decoded = unquote(url)

        score = 0

        # ----------------------------------------------------
        # 指定年度：最高優先
        # ----------------------------------------------------

        if f"{roc_year}年" in decoded:
            score += 1000

        if f"{roc_year} 年" in decoded:
            score += 1000

        # ----------------------------------------------------
        # 政府行政機關辦公日曆
        # ----------------------------------------------------

        if "中華民國政府行政機關辦公日曆表" in decoded:
            score += 300

        if "政府行政機關辦公日曆表" in decoded:
            score += 200

        if "辦公日曆表" in decoded:
            score += 100

        # ----------------------------------------------------
        # CSV
        # ----------------------------------------------------

        if ".csv" in decoded.lower():
            score += 10

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
            f"[資料] 候選 {score}：{decoded}"
        )

    # --------------------------------------------------------
    # 最重要：
    # 只允許「指定民國年份」的 CSV
    # --------------------------------------------------------

    exact_candidates = []

    for score, url, decoded in candidates:

        if (
            f"{roc_year}年" in decoded
            or f"{roc_year} 年" in decoded
        ):
            exact_candidates.append(
                (
                    score,
                    url,
                    decoded,
                )
            )

    if not exact_candidates:
        raise RuntimeError(
            f"找不到 {year} 年（民國 {roc_year} 年）"
            f"的官方政府辦公日曆 CSV。"
        )

    # 分數最高優先
    exact_candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    for score, url, decoded in exact_candidates:

        print()
        print(
            f"[資料] 嘗試 {year} 年 CSV："
            f"{decoded}"
        )

        try:

            response = http_get(url)

            if len(response.content) < 100:
                print(
                    "[資料] ⚠️ 檔案太小，跳過"
                )
                continue

            print(
                f"[資料] ✓ 找到 {year} 年官方 CSV"
            )

            print(
                f"[資料] ★ 使用 CSV："
                f"{decoded}"
            )

            return url

        except Exception as e:

            print(
                "[資料] ⚠️ CSV 下載失敗，"
                f"繼續嘗試：{e}"
            )

    raise RuntimeError(
        f"找到 {year} 年候選 CSV，"
        f"但無法成功下載。"
    )


# ============================================================
# CSV 讀取
# ============================================================

def detect_encoding(content):
    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp950",
        "big5",
    ]

    for encoding in encodings:

        try:
            text = content.decode(encoding)

            if "西元日期" in text or "日期" in text:
                return encoding

        except UnicodeDecodeError:
            pass

    return "utf-8-sig"


def find_column(fieldnames, keywords):

    for field in fieldnames:

        text = str(field).strip()

        for keyword in keywords:

            if keyword in text:
                return field

    return None


def normalize_holiday_name(note):

    text = str(note or "").strip()

    if not text:
        return None

    # --------------------------------------------------------
    # 春節相關
    # --------------------------------------------------------

    if "小年夜" in text:
        return "小年夜"

    if "除夕" in text:
        return "除夕"

    if "春節" in text:
        return "春節"

    # --------------------------------------------------------
    # 一般節日
    # --------------------------------------------------------

    if (
        "開國紀念日" in text
        or "元旦" in text
    ):
        return "元旦"

    if (
        "和平紀念日" in text
        or "二二八" in text
    ):
        return "228和平紀念日"

    if "兒童節" in text:
        return "兒童節"

    if (
        "清明節" in text
        or "民族掃墓節" in text
    ):
        return "清明節"

    if "端午節" in text:
        return "端午節"

    if "中秋節" in text:
        return "中秋節"

    if "國慶日" in text:
        return "國慶日"

    if "勞動節" in text:
        return "勞動節"

    if "臺灣光復" in text:
        return "臺灣光復暨金門古寧頭大捷紀念日"

    if "金門古寧頭" in text:
        return "臺灣光復暨金門古寧頭大捷紀念日"

    if "行憲紀念日" in text:
        return "行憲紀念日"

    if "教師節" in text:
        return "教師節"

    return None


def read_dgpa_csv(year):

    csv_url = find_csv_url(year)

    response = http_get(csv_url)

    encoding = detect_encoding(response.content)

    print(
        f"[資料] CSV 編碼：{encoding}"
    )

    text = response.content.decode(
        encoding,
        errors="replace",
    )

    # --------------------------------------------------------
    # 判斷 CSV 分隔符
    # --------------------------------------------------------

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
        f"[資料] 欄位：{fieldnames}"
    )

    date_column = find_column(
        fieldnames,
        [
            "西元日期",
            "日期",
        ],
    )

    holiday_column = find_column(
        fieldnames,
        [
            "是否放假",
        ],
    )

    note_column = find_column(
        fieldnames,
        [
            "備註",
            "節日",
        ],
    )

    if not date_column:
        raise RuntimeError(
            f"{year} 年 CSV 找不到日期欄位。"
        )

    if not holiday_column:
        raise RuntimeError(
            f"{year} 年 CSV 找不到是否放假欄位。"
        )

    rows = []

    for raw in reader:

        d = parse_date(
            raw.get(date_column)
        )

        if not d:
            continue

        if d.year != year:
            continue

        holiday_value = str(
            raw.get(holiday_column, "")
        ).strip()

        note = str(
            raw.get(note_column, "")
            if note_column
            else ""
        ).strip()

        rows.append(
            {
                "date": d,
                "holiday": holiday_value,
                "note": note,
            }
        )

    if not rows:
        raise RuntimeError(
            f"{year} 年 CSV 成功下載，"
            f"但解析結果為 0 筆。"
        )

    print(
        f"[資料] ✓ 成功解析 {len(rows)} 筆 {year} 年資料"
    )

    return rows


# ============================================================
# 農曆標籤
# ============================================================

def get_lunar_info(d):

    try:

        lunar = LunarDate.fromSolarDate(
            d.year,
            d.month,
            d.day,
        )

        return (
            lunar.year,
            lunar.month,
            lunar.day,
        )

    except Exception:
        return (
            None,
            None,
            None,
        )


def get_lunar_day_name(day):

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

    return names.get(day)


# ============================================================
# 春節判斷
# ============================================================

def lunar_holiday_name(d, note):

    note_name = normalize_holiday_name(note)

    if note_name == "小年夜":
        return "小年夜"

    if note_name == "除夕":
        return "除夕"

    if note_name == "春節":
        lunar_year, lunar_month, lunar_day = get_lunar_info(d)

        if lunar_month == 1 and lunar_day:
            return get_lunar_day_name(
                lunar_day
            )

        return "春節"

    return None


# ============================================================
# 政府假日事件
# ============================================================

def build_government_events(year):

    rows = read_dgpa_csv(year)

    events = []

    for row in rows:

        d = row["date"]
        holiday = row["holiday"]
        note = row["note"]

        # ----------------------------------------------------
        # 0 = 上班
        # 2 = 放假
        # ----------------------------------------------------

        try:
            holiday_flag = int(
                float(holiday)
            )
        except Exception:
            holiday_flag = None

        # ----------------------------------------------------
        # 補班
        # ----------------------------------------------------

        if holiday_flag != 2:

            if any(
                keyword in note
                for keyword in [
                    "補班",
                    "補行上班",
                    "調整上班",
                    "上班日",
                ]
            ):

                holiday_name = normalize_holiday_name(
                    note
                )

                if holiday_name:
                    summary = (
                        f"[補班]{holiday_name}"
                    )
                else:
                    # 嘗試從附近假日找名稱
                    nearby_name = find_nearby_holiday_name(
                        d,
                        rows,
                    )

                    if nearby_name:
                        summary = (
                            f"[補班]{nearby_name}"
                        )
                    else:
                        summary = "[補班]調整放假"

                events.append(
                    {
                        "date": d,
                        "summary": summary,
                        "type": "government",
                    }
                )

            continue

        # ----------------------------------------------------
        # 普通週六／週日不要加入
        #
        # 但：
        # 如果備註有正式節日名稱，
        # 即使假日在週末仍然保留。
        # ----------------------------------------------------

        holiday_name = normalize_holiday_name(
            note
        )

        lunar_name = lunar_holiday_name(
            d,
            note,
        )

        if lunar_name:
            holiday_name = lunar_name

        # 沒有節日名稱 + 週末
        # → 普通週末，不加入
        if (
            is_weekend(d)
            and not holiday_name
        ):
            continue

        # 沒有節日名稱
        # 但平日放假 → 嘗試使用備註
        if not holiday_name:

            if note:
                holiday_name = note.strip()

            else:
                # 不讓普通週末進來
                # 平日如果政府標示放假，
                # 仍保留一個合理名稱
                holiday_name = "政府放假日"

        # ----------------------------------------------------
        # 補假
        # ----------------------------------------------------

        is_makeup = (
            "補假" in note
            or "補休" in note
        )

        if is_makeup:

            if not holiday_name.endswith(
                "(補假)"
            ):
                holiday_name = (
                    f"{holiday_name}(補假)"
                )

        events.append(
            {
                "date": d,
                "summary": holiday_name,
                "type": "government",
            }
        )

    # 去除同一天重複
    unique = {}

    for event in events:

        key = (
            event["date"],
            event["summary"],
        )

        unique[key] = event

    return list(unique.values())


def find_nearby_holiday_name(
    target_date,
    rows,
):

    # 找前後 7 天內最接近的正式節日
    candidates = []

    for row in rows:

        d = row["date"]

        if abs(
            (d - target_date).days
        ) > 7:
            continue

        name = normalize_holiday_name(
            row["note"]
        )

        if not name:
            continue

        if name in [
            "小年夜",
            "除夕",
            "春節",
        ]:
            lunar_name = lunar_holiday_name(
                d,
                row["note"],
            )

            if lunar_name:
                name = lunar_name

        candidates.append(
            (
                abs(
                    (d - target_date).days
                ),
                name,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0]
    )

    return candidates[0][1]


# ============================================================
# 母親節
# ============================================================

def get_mothers_day(year):

    # 五月第二個星期日
    first_day = date(
        year,
        5,
        1,
    )

    days_until_sunday = (
        6 - first_day.weekday()
    ) % 7

    first_sunday = (
        first_day
        + timedelta(
            days=days_until_sunday
        )
    )

    second_sunday = (
        first_sunday
        + timedelta(days=7)
    )

    return second_sunday


# ============================================================
# 父親節
# ============================================================

def get_fathers_day(year):

    return date(
        year,
        8,
        8,
    )


def build_family_events(year):

    events = []

    events.append(
        {
            "date": get_mothers_day(year),
            "summary": "母親節",
            "type": "note",
        }
    )

    events.append(
        {
            "date": get_fathers_day(year),
            "summary": "父親節",
            "type": "note",
        }
    )

    return events


# ============================================================
# 高雄停班停課
# ============================================================

def recursive_objects(value):

    if isinstance(value, dict):

        yield value

        for child in value.values():
            yield from recursive_objects(
                child
            )

    elif isinstance(value, list):

        for child in value:
            yield from recursive_objects(
                child
            )


def extract_date_from_dict(item):

    if not isinstance(item, dict):
        return None

    for key, value in item.items():

        key_text = str(key)

        if any(
            word in key_text
            for word in [
                "日期",
                "date",
                "Date",
            ]
        ):

            parsed = parse_date(value)

            if parsed:
                return parsed

    return None


def extract_text_from_dict(item):

    if not isinstance(item, dict):
        return ""

    parts = []

    for key, value in item.items():

        parts.append(
            str(key)
        )

        if isinstance(
            value,
            (str, int, float),
        ):
            parts.append(
                str(value)
            )

    return " ".join(parts)


def build_kaohsiung_events():

    events = []

    try:

        response = http_get(
            KAOHSIUNG_API,
            timeout=30,
        )

        data = response.json()

        objects = list(
            recursive_objects(data)
        )

        seen = set()

        for item in objects:

            d = extract_date_from_dict(
                item
            )

            if not d:
                continue

            if d.year not in YEARS:
                continue

            text = extract_text_from_dict(
                item
            )

            # ------------------------------------------------
            # 只抓「停止上班」或「停止上課」
            # ------------------------------------------------

            if not any(
                keyword in text
                for keyword in [
                    "停止上班",
                    "停止上課",
                    "停班停課",
                ]
            ):
                continue

            summary = "高雄停班停課"

            if (
                "停止上班" in text
                and "停止上課" not in text
            ):
                summary = "高雄停止上班"

            elif (
                "停止上課" in text
                and "停止上班" not in text
            ):
                summary = "高雄停止上課"

            key = (
                d,
                summary,
            )

            if key in seen:
                continue

            seen.add(key)

            events.append(
                {
                    "date": d,
                    "summary": summary,
                    "type": "kaohsiung",
                }
            )

        print(
            f"[高雄] 成功取得："
            f"{len(events)} 筆"
        )

    except Exception as e:

        print(
            "[高雄] ⚠️ 目前沒有可用的停班停課資料："
            f"{e}"
        )

    return events


# ============================================================
# ICS 工具
# ============================================================

def escape_ics_text(text):

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


def fold_ics_line(line):

    # RFC 5545 建議每行最多 75 octets。
    # 這裡用 UTF-8 byte 長度處理。
    result = []

    current = ""

    current_bytes = 0

    for char in line:

        char_bytes = len(
            char.encode("utf-8")
        )

        if (
            current
            and current_bytes + char_bytes > 75
        ):

            result.append(current)

            current = " " + char
            current_bytes = (
                1 + char_bytes
            )

        else:

            current += char
            current_bytes += char_bytes

    if current:
        result.append(current)

    return "\r\n".join(result)


def make_uid(event):

    source = (
        f"{event['date'].isoformat()}|"
        f"{event['summary']}|"
        f"{event['type']}"
    )

    return (
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                source,
            )
        )
        + "@taiwan-calendar"
    )


def event_to_ics(event):

    d = event["date"]

    next_day = d + timedelta(
        days=1
    )

    uid = make_uid(event)

    summary = escape_ics_text(
        event["summary"]
    )

    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{next_day.strftime('%Y%m%d')}",
        f"SUMMARY:{summary}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]

    return "\r\n".join(
        fold_ics_line(line)
        for line in lines
    )


# ============================================================
# 產生 ICS
# ============================================================

def write_ics(events):

    events = sorted(
        events,
        key=lambda event: (
            event["date"],
            event["summary"],
        ),
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Shadel11//Taiwan Calendar//TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        f"X-WR-TIMEZONE:{TIMEZONE}",
    ]

    for event in events:

        lines.append(
            event_to_ics(event)
        )

    lines.append(
        "END:VCALENDAR"
    )

    content = "\r\n".join(
        lines
    ) + "\r\n"

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        f.write(content)

    print()
    print(
        "=" * 60
    )

    print(
        f"[完成] 已產生："
        f"{OUTPUT_FILE}"
    )

    print(
        f"[完成] 總事件數："
        f"{len(events)}"
    )

    print(
        "=" * 60
    )


# ============================================================
# 主程式
# ============================================================

def main():

    print()
    print(
        "=" * 70
    )

    print(
        f"{CALENDAR_NAME}：開始更新"
    )

    print(
        "=" * 70
    )

    all_events = []

    government_total = 0
    family_total = 0
    kaohsiung_total = 0

    # --------------------------------------------------------
    # 政府行事曆
    # --------------------------------------------------------

    for year in YEARS:

        events = build_government_events(
            year
        )

        government_total += len(
            events
        )

        all_events.extend(
            events
        )

        print(
            f"[統計] {year} 年政府假日事件："
            f"{len(events)} 筆"
        )

    # --------------------------------------------------------
    # 母親節／父親節
    # --------------------------------------------------------

    for year in YEARS:

        events = build_family_events(
            year
        )

        family_total += len(
            events
        )

        all_events.extend(
            events
        )

    print(
        f"[統計] 母親節／父親節："
        f"{family_total} 筆"
    )

    # --------------------------------------------------------
    # 高雄停班停課
    # --------------------------------------------------------

    kaohsiung_events = (
        build_kaohsiung_events()
    )

    kaohsiung_total = len(
        kaohsiung_events
    )

    all_events.extend(
        kaohsiung_events
    )

    print(
        f"[統計] 高雄停班停課："
        f"{kaohsiung_total} 筆"
    )

    # --------------------------------------------------------
    # 安全檢查
    # --------------------------------------------------------

    if government_total == 0:

        raise RuntimeError(
            "政府假日事件為 0，"
            "為避免產生錯誤行事曆，"
            "程式停止。"
        )

    # --------------------------------------------------------
    # 去除完全重複
    # --------------------------------------------------------

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

    print(
        f"[統計] 總行事曆事件："
        f"{len(all_events)} 個"
    )

    # --------------------------------------------------------
    # 產生 ICS
    # --------------------------------------------------------

    write_ics(
        all_events
    )


if __name__ == "__main__":
    main()
