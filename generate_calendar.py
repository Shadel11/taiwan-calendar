import csv
import io
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta


CALENDAR_NAME = "🇹🇼 台灣生活行事曆"

DGPA_DATASET = "https://data.gov.tw/dataset/14718"

KAOHSIUNG_API = (
    "https://openapi.kcg.gov.tw/Api/Service/Get/"
    "95eec21d-4ee7-4920-94ff-d36727bc171f"
)


# =========================================================
# SSL / 網路設定
# =========================================================

def build_ssl_context():
    """
    政府網站目前的部分憑證鏈會讓 GitHub Actions
    Ubuntu / Python OpenSSL 出現：

    CERTIFICATE_VERIFY_FAILED
    Missing Subject Key Identifier

    這裡只關閉 X509 strict 檢查，
    仍然保留正常 HTTPS 憑證驗證與主機名稱驗證。
    """

    context = ssl.create_default_context()

    if hasattr(ssl, "VERIFY_X509_STRICT"):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT

    return context


SSL_CONTEXT = build_ssl_context()


def fetch_bytes(url, timeout=90, retries=3):
    """
    網路下載工具：

    1. HTTPS
    2. SSL context
    3. 自動重試
    4. 政府網站較慢時給較長 timeout
    """

    last_error = None

    for attempt in range(1, retries + 1):

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
                    "Accept": (
                        "text/csv,"
                        "application/json,"
                        "text/plain,"
                        "*/*"
                    ),
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
                time.sleep(3)

    raise last_error


def fetch_text(url, timeout=90, retries=3):

    raw = fetch_bytes(
        url,
        timeout=timeout,
        retries=retries
    )

    return raw.decode(
        "utf-8-sig",
        errors="replace"
    )


