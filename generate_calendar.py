# -*- coding: utf-8 -*-

"""
台灣生活行事曆
==================================================

功能：
1. 自動抓取行政院人事行政總處官方辦公日曆
2. 自動產生「今年＋明年」
3. 普通週六、週日不建立事件
4. 官方國定假日保留
5. 春節依官方實際放假日期，自動顯示：
      小年夜
      除夕
      初一
      初二
      初三……
   不自行硬加初五、初六
6. 國定假日補假：
      ○○節(補假)
7. 政府補班：
      [補班]○○節
8. 母親節、父親節作為備注節日
9. 高雄天然災害停班停課
10. 自動產生 taiwan.ics
11. GitHub Actions 可每日自動更新
"""

import csv
import io
import json
import re
import ssl
import time
import uuid
import urllib.parse
import urllib.request

from datetime import date, datetime, timedelta


# =========================================================
# 基本設定
# =========================================================

CALENDAR_NAME = "台灣生活行事曆"

DGPA_DATASET = "https://data.gov.tw/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/Get/"
    "95eec21d-4ee7-4920-94ff-d36727bc171f"
)

OUTPUT_FILE = "taiwan.ics"


# =========================================================
# SSL / 網路
# =========================================================

def build_ssl_context():

    context = ssl.create_default_context()

    if hasattr(ssl, "VERIFY_X509_STRICT"):

        context.verify_flags &= (
            ~ssl.VERIFY_X509_STRICT
        )

    return context


SSL_CONTEXT = build_ssl_context()


def fetch_bytes(
    url,
    timeout=90,
    retries=4
):

    last_error = None

    for attempt in range(
        1,
        retries + 1
    ):

        try:

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "Chrome/154.0 Safari/537.36 "
                        "Taiwan-Life-Calendar"
                    ),
                    "Accept": "*/*",
                    "Connection": "close",
                }
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=SSL_CONTEXT
            ) as response:

                return response.read()

        except Exception as error:

            last_error = error

            print(
                f"[網路重試] 第 {attempt}/{retries} 次失敗："
                f"{error}"
            )

            if attempt < retries:

                time.sleep(
                    attempt * 3
                )

    raise last_error


def fetch_text(
    url,
    timeout=90,
    retries=4
):

    raw = fetch_bytes(
        url,
        timeout=timeout,
        retries=retries
    )

    return raw.decode(
        "utf-8-sig",
        errors="replace"
    )


def fetch_json(
    url,
    timeout=90,
    retries=4
):

    text = fetch_text(
        url,
        timeout=timeout,
        retries=retries
    )

    return json.loads(text)


# =========================================================
# 日期工具
# =========================================================

def normalize_date(value):

    if value is None:
        return None

    value = str(value).strip()

    # YYYYMMDD
    match = re.search(
        r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)",
        value
    )

    if match:

        try:

            return date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3))
            ).isoformat()

        except ValueError:
            pass

    # YYYY/MM/DD
    # YYYY-MM-DD
    match = re.search(
        r"(?<!\d)"
        r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})",
        value
    )

    if match:

        try:

            return date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3))
            ).isoformat()

        except ValueError:
            pass

    return None


def parse_iso_date(value):

    try:
        return date.fromisoformat(value)

    except Exception:
        return None


# =========================================================
# 農曆
# =========================================================

def get_lunar_date(solar):

    """
    支援 lunardate 不同版本可能使用的 API 名稱。
    """

    try:

        from lunardate import LunarDate

    except ImportError:

        raise RuntimeError(
            "找不到 lunardate。"
            "請確認 GitHub Actions 已安裝 lunardate。"
        )

    # 常見 API
    if hasattr(
        LunarDate,
        "fromSolarDate"
    ):

        return LunarDate.fromSolarDate(
            solar.year,
            solar.month,
            solar.day
        )

    # 相容部分版本
    if hasattr(
        LunarDate,
        "from_solar_date"
    ):

        return LunarDate.from_solar_date(
            solar.year,
            solar.month,
            solar.day
        )

    raise RuntimeError(
        "目前 lunardate 版本找不到農曆轉換函式。"
    )


def lunar_day_name(day):

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

    return names.get(
        day,
        f"初{day}"
    )


