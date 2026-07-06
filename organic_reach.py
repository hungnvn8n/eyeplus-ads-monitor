"""
Kéo chỉ số reach tự nhiên của Facebook Page qua Insights API.
Chạy: python organic_reach.py [--days 30] [--page PAGE_ID]
"""
import os, sys, requests, argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN")
GRAPH = "https://graph.facebook.com/v19.0"

# Pages Eye Plus
PAGES = {
    "chinh":  ("821332004654252",  "Kính Mắt Eye Plus"),
    "nu":     ("552228558319706",  "Kính Mắt Eye Plus - Nữ"),
    "young":  ("1062539773905872", "Kính Mắt Eye Plus 4Young"),
}

METRICS = [
    "page_impressions_organic_unique",      # Reach tự nhiên page (người)
    "page_post_impressions_organic_unique", # Reach tự nhiên bài đăng
    "page_impressions_viral_unique",        # Reach viral (share/tag)
    "page_impressions_paid_unique",         # Reach trả phí (để so sánh)
    "page_fans_online_per_day",             # Fan online theo ngày
]


def fetch_insights(page_id: str, days: int = 30) -> list[dict]:
    since = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    until = datetime.today().strftime("%Y-%m-%d")

    params = {
        "metric": ",".join(METRICS),
        "period": "day",
        "since": since,
        "until": until,
        "access_token": PAGE_TOKEN,
    }
    r = requests.get(f"{GRAPH}/{page_id}/insights", params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])


def pivot(data: list[dict]) -> dict[str, dict]:
    """Xoay data: {date -> {metric -> value}}"""
    result = {}
    for metric_obj in data:
        name = metric_obj["name"]
        for pt in metric_obj.get("values", []):
            date = pt["end_time"][:10]
            result.setdefault(date, {})[name] = pt["value"]
    return result


def print_table(page_name: str, pivoted: dict[str, dict]):
    label = {
        "page_impressions_organic_unique":      "Reach TN page",
        "page_post_impressions_organic_unique": "Reach TN bài đăng",
        "page_impressions_viral_unique":        "Reach viral",
        "page_impressions_paid_unique":         "Reach trả phí",
        "page_fans_online_per_day":             "Fan online/ngày",
    }
    print(f"\n{'='*72}")
    print(f"  {page_name}")
    print(f"{'='*72}")
    header = f"{'Ngày':<12}" + "".join(f"{v:>16}" for v in label.values())
    print(header)
    print("-" * 72)
    for date in sorted(pivoted.keys()):
        row = pivoted[date]
        line = f"{date:<12}"
        for k in METRICS:
            val = row.get(k, "-")
            if isinstance(val, (int, float)):
                line += f"{int(val):>16,}"
            else:
                line += f"{'—':>16}"
        print(line)


def save_csv(page_id: str, pivoted: dict[str, dict]):
    import csv
    filename = f"organic_reach_{page_id}_{datetime.today().strftime('%Y%m%d')}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Ngày"] + METRICS)
        for date in sorted(pivoted.keys()):
            row = pivoted[date]
            writer.writerow([date] + [row.get(m, "") for m in METRICS])
    print(f"\n  Đã lưu: {filename}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Số ngày nhìn lại (mặc định 30)")
    parser.add_argument("--page", default="chinh", choices=list(PAGES.keys()) + ["all"],
                        help="Page cần xem: chinh / nu / young / all")
    parser.add_argument("--csv", action="store_true", help="Xuất file CSV")
    args = parser.parse_args()

    if not PAGE_TOKEN:
        print("Lỗi: Không tìm thấy FB_PAGE_TOKEN trong .env")
        sys.exit(1)

    pages_to_run = list(PAGES.items()) if args.page == "all" else [(args.page, PAGES[args.page])]

    for key, (page_id, page_name) in pages_to_run:
        try:
            data = fetch_insights(page_id, args.days)
            pivoted = pivot(data)
            print_table(page_name, pivoted)
            if args.csv:
                save_csv(page_id, pivoted)
        except requests.HTTPError as e:
            print(f"\n[LỖI] {page_name}: {e.response.text}")


if __name__ == "__main__":
    main()
