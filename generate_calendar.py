```python
import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, unquote

import requests
from lunardate import LunarDate


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
    )
}


# ============================================================
# HTTP
# ============================================================

def http_get(url, timeout=TIMEOUT):
    """
    一般網站正常驗證 SSL。
    DGPA 的舊檔案偶爾會有憑證鏈問題，因此下載 DGPA CSV 時
    由 get_dgpa_csv() 使用 verify=False。
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
    DGPA 部分 CSV 的 HTTPS 憑證鏈會出現：

    CERTIFICATE_VERIFY_FAILED
    Missing Subject Key Identifier

    這不是 CSV 本身的問題，因此只對 DGPA 官方 CSV
    採用 verify=False。

    注意：
    我們只會下載「指定年度」的 CSV，
    不會再掃描歷史年度。
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

    # 20260101
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", value)
    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
        except ValueError:
            return None

    # 2026/01/01、2026-01-01、2026.01.01
    m = re.fullmatch(r"(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})", value)
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
    DGPA CSV 可能是：
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

    return content.decode("utf-8", errors="replace")


# ============================================================
# 找欄位
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
    normalized = {}

    for field in fieldnames or []:
        normalized[normalize_key(field)] = field

    for candidate in candidates:
        key = normalize_key(candidate)

        if key in normalized:
            return normalized[key]

    return None


# ============================================================
# 讀取 DGPA 官方 CSV
# ============================================================

def parse_dgpa_csv(content, target_year):
    """
    解析指定年度 DGPA CSV。

    官方欄位：
    西元日期
    星期
    是否放假
    備註

    0 = 上班
    2 = 放假
    """

    text = decode_csv(content)

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise ValueError("CSV 沒有欄位")

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
            f"找不到日期欄位，目前欄位：{reader.fieldnames}"
        )

    if not holiday_col:
        raise ValueError(
            f"找不到是否放假欄位，目前欄位：{reader.fieldnames}"
        )

    rows = []

    for raw in reader:
        d = parse_date(raw.get(date_col))

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

    rows.sort(key=lambda x: x["date"])

    return rows


# ============================================================
# 從 data.gov.tw 找「指定年度」CSV
# ============================================================

def get_dgpa_csv(year):
    """
    重要修正：

    舊版會：
        115
        114
        113
        112
        ...

    一路下載歷年 CSV。

    現在改成：

    1. 只讀 data.gov.tw dataset 14718
    2. 找指定年度 ROC 年份
    3. 只取得該年度 CSV
    4. 找到可用資料後立即停止
    5. 絕不下載其他年份

    2026 -> ROC 115
    2027 -> ROC 116
    """

    roc_year = year - 1911

    print(
        f"\n搜尋 {year} 年政府辦公日曆 "
        f"(民國 {roc_year} 年)"
    )

    response = http_get(DGPA_DATASET_URL)

    html = response.text

    # 找出 data.gov.tw 頁面中的所有 CSV 連結
    pattern = re.compile(
        r'href=["\']([^"\']+)["\'][^>]*>'
        r'\s*(?:CSV)?',
        re.IGNORECASE,
    )

    candidates = []

    for match in pattern.finditer(html):
        url = match.group(1)

        if not url:
            continue

        url = unquote(url)

        # 只接受 DGPA FileConversion CSV
        if "dgpa.gov.tw/FileConversion" not in url:
            continue

        candidates.append(url)

    # data.gov.tw 的 HTML 文字中也可能沒有完整地把
    # href 與名稱放在一起，因此直接用頁面文字附近
    # 的 FileConversion URL 再補抓一次。
    file_urls = re.findall(
        r'https?://www\.dgpa\.gov\.tw/FileConversion\?[^"\'>\s]+',
        html,
        flags=re.IGNORECASE,
    )

    candidates.extend(file_urls)

    # 去重但保留順序
    unique_candidates = []

    seen = set()

    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique_candidates.append(url)

    # ========================================================
    # 最重要的防呆：
    #
    # 只保留 URL / filename 中含有目標 ROC 年份的檔案
    #
    # 115 -> 2026
    # 116 -> 2027
    #
    # 因此 114、113、112... 根本不會進入下載流程。
    # ========================================================

    target_candidates = []

    for url in unique_candidates:
        decoded = unquote(url)

        if (
            f"{roc_year}年" in decoded
            or f"{roc_year}%E5%B9%B4" in url
        ):
            target_candidates.append(url)

    # data.gov.tw 目前頁面會列出：
    # 115年...
    # 115年...Google行事曆專用
    # 116年...
    # 116年...Google行事曆專用
    #
    # 優先使用一般辦公日曆 CSV，
    # 不優先使用 Google 專用格式。
    def candidate_score(url):
        decoded = unquote(url)

        score = 0

        if "Google行事曆專用" in decoded:
            score += 20

        if "Google" in decoded:
            score += 20

        if "政府行政機關辦公日曆表" in decoded:
            score -= 5

        return score

    target_candidates.sort(key=candidate_score)

    if not target_candidates:
        raise RuntimeError(
            f"找不到 {year} 年（民國 {roc_year} 年）"
            " DGPA 官方 CSV。"
        )

    print(
        f"找到 {len(target_candidates)} 個 "
        f"{year} 年候選檔案"
    )

    last_error = None

    for url in target_candidates:
        try:
            decoded_url = unquote(url)

            print(
                f"讀取 {year} 政府辦公日曆："
                f"{decoded_url}"
            )

            response = http_get_dgpa(url)

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
                f"⚠️ 此 {year} 年候選檔案無法使用，"
                f"嘗試下一個同年度檔案：{exc}"
            )

    raise RuntimeError(
        f"{year} 年 DGPA CSV 全部候選檔案皆無法讀取："
        f"{last_error}"
    )


# ============================================================
# 節日名稱
# ============================================================

def clean_note(note):
    if not note:
        return ""

    note = str(note).strip()

    # 去除常見多餘標記
    note = re.sub(
        r"^\s*[●•◆◇■□★☆]+\s*",
        "",
        note,
    )

    return note.strip()


def lunar_day_name(d):
    """
    將春節日期轉成：
    小年夜
    除夕
    初一
    初二
    初三
    ...

    只會對真正的農曆春節期間使用。
    """

    try:
        lunar = LunarDate.fromSolarDate(
            d.year,
            d.month,
            d.day,
        )
    except Exception:
        return None

    month = lunar.month
    day = lunar.day

    # 農曆十二月
    if month == 12:
        if day == 30:
            return "除夕"

        if day == 29:
            return "除夕"

        # 小年夜 = 除夕前一天
        if day in (28,):
            return "小年夜"

    # 農曆正月
    if month == 1:
        chinese_numbers = {
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

        return chinese_numbers.get(
            day,
            f"初{day}",
        )

    return None


def is_spring_festival_date(d):
    """
    春節只判斷農曆：
    農曆十二月二十八～正月十五。

    但實際產生事件時，
    仍然只使用 DGPA 官方 isHoliday=2 的日期。
    因此不會自行製造初八、初九、十五。
    """

    try:
        lunar = LunarDate.fromSolarDate(
            d.year,
            d.month,
            d.day,
        )
    except Exception:
        return False

    if lunar.month == 12 and lunar.day >= 28:
        return True

    if lunar.month == 1 and lunar.day <= 15:
        return True

    return False


# ============================================================
# 官方節日名稱
# ============================================================

def get_base_holiday_name(d, note=""):
    """
    以官方備註優先，
    再針對特殊節日做標準化。

    不會因為週末本身就建立事件。
    """

    note = clean_note(note)

    # 春節
    if is_spring_festival_date(d):
        lunar_name = lunar_day_name(d)

        if lunar_name:
            return lunar_name

        return "春節"

    # 母親節 / 父親節由其他函式處理
    # 這裡處理政府正式節日

    if d.month == 1 and d.day == 1:
        return "開國紀念日"

    if d.month == 2 and d.day == 28:
        return "和平紀念日"

    if d.month == 4 and d.day == 4:
        return "兒童節"

    if d.month == 4 and d.day == 5:
        return "清明節"

    if d.month == 5 and d.day == 1:
        return "勞動節"

    if d.month == 6:
        try:
            lunar = LunarDate.fromSolarDate(
                d.year,
                d.month,
                d.day,
            )

            if lunar.month == 5 and lunar.day == 5:
                return "端午節"
        except Exception:
            pass

    if d.month == 9:
        try:
            lunar = LunarDate.fromSolarDate(
                d.year,
                d.month,
                d.day,
            )

            if lunar.month == 8 and lunar.day == 15:
                return "中秋節"
        except Exception:
            pass

    if d.month == 9 and d.day == 28:
        return "孔子誕辰紀念日/教師節"

    if d.month == 10 and d.day == 10:
        return "國慶日"

    if d.month == 10 and 23 <= d.day <= 25:
        return "臺灣光復暨金門古寧頭大捷紀念日"

    if d.month == 12 and d.day == 25:
        return "行憲紀念日"

    # 如果官方備註有名稱，使用官方名稱
    if note:
        return note

    return None


# ============================================================
# 官方政府事件
# ============================================================

def build_government_events(year, rows):
    """
    建立政府正式假日。

    核心原則：

    1. 只使用 DGPA isHoliday=2
    2. 普通週六、週日不單獨建立事件
    3. 只有因官方節日而形成的連續假期才建立
    4. 補假單獨標示 (補假)
    5. 補班單獨標示 [補班]
    6. 春節只使用官方放假資料
    """

    holiday_rows = [
        r for r in rows
        if r["is_holiday"]
    ]

    work_rows = [
        r for r in rows
        if not r["is_holiday"]
    ]

    holiday_by_date = {
        r["date"]: r
        for r in holiday_rows
    }

    work_by_date = {
        r["date"]: r
        for r in work_rows
    }

    events = []

    # --------------------------------------------------------
    # 找出補班
    # --------------------------------------------------------

    makeup_work_dates = set()

    for d, row in work_by_date.items():
        weekday = d.weekday()

        # 官方資料標記為上班，但日期是六日
        if weekday >= 5:
            makeup_work_dates.add(d)

    # --------------------------------------------------------
    # 找出補假
    # --------------------------------------------------------

    makeup_holiday_dates = set()

    for d, row in holiday_by_date.items():

        note = clean_note(row.get("note", ""))

        if "補假" in note:
            makeup_holiday_dates.add(d)
            continue

        # 官方常見補假情況：
        # 假日落在週末，下一個工作日補休
        #
        # 但不能單純看到週末就判斷，
        # 必須有官方備註或位於正式節日區塊。
        #
        # 因此這裡主要依照官方備註判斷。
        if "補" in note and "假" in note:
            makeup_holiday_dates.add(d)

    # --------------------------------------------------------
    # 建立「有意義」的假日日期
    # --------------------------------------------------------

    meaningful_dates = []

    for d, row in holiday_by_date.items():

        weekday = d.weekday()
        note = clean_note(row.get("note", ""))

        # 補假
        if d in makeup_holiday_dates:
            meaningful_dates.append(d)
            continue

        # 非週末的政府假日
        if weekday < 5:
            meaningful_dates.append(d)
            continue

        # 週末如果有官方節日名稱／備註
        # 才保留。
        if note:
            meaningful_dates.append(d)
            continue

        # 春節官方假期中的週末
        # 必須保留，因為這不是普通週末。
        if is_spring_festival_date(d):
            meaningful_dates.append(d)
            continue

        # 其他節日若官方備註沒有填，
        # 依日期判斷是否屬正式節日區塊。
        if (
            (d.month == 1 and d.day <= 3)
            or
            (d.month == 2 and d.day in range(27, 29))
            or
            (d.month == 3 and d.day == 1)
            or
            (d.month == 4 and d.day in range(3, 7))
            or
            (d.month == 5 and d.day in range(1, 4))
            or
            (d.month == 6)
            or
            (d.month == 9)
            or
            (d.month == 10 and d.day in range(9, 12))
            or
            (d.month == 10 and d.day in range(23, 27))
            or
            (d.month == 12 and d.day in range(24, 28))
        ):
            # 必須真的屬於假日
            meaningful_dates.append(d)

    meaningful_dates = sorted(set(meaningful_dates))

    # --------------------------------------------------------
    # 逐日建立事件
    #
    # 這裡故意不把所有週末合併成一個「週末假期」。
    # --------------------------------------------------------

    for d in meaningful_dates:

        row = holiday_by_date.get(d, {})

        note = clean_note(
            row.get("note", "")
        )

        # 補假
        if d in makeup_holiday_dates:
            base_name = get_base_holiday_name(
                d,
                note,
            )

            if not base_name:
                base_name = "補假"

            # 春節補假不要變成「初四(補假)」
            # 官方名稱統一使用「春節(補假)」
            if is_spring_festival_date(d):
                title = "春節(補假)"
            else:
                title = f"{base_name}(補假)"

        else:
            title = get_base_holiday_name(
                d,
                note,
            )

            if not title:
                continue

        events.append(
            {
                "date": d,
                "end_date": d + timedelta(days=1),
                "title": title,
                "category": "government",
            }
        )

    # --------------------------------------------------------
    # 補班
    # --------------------------------------------------------

    for d in sorted(makeup_work_dates):

        # 找出補班對應的前後節日名稱
        title = None

        # 優先看前後幾天
        for offset in range(1, 15):
            before = d - timedelta(days=offset)
            after = d + timedelta(days=offset)

            if before in holiday_by_date:
                base = get_base_holiday_name(
                    before,
                    holiday_by_date[before].get("note", ""),
                )

                if base:
                    title = f"[補班]{base}"
                    break

            if after in holiday_by_date:
                base = get_base_holiday_name(
                    after,
                    holiday_by_date[after].get("note", ""),
                )

                if base:
                    title = f"[補班]{base}"
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

    return events


# ============================================================
# 母親節
# ============================================================

def get_mothers_day(year):
    """
    5 月第二個星期日
    """

    d = date(year, 5, 1)

    while d.weekday() != 6:
        d += timedelta(days=1)

    d += timedelta(days=7)

    return d


# ============================================================
# 父親節
# ============================================================

def get_fathers_day(year):
    """
    8 月 8 日
    """

    return date(year, 8, 8)


# ============================================================
# 高雄市停班停課
# ============================================================

def walk_json(value):
    """
    遞迴搜尋 JSON 中所有 dict/list。
    """

    if isinstance(value, dict):
        yield value

        for child in value.values():
            yield from walk_json(child)

    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def find_first_value(obj, keys):
    """
    不分大小寫尋找第一個符合欄位。
    """

    keyset = {
        str(k).strip().lower()
        for k in keys
    }

    for item in walk_json(obj):

        if not isinstance(item, dict):
            continue

        for key, value in item.items():

            if str(key).strip().lower() in keyset:
                if value not in (None, ""):
                    return value

    return None


def parse_kaohsiung_events():
    """
    高雄市政府停班停課 API。

    API 若沒有資料：
    正常回傳空集合，不讓整個行事曆失敗。
    """

    print("\n讀取高雄市停班停課資料...")

    try:
        response = http_get(
            KAOHSIUNG_API,
            timeout=20,
        )

        data = response.json()

    except Exception as exc:
        print(
            f"⚠️ 高雄市停班停課 API 無法讀取：{exc}"
        )

        return []

    events = []

    for item in walk_json(data):

        if not isinstance(item, dict):
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

        d = parse_date(date_value)

        if not d:
            # 嘗試 YYYY-MM-DDTHH...
            text = str(date_value).strip()

            m = re.search(
                r"(\d{4})-(\d{1,2})-(\d{1,2})",
                text,
            )

            if m:
                try:
                    d = date(
                        int(m.group(1)),
                        int(m.group(2)),
                        int(m.group(3)),
                    )
                except ValueError:
                    d = None

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
            title = str(title).strip()
        else:
            title = "高雄市停班停課"

        # 如果內容已經明確包含高雄停班停課，
        # 不需要再重複加文字。
        if "高雄" not in title:
            title = f"高雄市{title}"

        if location:
            location = str(location).strip()

            if (
                location
                and location not in title
                and "全市" not in title
            ):
                title = f"{title} - {location}"

        events.append(
            {
                "date": d,
                "end_date": d + timedelta(days=1),
                "title": title,
                "category": "kaohsiung",
            }
        )

    # 去重
    unique = {}

    for event in events:
        key = (
            event["date"],
            event["title"],
        )

        unique[key] = event

    result = list(unique.values())

    result.sort(
        key=lambda x: (
            x["date"],
            x["title"],
        )
    )

    print(
        f"高雄市停班停課：{len(result)}"
    )

    return result


# ============================================================
# 建立 ICS
# ============================================================

def ics_escape(value):
    if value is None:
        return ""

    value = str(value)

    return (
        value
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def event_uid(event):
    """
    穩定 UUID。

    同一個日期＋標題永遠得到相同 UID，
    避免 iPhone 訂閱後一直產生重複事件。
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
    return d.strftime("%Y%m%d")


def build_ics(events):
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
                f"DTSTART;VALUE=DATE:{format_ics_date(start)}",
                f"DTEND;VALUE=DATE:{format_ics_date(end)}",
                f"SUMMARY:{ics_escape(event['title'])}",
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")

    return "\r\n".join(lines) + "\r\n"


# ============================================================
# 主程式
# ============================================================

def main():

    print("=" * 60)
    print("🇹🇼 台灣生活行事曆產生器")
    print("=" * 60)

    all_events = []

    statistics = {
        "government": 0,
        "makeup_work": 0,
        "mother": 0,
        "father": 0,
        "kaohsiung": 0,
    }

    # --------------------------------------------------------
    # 政府官方假日
    # --------------------------------------------------------

    for year in TARGET_YEARS:

        print(
            f"\n========== {year} =========="
        )

        rows = get_dgpa_csv(year)

        government_events = build_government_events(
            year,
            rows,
        )

        for event in government_events:

            all_events.append(event)

            if event["category"] == "makeup_work":
                statistics["makeup_work"] += 1
            else:
                statistics["government"] += 1

    # --------------------------------------------------------
    # 母親節
    # --------------------------------------------------------

    for year in TARGET_YEARS:

        d = get_mothers_day(year)

        event = {
            "date": d,
            "end_date": d + timedelta(days=1),
            "title": "母親節",
            "category": "mother",
        }

        all_events.append(event)

        statistics["mother"] += 1

    # --------------------------------------------------------
    # 父親節
    # --------------------------------------------------------

    for year in TARGET_YEARS:

        d = get_fathers_day(year)

        event = {
            "date": d,
            "end_date": d + timedelta(days=1),
            "title": "父親節",
            "category": "father",
        }

        all_events.append(event)

        statistics["father"] += 1

    # --------------------------------------------------------
    # 高雄停班停課
    # --------------------------------------------------------

    kaohsiung_events = parse_kaohsiung_events()

    all_events.extend(
        kaohsiung_events
    )

    statistics["kaohsiung"] = len(
        kaohsiung_events
    )

    # --------------------------------------------------------
    # 最終去重
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 輸出統計
    # --------------------------------------------------------

    print("\n" + "=" * 60)
    print("產生結果")
    print("=" * 60)

    print(
        f"政府一般放假事件："
        f"{statistics['government']}"
    )

    print(
        f"政府補班事件："
        f"{statistics['makeup_work']}"
    )

    print(
        f"母親節："
        f"{statistics['mother']}"
    )

    print(
        f"父親節："
        f"{statistics['father']}"
    )

    print(
        f"高雄市停班停課："
        f"{statistics['kaohsiung']}"
    )

    print(
        f"全部事件："
        f"{len(all_events)}"
    )

    for year in TARGET_YEARS:

        count = sum(
            1
            for event in all_events
            if event["date"].year == year
        )

        print(
            f"{year}：{count} 筆"
        )

    # --------------------------------------------------------
    # 寫入 ICS
    # --------------------------------------------------------

    ics_content = build_ics(
        all_events
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        f.write(ics_content)

    print(
        f"\n已產生：{OUTPUT_FILE}"
    )

    print(
        f"事件總數：{len(all_events)}"
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
```