def get_spring_name(
    solar
):

    """
    只負責判斷春節相關名稱。

    注意：
    是否建立事件，不由這裡決定。
    最終仍然只以政府官方放假資料為準。
    """

    try:

        lunar = get_lunar_date(
            solar
        )

    except Exception as error:

        print(
            f"[WARN] 農曆轉換失敗："
            f"{solar} / {error}"
        )

        return None

    month = getattr(
        lunar,
        "month",
        None
    )

    day = getattr(
        lunar,
        "day",
        None
    )

    is_leap = getattr(
        lunar,
        "isLeapMonth",
        getattr(
            lunar,
            "is_leap_month",
            False
        )
    )

    if is_leap:
        return None

    # 農曆正月
    if month == 1:

        # 春節期間只需要處理常見初一～初十
        if 1 <= day <= 10:

            return lunar_day_name(
                day
            )

    # -----------------------------------------------------
    # 除夕
    # -----------------------------------------------------

    tomorrow = solar + timedelta(
        days=1
    )

    try:

        tomorrow_lunar = get_lunar_date(
            tomorrow
        )

        tomorrow_month = getattr(
            tomorrow_lunar,
            "month",
            None
        )

        tomorrow_day = getattr(
            tomorrow_lunar,
            "day",
            None
        )

        tomorrow_leap = getattr(
            tomorrow_lunar,
            "isLeapMonth",
            getattr(
                tomorrow_lunar,
                "is_leap_month",
                False
            )
        )

        if (
            not tomorrow_leap
            and tomorrow_month == 1
            and tomorrow_day == 1
        ):

            return "除夕"

    except Exception:
        pass

    # -----------------------------------------------------
    # 小年夜
    #
    # 小年夜 = 除夕前一天
    # -----------------------------------------------------

    day_after_tomorrow = solar + timedelta(
        days=2
    )

    try:

        after_lunar = get_lunar_date(
            day_after_tomorrow
        )

        after_month = getattr(
            after_lunar,
            "month",
            None
        )

        after_day = getattr(
            after_lunar,
            "day",
            None
        )

        after_leap = getattr(
            after_lunar,
            "isLeapMonth",
            getattr(
                after_lunar,
                "is_leap_month",
                False
            )
        )

        if (
            not after_leap
            and after_month == 1
            and after_day == 1
        ):

            return "小年夜"

    except Exception:
        pass

    return None


# =========================================================
# 官方資料
# =========================================================

def decode_csv(raw):

    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp950",
        "big5",
    ]

    for encoding in encodings:

        try:

            return raw.decode(
                encoding
            )

        except UnicodeDecodeError:

            continue

    return raw.decode(
        "utf-8",
        errors="replace"
    )


def get_official_calendar_url(
    year
):

    roc_year = year - 1911

    print(
        f"[官方] 尋找 {year} "
        f"（民國 {roc_year} 年）辦公日曆..."
    )

    try:

        html = fetch_text(
            DGPA_DATASET,
            timeout=90,
            retries=4
        )

    except Exception as error:

        print(
            "[ERROR] 官方資料集頁面取得失敗："
            f"{error}"
        )

        return None

    html = (
        html
        .replace(
            "&amp;",
            "&"
        )
        .replace(
            "&#x2F;",
            "/"
        )
        .replace(
            "&#47;",
            "/"
        )
        .replace(
            "&quot;",
            '"'
        )
    )

    links = re.findall(
        r'href=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE
    )

    candidates = []

    for link in links:

        decoded = urllib.parse.unquote(
            link
        )

        if str(roc_year) not in decoded:
            continue

        if ".csv" not in decoded.lower():
            continue

        if (
            "FileConversion"
            not in decoded
        ):
            continue

        if link.startswith("/"):

            link = (
                "https://data.gov.tw"
                + link
            )

        elif link.startswith("//"):

            link = (
                "https:"
                + link
            )

        elif link.startswith(
            "http://"
        ):

            link = (
                "https://"
                + link[len("http://"):]
            )

        candidates.append(
            link
        )

    # 優先 UTF-8
    utf8_candidates = [
        link
        for link in candidates
        if "utf8" in urllib.parse.unquote(
            link
        ).lower()
    ]

    if utf8_candidates:

        candidates = (
            utf8_candidates
            + [
                x
                for x in candidates
                if x not in utf8_candidates
            ]
        )

    # 排除 Google 行事曆版本
    normal_candidates = [
        link
        for link in candidates
        if "google" not in urllib.parse.unquote(
            link
        ).lower()
    ]

    if normal_candidates:
        candidates = normal_candidates

    if candidates:

        print(
            f"[官方] 找到 {year} CSV："
            f"{candidates[0]}"
        )

        return candidates[0]

    print(
        f"[ERROR] 找不到 {year} 官方辦公日曆 CSV"
    )

    return None