def fetch_json(url, timeout=90, retries=3):

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

    value = str(value).strip()

    # YYYYMMDD
    match = re.match(
        r"^(\d{4})(\d{2})(\d{2})$",
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

            return None

    # YYYY/MM/DD
    # YYYY-MM-DD
    match = re.match(
        r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
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

            return None

    return None


# =========================================================
# 官方行政機關辦公日曆
# =========================================================

def get_official_calendar_url(year):

    roc_year = year - 1911

    print(
        f"[官方] 正在尋找 {year} "
        f"（民國 {roc_year} 年）辦公日曆..."
    )

    try:

        html = fetch_text(
            DGPA_DATASET,
            timeout=60,
            retries=3
        )

    except Exception as error:

        print(
            f"[ERROR] 官方資料集頁面取得失敗："
            f"{error}"
        )

        return None

    # HTML entity
    html = (
        html
        .replace("&amp;", "&")
        .replace("&#x2F;", "/")
        .replace("&#47;", "/")
        .replace("&quot;", '"')
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

        # 找指定年度
        if str(roc_year) not in decoded:
            continue

        # 必須是 CSV
        if ".csv" not in decoded.lower():
            continue

        # 政府目前主要使用 FileConversion
        if "FileConversion" not in decoded:
            continue

        if link.startswith("/"):
            link = (
                "https://data.gov.tw"
                + link
            )

        elif link.startswith(
            "//"
        ):
            link = "https:" + link

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

    # 優先一般辦公日曆
    # 排除 Google 行事曆專用版本
    for link in candidates:

        decoded = urllib.parse.unquote(
            link
        )

        if "Google" not in decoded:

            print(
                f"[官方] 找到 {year} CSV："
                f"{link}"
            )

            return link

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


def get_official_holidays(year):

    url = get_official_calendar_url(
        year
    )

    if not url:
        return []

    try:

        print(
            f"[官方] 正在下載 {year} CSV..."
        )

        raw = fetch_bytes(
            url,
            timeout=120,
            retries=3
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

    reader = csv.DictReader(
        io.StringIO(text)
    )

    holidays = []

    for row in reader:

        raw_date = (
            row.get("西元日期")
            or row.get("\ufeff西元日期")
            or row.get("日期")
            or row.get("date")
            or ""
        ).strip()

        holiday_value = (
            row.get("是否放假")
            or row.get("isHoliday")
            or ""
        ).strip()

        # 官方規則：
        # 0 = 上班
        # 2 = 放假
        if holiday_value != "2":
            continue

        event_date = normalize_date(
            raw_date
        )

        if not event_date:
            continue

        description = (
            row.get("備註")
            or row.get("備註說明")
            or row.get("description")
            or ""
        ).strip()

        # 官方備註通常就是：
        # 元旦、春節、228和平紀念日、
        # 兒童節、清明節、端午節、
        # 中秋節、國慶日、補假等等。
        title = description

        if not title:
            title = "台灣政府放假日"

        title = (
            "🇹🇼 "
            + title
        )

        holidays.append({
            "date": event_date,
            "title": title,
            "description": (
                "資料來源：行政院人事行政總處"
                "\n"
                "中華民國政府行政機關辦公日曆表"
                "\n"
                f"資料年度：{year}"
            )
        })

    print(
        f"[完成] {year} 官方放假日："
        f"{len(holidays)} 天"
    )

    return holidays


# =========================================================
# 高雄市天然災害停班停課
# =========================================================

def get_kaohsiung_closures():

    print(
        "[高雄] 正在取得天然災害停班停課資料..."
    )

    try:

        data = fetch_json(
            KAOHSIUNG_API,
            timeout=120,
            retries=4
        )

    except Exception as error:

        print(
            "[ERROR] 高雄停班停課資料取得失敗："
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
            .replace(" ", "")
            .replace("\n", "")
            .replace("\r", "")
        )

        # 只抓停止上班／上課
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

        # 排除照常
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
                "title": "🌪️ 高雄市停班停課",
                "description": (
                    "資料來源：高雄市政府人事處"
                    "\n"
                    "天然災害停止上班上課相關訊息"
                    "\n"
                    + format_record(
                        record
                    )
                )
            })

    # 去重
    unique = {}

    for item in events:

        unique[
            item["date"]
        ] = item

    events = list(
        unique.values()
    )

    events.sort(
        key=lambda item:
        item["date"]
    )

    print(
        f"[完成] 高雄停班停課："
        f"{len(events)} 筆"
    )

    return events


def find_records(data):

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


def extract_dates(record):

    text = json.dumps(
        record,
        ensure_ascii=False
    )

    dates = set()

    # -----------------------------------------------------
    # 西元日期
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # 民國日期
    # -----------------------------------------------------

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

            year = (
                int(parts[0])
                + 1911
            )

            month = int(
                parts[1]
            )

            day = int(
                parts[2]
            )

            event_date = date(
                year,
                month,
                day
            )

            dates.add(
                event_date.isoformat()
            )

        except ValueError:

            pass

    return sorted(
        dates
    )


# =========================================================
# 母親節
# =========================================================

def mothers_day(year):

    # 五月第二個星期日

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
# ICS
# =========================================================

def escape_ics(value):

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def make_event(
    uid,
    event_date,
    title,
    description
):

    d = date.fromisoformat(
        event_date
    )

    end_date = (
        d + timedelta(days=1)
    )

    return "\r\n".join([
        "BEGIN:VEVENT",

        f"UID:{uid}@taiwan-calendar",

        (
            "DTSTART;VALUE=DATE:"
            f"{d.strftime('%Y%m%d')}"
        ),

        (
            "DTEND;VALUE=DATE:"
            f"{end_date.strftime('%Y%m%d')}"
        ),

        f"SUMMARY:{escape_ics(title)}",

        (
            "DESCRIPTION:"
            f"{escape_ics(description)}"
        ),

        "END:VEVENT",
    ])


def format_record(record):

    if not isinstance(
        record,
        dict
    ):
        return str(record)

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


# =========================================================
# 產生完整行事曆
# =========================================================

def generate_calendar():

    today = date.today()

    # 永遠自動產生：
    # 今年 + 明年
    years = [
        today.year,
        today.year + 1
    ]

    print("")
    print(
        "=========================================="
    )
    print(
        "🇹🇼 台灣生活行事曆開始更新"
    )
    print(
        f"更新年度：{years[0]} + {years[1]}"
    )
    print(
        "=========================================="
    )

    events = []

    official_count = {}

    # -----------------------------------------------------
    # 官方國定假日
    # -----------------------------------------------------

    for year in years:

        try:

            holidays = get_official_holidays(
                year
            )

            official_count[
                year
            ] = len(
                holidays
            )

            for item in holidays:

                events.append(
                    make_event(
                        f"official-{item['date']}",
                        item["date"],
                        item["title"],
                        item["description"]
                    )
                )

        except Exception as error:

            official_count[
                year
            ] = 0

            print(
                f"[ERROR] {year} 官方資料處理失敗："
                f"{error}"
            )

    # -----------------------------------------------------
    # 母親節
    # -----------------------------------------------------

    for year in years:

        event_date = mothers_day(
            year
        )

        events.append(
            make_event(
                f"mothers-day-{year}",
                event_date,
                "❤️ 母親節",
                "備注｜台灣常見慶祝節日，不屬政府國定假日"
            )
        )

    # -----------------------------------------------------
    # 父親節
    # -----------------------------------------------------

    for year in years:

        event_date = (
            f"{year}-08-08"
        )

        events.append(
            make_event(
                f"fathers-day-{year}",
                event_date,
                "💙 父親節",
                "備注｜台灣常見慶祝節日，不屬政府國定假日"
            )
        )

    # -----------------------------------------------------
    # 高雄停班停課
    # -----------------------------------------------------

    try:

        closures = (
            get_kaohsiung_closures()
        )

        for item in closures:

            events.append(
                make_event(
                    (
                        "kaohsiung-"
                        f"{item['date']}"
                    ),
                    item["date"],
                    item["title"],
                    item["description"]
                )
            )

    except Exception as error:

        print(
            "[ERROR] 高雄停班停課處理失敗："
            f"{error}"
        )

        closures = []

    # -----------------------------------------------------
    # UID 去重
    # -----------------------------------------------------

    unique = {}

    for event in events:

        uid = None

        for line in event.split(
            "\r\n"
        ):

            if line.startswith(
                "UID:"
            ):

                uid = line
                break

        if uid:

            unique[
                uid
            ] = event

    events = list(
        unique.values()
    )

    # -----------------------------------------------------
    # 日期排序
    # -----------------------------------------------------

    events.sort(
        key=lambda event:
        event
        .split(
            "DTSTART;VALUE=DATE:"
        )[1]
        .split(
            "\r\n"
        )[0]
    )

    # -----------------------------------------------------
    # VCALENDAR
    # -----------------------------------------------------

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

        f"X-WR-CALNAME:{CALENDAR_NAME}",

        "X-WR-TIMEZONE:Asia/Taipei",
    ]

    calendar.extend(
        events
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

    # -----------------------------------------------------
    # 最終統計
    # -----------------------------------------------------

    print("")
    print(
        "=========================================="
    )

    print(
        f"[統計] {years[0]} 官方放假："
        f"{official_count.get(years[0], 0)} 天"
    )

    print(
        f"[統計] {years[1]} 官方放假："
        f"{official_count.get(years[1], 0)} 天"
    )

    print(
        f"[統計] 高雄停班停課："
        f"{len(closures)} 筆"
    )

    print(
        "[統計] 母親節："
        f"{len(years)} 筆"
    )

    print(
        "[統計] 父親節："
        f"{len(years)} 筆"
    )

    print(
        f"[統計] 總行事曆事件："
        f"{len(events)} 個"
    )

    print(
        "=========================================="
    )

    return ics


# =========================================================
# 主程式
# =========================================================

if __name__ == "__main__":

    ics = generate_calendar()

    with open(
        "taiwan.ics",
        "w",
        encoding="utf-8",
        newline=""
    ) as file:

        file.write(
            ics
        )

    print("")
    print(
        "=========================================="
    )

    print(
        "✅ taiwan.ics 產生完成！"
    )

    print(
        f"✅ 共 {ics.count('BEGIN:VEVENT')} "
        "個行事曆事件"
    )

    print(
        "=========================================="
    )