# =========================================================
# 官方節日名稱
# =========================================================

def clean_text(
    value
):

    if value is None:
        return ""

    value = str(
        value
    )

    # 移除 emoji / 國旗
    value = re.sub(
        r"[\U0001F1E6-\U0001F1FF]",
        "",
        value
    )

    value = re.sub(
        r"[\U0001F300-\U0001FAFF]",
        "",
        value
    )

    value = (
        value
        .replace(
            "❤️",
            ""
        )
        .replace(
            "❤",
            ""
        )
        .replace(
            "🇹🇼",
            ""
        )
    )

    value = (
        value
        .replace(
            "\ufeff",
            ""
        )
        .replace(
            "\u3000",
            " "
        )
        .strip()
    )

    return value


def get_row_text(
    row
):

    parts = []

    for value in row.values():

        value = clean_text(
            value
        )

        if value:
            parts.append(
                value
            )

    return " ".join(
        parts
    )


def get_description(
    row
):

    for key in [
        "備註",
        "備註說明",
        "說明",
        "description",
        "Description",
    ]:

        value = row.get(
            key
        )

        if value:

            return clean_text(
                value
            )

    return ""


# =========================================================
# 節日名稱標準化
# =========================================================

def normalize_holiday_name(
    text
):

    text = clean_text(
        text
    )

    if not text:
        return None

    # -----------------------------------------------------
    # 春節
    # -----------------------------------------------------

    if any(
        x in text
        for x in [
            "春節",
            "農曆春節",
            "農曆年",
            "春節假期",
        ]
    ):

        return "春節"

    # -----------------------------------------------------
    # 一般節日
    # -----------------------------------------------------

    rules = [

        (
            [
                "元旦",
                "開國紀念日",
            ],
            "元旦"
        ),

        (
            [
                "二二八",
                "228",
                "２２８",
                "和平紀念日",
            ],
            "228和平紀念日"
        ),

        (
            [
                "兒童節",
            ],
            "兒童節"
        ),

        (
            [
                "清明節",
                "民族掃墓節",
            ],
            "清明節"
        ),

        (
            [
                "端午節",
            ],
            "端午節"
        ),

        (
            [
                "中秋節",
            ],
            "中秋節"
        ),

        (
            [
                "國慶日",
                "國慶",
                "國民體育日",
            ],
            "國慶日"
        ),

        (
            [
                "勞動節",
            ],
            "勞動節"
        ),

        (
            [
                "臺灣光復節",
                "台灣光復節",
            ],
            "臺灣光復節"
        ),

        (
            [
                "行憲紀念日",
            ],
            "行憲紀念日"
        ),

        (
            [
                "孔子誕辰",
            ],
            "孔子誕辰紀念日"
        ),

    ]

    for keywords, name in rules:

        if any(
            keyword in text
            for keyword in keywords
        ):

            return name

    # -----------------------------------------------------
    # 如果政府未來新增節日
    #
    # 不丟掉資料。
    # 盡量從官方文字中取得乾淨名稱。
    # -----------------------------------------------------

    remove_words = [
        "中華民國",
        "政府行政機關",
        "行政機關",
        "辦公日曆表",
        "放假日",
        "放假",
        "調整",
        "補休",
        "補假",
        "補班",
        "補行上班",
        "補行辦公",
    ]

    result = text

    for word in remove_words:

        result = result.replace(
            word,
            ""
        )

    result = re.sub(
        r"\s+",
        " ",
        result
    ).strip(
        " -_：:（）()"
    )

    if not result:
        return None

    # 避免把純「上班日」當成節日
    if result in [
        "上班",
        "上班日",
        "工作日",
        "辦公日",
    ]:
        return None

    return result


# =========================================================
# 判斷補假 / 補班
# =========================================================

def is_makeup_holiday(
    text
):

    return any(
        keyword in text
        for keyword in [
            "補假",
            "補休",
            "調整放假",
            "調整休假",
        ]
    )


def is_makeup_workday(
    text
):

    return any(
        keyword in text
        for keyword in [
            "補班",
            "補行上班",
            "補行辦公",
            "補上班",
        ]
    )


# =========================================================
# 官方 CSV 解析
# =========================================================

def get_official_holidays(
    year
):

    url = get_official_calendar_url(
        year
    )

    if not url:
        return []

    try:

        print(
            f"[官方] 下載 {year} CSV..."
        )

        raw = fetch_bytes(
            url,
            timeout=120,
            retries=4
        )

    except Exception as error:

        print(
            f"[ERROR] {year} CSV 下載失敗："
            f"{error}"
        )

        return []

    text = decode_csv(
        raw
    )

    try:

        reader = csv.DictReader(
            io.StringIO(text)
        )

        rows = list(
            reader
        )

    except Exception as error:

        print(
            f"[ERROR] {year} CSV 解析失敗："
            f"{error}"
        )

        return []

    print(
        f"[官方] {year} CSV 共 {len(rows)} 筆資料"
    )

    records = []

    for row in rows:

        if not isinstance(
            row,
            dict
        ):
            continue

        raw_date = (
            row.get("西元日期")
            or row.get("\ufeff西元日期")
            or row.get("日期")
            or row.get("date")
            or ""
        )

        event_date = normalize_date(
            raw_date
        )

        if not event_date:
            continue

        holiday_value = clean_text(
            row.get("是否放假")
            or row.get("isHoliday")
            or ""
        )

        description = get_description(
            row
        )

        row_text = get_row_text(
            row
        )

        records.append({
            "date": event_date,
            "holiday_value": holiday_value,
            "description": description,
            "text": row_text,
            "row": row,
        })

    # =====================================================
    # 第一階段：找出所有官方放假日
    # =====================================================

    holiday_records = []

    for record in records:

        # 官方 CSV：
        # 0 = 上班
        # 2 = 放假
        if record["holiday_value"] != "2":
            continue

        holiday_records.append(
            record
        )

    # =====================================================
    # 建立日期 → 官方資料
    # =====================================================

    holiday_by_date = {}

    for record in holiday_records:

        holiday_by_date[
            record["date"]
        ] = record

    # =====================================================
    # 取得正常節日名稱
    # =====================================================

    events = []

    for record in holiday_records:

        event_date = parse_iso_date(
            record["date"]
        )

        if not event_date:
            continue

        text = record["text"]

        description = record[
            "description"
        ]

        # -------------------------------------------------
        # 補假
        # -------------------------------------------------

        makeup = is_makeup_holiday(
            text
        )

        # -------------------------------------------------
        # 春節
        # -------------------------------------------------

        spring_name = get_spring_name(
            event_date
        )

        base_name = normalize_holiday_name(
            description
        )

        if base_name == "春節":
            base_name = spring_name

        # 如果官方備註沒有寫春節，
        # 但日期本身是春節相關日期，
        # 優先使用農曆名稱。
        if spring_name:

            if (
                "春節" in text
                or "農曆" in text
                or event_date.month in [
                    1,
                    2
                ]
            ):

                base_name = spring_name

        # -------------------------------------------------
        # 沒有辨識到名稱
        # -------------------------------------------------

        if not base_name:

            # 嘗試從整列資料判斷
            base_name = normalize_holiday_name(
                text
            )

        # -------------------------------------------------
        # 還是沒有名稱
        # -------------------------------------------------

        if not base_name:

            # 不要創造「台灣政府放假日」
            # 但也不要讓未知官方節日消失。
            #
            # 如果官方真的新增節日，
            # 優先使用備註文字。
            if description:

                base_name = clean_text(
                    description
                )

            else:

                continue

        # -------------------------------------------------
        # 補假名稱
        # -------------------------------------------------

        if makeup:

            title = (
                f"{base_name}(補假)"
            )

        else:

            title = base_name

        events.append({
            "date": record["date"],
            "title": title,
            "type": "holiday",
            "description": (
                "資料來源："
                "行政院人事行政總處"
                "\n"
                "中華民國政府行政機關辦公日曆表"
                "\n"
                f"資料年度：{year}"
            ),
        })

    # =====================================================
    # 補班
    # =====================================================
    #
    # 補班日的官方 CSV 通常只會告訴我們：
    # 「這天要上班」
    #
    # 所以要反向找它所補的節日。
    #
    # 判斷原則：
    # 1. 官方文字明確寫節日 → 直接使用
    # 2. 找附近因週末而產生的補假節日
    # 3. 找最近的官方假日群組
    # =====================================================

    workday_records = []

    for record in records:

        text = record["text"]

        if is_makeup_workday(
            text
        ):

            workday_records.append(
                record
            )

            continue

        # 部分官方資料只用：
        # 是否放假 = 0
        # 備註 = 某某節補行上班
        if (
            record["holiday_value"] == "0"
            and any(
                x in text
                for x in [
                    "補行",
                    "補班",
                    "補上班",
                ]
            )
        ):

            workday_records.append(
                record
            )

    # 已經建立的補班日期
    existing_workdays = set()

    for record in workday_records:

        event_date = parse_iso_date(
            record["date"]
        )

        if not event_date:
            continue

        # 避免重複
        if record["date"] in existing_workdays:
            continue

        existing_workdays.add(
            record["date"]
        )

        text = record["text"]

        # -------------------------------------------------
        # 先直接從官方文字取得節日
        # -------------------------------------------------

        base_name = normalize_holiday_name(
            record["description"]
        )

        if not base_name:

            base_name = normalize_holiday_name(
                text
            )

        # -------------------------------------------------
        # 如果官方文字沒有直接寫，
        # 嘗試找附近的補假節日
        # -------------------------------------------------

        if not base_name:

            nearby = []

            for holiday in holiday_records:

                holiday_date = parse_iso_date(
                    holiday["date"]
                )

                if not holiday_date:
                    continue

                distance = abs(
                    (
                        event_date
                        - holiday_date
                    ).days
                )

                if distance <= 7:

                    holiday_name = normalize_holiday_name(
                        holiday["description"]
                    )

                    if (
                        holiday_name
                        and holiday_name != "春節"
                    ):

                        nearby.append(
                            (
                                distance,
                                holiday_name
                            )
                        )

            if nearby:

                nearby.sort(
                    key=lambda x: x[0]
                )

                base_name = nearby[0][1]

        # -------------------------------------------------
        # 如果還是無法對應
        # -------------------------------------------------

        if not base_name:

            spring_name = get_spring_name(
                event_date
            )

            if spring_name:

                base_name = spring_name

        # -------------------------------------------------
        # 最後保底
        # -------------------------------------------------

        if not base_name:

            base_name = "政府補班日"

        events.append({
            "date": record["date"],
            "title": f"[補班]{base_name}",
            "type": "makeup_workday",
            "description": (
                "資料來源："
                "行政院人事行政總處"
                "\n"
                "政府公告補行上班日"
                "\n"
                f"資料年度：{year}"
            ),
        })

    # =====================================================
    # 去除完全重複
    # =====================================================

    unique = {}

    for event in events:

        key = (
            event["date"],
            event["title"]
        )

        unique[key] = event

    events = list(
        unique.values()
    )

    events.sort(
        key=lambda x: (
            x["date"],
            x["title"]
        )
    )

    print(
        f"[完成] {year} 官方相關事件："
        f"{len(events)} 筆"
    )

    return events


# =========================================================
# 母親節
# =========================================================

def mothers_day(
    year
):

    d = date(
        year,
        5,
        1
    )

    while d.weekday() != 6:

        d += timedelta(
            days=1
        )

    return (
        d + timedelta(days=7)
    ).isoformat()


# =========================================================
# 父親節
# =========================================================

def fathers_day(
    year
):

    return date(
        year,
        8,
        8
    ).isoformat()


# =========================================================
# 高雄停班停課
# =========================================================

def find_records(
    data
):

    if isinstance(
        data,
        list
    ):

        return data

    if not isinstance(
        data,
        dict
    ):

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

            result = find_records(
                data[key]
            )

            if result:

                return result

    for value in data.values():

        if isinstance(
            value,
            list
        ):

            return value

    return []


def extract_dates(
    record
):

    text = json.dumps(
        record,
        ensure_ascii=False
    )

    dates = set()

    western = re.findall(
        r"20\d{2}[/-]\d{1,2}[/-]\d{1,2}",
        text
    )

    for value in western:

        normalized = normalize_date(
            value
        )

        if normalized:

            dates.add(
                normalized
            )

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

        try:

            event_date = date(
                int(parts[0]) + 1911,
                int(parts[1]),
                int(parts[2])
            )

            dates.add(
                event_date.isoformat()
            )

        except ValueError:

            pass

    return sorted(
        dates
    )


def format_record(
    record
):

    if not isinstance(
        record,
        dict
    ):

        return str(
            record
        )

    parts = []

    for key, value in record.items():

        if value is None:
            continue

        value = str(
            value
        ).strip()

        if not value:
            continue

        parts.append(
            f"{key}：{value}"
        )

    return "\n".join(
        parts
    )


def get_kaohsiung_closures():

    print(
        "[高雄] 取得天然災害停班停課資料..."
    )

    try:

        data = fetch_json(
            KAOHSIUNG_API,
            timeout=30,
            retries=2
        )

    except Exception as error:

        print(
            "[WARN] 高雄停班停課資料取得失敗："
            f"{error}"
        )

        return []

    records = find_records(
        data
    )

    print(
        f"[高雄] API 回傳資料："
        f"{len(records)} 筆"
    )

    events = []

    for record in records:

        if not isinstance(
            record,
            dict
        ):

            continue

        full_text = json.dumps(
            record,
            ensure_ascii=False
        )

        normalized = (
            full_text
            .replace(
                " ",
                ""
            )
            .replace(
                "\n",
                ""
            )
            .replace(
                "\r",
                ""
            )
        )

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

        if any(
            keyword in normalized
            for keyword in [
                "照常上班上課",
                "照常上班",
                "照常上課",
            ]
        ):

            continue

        dates = extract_dates(
            record
        )

        for event_date in dates:

            events.append({
                "date": event_date,
                "title": "高雄市停班停課",
                "type": "disaster",
                "description": (
                    "資料來源：高雄市政府人事處"
                    "\n"
                    "天然災害停止上班上課相關訊息"
                    "\n"
                    + format_record(
                        record
                    )
                ),
            })

    unique = {}

    for item in events:

        unique[
            item["date"]
        ] = item

    events = list(
        unique.values()
    )

    events.sort(
        key=lambda x: x["date"]
    )

    print(
        f"[完成] 高雄停班停課："
        f"{len(events)} 筆"
    )

    return events


# =========================================================
# ICS
# =========================================================

def escape_ics(
    value
):

    return (
        str(value)
        .replace(
            "\\",
            "\\\\"
        )
        .replace(
            ";",
            "\\;"
        )
        .replace(
            ",",
            "\\,"
        )
        .replace(
            "\r\n",
            "\\n"
        )
        .replace(
            "\n",
            "\\n"
        )
        .replace(
            "\r",
            "\\n"
        )
    )


def make_uid(
    event
):

    raw = (
        f"{event['type']}:"
        f"{event['date']}:"
        f"{event['title']}"
    )

    return (
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                raw
            )
        )
        + "@taiwan-calendar"
    )


def make_event(
    event
):

    event_date = date.fromisoformat(
        event["date"]
    )

    end_date = (
        event_date
        + timedelta(days=1)
    )

    uid = make_uid(
        event
    )

    return "\r\n".join([
        "BEGIN:VEVENT",

        f"UID:{uid}",

        (
            "DTSTART;VALUE=DATE:"
            f"{event_date.strftime('%Y%m%d')}"
        ),

        (
            "DTEND;VALUE=DATE:"
            f"{end_date.strftime('%Y%m%d')}"
        ),

        (
            "SUMMARY:"
            f"{escape_ics(event['title'])}"
        ),

        (
            "DESCRIPTION:"
            f"{escape_ics(event['description'])}"
        ),

        "END:VEVENT",
    ])


# =========================================================
# 完整行事曆
# =========================================================

def generate_calendar():

    today = date.today()

    years = [
        today.year,
        today.year + 1
    ]

    print("")
    print(
        "=================================================="
    )
    print(
        "台灣生活行事曆開始更新"
    )
    print(
        f"更新年度：{years[0]} + {years[1]}"
    )
    print(
        "=================================================="
    )

    all_events = []

    official_holiday_count = {}

    makeup_holiday_count = {}

    makeup_workday_count = {}

    # =====================================================
    # 官方資料
    # =====================================================

    for year in years:

        try:

            yearly_events = (
                get_official_holidays(
                    year
                )
            )

            official_holiday_count[
                year
            ] = sum(
                1
                for event in yearly_events
                if event["type"] == "holiday"
                and not event["title"].endswith(
                    "(補假)"
                )
            )

            makeup_holiday_count[
                year
            ] = sum(
                1
                for event in yearly_events
                if event["title"].endswith(
                    "(補假)"
                )
            )

            makeup_workday_count[
                year
            ] = sum(
                1
                for event in yearly_events
                if event["type"] == "makeup_workday"
            )

            all_events.extend(
                yearly_events
            )

        except Exception as error:

            official_holiday_count[
                year
            ] = 0

            makeup_holiday_count[
                year
            ] = 0

            makeup_workday_count[
                year
            ] = 0

            print(
                f"[ERROR] {year} 官方資料處理失敗："
                f"{error}"
            )

    # =====================================================
    # 母親節
    # =====================================================

    for year in years:

        event_date = mothers_day(
            year
        )

        all_events.append({
            "date": event_date,
            "title": "母親節",
            "type": "note",
            "description": (
                "備注｜"
                "台灣常見慶祝節日，"
                "不屬政府國定假日"
            ),
        })

    # =====================================================
    # 父親節
    # =====================================================

    for year in years:

        event_date = fathers_day(
            year
        )

        all_events.append({
            "date": event_date,
            "title": "父親節",
            "type": "note",
            "description": (
                "備注｜"
                "台灣常見慶祝節日，"
                "不屬政府國定假日"
            ),
        })

    # =====================================================
    # 高雄停班停課
    # =====================================================

    try:

        closures = (
            get_kaohsiung_closures()
        )

        all_events.extend(
            closures
        )

    except Exception as error:

        print(
            "[WARN] 高雄停班停課處理失敗："
            f"{error}"
        )

        closures = []

    # =====================================================
    # 最終去重
    # =====================================================

    unique = {}

    for event in all_events:

        key = (
            event["date"],
            event["title"]
        )

        unique[key] = event

    all_events = list(
        unique.values()
    )

    # =====================================================
    # 排序
    # =====================================================

    all_events.sort(
        key=lambda event: (
            event["date"],
            event["title"]
        )
    )

    # =====================================================
    # 建立 ICS
    # =====================================================

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

        "X-WR-CALNAME:台灣生活行事曆",

        "X-WR-TIMEZONE:Asia/Taipei",

    ]

    for event in all_events:

        calendar.append(
            make_event(
                event
            )
        )

    calendar.append(
        "END:VCALENDAR"
    )

    ics = (
        "\r\n".join(
            calendar
        )
        + "\r\n"
    )

    # =====================================================
    # 統計
    # =====================================================

    official_total = sum(
        official_holiday_count.values()
    )

    makeup_holiday_total = sum(
        makeup_holiday_count.values()
    )

    makeup_workday_total = sum(
        makeup_workday_count.values()
    )

    mother_total = sum(
        1
        for event in all_events
        if event["title"] == "母親節"
    )

    father_total = sum(
        1
        for event in all_events
        if event["title"] == "父親節"
    )

    disaster_total = len(
        closures
    )

    print("")
    print(
        "=================================================="
    )

    print(
        f"[統計] {years[0]} 一般國定假日："
        f"{official_holiday_count.get(years[0], 0)} 筆"
    )

    print(
        f"[統計] {years[1]} 一般國定假日："
        f"{official_holiday_count.get(years[1], 0)} 筆"
    )

    print(
        f"[統計] 補假："
        f"{makeup_holiday_total} 筆"
    )

    print(
        f"[統計] 補班："
        f"{makeup_workday_total} 筆"
    )

    print(
        f"[統計] 母親節："
        f"{mother_total} 筆"
    )

    print(
        f"[統計] 父親節："
        f"{father_total} 筆"
    )

    print(
        f"[統計] 高雄停班停課："
        f"{disaster_total} 筆"
    )

    print(
        f"[統計] 總行事曆事件："
        f"{len(all_events)} 個"
    )

    print(
        "=================================================="
    )

    # =====================================================
    # 前 50 筆檢查
    # =====================================================

    print("")
    print(
        "[檢查] 前 50 個事件："
    )

    for event in all_events[:50]:

        print(
            f"  {event['date']}  "
            f"{event['title']}"
        )

    return ics


# =========================================================
# 主程式
# =========================================================

if __name__ == "__main__":

    ics = generate_calendar()

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline=""
    ) as file:

        file.write(
            ics
        )

    print("")
    print(
        "=================================================="
    )

    print(
        "taiwan.ics 產生完成！"
    )

    print(
        f"共 {ics.count('BEGIN:VEVENT')} "
        "個行事曆事件"
    )

    print(
        "=================================================="
    )
